"""Shared GUI/CLI lifecycle; default event observation can degrade to snapshots."""
import math
import time
from dataclasses import asdict

from autochess_context import native_local_player
from battle_history import BattleRecord, HistoryError

from collector import (Collector, SampleUnavailable, BattleChanged, ProcessChanged,
                       UnsupportedVersion, collector_process)
from enemy_telemetry import defense_enemy_projection
from event_capture import CaptureRetry, LiveEventCapture
from event_observer import ObserverStatus
from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS
from event_probe import EventProbe


DEFAULT_FRIDA_REMOTE = '127.0.0.1:27043'
WAITING_GAME_INIT = 'waiting_game_init'
_BOOT_POLL_PERIOD = 1.0
_LIFECYCLE_LOCATE_RETRY = 5.0


def _local_defense_enemy_damage(sample, scope):
    unknown = dict(scope='local_account', complete=False, by_uid={})
    if scope.get('phase') != 4:
        return unknown
    player = native_local_player(sample)
    if player is None:
        return unknown
    multiplayer = sample['multiplayer']
    statistics = player['statistics']
    if (multiplayer.get('scope_kind') != 'defense' or multiplayer.get('context_identity') != scope
            or statistics.get('session') != f"{multiplayer['scope']}:{player['player_uid']}"
            or statistics.get('game_time') != sample.get('game_time')):
        return unknown
    damage = statistics.get('enemy_damage')
    if not isinstance(damage, dict):
        return unknown
    return dict(damage, scope='local_account', complete=(damage.get('complete') is True
                                                         and player.get('coverage_complete') is True))


