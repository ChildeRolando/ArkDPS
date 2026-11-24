"""Terminal-only host binding uses accepted guarded evidence, never new samples."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from event_probe import NativePcBinding, ObserverReadRetry
from event_observer import FridaEventObserver, NativePcFridaObserver, ObserverStatus
from native_pc.native_hook_source import NativeHookEventProbe
from battle_lifecycle import BattleRuntime, _terminal_boundary
from test_battle_lifecycle import FakeCollector


ROOTS = dict(controller='0x10000', native='0x20000', logger='0x30000', stats='0x40000', level='0x50000')
BUILD, SESSION = 'owned-build', '42:100:owned-battle'


def collector():
    value = SimpleNamespace(process=SimpleNamespace(pid=42, process_instance=(42, 100), process_alive=lambda: True),
                            diagnostics=SimpleNamespace(version_actual=BUILD),
                            observer_binding=lambda **_: dict(native_session=SESSION, source_build=BUILD))
    return value


def raw_mark(**changes):
    return dict(dict(frame=30, game_time=1, sequence=5, epoch=9, active=0, active_hooks=[],
                     damage_settlement_scope=2, native_frame=30, native_fixed_time=str(1 << 32),
                     native_generation=7, native_process_pid=42, native_stage_id='owned-stage',
                     native_identity=deepcopy(ROOTS)), **changes)


def sample():
    return dict(frame=30, game_time=1., session='host-battle', native_session=SESSION,
                battle_metadata=dict(platform='native_pc', stage_id='owned-stage', source_build=BUILD))


def raw_terminal():
    boundary = dict(ROOTS, frame=30, game_time=1, sequence=5, epoch=9, active=0, damage_settlement_scope=2)
    return dict(retry=True, sequence=5, epoch=9, active=0, active_hooks=[],
                damage_settlement_scope=2, terminal_boundary=boundary)


def cached():
    current = collector()
    binding = NativePcBinding(current)
    mark = binding.bind('mark', raw_mark())
    assert binding.remember_verified_mark(sample(), mark)
    return binding, current


def test_destroyed_singleton_retry_can_only_bind_nested_terminal_proof():
    binding, current = cached()
    current.observer_binding = lambda **_: pytest.fail('terminal-only binding must not re-name a destroyed singleton')
    raw = raw_terminal()
    original = deepcopy(raw)
    bound = binding.bind_terminal_mark(raw)
    assert raw == original
    assert bound['retry'] is True and 'frame' not in bound and 'native_session' not in bound
    assert bound['terminal_boundary']['native_session'] == SESSION
    assert bound['terminal_boundary']['source_build'] == BUILD
    assert bound['terminal_boundary']['native_identity'] == ROOTS
    assert bound['terminal_binding']['status'] == 'verified'
    assert binding.bind_terminal_mark(raw) == bound
    with pytest.raises(ObserverReadRetry):
        binding.bind('mark', raw)  # This never became a normal sampling success.


def test_predictive_binding_alone_cannot_create_terminal_cache():
    binding = NativePcBinding(collector())
    binding.bind('mark', raw_mark())
    with pytest.raises(RuntimeError, match='independently ingested'):
        binding.bind_terminal_mark(raw_terminal())


@pytest.mark.parametrize('key,value', [('sequence', 6), ('epoch', 10), ('active', 1), ('active', False),
    ('frame', 31), ('frame', 30.), ('game_time', 2), ('damage_settlement_scope', 3),
    ('native_generation', 8), ('native_process_pid', 43), ('native_stage_id', 'another-stage'),
    ('native_session', 'another-session'), ('source_build', 'another-build'),
    ('native_fixed_time', '0'), ('native_frame', 31), ('controller', '0x60000'),
    ('native_identity', dict(ROOTS, native='0x60000')), ('native_process_created_filetime', 101)])
def test_terminal_candidate_never_overwrites_raw_conflicts(key, value):
    binding, _ = cached()
    raw = raw_terminal()
    raw['terminal_boundary'][key] = value
    with pytest.raises(RuntimeError):
        binding.bind_terminal_mark(raw)
    assert raw['terminal_boundary'][key] == value


@pytest.mark.parametrize('key,value', [('sequence', 6), ('epoch', 10), ('frame', 31), ('game_time', 2),
    ('damage_settlement_scope', 3), ('native_generation', 8), ('native_process_pid', 43),
    ('native_stage_id', 'another-stage'), ('native_session', 'another-session'),
    ('source_build', 'another-build'), ('native_fixed_time', '0'),
    ('native_identity', dict(ROOTS, logger='0x60000')), ('native_process_created_filetime', 101)])
def test_outer_terminal_conflicts_cannot_be_hidden_by_matching_nested_boundary(key, value):
    binding, _ = cached()
    raw = raw_terminal()
    raw[key] = value
    with pytest.raises(RuntimeError):
        binding.bind_terminal_mark(raw)


@pytest.mark.parametrize('change', [{'active': 1}, {'active': False}, {'active_hooks': ['Entity.ApplyModifier']},
                                    {'active_hooks': None}, {'fatal': 'callback failed'}, {'error': 'transport'},
                                    {'stopped': True}])
def test_outer_quiescence_is_mandatory_even_when_nested_active_is_zero(change):
    binding, _ = cached()
    with pytest.raises((RuntimeError, ObserverReadRetry)):
        binding.bind_terminal_mark(dict(raw_terminal(), **change))


def test_missing_outer_active_or_unknown_retry_is_not_a_success():
    binding, _ = cached()
    raw = raw_terminal()
    del raw['active']
    with pytest.raises(ObserverReadRetry):
        binding.bind_terminal_mark(raw)
    with pytest.raises(ObserverReadRetry):
        binding.bind_terminal_mark(dict(retry=True, active=0, active_hooks=[]))


@pytest.mark.parametrize('change', ['birth', 'build', 'exited', 'reset'])
def test_terminal_cache_cannot_cross_birth_build_or_reset(change):
    binding, current = cached()
    if change == 'birth':
        current.process.process_instance = (42, 101)
    elif change == 'build':
        current.diagnostics.version_actual = 'new-build'
    elif change == 'exited':
        current.process.process_alive = lambda: False
    else:
        binding.reset()
    with pytest.raises(RuntimeError):
        binding.bind_terminal_mark(raw_terminal())
    assert binding._terminal_cache is None


def test_live_bound_mark_also_binds_a_nested_raw_terminal_without_cache():
    binding = NativePcBinding(collector())
    raw = raw_mark(terminal_boundary=raw_terminal()['terminal_boundary'])
    result = binding.bind('mark', raw)
    assert result['terminal_boundary']['native_session'] == SESSION
    assert result['terminal_boundary']['source_build'] == BUILD
    assert binding._terminal_cache is None


@pytest.mark.parametrize('provider', ['native', 'frida'])
def test_both_observers_use_dedicated_terminal_binding_and_keep_raw(provider, monkeypatch):
    binding, current = cached()
    raw = raw_terminal()
    current.observer_binding = lambda **_: pytest.fail('no new singleton binding allowed')
    if provider == 'frida':
        observer = NativePcFridaObserver(current)
        monkeypatch.setattr(FridaEventObserver, '_request', lambda *_args, **_kwargs: deepcopy(raw))
    else:
        observer = NativeHookEventProbe(current)
        observer.status = ObserverStatus.READY
        observer._pipe = SimpleNamespace(request=lambda *_args, **_kwargs: deepcopy(raw))
    observer.binding = binding
    bound = observer.read_terminal_mark()
    assert bound['terminal_boundary']['native_session'] == SESSION
    assert observer.last_terminal_response == raw
    raw['terminal_boundary']['epoch'] += 1
    evidence = _terminal_boundary(observer)
    assert evidence['terminal_diagnostic']['status'] == 'rejected'
    assert 'terminal_boundary' not in evidence
    assert evidence['terminal_raw_candidate']['epoch'] == 10
    assert evidence['terminal_raw_mark']['retry'] is True


def test_state_three_then_destroy_then_absence_preserves_fence_without_replaying_or_ack():
    current, runtime = FakeCollector(), BattleRuntime()
    current.sample['session'] = 'boot:1:10:100:300:400'
    current.advance(time=10., frame=10, damage=0.)
    events, reads, acknowledgments, remembered = [], [], [], []
    terminal = {'active': 0, 'active_hooks': []}

    def mark():
        return dict(frame=current.sample['frame'], game_time=current.sample['game_time'],
                    sequence=len(events), epoch=len(events) + 1, active=0)

    observer = SimpleNamespace(mark=mark, read_through=lambda sequence: (reads.append(sequence) or {'events': deepcopy(events)}),
        commit_events=acknowledgments.append, read_terminal_mark=lambda: deepcopy(terminal),
        remember_verified_mark=lambda sample, watermark: remembered.append(deepcopy(watermark)))
    runtime.poll(current, observer)
    events.append(dict(sequence=1, frame=11, game_time=11., source='a', kind='damage', amount=20.,
                       target='enemy_1', real_delta=-20., hp_before=100., hp_after=80., killed=False,
                       damage_type=1, mitigation_before=25., mitigation_after=20.))
    current.advance(time=11., frame=11, damage=20.)
    runtime.poll(current, observer)
    current.lifecycle['state'] = 3
    assert runtime.poll(current, observer).kind == 'terminal'
    before = len(reads), len(acknowledgments), len(remembered)
    terminal['terminal_boundary'] = dict(mark(), controller='0x64', native='0xc8', logger='0xfa', stats='0x12c', level='0x190')
    current.lifecycle['battle'] = None
    final = runtime.poll(current, observer)
    assert final.kind == 'end'
    assert final.events['terminal_boundary'] == terminal['terminal_boundary']
    assert final.events['terminal_diagnostic']['status'] == 'candidate'
    assert (len(reads), len(acknowledgments), len(remembered)) == before
    archive, = runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished' and archive['statistics']['rows']['a']['damage'] == 20
    assert not runtime.sessions.pop_archives()


@pytest.mark.parametrize('failure,status', [(None, 'missing'), (ObserverReadRetry('busy'), 'retry'),
                                           (RuntimeError('unknown failure'), 'rejected')])
def test_terminal_absence_or_failure_has_an_explicit_journal_diagnostic(failure, status):
    def read():
        if failure:
            raise failure
        return {}
    result = _terminal_boundary(SimpleNamespace(read_terminal_mark=read))
    assert result['terminal_diagnostic']['status'] == status
    assert 'terminal_boundary' not in result
