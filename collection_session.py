"""Shared GUI/CLI lifecycle; default event observation can degrade to snapshots."""
import math
import time
from dataclasses import asdict

from battle_history import BattleRecord, HistoryError

from collector import Collector, SampleUnavailable, BattleChanged, UnsupportedVersion
from event_capture import LiveEventCapture
from event_stream import StreamGap


DEFAULT_FRIDA_REMOTE = '127.0.0.1:27043'


class CollectionSession:
    def __init__(self, adb, serial, *, frida_remote=DEFAULT_FRIDA_REMOTE, audit=None, log=print,
                 collector=None, observer_factory=None, history=None, on_history=None):
        self.collector = collector if collector is not None else Collector(adb, serial, log=log)
        self.frida_remote = frida_remote
        self.observer_factory = observer_factory
        self.observer = None
        self._pending_observers = []
        self.capture = None
        self.audit = audit or (lambda *_args: None)
        self.event_status = 'unavailable'
        self.event_error = None
        self._enemy_session = None
        self._enemy_last_poll = float('-inf')
        self._enemy_payload = None
        self._enemy_event_data = {}
        self.runtime = None
        self.closed = False
        self._resources_closed = False
        # Retain cleanup failures and retired observers across snapshot fallback
        # and battle retries. Shutdown must retry releases and report failures.
        self.cleanup_errors = {}
        self.history = history
        # Display owners get one notification per newly committed record; the
        # callback must not read history itself (the session owns the write).
        self.on_history = on_history
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
                and not self.capture.adapter.statistics.event_coverage_broken):
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
        self._attach_enemy(sample)
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
        self._attach_enemy(sample)
        self.audit('published', sample)
        return sample

    def _attach_enemy(self, sample):
        """Project committed enemy events alongside the independent damage total."""
        session = sample.get('session')
        if session is not None and session != self._enemy_session:
            self._enemy_session = session
            self._enemy_last_poll = float('-inf')
            self._enemy_payload = None
            self._enemy_event_data = {}
            telemetry = getattr(self.collector, 'enemy_telemetry', None)
            if telemetry is not None:
                telemetry.reset()
        stats = sample.get('statistics') or {}
        rows = stats.get('rows') or {}
        known_rows = [value for value in rows.values()
                      if isinstance(value, dict)
                      and isinstance(value.get('damage'), (int, float))]
        effective = [value.get('effective_damage') for value in known_rows]
        if not known_rows:
            our_damage = 0.0
        elif all(isinstance(value, (int, float)) and not isinstance(value, bool)
                 for value in effective):
            our_damage = sum(effective)
        else:
            our_damage = None
        read = getattr(self.collector, 'read_enemies', None)
        if read is None:
            sample['enemy'] = self._enemy_payload or {
                'status': 'unavailable', 'error': '敌人遥测未接入', 'ready': False,
                'rows': [], 'totals': {}, 'series': []}
            return
        now = time.monotonic()
        poll_due = now - self._enemy_last_poll >= 1.0
        event_data = {key: stats[key] for key in ('enemy_entry', 'enemy_phases', 'enemy_state')
                      if key in stats}
        state = event_data.get('enemy_state')
        if poll_due or (state is not None and state['complete']) or event_data != self._enemy_event_data:
            if poll_due:
                self._enemy_last_poll = now
            try:
                context = {'frame': sample.get('frame')} if state is not None else {}
                self._enemy_payload = read(game_time=sample.get('game_time'), our_damage=our_damage,
                                            **event_data, **context)
                self._enemy_event_data = event_data
            except Exception as exc:
                self._enemy_payload = {
                    'status': 'unavailable', 'error': f'敌人遥测失败：{exc}',
                    'ready': False, 'rows': [], 'totals': {}, 'series': []}
        enemy = self._enemy_payload or {
            'status': 'starting', 'error': None, 'ready': False,
            'rows': [], 'totals': {}, 'series': []}
        other_counters = (stats.get('non_team_output') or {}).values()
        other_damage = sum(amount for amount in (row.get('damage') for row in other_counters)
                           if type(amount) in (int, float) and math.isfinite(amount) and amount > 0)
        sample['enemy'] = dict(enemy, non_team_native_damage=other_damage) if other_damage else enemy

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
                # Announce before diagnostic I/O so a journal failure cannot hide
                # a record that is already durable on disk.
                if result.written and self.on_history is not None:
                    self.on_history()
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


def shutdown_report(session):
    """Report what a finished session actually released, from operation outcomes.

    A stale or foreign ``closed`` flag never substitutes for the recorded
    cleanup results: the receipt is built from ``cleanup_errors`` and the
    remaining observer/forward handles.
    """
    cleanup_errors = getattr(session, 'cleanup_errors', {}) if session is not None else {}
    issues = []
    session_closed = ((session is None or bool(getattr(session, 'closed', False)))
                      and not cleanup_errors)
    if not session_closed:
        issues.append('采集会话未关闭')
    observer_released = not cleanup_errors.get('observer')
    if session is not None:
        if getattr(session, '_pending_observers', []):
            observer_released = False
        observer = getattr(session, 'observer', None)
        if observer is not None:
            observer_released = (observer_released and
                                 not getattr(observer, 'cleanup_errors', {}) and
                                 getattr(observer, 'script', None) is None and
                                 getattr(observer, 'session', None) is None)
    if not observer_released:
        issues.append('事件观察器释放未确认')
    for resource, errors in cleanup_errors.items():
        issues.extend(f'{resource} 清理失败：{error}' for error in errors)
    forwards_released = True
    collector = getattr(session, 'collector', None) if session is not None else None
    registry = getattr(collector, 'forward_registry', None)
    owned = getattr(registry, 'owned', {}) if registry is not None else {}
    if owned:
        forwards_released = False
        issues.append(f'仍有 {len(owned)} 个 ADB 转发未释放')
    return dict(ok=not issues, session_closed=session_closed,
                observer_released=observer_released,
                forwards_released=forwards_released, issues=issues)
