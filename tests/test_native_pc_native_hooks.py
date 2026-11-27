"""Controlled transport/ownership checks, never evidence of game acquisition."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from threading import Lock
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry
from native_pc import native_hook_source as module
from native_pc import event_source


ROOT = Path(__file__).resolve().parents[1]


def test_native_statistics_has_no_live_dispatch_fallback():
    completed = subprocess.run(
        ['node', str(ROOT / 'tests/native_pc_autochess_quiet_boundary_fixture.js')],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    assert completed.stdout.strip() == 'PASS_OWNED_NATIVE_RPC_ROUTING'


def test_native_mark_requests_the_immutable_publication_protocol(monkeypatch):
    probe = module.NativeHookEventProbe(collector())
    requests = []

    def raw_request(operation, **arguments):
        requests.append((operation, arguments))
        return {'native_quiet_boundary_version': 1, 'native_deferred_callbacks': 2}

    monkeypatch.setattr(probe, '_raw_request', raw_request)
    monkeypatch.setattr(probe.binding, 'bind', lambda operation, response: response)
    monkeypatch.setattr(probe.binding, 'bind_terminal_mark', lambda response: response)
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)))
    probe.mark()
    probe.read_terminal_mark()
    assert requests == [('mark', {'timeout': None, 'native_publication_version': 1}),
                        ('mark', {'timeout': probe.terminal_mark_timeout})]
    kind, timing = records[0]
    assert kind == 'native_hook_request_timing'
    assert timing['native_quiet_boundary_version'] == 1 and timing['native_deferred_callbacks'] == 2


@pytest.mark.parametrize('response', [b'\xff\n', b'{private invalid JSON}\n', b'{"id":1,"id":1}\n'])
def test_invalid_response_is_first_transport_failure_without_reusing_pipe(response):
    pipe = module.NativePipe.__new__(module.NativePipe)
    pipe._lock, pipe._id, pipe._poisoned = Lock(), 0, False
    pipe.handle, pipe._buffer = 1, bytearray(response)
    pipe._io = lambda operation, buffer, size, deadline: size
    probe = module.NativeHookEventProbe(collector())
    probe._pipe, probe.status = pipe, ObserverStatus.READY
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError, match='native observer returned invalid JSON'):
        probe.mark()
    assert pipe._poisoned and probe.status == ObserverStatus.FAILED
    assert records[0][0] == 'native_hook_request_failed'
    assert records[0][1]['error_type'] == 'RuntimeError'


@pytest.mark.parametrize('response,expected', [
    ({'active': 2, 'mark_retry_reason': 'callback_pending_at_start'}, '开始时'),
    ({'active': 1, 'native_frame': 12}, '结束时'),
    ({'active': 0, 'native_frame': 12}, '时钟'),
])
def test_busy_marks_preserve_rejection_and_report_debug_timing(monkeypatch, response, expected):
    probe = module.NativeHookEventProbe(collector())
    monkeypatch.setattr(probe, '_raw_request', lambda *_args, **_kwargs: dict(response, retry=True))
    records = []
    for enabled in (False, True):
        probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: enabled)
        with pytest.raises(ObserverReadRetry, match=expected):
            probe.mark()
        assert len(records) == int(enabled)
    kind, timing = records[0]
    assert (kind, timing['op'], timing['outcome'], timing['active']) == (
        'native_hook_request_timing', 'mark', 'retry', response['active'])
    assert timing['transport_ms'] >= 0 and timing['binding_ms'] >= 0


def collector(*, build=module.BUILD_ID, initialized=True):
    process = SimpleNamespace(pid=42, process_instance=(42, 100),
                              process_alive=lambda: True, observer_boot_ready=lambda: initialized)
    return SimpleNamespace(process=process, diagnostics=SimpleNamespace(
        connection_ok=True, version_status=VersionStatus.COMPATIBLE, version_actual=build),
        observer_binding=lambda **_: dict(native_session='bound-session', source_build=build))


@pytest.mark.parametrize('sink_fails', [False, True])
def test_first_pipe_failure_is_recorded_without_debug_and_rethrows_original(sink_fails):
    probe = module.NativeHookEventProbe(collector())
    failure = OSError(233, 'private pipe details')
    calls, records = [], []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    def sink(kind, data):
        records.append((kind, data))
        if sink_fails:
            raise OSError('log unavailable')

    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    probe.set_request_timing_audit(sink, enabled=lambda: False)
    with pytest.raises(OSError) as caught:
        probe.mark()
    assert caught.value is failure
    assert probe.status == ObserverStatus.FAILED
    assert records == [('native_hook_request_failed', dict(op='mark', error_type='OSError', win32_error=233,
                                                          utc=records[0][1]['utc']))]
    with pytest.raises(RuntimeError, match='private pipe details'):
        probe.mark()
    assert len(calls) == len(records) == 1


def test_oversized_response_logs_bounded_evidence_before_later_cleanup():
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('response_too_large; bytes=11008924; limit=8388608; requested_event_count=20000')
    records = []

    def request(*args, **kwargs):
        raise failure

    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.read_through(20000)
    assert caught.value is failure
    assert records == [('native_hook_request_failed', dict(
        op='read', error_type='RuntimeError', win32_error=None, code='response_too_large',
        response_bytes=11008924, response_limit=8388608, requested_event_count=20000,
        utc=records[0][1]['utc']))]


class Loader:
    def __init__(self, pid):
        assert pid == 42
        self.created_filetime = 100
        self.closed = self.loaded = False
        self.fail_close = False

    def load(self, path):
        self.before_mutation()
        self.loaded = True
        self.path = path
        self.config = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))

    def close(self):
        if self.fail_close:
            raise RuntimeError('loader still pending')
        self.closed = True


def mark(**fields):
    return dict(dict(frame=0, game_time=0, native_frame=0, native_fixed_time='0', native_generation=1,
                     native_identity={}, native_stage_id='stage', native_process_pid=42,
                     sequence=0, epoch=0, active=0), **fields)


class Pipe:
    def __init__(self, name, pid):
        assert name.startswith(r'\\.\pipe\ArkDPS.NativeEvents.') and pid == 42
        self.calls = []
        self.closed = False
        self.fail_close = False
        self.ack = None
        self.failure = None
        self.hello = dict(provider=module.PROVIDER, protocol=1, ready=dict(ready=True, arch='x64'))

    def request(self, operation, **arguments):
        self.calls.append((operation, arguments))
        if self.failure:
            raise self.failure
        if operation == 'initialize':
            assert len(arguments['token']) == 64
            return self.hello
        if operation == 'ack':
            return dict(acknowledged=arguments['sequence'] if self.ack is None else self.ack)
        if operation == 'stop':
            return dict(active=0, error=None, native_quiet=True)
        return mark(events=[]) if operation == 'read' else mark()

    def close(self):
        if self.fail_close:
            raise RuntimeError('pipe cancellation pending')
        self.closed = True


def test_scheduler_startup_and_preference_changes_are_control_only(setup_probe):
    make, _, pipes = setup_probe
    probe = make()
    selected = [False]
    probe.set_autochess_setting(lambda: selected[0])
    factory = probe.pipe_factory
    def scheduler_pipe(name, pid):
        pipe = factory(name, pid)
        pipe.hello.update(native_mark_scheduler_version=1, mode_control_version=1)
        pipe.hello['ready']['native_sampling_version']=1
        original = pipe.request
        def request(operation, **arguments):
            if operation == 'configure_mode':
                pipe.calls.append((operation, arguments))
                return dict(mode_control_version=1, applied=True, mode_epoch=1,
                            desired_mode='all' if arguments['autochess_enabled'] else 'local')
            return original(operation, **arguments)
        pipe.request=request
        return pipe
    probe.pipe_factory=scheduler_pipe
    probe.start()
    assert [op for op,_ in pipes[0].calls]==['initialize','configure_mode']
    assert probe._committed_sequence==probe._acknowledged_sequence==0
    selected[0]=True
    probe.mark()
    assert [op for op,_ in pipes[0].calls][-2:]==['configure_mode','mark']
    assert pipes[0].calls[-1][1]['sampling']=='fresh'
    assert 'autochess_enabled' not in pipes[0].calls[-1][1]


@pytest.fixture
def setup_probe(tmp_path, monkeypatch):
    source_root = tmp_path / 'source-root'
    source_root.mkdir()
    for name in ('event_probe_store.js', 'event_probe_native_pc.js', 'event_probe_runtime.js', 'event_probe_autochess.js'):
        (source_root / name).write_bytes((ROOT / name).read_bytes())
    binary = source_root / 'source.dll'
    binary.write_bytes(b'controlled-not-an-executable')
    binary.with_suffix('.build.json').write_text(json.dumps(dict(sha256=sha256(binary.read_bytes()).hexdigest())))
    def owned_binary(root, *, _read_capture=None):
        # This transport fixture substitutes an owned validator, not a real DLL.
        if _read_capture is not None:
            for path in (binary, binary.with_suffix('.build.json')):
                _read_capture[path.resolve()] = path.read_bytes()
        return binary
    monkeypatch.setattr(module, 'native_hook_binary', owned_binary)
    loaders, pipes = [], []
    def make_loader(pid):
        value = Loader(pid)
        loaders.append(value)
        return value
    def make_pipe(name, pid):
        value = Pipe(name, pid)
        pipes.append(value)
        return value
    def make(**options):
        return module.NativeHookEventProbe(collector(**options), root=source_root, runtime_dir=tmp_path / 'sessions',
            loader_factory=make_loader, pipe_factory=make_pipe)
    make.source_root = source_root
    return make, loaders, pipes


@pytest.mark.parametrize('source_build', list(module.access.KNOWN_BUILDS))
def test_real_source_is_submitted_without_frida_or_fabricated_events(setup_probe, monkeypatch, source_build):
    import builtins
    original = builtins.__import__
    def reject_frida(name, *args, **kwargs):
        if name == 'frida' or name.startswith('frida.'):
            raise AssertionError('native observer attempted to import Frida')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_frida)
    make, loaders, pipes = setup_probe
    probe = make(build=source_build)
    probe.set_autochess_setting(lambda: False)
    probe.start()
    assert probe.status == ObserverStatus.READY
    assert not probe.hook_disable_confirmed
    config = loaders[0].config
    assert config['process_instance'] == [42, 100]
    assert 'globalThis.arkdpsAutoChessMode = "local";' in config['source']
    assert module.assemble_probe_source('native_pc').source in config['source']
    assert not loaders[0].path.with_suffix('.json').exists()
    source = probe.probe_modes['probe_source']
    assert source['provider'] == 'arkdps-native-hooks' and source['frida_required'] is False
    assert source['live_acceptance'] == 'not_verified_for_this_run'
    assert config['token'] not in json.dumps(probe.probe_modes)
    assert probe.mark()['native_session'] == 'bound-session'
    assert probe.mark()['source_build'] == source_build
    assert pipes[0].calls[-1] == ('mark', dict(timeout=5.0, autochess_enabled=False,
                                             native_publication_version=1))
    probe.stop()
    assert loaders[0].closed and pipes[0].closed
    assert probe.hook_disable_confirmed


def test_native_hook_observer_declares_existing_guarded_identity_and_typed_busy_capabilities():
    assert getattr(module.NativeHookEventProbe, 'native_autochess_identity_sampling', None) is True
    assert getattr(module.NativeHookEventProbe, 'native_clock_busy_retry', None) is True


@pytest.mark.parametrize('collection_mode', ['local', 'all'])
@pytest.mark.parametrize('scenario', ['match', 'unverified', 'preparing', 'mark_retry',
                                     'clock_changed', 'root_changed', 'stage_changed', 'comparison'])
def test_real_native_hook_capability_uses_only_the_existing_independent_identity_gate(
        tmp_path, monkeypatch, scenario, collection_mode):
    from battle_lifecycle import BattleRuntime
    from event_capture import CaptureRetry
    from test_native_pc_autochess_collector_gate import fixture, ROOTS
    from test_native_pc_event_identity_sampling import readers, AUTO

    # Only acquisition is synthetic: reuse the owning guarded-PC fixture and
    # real NativeHookEventProbe class capability, collector and BattleFeed.
    calls = readers(monkeypatch)
    collector, probe, fixture_observer, _, _ = fixture(tmp_path,
        mode='unknown' if scenario == 'unverified' else 'valid', stage=AUTO, failed_field=None)
    collector.allow_event_identity_sampling = scenario != 'comparison'
    observer = module.NativeHookEventProbe.__new__(module.NativeHookEventProbe)
    original_mark = fixture_observer.mark

    def mark_with_mode():
        mark = original_mark()
        mark['native_stage_id'] = AUTO
        mark['native_autochess_context']['collection_mode'] = collection_mode
        if scenario == 'preparing':
            mark['native_autochess_context']['phase'] = 3
        if scenario == 'mark_retry':
            mark['retry'] = True
        return mark

    observer.mark = mark_with_mode
    def read_with_mode(through):
        batch = fixture_observer.read_through(through)
        batch['native_autochess_context']['collection_mode'] = collection_mode
        if scenario == 'preparing':
            batch['native_autochess_context']['phase'] = 3
        return batch
    observer.read_baseline = observer.read_through = read_with_mode
    observer.commit_events = fixture_observer.commit_events
    original_sample = probe.sample

    def sample(*, extra_reader):
        report = original_sample(extra_reader=extra_reader)
        raw = report['sample']
        context = dict(stage_id=AUTO, stage_status='available', frame=raw['frame'], game_time=raw['game_time'],
            battle_controller=int(ROOTS['controller'], 16), battle_logger=int(ROOTS['logger'], 16),
            battle_stats=int(ROOTS['stats'], 16), level_data=int(ROOTS['level'], 16))
        if scenario == 'clock_changed':
            context['frame'] += 1
        elif scenario == 'root_changed':
            context['battle_stats'] += 8
        elif scenario == 'stage_changed':
            context['stage_id'] = 'Obt/Main/level_main_01-07'
        raw.update(extra_reader(None, context))
        return report

    probe.sample = sample
    if scenario == 'mark_retry':
        with pytest.raises(CaptureRetry):
            BattleRuntime().feed.acquire(collector, observer)
    else:
        observation = BattleRuntime().feed.acquire(collector, observer)
        if scenario == 'preparing':
            assert observation.kind == 'preparing' and observation.reason == 'empty_roster'
        elif scenario == 'match':
            assert observation.kind == 'sample'
    assert 'actor_rows' in calls  # This initial-skill witness is not skipped.
    assert ('roster' not in calls) is (scenario == 'match')
    assert ('presence' not in calls) is (scenario == 'match')


def test_prevalidated_material_consumes_pinned_bytes_without_revalidating_or_reassembling_at_birth(
        prepared_setup, setup_probe, monkeypatch):
    prepare, _, _, loaders, pipes = prepared_setup
    root = setup_probe[0].source_root
    material = module.prepare_native_hook_material(root)
    # Disk can change after preflight. This attempt uses the immutable bytes;
    # a later fresh factory must validate the disk again, and claim still checks JS.
    (root / 'source.dll').write_bytes(b'replaced disk image must not be loaded')
    monkeypatch.setattr(module, 'native_hook_binary', lambda *_a, **_k: pytest.fail('birth revalidated payload'))
    monkeypatch.setattr(module, '_native_source', lambda *_a, **_k: pytest.fail('birth reassembled source'))
    prepared = prepare(material=material)
    try:
        prepared.prepare((42, 100))
        assert len(loaders) == 1 and not pipes and prepared.hook_disable_confirmed
        assert loaders[0].path.read_bytes() == material.image
        assert loaders[0].config['source'] == material.source
    finally:
        prepared.close()


@pytest.mark.parametrize('fault', ['type', 'root', 'environment', 'untyped_environment', 'mutable_bytes', 'image_digest'])
def test_prevalidated_material_binding_errors_never_create_or_load_a_process_owner(
        prepared_setup, setup_probe, monkeypatch, tmp_path, fault):
    from dataclasses import replace
    prepare, _, _, loaders, pipes = prepared_setup
    material = module.prepare_native_hook_material(setup_probe[0].source_root)
    if fault == 'type':
        material = object()
    elif fault == 'root':
        material = replace(material, root=tmp_path / 'different-owner')
    elif fault == 'environment':
        material = replace(material, effective_env=(material.effective_env[0], material.effective_env[1], not material.effective_env[2]))
    elif fault == 'untyped_environment':
        material = replace(material, effective_env=tuple(int(value) for value in material.effective_env))
    elif fault == 'mutable_bytes':
        material = replace(material, image=bytearray(material.image))
    else:
        material = replace(material, image_sha256='0' * 64)
    prepared = prepare(material=material)
    with pytest.raises(RuntimeError, match='已校验载荷'):
        prepared.prepare((42, 100))
    assert not loaders and not pipes and prepared.hook_disable_confirmed
    prepared.close()


def test_material_keeps_claim_time_source_and_environment_change_gate(prepared_setup, setup_probe, monkeypatch):
    prepare, make_probe, _, loaders, _ = prepared_setup
    material = module.prepare_native_hook_material(setup_probe[0].source_root)
    prepared = prepare(material=material)
    prepared.prepare((42, 100))
    monkeypatch.setenv('ARKDPS_COALESCE_ENEMY_HP', '0' if material.effective_env[2] else '1')
    with pytest.raises(RuntimeError, match='源码或采集参数已变化'):
        make_probe(prepared).start()
    assert not prepared.claimed
    prepared.close()
    assert loaders[0].closed


def test_confirmed_old_process_exit_skips_remote_stop_but_still_releases_local_owners(setup_probe):
    make, loaders, pipes = setup_probe
    probe = make()
    probe.start()
    checked = []
    loaders[0].confirmed_process_exit = lambda expected: checked.append(expected) or True
    pipes[0].failure = OSError(232, 'native observer pipe I/O failed')
    # The collector may already describe another birth. Only the saved owner
    # birth, not this mutable process property, is admissible to the proof.
    probe.collector.process.process_instance = (42, 200)
    probe.stop()
    assert checked == [(42, 100)]
    assert not any(op == 'stop' for op, _ in pipes[0].calls)
    assert probe.hook_disable_confirmed and loaders[0].closed and pipes[0].closed


def test_death_confirmed_after_stop_error_clears_only_that_protocol_error(setup_probe):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    answers = iter((False, True))
    loaders[0].confirmed_process_exit = lambda expected: next(answers)
    pipes[0].failure = OSError(232, 'native observer pipe I/O failed')
    probe.stop()
    assert probe.hook_disable_confirmed and not probe.cleanup_errors
    assert [op for op, _ in pipes[0].calls].count('stop') == 1
    assert loaders[0].closed and pipes[0].closed


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
def test_alive_or_unknown_exit_cannot_replace_native_quiet_stop_proof(setup_probe, confirmation):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    loaders[0].confirmed_process_exit = lambda expected: confirmation
    failure = pipes[0].failure = OSError(232, 'native observer pipe I/O failed')
    with pytest.raises(OSError, match='native observer pipe I/O failed') as error:
        probe.stop()
    assert error.value is failure and error.value.errno == 232
    assert not probe.hook_disable_confirmed
    assert [op for op, _ in pipes[0].calls].count('stop') == 1
    assert probe.cleanup_errors['hooks'] == str(failure)


@pytest.mark.parametrize('confirmation', [False, None])
def test_pipe_232_with_unproved_exit_retains_real_pending_local_owners(setup_probe, confirmation):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    loaders[0].confirmed_process_exit = lambda expected: confirmation
    pipes[0].failure = OSError(232, 'native observer pipe I/O failed')
    loaders[0].fail_close = pipes[0].fail_close = True
    with pytest.raises(RuntimeError, match='pending'):
        probe.stop()
    assert not probe.hook_disable_confirmed
    assert probe._loader is loaders[0] and probe._pipe is pipes[0]
    assert set(probe.cleanup_errors) == {'pipe', 'loader'}


def test_exit_proof_after_pipe_232_does_not_hide_real_local_cleanup_error(setup_probe):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    answers = iter((False, True))
    loaders[0].confirmed_process_exit = lambda expected: next(answers)
    pipes[0].failure = OSError(232, 'native observer pipe I/O failed')
    loaders[0].fail_close = pipes[0].fail_close = True
    with pytest.raises(RuntimeError, match='pending'):
        probe.stop()
    assert probe.hook_disable_confirmed
    assert probe._loader is loaders[0] and probe._pipe is pipes[0]
    assert set(probe.cleanup_errors) == {'pipe', 'loader'}
    assert [op for op, _ in pipes[0].calls].count('stop') == 1
    loaders[0].confirmed_process_exit = lambda expected: True
    loaders[0].fail_close = pipes[0].fail_close = False
    probe.stop()
    assert not probe.cleanup_errors and probe._loader is probe._pipe is None
    assert [op for op, _ in pipes[0].calls].count('stop') == 1


def test_confirmed_exit_does_not_swallow_pending_local_pipe_or_loader_cleanup(setup_probe):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    loaders[0].confirmed_process_exit = lambda expected: True
    loaders[0].fail_close = pipes[0].fail_close = True
    with pytest.raises(RuntimeError, match='pending'):
        probe.stop()
    assert probe.hook_disable_confirmed
    assert probe._loader is loaders[0] and probe._pipe is pipes[0]
    assert set(probe.cleanup_errors) == {'pipe', 'loader'}
    loaders[0].fail_close = pipes[0].fail_close = False
    probe.stop()
    assert not probe.cleanup_errors and probe._loader is probe._pipe is None
    assert not any(op == 'stop' for op, _ in pipes[0].calls)


@pytest.mark.parametrize('reply', [dict(active=1, native_quiet=True), dict(active=True, native_quiet=True),
    dict(active=0, native_quiet=False), dict(active=0), dict(active=0, native_quiet=True, error='stop failed')])
def test_unproved_death_still_requires_the_original_active_zero_and_native_quiet_reply(setup_probe, reply):
    make, loaders, pipes = setup_probe
    probe = make(); probe.start()
    loaders[0].confirmed_process_exit = lambda expected: None
    original_request = pipes[0].request
    def request(operation, **arguments):
        result = original_request(operation, **arguments)
        return reply if operation == 'stop' else result
    pipes[0].request = request
    with pytest.raises(RuntimeError, match='in-flight callbacks'):
        probe.stop()
    assert not probe.hook_disable_confirmed and [op for op, _ in pipes[0].calls].count('stop') == 1


@pytest.mark.parametrize('options', [dict(build='unknown'), dict(initialized=False)])
def test_unknown_build_and_uninitialized_vm_never_mutate(setup_probe, options):
    make, loaders, pipes = setup_probe
    probe = make(**options)
    with pytest.raises(RuntimeError):
        probe.start()
    assert not loaders and not pipes
    assert probe.hook_disable_confirmed


def test_process_birth_change_is_rejected_before_load(setup_probe):
    make, loaders, _ = setup_probe
    probe = make()
    probe.collector.process.process_instance = (42, 101)
    with pytest.raises(RuntimeError, match='实例'):
        probe.start()
    assert loaders[0].closed and not loaders[0].loaded


def test_read_cursor_and_ack_are_idempotent_not_reconstructed(setup_probe):
    make, _, pipes = setup_probe
    probe = make()
    probe.start()
    probe.read_baseline(2)
    assert pipes[0].calls[-1][1] == dict(timeout=5.0, after=2, through=2, baseline=True)
    assert probe.commit_events(2) is True
    assert probe.commit_events(2) is True
    assert len([x for x in pipes[0].calls if x[0] == 'ack']) == 1
    probe.read_through(5)
    assert pipes[0].calls[-1][1] == dict(timeout=5.0, after=2, through=5)
    pipes[0].ack = 4
    assert probe.commit_events(5) is False
    with pytest.raises(RuntimeError, match='acknowledgement'):
        probe.read_through(6)
    probe.stop()


def test_transport_failure_keeps_previous_prefix_but_no_reuse(setup_probe):
    make, _, pipes = setup_probe
    probe = make()
    probe.start()
    assert probe.commit_events(2)
    pipes[0].failure = ObserverReadRetry('timed out')
    assert not probe.commit_events(3)
    assert probe._acknowledged_sequence == 2
    assert probe.status == ObserverStatus.FAILED
    probe.stop()
    assert not probe.hook_disable_confirmed


def test_failed_cleanup_retains_owners_for_retry(setup_probe):
    make, loaders, pipes = setup_probe
    probe = make()
    probe.start()
    loaders[0].fail_close = pipes[0].fail_close = True
    with pytest.raises(RuntimeError, match='pending'):
        probe.stop()
    assert probe._loader is loaders[0] and probe._pipe is pipes[0]
    assert set(probe.cleanup_errors) == {'loader', 'pipe'}
    loaders[0].fail_close = pipes[0].fail_close = False
    probe.stop()
    assert probe._loader is probe._pipe is None and not probe.cleanup_errors


def test_native_binary_is_not_silently_used_from_portable_package(tmp_path):
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'release.json').write_text('{}')
    with pytest.raises(RuntimeError, match='清单'):
        module.native_hook_binary(tmp_path / 'collector')


def test_native_binary_build_digest_is_required(tmp_path):
    binary = tmp_path / 'runtime/native-pc/native-event-runtime' / module.DLL_NAME
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'controlled')
    manifest = binary.with_suffix('.build.json')
    manifest.write_text(json.dumps(dict(sha256='wrong')))
    with pytest.raises(RuntimeError, match='摘要'):
        module.native_hook_binary(tmp_path)
    sources = tmp_path / 'native_pc/native_event_runtime'
    sources.mkdir(parents=True)
    for name in module.RUNTIME_SOURCES:
        (sources / name).write_text('owned source')
    manifest.write_text(json.dumps(dict(sha256=sha256(binary.read_bytes()).hexdigest(),
        provider=module.PROVIDER, protocol=module.PROTOCOL,
        sources={name: sha256((sources / name).read_bytes()).hexdigest() for name in module.RUNTIME_SOURCES})))
    assert module.native_hook_binary(tmp_path) == binary
    (sources / 'engine.cpp').write_text('changed source')
    with pytest.raises(RuntimeError, match='源码'):
        module.native_hook_binary(tmp_path)


def test_explicit_observer_factory_is_preserved_for_native_capture(tmp_path):
    factory = object()
    source = event_source.PcEventSource(tmp_path, observer_factory=factory)
    assert source.select(enabled=True, backend='native') is factory
    assert source.select(enabled=False, backend='native') is None


def test_enemy_hp_preference_replaces_native_runtime_only_when_changed(tmp_path, monkeypatch):
    created = []

    class Runtime:
        closed = False

        def __init__(self, game_root, runtime_dir, *, collector_root, enemy_hp_events):
            self.enemy_hp_events = enemy_hp_events
            self.observer_factory = object()
            created.append(self)

        def start(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(event_source, '_native_pc_hook_runtime', Runtime)
    source = event_source.PcEventSource(tmp_path)
    connection = dict(enabled=True, backend='native', game_root=tmp_path)
    first = source.select(**connection, enemy_hp_events=False)
    assert source.select(**connection, enemy_hp_events=False) is first
    second = source.select(**connection, enemy_hp_events=True)
    assert second is not first
    assert [runtime.enemy_hp_events for runtime in created] == [False, True]
    assert created[0].closed and not created[1].closed


@pytest.fixture
def prepared_setup(tmp_path, monkeypatch, setup_probe):
    make, loaders, pipes = setup_probe
    source_root = make.source_root
    game_root = tmp_path / 'official-game'
    state = SimpleNamespace(birth=100, path=game_root / 'Arknights.exe', alive=True,
                            build=module.BUILD_ID, fail_close=False, fail_load=False)
    monkeypatch.setattr(module.access, 'verify_build', lambda root: (
        dict(known_build=True, amd64=True, source_build=state.build), []))

    class EarlyLoader(Loader):
        def __init__(self, pid):
            super().__init__(pid)
            self.handle = object()
            self.api = SimpleNamespace(process_times=lambda handle: (state.birth,),
                exit_code=lambda handle: 259 if state.alive else 0,
                image_path=lambda handle: str(state.path))
            loaders.append(self)

        def load(self, path):
            if state.fail_load:
                self.fail_close = state.fail_close
                raise RuntimeError('controlled pending loader')
            super().load(path)

    def prepare(**options):
        return module.PreparedNativeHooks(game_root, root=source_root, runtime_dir=tmp_path / 'early',
                                          loader_factory=EarlyLoader, **options)

    def probe(prepared, **options):
        selected = collector(**options)
        selected.game_root = game_root

        def reject_second_load(pid):
            raise AssertionError('adoption attempted another DLL load')

        def make_pipe(name, pid):
            value = Pipe(name, pid)
            pipes.append(value)
            return value

        return module.NativeHookEventProbe(selected, root=source_root, prepared=prepared,
            loader_factory=reject_second_load, pipe_factory=make_pipe)

    return prepare, probe, state, loaders, pipes


@pytest.mark.parametrize('source_build', list(module.access.KNOWN_BUILDS))
def test_preparation_loads_without_vm_or_pipe_then_adopts_after_vm_gate(prepared_setup, monkeypatch, source_build):
    prepare, make_probe, state, loaders, pipes = prepared_setup
    state.build = source_build
    monkeypatch.setenv('ARKDPS_COALESCE_ENEMY_HP', '0')
    prepared = prepare().prepare((42, 100))
    assert len(loaders) == 1 and loaders[0].loaded and not pipes
    assert prepared.hook_disable_confirmed and not prepared.claimed
    assert prepared.prepared_at is not None and prepared.expires_at == prepared.prepared_at + 120
    assert prepared.diagnostics()['prepared'] is True
    assert prepared.diagnostics()['source_build'] == source_build
    config = loaders[0].config
    assert config['source'].startswith('globalThis.arkdpsCoalesceEnemyHp = false;\n')
    assert config['token'] not in json.dumps(prepared.diagnostics())
    not_ready = make_probe(prepared, initialized=False, build=source_build)
    with pytest.raises(RuntimeError, match='IL2CPP'):
        not_ready.start()
    assert not prepared.claimed and not pipes and not loaders[0].closed
    probe = make_probe(prepared, build=source_build)
    probe.start()
    assert prepared.claimed and probe._loader is loaders[0] and len(loaders) == 1
    assert pipes[0].calls[0][0] == 'initialize'
    assert probe.probe_modes['probe_source']['submitted_sha256'] == sha256(config['source'].encode()).hexdigest()
    assert not probe.hook_disable_confirmed
    prepared.close()
    assert not loaders[0].closed and not pipes[0].closed
    probe.stop()
    assert loaders[0].closed and pipes[0].closed and probe.hook_disable_confirmed


def test_preparation_cannot_be_adopted_with_another_registered_pc_build(prepared_setup):
    prepare, make_probe, state, loaders, pipes = prepared_setup
    official, bilibili = module.access.KNOWN_BUILDS
    state.build = bilibili
    prepared = prepare().prepare((42, 100))
    assert prepared.diagnostics()['source_build'] == bilibili
    with pytest.raises(RuntimeError, match='客户端版本不匹配'):
        make_probe(prepared, build=official).start()
    assert not prepared.claimed and not pipes and not loaders[0].closed
    prepared.close()
    assert loaders[0].closed


@pytest.mark.parametrize('birth', [(0, 100), (42, 0), (True, 100), (42,), '42'])
def test_preparation_rejects_invalid_birth_without_load(prepared_setup, birth):
    prepare, _, _, loaders, pipes = prepared_setup
    prepared = prepare()
    with pytest.raises(ValueError):
        prepared.prepare(birth)
    assert not loaders and not pipes and prepared.prepared_at is None


@pytest.mark.parametrize('problem', ['build', 'birth', 'path', 'exited'])
def test_preparation_checks_build_birth_path_and_liveness_before_load(prepared_setup, problem):
    prepare, _, state, loaders, pipes = prepared_setup
    if problem == 'build':
        state.build = 'unknown'
    elif problem == 'birth':
        state.birth = 101
    elif problem == 'path':
        state.path = state.path.parent / 'different.exe'
    else:
        state.alive = False
    prepared = prepare()
    with pytest.raises(RuntimeError):
        prepared.prepare((42, 100))
    assert not any(loader.loaded for loader in loaders) and not pipes
    prepared.close()
    assert all(loader.closed for loader in loaders)


def test_preparation_is_single_attempt_and_expiry_never_reloads(prepared_setup, monkeypatch):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    monkeypatch.setattr(module.time, 'monotonic', lambda: prepared.expires_at)
    with pytest.raises(RuntimeError, match='120 秒'):
        make_probe(prepared).start()
    with pytest.raises(RuntimeError, match='重复加载'):
        prepared.prepare((42, 100))
    assert len(loaders) == 1 and not pipes and not prepared.claimed
    prepared.close()
    assert loaders[0].closed


@pytest.mark.parametrize('change', ['birth', 'collector_path', 'source_options', 'source_nested', 'config', 'dll'])
def test_adoption_rechecks_original_identity_and_immutable_source(prepared_setup, monkeypatch, change):
    prepare, make_probe, state, loaders, pipes = prepared_setup
    if change == 'source_nested':
        monkeypatch.setenv('ARKDPS_TRACE_HP_CONTEXT', '1')
        monkeypatch.delenv('ARKDPS_TRACE_NESTED_PARENT', raising=False)
    prepared = prepare().prepare((42, 100))
    probe = make_probe(prepared)
    if change == 'birth':
        state.birth = 101
    elif change == 'collector_path':
        probe.collector.game_root = state.path.parent / 'other'
    elif change == 'source_options':
        monkeypatch.setenv('ARKDPS_TRACE_HP_CONTEXT', '1')
    elif change == 'source_nested':
        monkeypatch.setenv('ARKDPS_TRACE_NESTED_PARENT', '1')
    elif change == 'config':
        prepared._config.write_text('{}', encoding='utf-8')
    else:
        prepared._staged.write_bytes(b'changed')
    with pytest.raises(RuntimeError):
        probe.start()
    assert not prepared.claimed and not pipes and not loaders[0].closed
    prepared.close()
    assert loaders[0].closed


def test_failed_preparation_cleanup_retains_pending_owner(prepared_setup):
    prepare, _, state, loaders, _ = prepared_setup
    state.fail_load = state.fail_close = True
    prepared = prepare()
    with pytest.raises(RuntimeError, match='pending'):
        prepared.prepare((42, 100))
    assert prepared.prepared_at is None and not prepared.diagnostics()['prepared']
    with pytest.raises(RuntimeError, match='pending'):
        prepared.close()
    assert prepared._loader is loaders[0] and prepared._config.exists()
    assert 'loader' in prepared.cleanup_errors
    loaders[0].fail_close = False
    prepared.close()
    assert prepared._loader is prepared._config is None and not prepared.cleanup_errors


def test_claim_is_single_owner_and_initialize_failure_cleans_observer_not_preparer(prepared_setup):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    probe = make_probe(prepared)
    factory = probe.pipe_factory

    def incompatible_pipe(name, pid):
        value = factory(name, pid)
        value.hello['ready']['arch'] = 'arm64'
        return value

    probe.pipe_factory = incompatible_pipe
    with pytest.raises(RuntimeError, match='握手'):
        probe.start()
    assert prepared.claimed and loaders[0].closed and pipes[0].closed
    assert not probe.hook_disable_confirmed
    with pytest.raises(RuntimeError, match='移交'):
        make_probe(prepared).start()
    prepared.close()
    assert len(loaders) == len(pipes) == 1


def test_cancelled_preparation_never_opens_loader(prepared_setup):
    prepare, _, _, loaders, pipes = prepared_setup
    prepared = prepare()
    prepared.cancelled = lambda: True
    with pytest.raises(InterruptedError):
        prepared.prepare((42, 100))
    assert not loaders and not pipes
    prepared.close()


def test_adopted_initialization_failure_retains_observer_cleanup_ownership(prepared_setup):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    probe = make_probe(prepared)
    factory = probe.pipe_factory

    def failed_pipe(name, pid):
        value = factory(name, pid)
        value.failure = RuntimeError('controlled initialization failure')
        value.fail_close = True
        loaders[0].fail_close = True
        return value

    probe.pipe_factory = failed_pipe
    with pytest.raises(RuntimeError, match='initialization'):
        probe.start()
    assert prepared.claimed and probe._loader is loaders[0] and probe._pipe is pipes[0]
    assert set(probe.cleanup_errors) == {'loader', 'pipe'}
    prepared.close()
    assert not loaders[0].closed and not pipes[0].closed
    loaders[0].fail_close = pipes[0].fail_close = False
    probe.stop()
    assert probe._loader is probe._pipe is None


@pytest.mark.parametrize('enabled', [True, False])
def test_prepared_ui_preference_uses_initial_then_dynamic_mark_without_rewriting_source(prepared_setup, enabled):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    source = loaders[0].config['source']
    assert source == module._native_source(ROOT, None)[1]
    assert 'globalThis.arkdpsAutoChessMode = ' not in source
    selected = [enabled]
    probe = make_probe(prepared)
    probe.set_autochess_setting(lambda: selected[0])
    factory = probe.pipe_factory
    observed_status = []

    def initial_lobby_pipe(name, pid):
        pipe = factory(name, pid)
        original = pipe.request

        def request(operation, **arguments):
            result = original(operation, **arguments)
            if operation == 'mark':
                observed_status.append(probe.status)
                # Current combat may already have latched a different mode.
                # Preserve that engine-owned result, and do not fake a baseline.
                result.update(retry=probe.status == ObserverStatus.ATTACHING, sequence=35,
                    native_autochess_context=dict(collection_mode='all'))
            return result

        pipe.request = request
        return pipe

    probe.pipe_factory = initial_lobby_pipe
    probe.start()
    assert observed_status == [ObserverStatus.ATTACHING]
    assert probe.status == ObserverStatus.READY and prepared.claimed
    assert [operation for operation, _ in pipes[0].calls] == ['initialize', 'mark']
    assert pipes[0].calls[1] == ('mark', dict(timeout=5.0, autochess_enabled=enabled))
    assert probe._committed_sequence == probe._acknowledged_sequence == 0
    assert probe.probe_modes['probe_source']['submitted_sha256'] == sha256(source.encode()).hexdigest()
    assert len(loaders) == 1 and loaders[0].config['source'] == source
    selected[0] = not enabled
    result = probe.mark()
    assert pipes[0].calls[-1] == ('mark', dict(timeout=5.0, autochess_enabled=not enabled,
                                             native_publication_version=1))
    assert result['native_autochess_context']['collection_mode'] == 'all'
    assert not any(operation in ('read', 'ack') for operation, _ in pipes[0].calls)
    probe.stop()


@pytest.mark.parametrize('failure', ['transport', 'error', 'stopped', 'invalid_bool'])
def test_initial_ui_preference_failure_never_publishes_ready(prepared_setup, failure):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    probe = make_probe(prepared)
    probe.set_autochess_setting(lambda: 1 if failure == 'invalid_bool' else False)
    factory = probe.pipe_factory

    def failing_pipe(name, pid):
        pipe = factory(name, pid)
        original = pipe.request

        def request(operation, **arguments):
            result = original(operation, **arguments)
            if operation == 'mark':
                if failure == 'transport':
                    raise RuntimeError('initial preference transport failed')
                return dict(error='initial preference rejected') if failure == 'error' else dict(stopped=True)
            return result

        pipe.request = request
        return pipe

    probe.pipe_factory = failing_pipe
    with pytest.raises((RuntimeError, ValueError)):
        probe.start()
    assert probe.status == ObserverStatus.FAILED and not probe._started
    assert prepared.claimed and loaders[0].closed and pipes[0].closed
    assert not probe.hook_disable_confirmed
    assert not probe.probe_modes
    assert not any(operation in ('read', 'ack') for operation, _ in pipes[0].calls)


def test_prepared_business_source_change_is_rejected_even_with_ui_callback(prepared_setup, monkeypatch):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    prepared = prepare().prepare((42, 100))
    original = module.assemble_probe_source

    def changed_source(*args, **kwargs):
        from dataclasses import replace
        assembled = original(*args, **kwargs)
        return replace(assembled, source=assembled.source + '\n// changed business source\n')

    monkeypatch.setattr(module, 'assemble_probe_source', changed_source)
    probe = make_probe(prepared)
    probe.set_autochess_setting(lambda: True)
    with pytest.raises(RuntimeError, match='源码'):
        probe.start()
    assert not prepared.claimed and not pipes and not loaders[0].closed
    prepared.close()


@pytest.mark.parametrize('suffix, accepted', [
    ('h=2,e=1,t=18446744073709551615,r=3,p=0,f=-1,g=1,m=8,k=1,q=1:123.2:456', True),
    ('h=2,e=1,t=3,r=3,p=0,f=1,g=1,m=8,k=1,q=0; token=secret', False),
    ('h=2,e=1,t=3,r=3,p=0,f=1,g=1,m=8,k=99,q=0', False),
])
def test_replay_gap_audit_is_numeric_bounded_and_keeps_failure(suffix, accepted):
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('native runtime mark failed; error_type=std::exception; native_replay_gap:v1,' + suffix)
    def request(*args, **kwargs):
        raise failure
    records = []
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.mark()
    assert caught.value is failure
    assert probe.status == ObserverStatus.FAILED
    data = records[0][1]
    assert (data.get('code') == 'native_replay_gap') is accepted
    assert 'secret' not in repr(data)
    if accepted:
        assert data['replay_ticket'] == 2**64 - 1
        assert data['replay_field_id'] == -1
        assert data['metadata_read_size'] == 8
        assert data['replay_reason_codes'] == '1'
        assert data['replay_script_lines'] == '1:123.2:456'


@pytest.mark.parametrize('suffix, accepted', [('4', True), ('10', False), ('4; token=private', False)])
def test_native_failure_code_is_recorded_without_debug(suffix, accepted):
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('native runtime mark failed; error_type=std::exception; native capture failed: code=' + suffix)
    def request(*_args, **_kwargs):
        raise failure
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.mark()
    assert caught.value is failure and probe.status == ObserverStatus.FAILED
    assert (records[0][1].get('code') == 'native_capture_failed') is accepted
    assert 'private' not in repr(records)
    if accepted:
        assert records[0][1]['native_failure_code'] == 4


def test_combined_native_fault_and_replay_gap_keep_both_bounded_causes():
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('native runtime mark failed; error_type=std::exception; '
        'native capture failed: code=6; native_replay_gap:v1,h=2,e=1,t=17,r=3,p=0,f=-1,g=1,m=8,k=1,q=0')
    def request(*_args, **_kwargs):
        raise failure
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.mark()
    assert caught.value is failure and probe.status == ObserverStatus.FAILED
    assert records[0][1]['native_failure_code'] == 6
    assert records[0][1]['code'] == 'native_replay_gap'
    assert records[0][1]['replay_ticket'] == 17


@pytest.mark.parametrize('detail, reason, code', [
    *[(text, reason, None) for text, reason in module.SAMPLING_FAILURE_REASONS.items()],
    ('sampling structural capture error: 0', 'structural_capture_error', 0),
    ('sampling structural capture error: 10', 'structural_capture_error', 10),
    ('sampling structural capture error: 11', None, None),
    ('sampling structural capture error: 01', None, None),
    ('sampling publication failed; token=PRIVATE', None, None),
    ('sampling publication failed\nPRIVATE', None, None),
    ('sampling publication certificate mismatch 0xPRIVATE', None, None),
    ('PRIVATE', None, None),
])
def test_sampling_failure_audit_is_finite_and_preserves_original_failure(detail, reason, code):
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('native runtime mark failed; error_type=std::exception; ' + detail)
    def request(*_args, **_kwargs):
        raise failure
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.mark()
    assert caught.value is failure and probe.status == ObserverStatus.FAILED
    data = records[0][1]
    assert data.get('sampling_failure_reason') == reason
    assert data.get('sampling_capture_error_code') == code
    assert 'PRIVATE' not in repr(data)


_SAMPLING_STAGE = 'native_sampling_failure:v1,s=7,c=32,j=599'
_SAMPLING_GAP = 'native_sampling_gap:v1,t=18446744073709551615,r=65535,p=65535,f=-1,g=16,m=1048576,k=0.31,q=1:999999.2:999999.1:999999'


@pytest.mark.parametrize('detail, accepted', [
    (_SAMPLING_STAGE, True),
    ('native_sampling_failure:v1,s=1,c=0,j=0', True),
    (_SAMPLING_GAP, True),
    (_SAMPLING_STAGE + '; ' + _SAMPLING_GAP, True),
    *[(_SAMPLING_STAGE.replace(old, new), False) for old, new in
      [('s=7', 's=0'), ('s=7', 's=8'), ('c=32', 'c=33'), ('c=32', 'c=-1'),
       ('j=599', 'j=600'), ('j=599', 'j=99'), ('j=599', 'j=001')]],
    *[(_SAMPLING_GAP.replace(old, new), False) for old, new in
      [('18446744073709551615', '18446744073709551616'), ('r=65535', 'r=65536'),
       ('p=65535', 'p=65536'), ('f=-1', 'f=-2'), ('g=16', 'g=0'), ('g=16', 'g=17'),
       ('m=1048576', 'm=1048577'), ('k=0.31', 'k=32'), ('k=0.31', 'k=' + '.'.join(['1'] * 17)),
       ('q=1:999999.2:999999.1:999999', 'q=3:123'),
       ('q=1:999999.2:999999.1:999999', 'q=1:1.1:1.1:1.1:1')]],
    (_SAMPLING_STAGE + '; ' + _SAMPLING_GAP + '; token=PRIVATE', False),
    (_SAMPLING_STAGE + '\nPRIVATE', False),
])
def test_numeric_sampling_failure_context_is_strict_and_keeps_original_exception(detail, accepted):
    probe = module.NativeHookEventProbe(collector())
    failure = RuntimeError('native runtime mark failed; error_type=std::exception; ' + detail)
    def request(*_args, **_kwargs):
        raise failure
    probe._pipe = SimpleNamespace(request=request)
    probe.status = ObserverStatus.READY
    records = []
    probe.set_request_timing_audit(lambda kind, data: records.append((kind, data)), enabled=lambda: False)
    with pytest.raises(RuntimeError) as caught:
        probe.mark()
    assert caught.value is failure and probe.status == ObserverStatus.FAILED
    data = records[0][1]
    assert ('sampling_failure_reason' in data) is accepted
    assert 'PRIVATE' not in repr(data)
    assert 'replay_hook_id' not in data and 'replay_edge' not in data
    if accepted and 'native_sampling_failure:' in detail:
        assert data['sampling_stage'] in range(1, 8)
        assert 0 <= data['sampling_lane_fault'] <= 32
    if accepted and 'native_sampling_gap:' in detail:
        assert data['code'] == 'native_sampling_gap'
        assert data['replay_ticket'] == 2**64 - 1
        assert data['replay_field_id'] == -1
        assert data['replay_reason_codes'] == '0.31'
