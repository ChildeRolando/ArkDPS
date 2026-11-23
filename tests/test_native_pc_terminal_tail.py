"""Owned synthetic protocol/lifecycle proofs; these are not live acceptance."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle_lifecycle import BattleRuntime
from event_observer import ObserverStatus, NativePcFridaObserver, FridaEventObserver
from event_probe import ObserverReadRetry, acquire_native_terminal_tail
from native_pc.native_hook_source import NativeHookEventProbe
from snapshot_adapter import BattleStatisticsAdapter, FrameMismatch
from stats_core import Statistics
from test_battle_lifecycle import FakeCollector
from test_native_pc_terminal_binding import cached, raw_terminal, ROOTS, BUILD, SESSION


def tail_mark(sequence=8, epoch=12):
    raw = raw_terminal()
    raw.update(sequence=sequence, epoch=epoch, terminal_read_version=1)
    raw['terminal_boundary'].update(sequence=sequence, epoch=epoch)
    return raw


def test_terminal_read_authorizes_later_exact_clock_without_mutating_native_counter_cache():
    binding, current = cached()
    original = deepcopy(binding._terminal_cache)
    current.observer_binding = lambda **_: pytest.fail('destroyed roots are not a new guarded snapshot')
    raw = tail_mark()
    before = deepcopy(raw)
    bound = binding.bind_terminal_tail_mark(raw, 5)
    assert bound['retry'] is True and bound['terminal_boundary']['sequence'] == 8
    assert bound['terminal_boundary']['native_session'] == SESSION
    assert raw == before and binding._terminal_cache == original
    assert binding.bind_terminal_tail_mark(raw, 8) == bound
    with pytest.raises(RuntimeError):
        binding.bind_terminal_mark(raw)  # The ordinary exact-fence contract stays strict.
    with pytest.raises(ObserverReadRetry):
        binding.bind('mark', raw)


def test_active_hook_contradiction_cannot_become_a_trusted_terminal_cache():
    binding, _ = cached()
    from test_native_pc_terminal_binding import sample, raw_mark
    mark = binding.bind('mark', raw_mark(active_hooks=['Entity.ApplyModifier']))
    assert binding.remember_verified_mark(sample(), mark) is False
    assert binding._terminal_cache is None


@pytest.mark.parametrize('change', [dict(sequence=4, epoch=12), dict(sequence=8, epoch=9),
    dict(active=1), dict(active=False), dict(active_hooks=['hook']), dict(terminal_read_version=True),
    dict(native_generation=8), dict(native_stage_id='wrong'), dict(native_fixed_time='0'),
    dict(native_identity=dict(ROOTS, logger='0x60000')), dict(native_process_created_filetime=101),
    dict(error='failed'), dict(fatal='failed'), dict(stopped=True)])
def test_terminal_tail_rejects_regression_active_scope_and_unknown_failures(change):
    binding, _ = cached()
    raw = dict(tail_mark(), **change)
    with pytest.raises((RuntimeError, ObserverReadRetry)):
        binding.bind_terminal_tail_mark(raw, 5)


@pytest.mark.parametrize('key,value', [('frame', 31), ('game_time', 2), ('damage_settlement_scope', 3),
    ('native_generation', 8), ('native_session', 'wrong'), ('source_build', 'wrong'),
    ('native_identity', dict(ROOTS, level='0x60000')), ('native_process_created_filetime', 101)])
def test_terminal_tail_never_overwrites_candidate_conflict(key, value):
    binding, _ = cached()
    raw = tail_mark()
    raw['terminal_boundary'][key] = value
    with pytest.raises(RuntimeError):
        binding.bind_terminal_tail_mark(raw, 5)
    assert raw['terminal_boundary'][key] == value


@pytest.mark.parametrize('change', ['birth', 'build', 'exit', 'reset'])
def test_tail_authorization_cannot_survive_owner_change(change):
    binding, current = cached()
    if change == 'birth':
        current.process.process_instance = (42, 101)
    elif change == 'build':
        current.diagnostics.version_actual = 'new'
    elif change == 'exit':
        current.process.process_alive = lambda: False
    else:
        binding.reset()
    with pytest.raises(RuntimeError):
        binding.bind_terminal_tail_mark(tail_mark(), 5)


def owned_battle():
    current = FakeCollector()
    current.core.mc.process_instance = (42, 100)
    current.core.mc.pid = 42
    current.core.mc.process_alive = lambda: True
    current.process = current.core.mc
    current.diagnostics = SimpleNamespace(version_actual=BUILD)
    current.observer_binding = lambda **_: dict(native_session=SESSION, source_build=BUILD)
    current.lifecycle['metadata'] = dict(platform='native_pc', source_build=BUILD,
                                        stage_id='owned-stage', evidence='owned-fixture')
    current.sample['session'] = SESSION
    current.advance(time=10., frame=30, damage=0.)
    records, calls, closed = [], [], [False]

    def mark():
        frame, when = current.sample['frame'], current.sample['game_time']
        result = dict(frame=frame, game_time=when, sequence=len(records), epoch=len(records) + 9,
            active=0, active_hooks=[], damage_settlement_scope=2, native_frame=frame,
            native_fixed_time=str(int(when * (1 << 32))), native_generation=7,
            native_process_pid=42, native_stage_id='owned-stage', native_identity=deepcopy(ROOTS),
            terminal_read_version=1)
        if closed[0]:
            boundary = dict(ROOTS, frame=frame, game_time=when, sequence=len(records),
                            epoch=len(records) + 9, active=0, damage_settlement_scope=2)
            return dict(retry=True, active=0, active_hooks=[], sequence=len(records), epoch=len(records) + 9,
                        terminal_boundary=boundary, terminal_read_version=1)
        return result

    def request(operation, **arguments):
        if operation == 'mark':
            return mark()
        if operation in ('read', 'terminal_read'):
            after, through = arguments['after'], arguments['through']
            calls.append(('read', operation, after, through))
            result = mark()
            result.update(events=deepcopy(records[after:through]), retry=False)
            if operation == 'terminal_read':
                result.update(terminal_only=True, after=after, through=through)
            return result
        if operation == 'ack':
            calls.append(('ack', arguments['sequence']))
            return dict(acknowledged=arguments['sequence'])
        raise AssertionError(operation)

    observer = NativeHookEventProbe(current)
    observer._pipe = SimpleNamespace(request=request)
    observer.status = ObserverStatus.READY
    observer.probe_modes = dict(terminal_read_version=1)
    return current, BattleRuntime(), observer, records, calls, closed


def damage(sequence, amount, when=11., frame=31):
    return dict(sequence=sequence, frame=frame, game_time=when, source='a', kind='damage', amount=amount,
                target='enemy_1', real_delta=-amount, hp_before=100., hp_after=100.-amount,
                killed=False, damage_type=1)


def pending_battle():
    current, runtime, observer, records, calls, closed = owned_battle()
    runtime.poll(current, observer)
    records.append(damage(1, 20.))
    current.advance(time=11., frame=31, damage=20.)
    runtime.poll(current, observer)
    current.lifecycle['state'] = 3
    assert runtime.poll(current, observer).kind == 'terminal'
    assert runtime.sessions.event_status == 'recording'
    assert runtime.sessions.metadata['terminal_pending'] is True
    assert runtime.sessions.adapter.statistics.end_time is None
    assert not runtime.sessions.adapter.event_stream.closed
    return current, runtime, observer, records, calls, closed


def test_state3_destroy_tail_is_journaled_then_consumed_then_ack_without_native_counter_replay(monkeypatch):
    current, runtime, observer, records, calls, closed = pending_battle()
    cache = deepcopy(observer.binding._terminal_cache)
    records.extend(damage(i, 1.) for i in (2, 3, 4))
    closed[0] = True
    current.lifecycle['battle'] = None
    order = []
    runtime.record = lambda observation: order.append('journal:' + observation.kind)
    accept = BattleStatisticsAdapter.accept_event
    monkeypatch.setattr(BattleStatisticsAdapter, 'accept_event',
        lambda self, **event: (order.append('event:' + str(event['sequence'])), accept(self, **event))[1])
    commit = observer.commit_events
    observer.commit_events = lambda sequence: (order.append('ack:' + str(sequence)), commit(sequence))[1]
    monkeypatch.setattr(BattleStatisticsAdapter, 'update',
                        lambda *_a, **_k: pytest.fail('tail must never re-apply cached cumulative rows'))
    final = runtime.poll(current, observer)
    assert order == ['journal:end', 'event:2', 'event:3', 'event:4', 'ack:4']
    assert final.events['terminal_tail_version'] == 1
    assert final.events['read_after'] == 1 and final.events['sequence'] == 4
    assert [e['sequence'] for e in final.events['drain']['events']] == [2, 3, 4]
    assert observer.binding._terminal_cache == cache
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    assert archive['metadata']['terminal_ack_status'] == 'confirmed'
    assert archive['metadata']['final_counter_status'] == 'unknown'
    assert archive['metadata']['native_counter_watermark']['sequence'] == 1
    assert archive['metadata']['event_source_final_watermark']['sequence'] == 4
    assert not archive['statistics']['totals_complete']
    assert not archive['statistics']['rows']['a']['counter_snapshot_complete']
    assert runtime.sessions.adapter.statistics.sources['a']['values']['damage'] == 20.
    assert runtime.sessions.adapter.event_stream.sequence == 4
    assert runtime.sessions.adapter.event_stream.closed
    assert not runtime.sessions.pop_archives()


def test_lost_terminal_ack_retries_same_ticket_without_ordinary_mark_binding_or_double_consumption(monkeypatch):
    current, runtime, observer, records, calls, closed = pending_battle()
    records.append(damage(2, 3.))
    closed[0] = True
    current.lifecycle['battle'] = None
    attempts = []
    observer.commit_events = lambda sequence: (attempts.append(sequence) or len(attempts) > 1)
    final = runtime.poll(current, observer)
    assert final.events['sequence'] == 2
    assert runtime.sessions.event_status == 'gap'
    assert runtime.sessions.metadata['terminal_ack_status'] == 'pending'
    assert runtime._pending_event_ack is not None
    monkeypatch.setattr(observer, 'mark', lambda: pytest.fail('gone singleton cannot use ordinary mark'))
    monkeypatch.setattr(BattleStatisticsAdapter, 'update', lambda *_a, **_k: pytest.fail('no counter replay'))
    monkeypatch.setattr(BattleStatisticsAdapter, 'accept_event', lambda *_a, **_k: pytest.fail('no event replay'))
    runtime.poll(current, observer)
    assert attempts == [2, 2]
    assert runtime._pending_event_ack is None
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    assert archive['metadata']['terminal_ack_status'] == 'confirmed'
    assert ('read', 'terminal_read', 2, 2) in calls


@pytest.mark.parametrize('failure', ['gap', 'scope', 'active', 'missing'])
def test_terminal_tail_failures_do_not_consume_ack_or_certify_final_counters(failure, monkeypatch):
    current, runtime, observer, records, calls, closed = pending_battle()
    records.extend([damage(2, 1.), damage(3, 1.)])
    closed[0] = True
    current.lifecycle['battle'] = None
    request = observer._pipe.request

    def damaged(operation, **arguments):
        result = request(operation, **arguments)
        if failure == 'missing' and operation == 'mark':
            result.pop('terminal_boundary', None)
        if failure == 'scope' and 'terminal_boundary' in result:
            result['terminal_boundary']['damage_settlement_scope'] = 3
        if failure == 'active' and operation == 'terminal_read':
            result['active'] = 1
        if failure == 'gap' and operation == 'terminal_read':
            result['events'] = result['events'][1:]
        return result

    observer._pipe.request = damaged
    now = [100.]
    monkeypatch.setattr('battle_lifecycle.time.monotonic', lambda: now[0])
    ack_before = [c for c in calls if c[0] == 'ack']
    final = runtime.poll(current, observer)
    if final.kind == 'terminal':
        now[0] += 2.1
        final = runtime.poll(current, observer)
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'gap'
    assert archive['metadata']['final_counter_status'] == 'unknown'
    assert [c for c in calls if c[0] == 'ack'] == ack_before
    assert runtime.sessions.adapter.event_stream.sequence == 1
    assert not archive['statistics']['rows']['a']['counter_snapshot_complete']


def test_pending_terminal_cumulative_revision_keeps_event_entry_open_and_same_endpoint():
    current, runtime, observer, records, calls, closed = pending_battle()
    current.sample['rows'][0]['damage'] = 22.
    current.sample['rows'][0]['physical'] = 22.
    runtime.poll(current, observer)
    assert runtime.sessions.adapter.statistics.sources['a']['values']['damage'] == 22.
    assert runtime.sessions.adapter.statistics.end_time is None
    assert not runtime.sessions.adapter.event_stream.closed
    adapter = runtime.sessions.adapter
    changed = dict(runtime.sessions.latest_sample, game_time=12., frame=32)
    with pytest.raises(FrameMismatch):
        adapter.update(changed, current.roster, terminal=True, defer_finish=True)


def test_no_new_tail_events_still_reads_real_zero_length_terminal_window():
    current, runtime, observer, records, calls, closed = pending_battle()
    closed[0] = True
    current.lifecycle['battle'] = None
    end = runtime.poll(current, observer)
    archive, = runtime.sessions.pop_archives()
    assert end.events['drain']['events'] == []
    assert ('read', 'terminal_read', 1, 1) in calls
    assert archive['event_status'] == 'finished'
    assert archive['metadata']['terminal_ack_status'] == 'confirmed'
    assert archive['metadata']['final_counter_status'] == 'verified_at_final_watermark'
    assert archive['statistics']['totals_complete']


@pytest.mark.parametrize('provider', ['native', 'frida'])
def test_both_provider_tail_entrypoints_use_dedicated_raw_binding(provider, monkeypatch):
    binding, current = cached()
    raw = tail_mark()
    batch = dict(raw, retry=False, terminal_only=True, after=5, through=8,
                 events=[{'sequence': n} for n in (6, 7, 8)])
    calls = []

    def request(_self, operation, **arguments):
        calls.append((operation, arguments))
        return deepcopy(batch if operation == 'terminal_read' else raw)

    if provider == 'frida':
        observer = NativePcFridaObserver(current)
        monkeypatch.setattr(FridaEventObserver, '_request', request)
    else:
        observer = NativeHookEventProbe(current)
        monkeypatch.setattr(observer, '_raw_request', lambda op, **args: request(None, op, **args))
    observer.binding = binding
    monkeypatch.setattr(observer, '_request', lambda *_a, **_k: pytest.fail('ordinary sampling bind is forbidden'))
    result = observer.read_terminal_tail(5)
    assert [op for op, _ in calls] == ['mark', 'terminal_read', 'mark']
    assert result['drain']['native_session'] == SESSION
    assert result['sequence'] == 8 and result['read_after'] == 5
    assert result['before'] == result['after'] == result['terminal_boundary']
    assert binding._terminal_cache[1]['sequence'] == 5


@pytest.mark.parametrize('failure', ['closing_changed', 'closing_active', 'wrong_after', 'wrong_through',
                                     'retry_batch', 'wrong_boundary', 'unknown_error'])
def test_terminal_read_ticket_or_closing_failure_never_becomes_success(failure):
    binding, _ = cached()
    raw = tail_mark()
    observer = SimpleNamespace(binding=binding, terminal_mark_timeout=.5, _mark_options=lambda: {})
    calls = []

    def request(operation, **arguments):
        calls.append(operation)
        result = deepcopy(raw)
        if operation == 'terminal_read':
            result.update(retry=False, terminal_only=True, after=5, through=8, events=[])
            if failure == 'wrong_after':
                result['after'] = 4
            elif failure == 'wrong_through':
                result['through'] = 9
            elif failure == 'retry_batch':
                result['retry'] = True
            elif failure == 'wrong_boundary':
                result['terminal_boundary']['epoch'] += 1
            elif failure == 'unknown_error':
                result['error'] = 'unknown source failure'
        elif len(calls) == 3:
            if failure == 'closing_changed':
                result['sequence'] += 1
                result['epoch'] += 1
                result['terminal_boundary'].update(sequence=9, epoch=13)
            elif failure == 'closing_active':
                result['active'] = 1
        return result

    with pytest.raises((RuntimeError, ObserverReadRetry)):
        acquire_native_terminal_tail(observer, request, 5)
    assert binding._terminal_cache[1]['sequence'] == 5


def test_lost_terminal_ack_cannot_be_retried_after_birth_change():
    current, runtime, observer, records, calls, closed = pending_battle()
    records.append(damage(2, 1.))
    closed[0] = True
    current.lifecycle['battle'] = None
    attempts = []
    observer.commit_events = lambda sequence: (attempts.append(sequence) or False)
    runtime.poll(current, observer)
    current.process.process_instance = (42, 101)
    runtime.poll(current, observer)
    assert attempts == [2]
    assert runtime.sessions.event_status == 'gap'
    assert runtime.sessions.metadata['terminal_ack_status'] == 'pending'


def busy_terminal(monkeypatch):
    current, runtime, observer, records, calls, closed = pending_battle()
    records.append(damage(2, 1.))
    closed[0] = True
    current.lifecycle['battle'] = None
    now, busy, requests = [100.], [True], []
    monkeypatch.setattr('battle_lifecycle.time.monotonic', lambda: now[0])
    original = observer._pipe.request

    def request(operation, **arguments):
        requests.append((operation, arguments))
        result = original(operation, **arguments)
        if operation == 'mark' and busy[0]:
            result.update(active=1, retry=True)
        return result

    observer._pipe.request = request
    return current, runtime, observer, now, busy, requests


def test_typed_busy_destroy_retries_then_succeeds_without_reconsuming_initial_prefix(monkeypatch):
    current, runtime, observer, now, busy, requests = busy_terminal(monkeypatch)
    first = runtime.poll(current, observer)
    assert first.kind == 'terminal' and first.reason == 'terminal_tail_pending'
    assert first.events['terminal_diagnostic']['status'] == 'retry'
    assert runtime.feed.identity is not None
    assert runtime.sessions.adapter.event_stream.sequence == 1
    assert not runtime.sessions.pop_archives()
    now[0] += .1
    busy[0] = False
    final = runtime.poll(current, observer)
    assert final.kind == 'end' and final.events['sequence'] == 2
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'
    assert [op for op, _ in requests] == ['mark', 'mark', 'terminal_read', 'mark', 'ack']


def test_owned_400ms_active_window_can_become_quiet_within_terminal_budget(monkeypatch):
    current, runtime, observer, now, busy, requests = busy_terminal(monkeypatch)
    observations = []
    runtime.record = observations.append
    for index in range(4):
        now[0] = 100. + .1 * index
        assert runtime.poll(current, observer).reason == 'terminal_tail_pending'
    now[0] = 100.4
    busy[0] = False
    assert runtime.poll(current, observer).kind == 'end'
    assert len([o for o in observations if o.reason == 'terminal_tail_pending']) == 4
    assert len([op for op, _ in requests if op == 'terminal_read']) == 1
    assert len([op for op, _ in requests if op == 'ack']) == 1
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished'


@pytest.mark.parametrize('limit', ['deadline', 'attempts'])
def test_busy_terminal_budget_expiry_is_an_explicit_gap_without_new_admission(monkeypatch, limit):
    current, runtime, observer, now, busy, requests = busy_terminal(monkeypatch)
    assert runtime.poll(current, observer).kind == 'terminal'
    if limit == 'deadline':
        before = len(requests)
        now[0] = 102.
        final = runtime.poll(current, observer)
        assert len(requests) == before
        assert final.events['terminal_diagnostic']['reason'] == 'terminal_tail_retry_budget_expired'
    else:
        for index in range(1, 20):
            now[0] = 100. + index * .01
            final = runtime.poll(current, observer)
        assert len(requests) == 20 and final.kind == 'end'
        assert final.events['terminal_diagnostic']['reason'] == 'terminal_tail_retry_budget_expired'
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'gap'
    assert not archive['statistics']['totals_complete']
    assert all(op == 'mark' for op, _ in requests)


@pytest.mark.parametrize('change', ['birth', 'close', 'stop', 'transport'])
def test_pending_terminal_can_exit_on_owner_change_close_stop_or_transport(monkeypatch, change):
    current, runtime, observer, now, busy, requests = busy_terminal(monkeypatch)
    assert runtime.poll(current, observer).kind == 'terminal'
    busy[0] = False
    count = len(requests)
    if change == 'birth':
        current.process.process_instance = (42, 101)
    elif change == 'close':
        runtime.disconnected('capture_stopped')
    elif change == 'stop':
        observer.status = ObserverStatus.STOPPED
    else:
        def failed(*_args, **_kwargs):
            raise ObserverReadRetry('pipe timeout; not a decoded producer busy')
        observer._pipe.request = failed
    final = runtime.poll(current, observer)
    assert final.kind == 'end'
    assert final.events['terminal_diagnostic']['status'] == 'rejected'
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'gap'
    assert runtime.sessions.adapter.event_stream.sequence == 1
    assert not any(op in ('terminal_read', 'ack') for op, _ in requests[count:])
    if change == 'close':
        assert len(requests) == count


def test_terminal_helper_stops_admitting_requests_when_read_budget_expires(monkeypatch):
    binding, _ = cached()
    observer = SimpleNamespace(binding=binding, terminal_mark_timeout=.5, _mark_options=lambda: {})
    now, requests = [100.], []
    monkeypatch.setattr('event_probe.time.monotonic', lambda: now[0])

    def request(operation, **arguments):
        requests.append((operation, arguments['timeout']))
        now[0] = 100.3
        return tail_mark()

    with pytest.raises(ObserverReadRetry, match='admission budget'):
        acquire_native_terminal_tail(observer, request, 5, deadline=100.2)
    assert len(requests) == 1 and requests[0][0] == 'mark'
    assert requests[0][1] == pytest.approx(.2)


@pytest.mark.parametrize('kind', ['ended', 'past', 'future', 'unknown', 'missing', 'decreased', 'negative', 'nan'])
def test_pending_total_revision_is_transactional_and_does_not_relax_normal_snapshot(kind):
    stats = Statistics('owned', 0)
    stats.register_source('a', 'a')
    stats.snapshot(0, {'a': dict(damage=10., healing=1.)})
    counters, when = {'a': dict(damage=12., healing=2.)}, 0
    if kind == 'ended':
        stats.finish(0)
    elif kind == 'past':
        when = -1
    elif kind == 'future':
        when = 1
    elif kind == 'unknown':
        counters['z'] = dict(damage=2., healing=1.)
    elif kind == 'missing':
        counters['a'].pop('healing')
    elif kind == 'decreased':
        counters['a']['healing'] = 0.
    elif kind == 'negative':
        counters['a']['healing'] = -1.
    else:
        counters['a']['healing'] = float('nan')
    before = deepcopy(stats.sources)
    with pytest.raises((ValueError, TypeError)):
        stats.revise_pending_totals(when, counters)
    assert stats.sources == before


def test_pending_revision_retains_values_but_invalidated_final_totals_are_never_resealed():
    stats = Statistics('owned', 0)
    stats.register_source('a', 'a')
    stats.snapshot(0, {'a': dict(damage=10., healing=1.)})
    with pytest.raises(ValueError, match='conflicting counters'):
        stats.snapshot(0, {'a': dict(damage=12., healing=2.)})
    assert stats.revise_pending_totals(0, {'a': dict(damage=12., healing=2.)})
    assert stats.end_time is None
    stats.invalidate_snapshots()
    stats.finish(0)
    stats.seal_totals()
    assert stats.sources['a']['values'] == dict(damage=12., healing=2.)
    assert stats.sources['a']['totals_valid'] is False
    assert not stats.result()['rows']['a']['counter_snapshot_complete']
