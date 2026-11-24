from dataclasses import replace
import io
import json
import sys
from types import SimpleNamespace

import pytest

import app
from connection import ConnectionConfig, ConnectionConfigStore
import desktop_bridge
from native_pc import event_source
from native_pc.internal_entry import HOST_PATH, internal_host, live_native_host


def test_legacy_defaults_to_frida_and_mumu_retains_pc_preference(tmp_path):
    old = ConnectionConfig.from_dict({'version': 1, 'platform': 'native_pc'})
    assert old.native_pc_event_backend == 'frida' and old.native_pc_event_feed is None
    store = ConnectionConfigStore(tmp_path / 'connection.json')
    selected = ConnectionConfig.from_dict({
        **old.to_dict(), 'native_pc_event_backend': 'native',
        'native_pc_event_feed': str(tmp_path / 'combat log.jsonl'),
    })
    store.save(replace(selected, platform='mumu'))
    assert replace(store.load().config, platform='native_pc') == selected


@pytest.mark.parametrize('invalid', [
    {'native_pc_event_backend': 'other'}, {'native_pc_event_backend': None},
    {'native_pc_event_feed': 'relative.jsonl'}, {'native_pc_event_feed': False},
])
def test_invalid_pc_source_fields_are_rejected(invalid):
    with pytest.raises(ValueError):
        ConnectionConfig.from_dict(invalid)


def test_pc_source_switch_waits_for_old_runtime_and_native_never_uses_frida(tmp_path, monkeypatch):
    calls = []

    class FridaRuntime:
        fail_close = True
        observer_factory = object()

        def start(self):
            calls.append('Frida start')

        def close(self):
            calls.append('Frida close')
            if self.fail_close:
                raise RuntimeError('old owner still active')

    frida = FridaRuntime()
    monkeypatch.setattr(event_source, '_native_pc_frida_runtime', lambda *_: frida)
    native_factory = object()
    native = SimpleNamespace(observer_factory=native_factory,
        start=lambda: calls.append('native start'), close=lambda: calls.append('native close'))
    monkeypatch.setattr(event_source, '_native_pc_hook_runtime', lambda *_, **__: native)
    source = event_source.PcEventSource(tmp_path)
    root = tmp_path / 'game'
    assert source.select(enabled=True, game_root=root) is frida.observer_factory
    with pytest.raises(event_source.PcEventSourceCleanupError, match='old owner'):
        source.select(enabled=True, game_root=root, backend='native')
    assert source.runtime is frida
    frida.fail_close = False
    observer_factory = source.select(enabled=True, game_root=root, backend='native')
    assert source.runtime is native and calls == ['Frida start', 'Frida close', 'Frida close', 'native start']
    assert observer_factory is native_factory
    assert source.select(enabled=False, game_root=root, backend='native') is None
    assert calls[-1] == 'native close'


def test_native_runtime_reuses_owner_retries_explicitly_and_never_prepares_frida(tmp_path, monkeypatch):
    calls = []
    factory = object()
    native = SimpleNamespace(observer_factory=factory, start=lambda: calls.append('start'),
        retry=lambda: calls.append('retry'), close=lambda: calls.append('close'))
    monkeypatch.setattr(event_source, '_native_pc_hook_runtime', lambda *_, **__: native)
    monkeypatch.setattr(event_source, '_native_pc_frida_runtime', lambda *_: pytest.fail('native imported Frida'))
    source = event_source.PcEventSource(tmp_path, collector_root=tmp_path)
    assert source.select(enabled=True, game_root=tmp_path / 'game', backend='native') is factory
    assert source.select(enabled=True, game_root=tmp_path / 'game', backend='native') is factory
    assert calls == ['start']
    source.select(enabled=True, game_root=tmp_path / 'game', backend='native', explicit_retry=True)
    assert calls == ['start', 'retry']
    source.close()
    assert source.runtime is None and calls[-1] == 'close'


def test_desktop_worker_reports_launch_override_without_saving_it(tmp_path, monkeypatch):
    state_dir = tmp_path / 'isolated'
    feed = tmp_path / 'cooperative.jsonl'
    monkeypatch.setattr(sys, 'argv', [
        'desktop_bridge.py', '--offline', '--state-dir', str(state_dir),
        '--native-pc-event-backend', 'native', '--native-pc-event-feed', str(feed),
    ])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(
        '{"version":1,"id":1,"op":"hello"}\n{"version":1,"id":2,"op":"snapshot"}\n'
        '{"version":1,"id":3,"op":"quit"}\n'))
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    desktop_bridge.main()
    state = json.loads(output.getvalue().splitlines()[1])['state']
    assert state['connection_config']['native_pc_event_backend'] == 'frida'
    assert state['effective_connection_config']['native_pc_event_backend'] == 'native'
    assert state['effective_connection_config']['native_pc_event_feed'] == str(feed)
    assert not (state_dir / 'connection.json').exists()


def test_cli_reads_saved_kernel_but_one_off_override_does_not_persist(tmp_path, monkeypatch):
    import capture_cli

    feed = str(tmp_path / 'feed.jsonl')
    store = ConnectionConfigStore(tmp_path / 'runtime' / 'connection.json')
    config = ConnectionConfig(platform='native_pc', native_game_root='game',
                              native_pc_event_backend='native', native_pc_event_feed=feed)
    store.save(config)
    args_seen = []
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(app.socket, 'socket', lambda: SimpleNamespace(
        bind=lambda _address: None, listen=lambda _backlog: None, close=lambda: None))
    monkeypatch.setattr(capture_cli, 'run_capture', lambda args, **_: args_seen.append(args))
    argv = ['app.py', '--capture-seconds', '1']
    monkeypatch.setattr(sys, 'argv', argv)
    assert app.main() is None
    monkeypatch.setattr(sys, 'argv', [*argv, '--native-pc-event-backend', 'frida'])
    assert app.main() is None
    monkeypatch.setattr(sys, 'argv', [*argv, '--native-pc-event-backend', 'native'])
    assert app.main() is None
    assert [(x.native_pc_event_backend, x.native_pc_event_feed) for x in args_seen] == [
        ('native', feed), ('frida', feed), ('native', None)]
    assert store.load().config == config


def test_live_host_manifest_is_separate_from_the_controlled_build(tmp_path):
    from hashlib import sha256

    host = tmp_path / HOST_PATH
    host.parent.mkdir(parents=True)
    host.write_bytes(b'scoped host')
    digest = sha256(host.read_bytes()).hexdigest()
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'release.json').write_text(json.dumps({
        'native_pc_event_backend': dict(path=HOST_PATH, sha256=digest, provider='internal-test-host',
                                        protocol=1, source_version=1, scope='cooperative-live'),
        'files': {HOST_PATH: digest},
    }))
    assert live_native_host(tmp_path / 'collector') == host
    with pytest.raises(ValueError, match='没有 ArkPie'):
        internal_host(tmp_path / 'collector')


def test_native_hooks_missing_from_portable_fail_before_loading(tmp_path):
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    (metadata / 'release.json').write_text('{}')
    source = event_source.PcEventSource(tmp_path / 'state', collector_root=tmp_path / 'collector')
    factory = source.select(enabled=True, backend='native')
    with pytest.raises(RuntimeError, match='缺少 ArkPie PC 采集组件'):
        factory(object(), None)
