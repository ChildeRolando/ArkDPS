"""DPS is measured in game seconds, never wall-clock seconds."""
import time
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps


_timing_window = ContextVar('arkdps_timing_window', default=None)


class TimingWindow:
    """Bounded, opt-in wall-time accounting; never called per damage event.

    A window belongs to one host thread. Nested phases retain both inclusive
    and exclusive time. Empty/missing phases are unknown, not measured zeroes.
    Emission happens at batch boundaries, at most once per second.
    """
    def __init__(self, enabled, sink, *, source, clock=time.perf_counter_ns):
        self.enabled, self.sink, self.source, self.clock = enabled, sink, source, clock
        self.rows, self.stack = {}, []
        self.started = None
        self.failures = 0

    def add(self, phase, elapsed, exclusive=None):
        if phase not in self.rows and len(self.rows) >= 32:
            phase = 'other'
        row = self.rows.setdefault(phase, [0, 0, 0, 0])
        row[0] += 1
        row[1] += max(0, elapsed)
        row[2] += max(0, elapsed if exclusive is None else exclusive)
        row[3] = max(row[3], elapsed)

    def clear(self):
        self.rows.clear()
        self.started = None

    @contextmanager
    def batch(self):
        if not self.enabled():
            self.clear()
            yield
            return
        if self.started is None:
            self.started = self.clock()
        token = _timing_window.set(self)
        try:
            yield
        finally:
            _timing_window.reset(token)
            self.flush()

    @contextmanager
    def phase(self, name):
        start = self.clock()
        frame = [0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = self.clock() - start
            self.stack.pop()
            if self.stack:
                self.stack[-1][0] += elapsed
            self.add(name, elapsed, elapsed - frame[0])

    def flush(self):
        now = self.clock()
        if self.started is None:
            self.started = now
        if now - self.started < 1_000_000_000:
            return
        data = dict(schema=1, source=self.source, pid=os.getpid(),
                    utc=datetime.now(timezone.utc).isoformat(),
                    monotonic_ns=now, window_ns=now-self.started,
                    clock='perf_counter_ns', sampling='all_batches',
                    emission_failures=self.failures,
                    phases={name: dict(calls=v[0], inclusive_ns=v[1], exclusive_ns=v[2], max_ns=v[3])
                            for name, v in self.rows.items()})
        self.rows.clear()
        self.started = now
        try:
            self.sink('performance_timing', data)
        except Exception:
            self.failures += 1  # Diagnostics must not change delivery/ACK behavior.


def timed(name):
    """No clock reads when there is no explicitly enabled batch."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            window = _timing_window.get()
            if window is None:
                return function(*args, **kwargs)
            with window.phase(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def timing_batch(function):
    with_phase = timed(function.__qualname__)(function)
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._performance_timings.batch():
            return with_phase(self, *args, **kwargs)
    return wrapped


def observation_age(sample, now=None):
    if sample is None or 'captured_monotonic' not in sample:
        return float('inf')
    return max(0.0,(time.monotonic() if now is None else now)-sample['captured_monotonic'])

NATIVE_CAPTURE_RETRY_REASONS = frozenset((
    'native_fresh_publication_not_ready', 'native_publication_not_ready',
    'native_publication_token_expired', 'native_publication_range_unavailable',
    'native_publication_unserved_prefix', 'native_terminal_publication_not_ready',
    'other', 'none'))

# Native async profiling is a separate log lane, never an input to statistics.
# Keep this dependency-free allowlist shared by acquisition and journal output.
_ASYNC_HOOK_LABELS = frozenset((
    'Entity.ApplyModifier', 'Entity.OnAfterOutputDamage', 'Unit.OnAfterOutputDamage',
    'Unit.OnTakeDamage', 'Entity.OnTakeDamage', 'Entity.SetHpInternal',
    'Entity.SetHpInternalDirectly', 'Entity.OnAttributeDirty', 'hp.Entity.SetHpInternal',
    'hp.Entity.SetHpInternalDirectly', 'hp.Entity.OnAttributeDirty', 'enemy entry Born',
    'enemy entry _UpdatePlayerSideAndMapLayer', 'Enemy.Born', 'Enemy._UpdatePlayerSideAndMapLayer',
    'Enemy.DoFakeDeath', 'Enemy.DoReborn', 'Enemy.OnFinish', 'Entity.OnFinish',
    'BObject.OnRecycle', 'Character.Born', 'BasicSkill.OnCastFinish', 'Character.OnSkillStart',
    'Character.SwitchToSkillState', 'Character.OnSkillCastSucceed', 'Character.OnToggleSkillStart',
    'Character.OnSkillRetriggered', 'Character.OnSkillFinish', 'Character.SwitchOutFromSkillState',
    'Character.OnAfterSkill', 'BattleController.OnCharacterFinished', 'Buff.OnTakeDamage',
    'BlockDamage._AssignDynamicVar', 'BattleFormula.CalculateDamage', 'BattleFormula.CalculateDamageNoEvent',
    'BattleFormula.CalculateDamageInternal', 'AttackInfo.GetFinalAtk',
    'Modifier.NewDamageModifierAfterCalculate', 'BattleController.set_state',
    'BattleController.Destroy', 'BattleController.ClearRunTimeData'))
_ASYNC_UNAVAILABLE = frozenset(('missing', 'invalid_schema', 'identity_unavailable', 'identity_mismatch',
    'invalid_numbers', 'invalid_lanes', 'capacity_exceeded', 'invalid_lane_row', 'invalid_samples',
    'invalid_sample_row', 'unsupported_semantics'))


def sanitize_native_async_capture(data, *, native=None, identity=None):
    """Only fixed-schema bounded diagnostics; invalid evidence stays unavailable.

    identity is the observer's already verified (PID, process FILETIME) pair.
    Journal callers may supply the same pair from the already sanitized record.
    No process, wall-clock, native, or gameplay operation happens here.
    """
    def integer(value, maximum=2**64-1, minimum=0):
        return type(value) is int and minimum <= value <= maximum

    def pair(value):
        return (type(value) in (tuple, list) and len(value) == 2
                and integer(value[0], 0xffffffff, 1) and integer(value[1], minimum=1))

    bound = tuple(identity) if pair(identity) else None
    def unavailable(reason):
        result = dict(schema=1, available=False, reason=reason)
        if bound:
            result.update(pid=bound[0], process_created_filetime=bound[1])
        return result

    if data is None:
        return unavailable('missing')
    if type(data) is not dict:
        return unavailable('invalid_schema')
    if data.get('schema') == 1 and data.get('available') is False:
        reason = data.get('reason')
        return unavailable(reason if type(reason) is str and reason in _ASYNC_UNAVAILABLE else 'invalid_schema')
    if type(data.get('version')) is not int or data['version'] != 1:
        return unavailable('invalid_schema')
    if type(native) is dict:
        native_pair = (native.get('pid'), native.get('process_created_filetime'))
        if type(native.get('schema')) is not int or native['schema'] != 1 or not pair(native_pair):
            return unavailable('identity_unavailable')
        if bound and bound != native_pair:
            return unavailable('identity_mismatch')
        bound = native_pair
    if not bound:
        return unavailable('identity_unavailable')
    # On journal re-sanitization, do not relabel a record from another birth.
    if 'pid' in data or 'process_created_filetime' in data:
        if (data.get('pid'), data.get('process_created_filetime')) != bound:
            return unavailable('identity_mismatch')
    numbers = ('reserved_ticket', 'processed_ticket', 'closed_ticket', 'published_ticket',
        'desired_mode_epoch', 'published_mode_epoch', 'activity_generation', 'active', 'failure_code',
        'captured_prefix_lag_records', 'publication_lag_records', 'published_event_sequence',
        'delivered_event_sequence', 'read_prefix_lag_events', 'queue_age_qpc_frequency')
    booleans = ('publication_ready', 'queue_age_available')
    if (any(not integer(data.get(key)) for key in numbers)
            or any(type(data.get(key)) is not bool for key in booleans)
            or not integer(data['failure_code'], 9) or data['queue_age_qpc_frequency'] == 0
            or not data['published_ticket'] <= data['closed_ticket'] <= data['processed_ticket'] <= data['reserved_ticket']
            or data['delivered_event_sequence'] > data['published_event_sequence']
            or data['captured_prefix_lag_records'] != data['reserved_ticket'] - data['processed_ticket']
            or data['publication_lag_records'] != data['processed_ticket'] - data['published_ticket']
            or data['read_prefix_lag_events'] != data['published_event_sequence'] - data['delivered_event_sequence']):
        return unavailable('invalid_numbers')
    result = dict(schema=1, version=1, available=True, pid=bound[0], process_created_filetime=bound[1])
    result.update((key, data[key]) for key in numbers + booleans)
    if 'sampling' in data:
        sampling = data['sampling']
        reasons = ('', 'sampling_plan_unavailable', 'sampling_callback_active', 'sampling_raw_prefix_open',
                   'sampling_native_activity_changed', 'sampling_terminal_activity_changed',
                   'sampling_projection_not_ready', 'sampling_terminal_not_current', 'sampling_journal_not_ready')
        def raw_decimal(value):
            return (type(value) is str and 1 <= len(value) <= 20 and value.isascii()
                    and value.isdecimal() and (value == '0' or value[0] != '0')
                    and int(value) <= 2**64-1)
        valid = (type(sampling) is dict and type(sampling.get('version')) is int and sampling['version'] == 1
                 and type(sampling.get('plan_available')) is bool and integer(sampling.get('attempts'))
                 and all(raw_decimal(sampling.get(key)) for key in ('serial', 'request', 'completed_request'))
                 and (sampling.get('retry_reason') in reasons
                      or sampling.get('retry_reason') in tuple('sampling_capture_error_'+str(n) for n in range(32))))
        if not valid:
            return unavailable('invalid_schema')
        result['sampling'] = {key: sampling[key] for key in
                              ('version', 'plan_available', 'attempts', 'serial', 'request', 'completed_request', 'retry_reason')}
    if 'planned_hp_boundary_edges' in data:
        if not integer(data['planned_hp_boundary_edges'], data['processed_ticket']):
            return unavailable('invalid_numbers')
        result['planned_hp_boundary_edges'] = data['planned_hp_boundary_edges']
    lanes = data.get('lane_metrics')
    if type(lanes) is not list:
        return unavailable('invalid_lanes')
    if len(lanes) > 16:
        return unavailable('capacity_exceeded')
    lane_numbers = ('captured_records', 'consumed_records', 'capture_uncertain_records', 'captured_bytes', 'consumed_bytes', 'thread_id', 'lane_generation',
                    'peak_resident_records', 'peak_resident_bytes', 'age_sample_count')
    nullable = ('resident_records_approx', 'resident_bytes_approx', 'last_capture_to_replay_qpc',
                'maximum_capture_to_replay_qpc', 'observed_pending_age_lower_bound_qpc')
    lane_ids = [row.get('lane') for row in lanes if type(row) is dict and integer(row.get('lane'), 15)]
    if len(set(lane_ids)) != len(lane_ids):
        return unavailable('invalid_lanes')
    by_lane = {}
    result['lane_metrics'] = []
    for row in lanes:
        valid = (type(row) is dict and integer(row.get('lane'), 15)
            and all(integer(row.get(key)) for key in lane_numbers)
            and integer(row.get('thread_id'), 0xffffffff, 1) and integer(row.get('lane_generation'), 0xffffffff)
            and all(key in row and (row[key] is None or integer(row[key])) for key in nullable)
            and type(row.get('counter_snapshot_consistent')) is bool and type(row.get('queue_age_available')) is bool)
        birth = row.get('thread_birth') if type(row) is dict else None
        valid = (valid and type(birth) is str and 1 <= len(birth) <= 20 and birth.isascii()
            and birth.isdecimal() and birth[0] != '0' and int(birth) <= 2**64-1)
        if valid:
            valid = (row['lane'] not in by_lane
                and (not row['counter_snapshot_consistent'] or row['captured_records'] >= row['consumed_records']
                     and row['resident_records_approx'] == row['captured_records'] - row['consumed_records']
                     and row['captured_bytes'] >= row['consumed_bytes']
                     and row['resident_bytes_approx'] == row['captured_bytes'] - row['consumed_bytes'])
                and (row['counter_snapshot_consistent'] or all(row[key] is None for key in nullable[:2]))
                and (row['queue_age_available'] and row['age_sample_count'] > 0
                     and all(row[key] is not None for key in nullable[2:4])
                     or not row['queue_age_available'] and all(row[key] is None for key in nullable[2:4])))
        if not valid:
            result['lane_metrics'].append(dict(available=False, reason='invalid_lane_row'))
            continue
        clean = dict(available=True, lane=row['lane'], thread_birth=birth,
                     counter_snapshot_consistent=row['counter_snapshot_consistent'], queue_age_available=row['queue_age_available'])
        clean.update((key, row[key]) for key in lane_numbers + nullable)
        result['lane_metrics'].append(clean); by_lane[row['lane']] = clean
    timing = data.get('callback_timing')
    if type(timing) is dict and timing.get('available') is False and timing.get('reason') in ('unsupported_semantics', 'invalid_samples'):
        result['callback_timing'] = dict(available=False, reason=timing['reason'])
        return result
    semantics = dict(selection='xorshift_approximately_1_in_128', scope='after_sampling_decision_through_capture_enqueue',
        includes_first_touch=False, includes_original_function=False, includes_identity_lookup=True,
        includes_metrics_recording=False, sample_sums_are_not_extrapolated=True)
    if (type(timing) is not dict or type(timing.get('enabled')) is not bool
            or any(type(timing.get(key)) is not type(value) or timing[key] != value for key, value in semantics.items())
            or not integer(timing.get('qpc_frequency'), minimum=1)
            or timing['qpc_frequency'] != data['queue_age_qpc_frequency']):
        result['callback_timing'] = dict(available=False, reason='unsupported_semantics')
        return result
    samples = timing.get('samples')
    if type(samples) is not list or len(samples) > 16*128*2:
        result['callback_timing'] = dict(available=False, reason='invalid_samples')
        return result
    clean_timing = dict(available=True, enabled=timing['enabled'], qpc_frequency=timing['qpc_frequency'],
                        **semantics, samples=[])
    keys = ('sample_count', 'sample_sum_qpc', 'sample_max_qpc', 'sample_native_reads',
            'sample_native_read_bytes', 'sample_enqueued_bytes', 'sample_drops')
    seen = set()
    for row in samples:
        valid = (type(row) is dict and integer(row.get('lane'), 15) and integer(row.get('lane_generation'), 0xffffffff)
            and integer(row.get('hook_id'), 127) and type(row.get('hook')) is str and row['hook'] in _ASYNC_HOOK_LABELS
            and type(row.get('edge')) is str and row['edge'] in ('entry', 'leave')
            and all(integer(row.get(key)) for key in keys) and row['sample_count'] > 0
            and row['sample_max_qpc'] <= row['sample_sum_qpc'] and row['sample_drops'] <= row['sample_count'])
        lane = by_lane.get(row['lane']) if valid else None
        tag = (row.get('lane'), row.get('lane_generation'), row.get('hook_id'), row.get('edge')) if type(row) is dict else None
        if not valid or not lane or row['lane_generation'] != lane['lane_generation'] or tag in seen:
            clean_timing['samples'].append(dict(available=False, reason='invalid_sample_row'))
            continue
        seen.add(tag)
        clean = dict(available=True, **{key: row[key] for key in ('lane', 'lane_generation', 'hook_id', 'hook', 'edge') + keys})
        clean.update(thread_id=lane['thread_id'], thread_birth=lane['thread_birth'])
        clean_timing['samples'].append(clean)
    result['callback_timing'] = clean_timing
    return result
