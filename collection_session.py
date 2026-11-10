"""Shared GUI/CLI lifecycle; default event observation can degrade to snapshots."""
import time
from dataclasses import asdict

from battle_history import BattleRecord, HistoryError

from collector import Collector, SampleUnavailable, BattleChanged, UnsupportedVersion
from event_capture import LiveEventCapture
from event_stream import StreamGap


DEFAULT_FRIDA_REMOTE = '127.0.0.1:27043'


class CollectionSession:
    def __init__(self, adb, serial, *, frida_remote=DEFAULT_FRIDA_REMOTE, audit=None, log=print,
                 collector=None, observer_factory=None, history=None):
        self.collector = collector if collector is not None else Collector(adb, serial, log=log)
        self.frida_remote = frida_remote
        self.observer_factory = observer_factory
        self.observer = None
        self._pending_observers = []
        self.capture = None
        self.audit = audit or (lambda *_args: None)
        self.event_status = 'unavailable'
        self.event_error = None
        self.runtime = None
        self.closed = False
        self._resources_closed = False
        # Retain cleanup failures and retired observers across snapshot fallback
        # and battle retries. Shutdown must retry releases and report failures.
        self.cleanup_errors = {}
        self.history = history
        self.history_error = None
        self._pending_history = None
        self._observer_generation = None

    def enable_lifecycle(self, runtime=None):
        """Use ordered battle generations in the GUI and capture entry points."""
        if self.capture is not None:
            raise RuntimeError('enable lifecycle before connecting')
        from battle_lifecycle import BattleRuntime
        self.runtime = runtime if runtime is not None else BattleRuntime()
        self._pending_history = self.runtime.pending_history
        self.runtime.record = lambda observation: self.audit('lifecycle_observation', observation)

    def connect(self):
        self.collector.connect(locate=False)
        self.audit('connected', {'diagnostics': vars(self.collector.diagnostics),
                                 'pid': self.collector.core.mc.pid,
                                 'process_instance': getattr(self.collector.core.mc, 'process_instance', None)})
        if self.frida_remote:
            self._attach_observer()
        self.capture = LiveEventCapture(self.collector, self.observer, audit=self.audit)
        if self.runtime is not None:
            self.collector.event_capability = dict(status=self.event_status, error=self.event_error)
            self.collector.locate_lifecycle()
            self.audit('lifecycle_header', {'feed': self.runtime.feed.id})

    def _attach_observer(self):
        self.event_status, self.event_error = 'attaching', None
        try:
            if self.observer_factory is None:
                from event_observer import FridaEventObserver
                self.observer_factory = FridaEventObserver
            self.observer = self.observer_factory(self.collector, self.frida_remote)
            self.observer.start()
            self.event_status = 'ready'
            self.audit('observer_ready', {'remote': self.frida_remote})
        except Exception as exc:
            self.event_status, self.event_error = 'failed', str(exc)
            self.audit('observer_error', {'reason': str(exc)})
            self._retire_observer()

    def _retire_observer(self):
        """Remove an unusable observer from capture, not from cleanup ownership."""
        observer, self.observer = self.observer, None
        if observer is None:
            return
        try:
            observer.stop()
        except BaseException as cleanup:
            # Store the owner before audit I/O or an interrupt can escape.
            self._pending_observers.append(observer)
            self.cleanup_errors.setdefault('observer', []).append(str(cleanup))
            self.audit('observer_cleanup_error', {'reason': str(cleanup)})
            if not isinstance(cleanup, Exception):
                raise

    def _prepare_battle_observer(self, observation):
        generation = observation.generation
        previous, self._observer_generation = self._observer_generation, generation
        error = (observation.events or {}).get('error')
        if (previous is None or previous == generation or not self.frida_remote or
                (self.event_status not in ('failed', 'gap') and not error)):
            return False, self.observer
        # The feed has confirmed a different battle. A failed attempt belongs
        # to this generation even when its first event barrier needs a retry.
        old_status, old_error = self.event_status, error or self.event_error
        self._retire_observer()
        self._attach_observer()
        self.collector.event_capability = dict(status=self.event_status, error=self.event_error)
        self.audit('observer_retry', dict(generation=generation, previous_status=old_status,
                                         previous_error=old_error, event_status=self.event_status,
                                         event_error=self.event_error))
        return True, self.observer

    def locate(self, *, force=False, wait_seconds=0, cancelled=None):
        if self.runtime is not None:
            return
        deadline = time.monotonic() + wait_seconds
        while True:
            if cancelled is not None and cancelled():
                raise InterruptedError('collection cancelled before battle')
            try:
                self.collector.locate_battle(force=force)
                self.audit('battle_located', {})
                return
            except (BattleChanged, UnsupportedVersion):
                raise
            except SampleUnavailable as exc:
                self.audit('waiting_for_battle', {'reason': str(exc)})
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)

    def read(self):
        if self.capture is None:
            raise RuntimeError('collection session is not connected')
        if self.runtime is not None:
            return self._read_lifecycle()
        try:
            stats = self.capture.adapter.statistics
            result = (self.capture.snapshot() if self.capture.closed or stats is not None and stats.end_time is not None
                      else self.capture.sample())
        except StreamGap as exc:
            self.event_status, self.event_error = 'gap', str(exc)
            self.audit('event_gap', {'reason': str(exc), 'statistics': self.capture.adapter.result()})
            if self.observer is not None:
                try:
                    self.observer.stop()
                except Exception as cleanup:
                    self.cleanup_errors.setdefault('observer', []).append(str(cleanup))
                    self.audit('observer_cleanup_error', {'reason': str(cleanup)})
            result = self.capture.snapshot()
        sample = dict(self.capture.last_sample)
        if sample.get('state') == 3 and self.capture.adapter.statistics.end_time is None:
            self.capture.adapter.finish(sample['game_time'])
            result = self.capture.adapter.result()
        stream = self.capture.adapter.event_stream
        if (stream is not None and stream.closed and self.capture.adapter.statistics.end_time is not None
                and self.capture.adapter.statistics.event_coverage_end is None):
            self.event_status = 'finished'
        elif stream is not None and stream.closed and self.event_status in ('ready', 'recording'):
            self.event_status = 'gap'
            self.event_error = 'event coverage closed by the statistics adapter'
        elif stream is not None and not stream.closed:
            self.event_status = 'recording'
        sample.update(statistics=result, event_status=self.event_status, event_error=self.event_error,
                      snapshot_revision=result.get('snapshot_revision'),
                      event_watermark=({'sequence': stream.sequence, 'frame': stream.frame,
                                        'game_time': stream.game_time} if stream else None))
        self.audit('published', sample)
        return sample

    def _read_lifecycle(self):
        from event_capture import CaptureRetry
        from snapshot_adapter import FrameMismatch
        from roster import RosterUnavailable
        try:
            observation = self.runtime.poll(self.collector, self.observer,
                                           prepare_observer=self._prepare_battle_observer)
        except (CaptureRetry, FrameMismatch, RosterUnavailable, SampleUnavailable) as exc:
            if isinstance(exc, BattleChanged):
                raise
            raise CaptureRetry(str(exc)) from exc
        finally:
            self._drain_archives()
        if self.runtime.sessions.capture is not None:
            self.capture = self.runtime.sessions.capture
        if observation.kind not in ('sample', 'terminal') or observation.sample is None:
            raise CaptureRetry(f'lifecycle: {observation.kind}/{observation.reason}')
        sample = self.runtime.display_sample()
        if sample is None:
            raise CaptureRetry('waiting for a reliable battle baseline')
        status = sample['event_status']
        if status == 'unavailable':
            sample.update(event_status=self.event_status, event_error=self.event_error)
        else:
            self.event_status, self.event_error = status, sample['event_error']
        if (self.runtime.sessions.state == 'finalizing' and
                sample['event_status'] == 'recording'):
            sample['event_status'] = self.event_status = 'finished'
        self.audit('published', sample)
        return sample

    def _drain_archives(self):
        archives = self.runtime.sessions.pop_archives()
        if self.history is not None:
            # Claim the entire batch before diagnostic I/O can interrupt handoff.
            self._pending_history.extend(archives)
        for archive in archives:
            self.audit('archive_ready', archive)
        if self.history is None:
            return
        self.history_error = None
        # Attempt each handoff once per drain. A bad record must not starve
        # other battles or be retried indefinitely in this same read.
        for _ in range(len(self._pending_history)):
            archive = self._pending_history.popleft()
            try:
                result = self.history.save(BattleRecord.from_archive(archive))
            except HistoryError as exc:
                # Keep failures across reads, closes and transport replacement.
                self._pending_history.append(archive)
                if self.history_error is None:
                    self.history_error = str(exc)
                self.audit('history_error', dict(battle_id=archive['battle_id'], error=str(exc)))
            else:
                self.audit('history_saved', dict(battle_id=archive['battle_id'], written=result.written,
                           diagnostics=[asdict(d) for d in result.diagnostics]))

    def close(self):
        if self.closed:
            return
        errors = []
        if not self._resources_closed:
            try:
                if self.runtime is not None:
                    self._drain_archives()
                    self.runtime.disconnected('capture_stopped')
                    self.audit('lifecycle_summary', self.runtime.sessions.live())
                if self.capture is not None:
                    self.capture.close()
                    self.audit('coverage_closed', self.capture.adapter.result())
            except BaseException as exc:
                errors.append(('capture', exc))
            for observer in tuple(self._pending_observers) + ((self.observer,)
                                                            if self.observer is not None else ()):
                try:
                    observer.stop()
                except BaseException as exc:
                    errors.append(('observer', exc))
                else:
                    if observer in self._pending_observers:
                        self._pending_observers.remove(observer)
            try:
                self.collector.close()
            except BaseException as exc:
                errors.append(('collector', exc))
            for resource, exc in errors:
                self.cleanup_errors.setdefault(resource, []).append(str(exc))
            self._resources_closed = not errors

        self.event_status = 'stopped'
        try:
            self.audit('stopped', {'cleanup_errors': self.cleanup_errors} if self.cleanup_errors else {})
        except BaseException as exc:
            self.cleanup_errors.setdefault('audit', []).append(str(exc))
            self.closed = False
            raise
        self.closed = self._resources_closed and not self.cleanup_errors
        if errors:
            raise errors[0][1]
