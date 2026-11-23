import io
import json
import sys

import pytest

import desktop_bridge
from native_pc.internal_entry import HOST_NAME, internal_host, internal_state_dir


def test_internal_state_is_disjoint_from_product_state(tmp_path):
    repo = tmp_path / 'collector'
    user_state = repo / 'runtime'
    user_state.mkdir(parents=True)
    with pytest.raises(ValueError, match='标准运行目录'):
        internal_state_dir(repo, user_state / 'capture')
    with pytest.raises(ValueError, match='标准运行目录'):
        internal_state_dir(repo, tmp_path)
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'settings.json').write_text('{}')
    with pytest.raises(ValueError, match='空状态目录'):
        internal_state_dir(repo, unrelated)
    isolated = internal_state_dir(repo, tmp_path / 'experiment')
    assert internal_state_dir(repo, isolated) == isolated


def test_desktop_accepts_only_its_own_initial_startup_logs(tmp_path):
    directory = tmp_path / 'desktop'
    directory.mkdir()
    (directory / 'wpf-startup.log').touch()
    (directory / 'wpf-collector-20261123-192521-663.log').touch()
    with pytest.raises(ValueError, match='空状态目录'):
        internal_state_dir(tmp_path / 'collector', directory)
    assert internal_state_dir(tmp_path / 'collector', directory, desktop=True) == directory


def test_bundle_requires_a_verified_internal_host(tmp_path):
    from hashlib import sha256

    collector = tmp_path / 'collector'
    host = collector / 'native_pc' / 'internal_event_host' / HOST_NAME
    host.parent.mkdir(parents=True)
    host.write_bytes(b'controlled host')
    metadata = tmp_path / 'metadata'
    metadata.mkdir()
    manifest = metadata / 'release.json'
    manifest.write_text('{}')
    with pytest.raises(ValueError, match='没有自研'):
        internal_host(collector)
    manifest.write_text(json.dumps({
        'internal_event_backend': {'protocol': 1},
        'files': {host.relative_to(tmp_path).as_posix(): sha256(host.read_bytes()).hexdigest()},
    }))
    assert internal_host(collector) == host
    host.write_bytes(b'altered')
    with pytest.raises(ValueError, match='清单不一致'):
        internal_host(collector)


def test_internal_bridge_excludes_device_detection(monkeypatch, tmp_path):
    def detect(*args, **kwargs):
        pytest.fail('internal bridge must not invoke device detection')

    monkeypatch.setattr(desktop_bridge, 'detect_instances', detect)
    output = io.StringIO()
    stream = io.StringIO(
        '{"op":"hello","id":1,"version":1}\n'
        '{"op":"detect","id":2,"version":1,"platform":"mumu"}\n'
        '{"op":"snapshot","id":3,"version":1}\n'
        '{"op":"quit","id":4,"version":1}\n')
    desktop_bridge.serve(stream, output, desktop_bridge.DesktopState(tmp_path), internal_capture=True)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply['kind'] for reply in replies] == ['hello', 'error', 'snapshot', 'bye']


def test_internal_bridge_rejects_live_options_before_starting(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['desktop_bridge.py', '--offline',
        '--internal-capture', 'normal', '--state-dir', str(tmp_path), '--native-game-root', 'unused'])
    monkeypatch.setattr(desktop_bridge.socket, 'socket', lambda *_: pytest.fail('must not open a socket'))
    with pytest.raises(SystemExit) as failure:
        desktop_bridge.main()
    assert failure.value.code == 2
