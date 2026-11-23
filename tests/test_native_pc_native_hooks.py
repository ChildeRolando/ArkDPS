"""Controlled transport/ownership checks, never evidence of game acquisition."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import ObserverStatus
from event_probe import ObserverReadRetry
from native_pc import native_hook_source as module
from native_pc import event_source


ROOT = Path(__file__).resolve().parents[1]


def collector(*, build=module.BUILD_ID, initialized=True):
    process = SimpleNamespace(pid=42, process_instance=(42, 100),
                              process_alive=lambda: True, observer_boot_ready=lambda: initialized)
    return SimpleNamespace(process=process, diagnostics=SimpleNamespace(
        connection_ok=True, version_status=VersionStatus.COMPATIBLE, version_actual=build),
        observer_binding=lambda **_: dict(native_session='bound-session', source_build=build))


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


@pytest.fixture
def setup_probe(tmp_path, monkeypatch):
    binary = tmp_path / 'source.dll'
    binary.write_bytes(b'controlled-not-an-executable')
    binary.with_suffix('.build.json').write_text(json.dumps(dict(sha256=sha256(binary.read_bytes()).hexdigest())))
    monkeypatch.setattr(module, 'native_hook_binary', lambda root: binary)
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
        return module.NativeHookEventProbe(collector(**options), root=ROOT, runtime_dir=tmp_path / 'sessions',
            loader_factory=make_loader, pipe_factory=make_pipe)
    return make, loaders, pipes


def test_real_source_is_submitted_without_frida_or_fabricated_events(setup_probe, monkeypatch):
    import builtins
    original = builtins.__import__
    def reject_frida(name, *args, **kwargs):
        if name == 'frida' or name.startswith('frida.'):
            raise AssertionError('native observer attempted to import Frida')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', reject_frida)
    make, loaders, pipes = setup_probe
    probe = make()
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
    assert source['live_acceptance'] == 'pending'
    assert config['token'] not in json.dumps(probe.probe_modes)
    assert probe.mark()['native_session'] == 'bound-session'
    assert pipes[0].calls[-1] == ('mark', dict(timeout=5.0, autochess_enabled=False))
    probe.stop()
    assert loaders[0].closed and pipes[0].closed
    assert probe.hook_disable_confirmed


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
    assert pipes[0].calls[-1][1] == dict(timeout=5.0, after=2, through=2)
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
    with pytest.raises(RuntimeError, match='实机验收'):
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


@pytest.fixture
def prepared_setup(tmp_path, monkeypatch, setup_probe):
    _, loaders, pipes = setup_probe
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

    def prepare():
        return module.PreparedNativeHooks(game_root, root=ROOT, runtime_dir=tmp_path / 'early',
                                          loader_factory=EarlyLoader)

    def probe(prepared, **options):
        selected = collector(**options)
        selected.game_root = game_root

        def reject_second_load(pid):
            raise AssertionError('adoption attempted another DLL load')

        def make_pipe(name, pid):
            value = Pipe(name, pid)
            pipes.append(value)
            return value

        return module.NativeHookEventProbe(selected, root=ROOT, prepared=prepared,
            loader_factory=reject_second_load, pipe_factory=make_pipe)

    return prepare, probe, state, loaders, pipes


def test_preparation_loads_without_vm_or_pipe_then_adopts_after_vm_gate(prepared_setup, monkeypatch):
    prepare, make_probe, _, loaders, pipes = prepared_setup
    monkeypatch.setenv('ARKDPS_COALESCE_ENEMY_HP', '0')
    prepared = prepare().prepare((42, 100))
    assert len(loaders) == 1 and loaders[0].loaded and not pipes
    assert prepared.hook_disable_confirmed and not prepared.claimed
    assert prepared.prepared_at is not None and prepared.expires_at == prepared.prepared_at + 120
    assert prepared.diagnostics()['prepared'] is True
    config = loaders[0].config
    assert config['source'].startswith('globalThis.arkdpsCoalesceEnemyHp = false;\n')
    assert config['token'] not in json.dumps(prepared.diagnostics())
    not_ready = make_probe(prepared, initialized=False)
    with pytest.raises(RuntimeError, match='IL2CPP'):
        not_ready.start()
    assert not prepared.claimed and not pipes and not loaders[0].closed
    probe = make_probe(prepared)
    probe.start()
    assert prepared.claimed and probe._loader is loaders[0] and len(loaders) == 1
    assert pipes[0].calls[0][0] == 'initialize'
    assert probe.probe_modes['probe_source']['submitted_sha256'] == sha256(config['source'].encode()).hexdigest()
    assert not probe.hook_disable_confirmed
    prepared.close()
    assert not loaders[0].closed and not pipes[0].closed
    probe.stop()
    assert loaders[0].closed and pipes[0].closed and probe.hook_disable_confirmed


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
    assert pipes[0].calls[-1] == ('mark', dict(timeout=5.0, autochess_enabled=not enabled))
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