class CollectionSession:
    def __init__(self, adb, serial, *, frida_remote=DEFAULT_FRIDA_REMOTE, audit=None, log=print,
                 collector=None, observer_factory=None, prepare_observer=None,
                 history=None, on_history=None, on_history_result=None, state_dir=None,
                 autochess_enabled=None, debug_mode=None):
        self.collector = (collector if collector is not None else
                          Collector(adb, serial, log=log, state_dir=state_dir))
        self.frida_remote = frida_remote
        self.observer_factory = observer_factory
        self.prepare_observer = prepare_observer
        self.autochess_enabled = autochess_enabled
        self.debug_mode = debug_mode
        self.observer: EventProbe | None = None
        self._pending_observers = []
        self.capture = None
        self.audit = audit or (lambda *_args: None)
        self.event_status = 'unavailable'
        self.event_error = None
        self._next_boot_check = float('-inf')
        self._next_lifecycle_locate = float('-inf')
        self._last_boot_probe = None
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
        self.on_history_result = on_history_result
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

    def _is_native_pc(self):
        diagnostics = getattr(self.collector, 'diagnostics', None)
        return getattr(diagnostics, 'platform', None) == 'native_pc'

    def _observer_requested(self):
        if self._is_native_pc():
            # Native Windows observation has no remote Android endpoint.
            # The empty string is the explicit --no-events sentinel.
            return self.runtime is not None and self.frida_remote is None
        return bool(self.frida_remote)

    def connect(self):
        # All capabilities, including snapshot-only collection, use the same
        # generation, terminal-evidence and archive owners.
        if self.runtime is None:
            self.enable_lifecycle()
        self.collector.connect(locate=False)
        process = collector_process(self.collector)
        self.audit('connected', {'diagnostics': vars(self.collector.diagnostics),
                                 'pid': process.pid,
                                 'process_instance': getattr(process, 'process_instance', None)})
        if self._is_native_pc():
            self.event_error = getattr(self.collector, 'event_capability', {}).get('error')
            if self.observer_factory is not None and not self._observer_requested():
                self.event_error = 'PC 观察者需要生命周期模式及 frida_remote=None'
            if self._observer_requested() and callable(getattr(self.collector, 'observer_boot_ready', None)):
                self.event_status, self.event_error = WAITING_GAME_INIT, None
                self._next_boot_check = float('-inf')
                if not self._finish_when_game_ready():
                    self.audit('observer_waiting_game_init', {'pid': process.pid})
                return
            self._finish_connect()
            return
        if not self.frida_remote:
            capability = getattr(self.collector, 'event_capability', {})
            self.event_error = capability.get('error')
            self._finish_connect()
            return
        self.event_status, self.event_error = WAITING_GAME_INIT, None
        self._next_boot_check = float('-inf')
        if not self._finish_when_game_ready():
            self.audit('observer_waiting_game_init', {'pid': collector_process(self.collector).pid})

    def _finish_when_game_ready(self):
        if self.event_status != WAITING_GAME_INIT:
            return True
        now = time.monotonic()
        if now < self._next_boot_check:
            return False
        self._next_boot_check = now + _BOOT_POLL_PERIOD
        pid = collector_process(self.collector).pid
        source = 'build_metadata'
        if not self.collector.observer_boot_ready():
            fast_reason = getattr(self.collector, 'observer_boot_reason', None)
            lifecycle_bound = self.runtime is not None and self.capture is not None
            lifecycle_ready = getattr(self.collector, 'observer_lifecycle_ready', None)
            # A provider must explicitly supply this readiness proof. Native
            # PC keeps its VM initialization gate; scene presence cannot replace it.
            fallback_ready = (not self._is_native_pc() and lifecycle_bound
                              and callable(lifecycle_ready) and lifecycle_ready())
            probe = dict(pid=pid, fast_reason=fast_reason,
                         lifecycle_reason=(getattr(self.collector, 'observer_boot_reason', None)
                                           if lifecycle_bound else 'lifecycle_unbound'),
                         lifecycle_ready=bool(fallback_ready))
            if probe != self._last_boot_probe:
                self.audit('observer_game_init_probe', probe)
                self._last_boot_probe = probe
            if not fallback_ready:
                return False
            source = 'lifecycle'
        self.audit('observer_game_init_ready', {'pid': pid, 'source': source})
        self._finish_connect()
        return True

    def _finish_connect(self):
        if self._observer_requested():
            self._attach_observer()
        self._start_capture()

    def _start_capture(self):
        if self.runtime is not None:
            self.collector.event_capability = dict(status=self.event_status, error=self.event_error)
            if self.capture is None:
                self.collector.locate_lifecycle()
                self.audit('lifecycle_header', {'feed': self.runtime.feed.id})
        if self.capture is None:
            self.capture = LiveEventCapture(self.collector, self.observer, audit=self.audit)

    def _start_pending_lifecycle(self):
        if (self._is_native_pc() or self.runtime is None
                or not callable(getattr(self.collector, 'observer_lifecycle_ready', None))
                or time.monotonic() < self._next_lifecycle_locate):
            raise CaptureRetry('等待游戏初始化')
        try:
            self._start_capture()
        except (BattleChanged, UnsupportedVersion):
            raise
        except (OSError, RuntimeError, UnicodeError) as exc:
            self._next_lifecycle_locate = time.monotonic() + _LIFECYCLE_LOCATE_RETRY
            self.audit('observer_lifecycle_locate_retry', {
                'pid': collector_process(self.collector).pid, 'reason': getattr(exc, 'reason', str(exc)),
            })
            raise CaptureRetry('等待游戏初始化') from exc

    def wait_for_game_init(self, *, wait_seconds, cancelled=None):
        """Bound the CLI readiness wait, using the same probe as live collection."""
        deadline = time.monotonic() + wait_seconds
        while self.event_status == WAITING_GAME_INIT:
            if cancelled is not None and cancelled():
                raise InterruptedError('collection cancelled before game initialization')
            try:
                game_ready = self._finish_when_game_ready()
            except ProcessChanged:
                if self.runtime is not None and self.capture is not None:
                    self.runtime.process_changed()
                    self._drain_archives()
                raise
            if game_ready:
                return
            if (not self._is_native_pc() and self.runtime is not None
                    and callable(getattr(self.collector, 'observer_lifecycle_ready', None))):
                # The headless entry waits before its first public read. Use
                # the same guarded lifecycle path as desktop collection so
                # scene readiness and pre-observer counters can be established.
                try:
                    self.read()
                except CaptureRetry:
                    pass
                if self.event_status != WAITING_GAME_INIT:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SampleUnavailable('等待游戏初始化超时')
            time.sleep(min(0.2, remaining))

    def _attach_observer(self):
        # A failed stop can leave live hooks or a native session behind. The
        # session keeps their ownership across snapshot fallback and must not
        # construct another observer until every retired owner is released.
        if not self._retire_observer():
            self.event_status = 'failed'
            self.event_error = '旧事件观察者尚未释放，请重试清理后重新连接'
            self.audit('observer_attach_blocked', {
                'reason': self.event_error, 'pending_observers': len(self._pending_observers),
            })
            return False
        self.event_status, self.event_error = 'attaching', None
        try:
            if self.prepare_observer is not None:
                ok, detail = self.prepare_observer()
                if not ok:
                    raise RuntimeError(detail)
            if self.observer_factory is None:
                from event_observer import FridaEventObserver, NativePcFridaObserver
                self.observer_factory = NativePcFridaObserver if self._is_native_pc() else FridaEventObserver
            self.observer = self.observer_factory(self.collector, self.frida_remote)
            if self.autochess_enabled is not None:
                configure = getattr(self.observer, 'set_autochess_setting', None)
                if callable(configure):
                    configure(self.autochess_enabled)
            timing = getattr(self.observer, 'set_request_timing_audit', None) if self._is_native_pc() else None
            if callable(timing):
                if self.debug_mode is None:
                    timing(self.audit)
                else:
                    timing(self.audit, enabled=self.debug_mode)
            self.observer.start()
            self.event_status = 'ready'
            self.audit('observer_ready', {
                'remote': self.frida_remote, **getattr(self.observer, 'probe_modes', {}),
            })
            return True
        except Exception as exc:
            self.event_status, self.event_error = 'failed', str(exc)
            failure = {'reason': str(exc), 'error_type': type(exc).__name__}
            code = getattr(exc, 'code', None)
            if type(code) in (str, int):
                failure['code'] = code
            script_error = getattr(self.observer, 'script_error', None)
            if isinstance(script_error, dict) and script_error:
                failure['script_error'] = dict(script_error)
            modes = getattr(self.observer, 'probe_modes', None)
            if isinstance(modes, dict) and isinstance(modes.get('probe_source'), dict):
                failure['probe_source'] = dict(modes['probe_source'])
            self.audit('observer_error', failure)
            self._retire_observer()
            return False

    def _retire_observer(self):
        """Remove an unusable observer from capture, not from cleanup ownership."""
        observer, self.observer = self.observer, None
        if observer is not None and self.runtime is not None:
            self.runtime.observer_retired(observer)
        if self.capture is not None and self.capture.observer is observer:
            self.capture.observer = None
        if observer is not None and observer not in self._pending_observers:
            # Claim ownership before stop or diagnostic I/O can fail.
            self._pending_observers.append(observer)
        for pending in tuple(self._pending_observers):
            try:
                pending.stop()
            except BaseException as cleanup:
                self.cleanup_errors.setdefault('observer', []).append(str(cleanup))
                self.audit('observer_cleanup_error', {'reason': str(cleanup), 'error_type': type(cleanup).__name__})
                if not isinstance(cleanup, Exception):
                    raise
            else:
                self._pending_observers.remove(pending)
        return not self._pending_observers

    def _prepare_battle_observer(self, observation):
        generation = observation.generation
        previous, self._observer_generation = self._observer_generation, generation
        error = (observation.events or {}).get('error')
        if (self.event_status == WAITING_GAME_INIT or
                previous is None or previous == generation or not self._observer_requested()):
            return False, self.observer
        status = getattr(self.observer, 'status', None)
        # Sampling admission and transport lifetime are independent. A guarded
        # read can fail while the producer, cursor and queued prefix remain live.
        # An untyped external provider has no positive transport-health proof.
        observer_ready = self.observer is not None and (
            status == ObserverStatus.READY or (status is None and not error))
        process_owned = self.observer is not None and not getattr(
            self.observer, 'can_restart_in_process', True)
        if observer_ready or process_owned:
            if error or not observer_ready:
                self.audit('observer_recovery_deferred', dict(
                    generation=generation, observer_status=status,
                    code='sample_rejected' if observer_ready else 'observer_unavailable',
                    can_restart_in_process=not process_owned))
            return False, self.observer
        # The feed has confirmed a different battle. A failed attempt belongs
        # to this generation even when its first event barrier needs a retry.
        old_status, old_error = self.event_status, error or self.event_error
        self._attach_observer()
        self.collector.event_capability = dict(status=self.event_status, error=self.event_error)
        self.audit('observer_retry', dict(generation=generation, previous_status=old_status,
                                         previous_error=old_error, event_status=self.event_status,
                                         event_error=self.event_error))
        return True, self.observer

    def locate(self, *, force=False, wait_seconds=0, cancelled=None):
        """Compatibility readiness gate; BattleFeed alone binds battle roots.

        `force` no longer drives a second battle locator. Each read acquires
        guarded lifecycle evidence, including when no battle is active yet.
        """
        self.wait_for_game_init(wait_seconds=wait_seconds, cancelled=cancelled)

    def read(self):
        try:
            if not self._finish_when_game_ready() and self.capture is None:
                self._start_pending_lifecycle()
        except ProcessChanged:
            if self.runtime is not None and self.capture is not None:
                self.runtime.process_changed()
                self._drain_archives()
            raise
        if self.capture is None:
            raise RuntimeError('collection session is not connected')
        return self._read_lifecycle()

    def _read_lifecycle(self):
        from snapshot_adapter import FrameMismatch
        from roster import RosterUnavailable
        try:
            observation = self.runtime.poll(self.collector, self.observer,
                                           prepare_observer=self._prepare_battle_observer)
        except (CaptureRetry, FrameMismatch, RosterUnavailable, SampleUnavailable) as exc:
            if isinstance(exc, (CaptureRetry, BattleChanged)):
                raise
            raise CaptureRetry(str(exc)) from exc
        finally:
            self._drain_archives()
        if self.runtime.sessions.capture is not None:
            self.capture = self.runtime.sessions.capture
        if (observation.kind not in ('sample', 'terminal') or observation.sample is None
                or observation.generation != self.runtime.sessions.generation):
            raise CaptureRetry(f'lifecycle: {observation.kind}/{observation.reason}')
        sample = self.runtime.display_sample()
        if sample is None:
            raise CaptureRetry('waiting for a reliable battle baseline')
        status = sample['event_status']
        if status == 'unavailable' or self.event_status == WAITING_GAME_INIT:
            sample.update(event_status=self.event_status, event_error=self.event_error)
        else:
            self.event_status, self.event_error = status, sample['event_error']
        self._attach_enemy(sample)
        self.audit('published', sample)
        return sample

    def _attach_enemy(self, sample):
        """Project enemy lifecycle and HP damage at the same committed watermark."""
        events_only = sample.get('native_counter_scope') == 'events_only'
        event_scope = sample.get('enemy_world_scope') or {}
        context_phase = (sample.get('autochess_context') or {}).get('phase')
        defense = events_only and event_scope.get('phase', context_phase) == 4
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
        if events_only or sample.get('native_counter_scope') == NATIVE_AUTOCHESS_CHARACTER_COUNTERS:
            # Neither the event-only baseline nor unscoped character counters
            # establish a native team-damage amount.
            our_damage = None
        elif not known_rows:
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
        frame_bound = getattr(self.collector, 'enemy_frame_bound', False)
        event_data = {key: stats[key] for key in ('enemy_entry', 'enemy_phases', 'enemy_state', 'enemy_damage')
                      if key in stats}
        if defense:
            event_data['enemy_damage'] = _local_defense_enemy_damage(sample, event_scope)
        state = event_data.get('enemy_state')
        if (frame_bound or poll_due or (state is not None and state['complete'])
                or event_data != self._enemy_event_data):
            if poll_due:
                self._enemy_last_poll = now
            try:
                context = {'frame': sample.get('frame')} if frame_bound or state is not None else {}
                if events_only and frame_bound:
                    # A terminal END context may already be outside the combat
                    # scope. Use the committed world prefix's own identity.
                    context.update(allow_partial=True, event_scope=event_scope)
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
        if defense:
            sample['enemy'] = defense_enemy_projection(enemy)
            return
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
                if result.written:
                    if self.on_history_result is not None:
                        self.on_history_result(result)
                    if self.on_history is not None:
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
                    if self.capture is not None:
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
