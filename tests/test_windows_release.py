"""Release provenance must work without Git and must reject stale/dirty candidates."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from release_identity import load_manifest
import capture_cli
from test_capture_cli import Session, options, stub_git_and_clock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_builder', ROOT / 'tools/build_windows.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
HEAD = 'a' * 40


def manifest(tmp_path):
    (tmp_path / 'resource.txt').write_text('resource')
    value = dict(schema_version=1, head=HEAD, files={
        'resource.txt': hashlib.sha256(b'resource').hexdigest()})
    (tmp_path / 'build-manifest.json').write_text(json.dumps(value))
    return value


def test_manifest_hashes_reject_modified_resource(tmp_path):
    manifest(tmp_path)
    assert load_manifest(tmp_path, verify=True)['head'] == HEAD
    (tmp_path / 'resource.txt').write_text('modified')
    with pytest.raises(ValueError, match='checksum mismatch'):
        load_manifest(tmp_path, verify=True)


@pytest.mark.parametrize('field,value', [('head', 'dirty'), ('schema_version', 2),
                                        ('files', {}), ('files', {'../outside': 'a'*64}),
                                        ('files', {'C:/outside': 'a'*64}),
                                        ('files', {r'folder\outside': 'a'*64}),
                                        ('files', {'/absolute': 'a'*64})])
def test_invalid_manifests_rejected(tmp_path, field, value):
    data = manifest(tmp_path)
    data[field] = value
    (tmp_path / 'build-manifest.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_manifest(tmp_path, verify=True)


def test_builder_refuses_dirty_tree_and_wrong_head(monkeypatch):
    monkeypatch.setattr(builder, 'git', lambda *args: HEAD if args[0] == 'rev-parse' else ' M app.py')
    with pytest.raises(RuntimeError, match='clean committed'):
        builder.candidate_head(HEAD)
    with pytest.raises(RuntimeError, match='candidate mismatch'):
        builder.candidate_head('b'*40)


def test_only_tracked_resources_are_bundled(monkeypatch):
    monkeypatch.setattr(builder, 'git', lambda *args: 'vendor/ark-source/LICENSE\nassets/arkdps.ico')
    resources = builder.tracked_data()
    assert all(str(p).startswith(str(ROOT)) for p, _ in resources)
    assert (ROOT / 'vendor/ark-source/LICENSE', 'vendor/ark-source') in resources
    assert not any('runtime' in p.parts or 'tests' in p.parts for p, _ in resources)
    assert (ROOT / 'event_probe_android2791.js', '.') in resources
    assert (ROOT / 'LICENSE', '.') in resources


def test_frozen_capture_uses_manifest_not_git(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    monkeypatch.setattr(capture_cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(capture_cli, '__file__', str(tmp_path / 'capture_cli.py'))
    manifest(tmp_path)
    def no_git(*args, **kwargs):
        raise AssertionError('portable artifact must not require Git')
    monkeypatch.setattr(capture_cli.subprocess, 'check_output', no_git)
    args = options(tmp_path)
    args.expect_head = HEAD
    result = capture_cli.run_capture(args, session_factory=Session)
    assert result['error'] is None
    assert json.loads((args.output_dir / 'READY.json').read_text())['head'] == HEAD


def test_frozen_capture_refuses_wrong_candidate_before_connect(tmp_path, monkeypatch):
    stub_git_and_clock(monkeypatch)
    monkeypatch.setattr(capture_cli.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(capture_cli, '__file__', str(tmp_path / 'capture_cli.py'))
    manifest(tmp_path)
    args = options(tmp_path)
    args.expect_head = 'b'*40
    with pytest.raises(RuntimeError, match='candidate mismatch'):
        capture_cli.run_capture(args, session_factory=Session)
    assert not args.output_dir.exists()


def test_live_measurements_do_not_treat_paused_frames_or_new_battle_as_active():
    from release_probe import summarize
    def point(frame, seconds, *, session='a', panels=1):
        return dict(session=session, frame=frame, game_time=seconds,
                    captured_monotonic=seconds, speed_level=1, panels=panels,
                    huds=1, read_ms=5, render_complete_age_ms=15)
    points = [point(1, 1), point(1, 1), point(2, 2),
              point(1, 1, session='b'), point(2, 2, session='b', panels=3)]
    result = summarize(points)
    assert result['speed_level=1,panels=1,huds=1']['samples'] == 1
    assert result['speed_level=1,panels=3,huds=1']['samples'] == 1
    assert result['speed_level=1,panels=1,huds=1']['interval_ms']['p95'] == 1000
    assert summarize([point(1, 1), point(1, 1)]) == {}


def test_real_windows_process_resources_are_positive():
    from release_probe import process_resources
    result = process_resources()
    assert result['private_bytes'] > 0 and result['working_set_bytes'] > 0
    assert result['cpu_seconds'] >= 0


def test_frozen_module_relative_resources_are_mirrored(monkeypatch):
    paths = ['vendor/ark-source/tools/enemy_health/bin/memsrv',
             'vendor/ark-source/tools/enemy_health/generated_offsets.json',
             'vendor/ark-source/game_data/manifest.json',
             'vendor/ark-source/tools/enemy_health/memcore.py']
    monkeypatch.setattr(builder, 'git', lambda *args: '\n'.join(paths))
    resources = builder.tracked_data()
    assert (ROOT / paths[0], 'tools/enemy_health/bin') in resources
    assert (ROOT / paths[1], 'tools/enemy_health') in resources
    assert (ROOT / paths[2], 'game_data') in resources
    assert (ROOT / paths[3], 'tools/enemy_health') not in resources
