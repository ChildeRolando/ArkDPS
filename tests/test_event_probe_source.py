"""Source composition preserves old wire behavior and records host-owned bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from event_observer import FridaEventObserver, NativePcFridaObserver, ObserverStatus
from event_probe import assemble_probe_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform,adapter", [
    ("mumu", "event_probe_android2791.js"), ("native_pc", "event_probe_native_pc.js")])
def test_source_composition_and_component_hashes_bind_the_same_bytes(platform, adapter, tmp_path):
    context = "event_probe_autochess.js"
    for name in (context, adapter, "event_probe_runtime.js"):
        # Production checkout/package may contain CRLF. Compilation matches
        # the existing read_text boundary; file hashes still bind raw bytes.
        raw = (ROOT / name).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (tmp_path / name).write_bytes(raw)
    assembled = assemble_probe_source(platform, directory=tmp_path)
    manifest = assembled.audit()
    assert manifest["assembly_version"] == 2
    assert manifest["adapter_file"] == adapter
    assert manifest["runtime_file"] == "event_probe_runtime.js"
    assert manifest["context_file"] == context
    for key, name in (("context", context), ("adapter", adapter), ("runtime", "event_probe_runtime.js")):
        raw = (tmp_path / name).read_bytes()
        assert manifest[key + "_sha256"] == hashlib.sha256(raw).hexdigest()
        normalized = raw.decode().replace("\r\n", "\n")
        assert manifest[key + "_source_sha256"] == hashlib.sha256(normalized.encode()).hexdigest()
    expected = "\n".join((tmp_path / name).read_text(encoding="utf-8")
                         for name in (context, adapter, "event_probe_runtime.js"))
    assert assembled.source == expected
    assert manifest["assembled_sha256"] == hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
def test_fixture_loads_exact_host_assembled_source(platform):
    assembled = assemble_probe_source(platform)
    code = """const {loadProbeSource}=require('./tests/native_pc_frida_probe_fixture.js');
      const crypto=require('node:crypto');
      process.stdout.write(crypto.createHash('sha256').update(loadProbeSource(process.argv[1])).digest('hex'));"""
    result = subprocess.run(["node", "-e", code, str(ROOT / assembled.adapter_file)],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == assembled.assembled_sha256


def test_missing_or_wrong_source_format_cannot_fall_back_to_a_different_probe(tmp_path):
    with pytest.raises(ValueError, match="platform"):
        assemble_probe_source("producer-selected-fallback")
    with pytest.raises(FileNotFoundError):
        assemble_probe_source("native_pc", directory=tmp_path)
    (tmp_path / "event_probe_native_pc.js").write_text("// old standalone script")
    (tmp_path / "event_probe_runtime.js").write_text("// shared")
    with pytest.raises(ValueError, match="format"):
        assemble_probe_source("native_pc", directory=tmp_path)


def test_source_reader_assembles_exact_verified_snapshot_without_disk_rereads(tmp_path):
    expected = assemble_probe_source('native_pc')
    names = (expected.adapter_file, expected.runtime_file, expected.context_file)
    snapshot = {tmp_path / name: (ROOT / name).read_bytes() for name in names}
    calls = []

    def read(path):
        calls.append(path)
        return snapshot[path]

    # The directory is deliberately empty: a path cache followed by another
    # disk read would fail instead of composing the admitted bytes.
    actual = assemble_probe_source('native_pc', directory=tmp_path, read_file=read)
    assert actual == expected
    assert calls == [tmp_path / name for name in names]
    with pytest.raises(TypeError, match='immutable bytes'):
        assemble_probe_source('native_pc', directory=tmp_path,
                              read_file=lambda path: bytearray(snapshot[path]))


def test_amount_skill_and_settlement_logic_has_one_shared_owner():
    common = (ROOT / "event_probe_runtime.js").read_text(encoding="utf-8")
    for name in ("record", "settleDamage", "skillIdentity", "recordHpContext",
                 "markEvents", "readEvents", "ackEvents", "installEnemyStateHooks"):
        assert common.count("function " + name + "(") == 1
        for adapter in ("event_probe_android2791.js", "event_probe_native_pc.js"):
            assert "function " + name + "(" not in (ROOT / adapter).read_text(encoding="utf-8")
    for adapter in ("event_probe_android2791.js", "event_probe_native_pc.js"):
        source = (ROOT / adapter).read_text(encoding="utf-8")
        for event_field in ("settlement_complete", "skill_start", "shield_absorbed", "mitigation_before"):
            assert event_field not in source


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("load_failure", [False, True])
def test_submitted_source_manifest_survives_ready_or_load_failure(monkeypatch, platform, load_failure):
    loaded = []
    class Script:
        is_destroyed = False
        def on(self, _event, callback):
            self.notify = callback
        def load(self):
            if load_failure:
                raise RuntimeError("owned synthetic load failure")
            self.notify({"type": "send", "payload": {
                "ready": True, "arch": "x64" if platform == "native_pc" else "arm64", "probe_source": {"submitted_sha256": "producer-forgery"},
                "assembled_sha256": "producer-forgery"}}, None)
        @property
        def exports_sync(self):
            return SimpleNamespace(stop=lambda: None)
        def unload(self):
            self.is_destroyed = True
    class Session:
        is_detached = False
        def on(self, *_args):
            pass
        def create_script(self, source):
            loaded.append(source)
            return Script()
        def detach(self):
            self.is_detached = True
    session = Session()
    device = SimpleNamespace(attach=lambda *_args, **_kwargs: session)
    monkeypatch.setitem(sys.modules, "frida", SimpleNamespace(
        __version__="17.26.1", get_local_device=lambda: device,
        get_device_manager=lambda: SimpleNamespace(add_remote_device=lambda _: device)))
    monkeypatch.setenv("ARKDPS_TRACE_HP_CONTEXT", "1")
    monkeypatch.setenv("ARKDPS_COALESCE_ENEMY_HP", "0")
    collector = SimpleNamespace(diagnostics=SimpleNamespace(
        connection_ok=True, version_status=VersionStatus.COMPATIBLE),
        process=SimpleNamespace(pid=123, process_instance=(123, 456), process_alive=lambda: True),
        core=SimpleNamespace(mc=SimpleNamespace(pid=123)))
    observer = NativePcFridaObserver(collector) if platform == "native_pc" else FridaEventObserver(collector, "owned")
    if load_failure:
        with pytest.raises(RuntimeError, match="owned synthetic load failure"):
            observer.start()
        assert observer.status == ObserverStatus.FAILED
        assert observer.session is observer.script is None
    else:
        observer.start()
        assert observer.status == ObserverStatus.READY
    manifest = observer.probe_modes["probe_source"]
    assembled = assemble_probe_source(platform)
    assert {key: manifest[key] for key in assembled.audit()} == assembled.audit()
    assert loaded[0].startswith("globalThis.arkdpsCoalesceEnemyHp = false;\n")
    assert manifest["submitted_sha256"] == hashlib.sha256(loaded[0].encode()).hexdigest()
    assert manifest["submitted_sha256"] != manifest["assembled_sha256"]
    # A second producer ready notification cannot erase or replace provenance.
    observer._message({"type": "send", "payload": {
        "ready": True, "arch": "x64" if platform == "native_pc" else "arm64", "probe_source": {"submitted_sha256": "producer-forgery"}}}, None)
    assert observer.probe_modes["probe_source"] == manifest
    if not load_failure:
        observer.stop()


# Frozen pre-extraction source: PC27073143 / Android7d245a29 at43bb3bb.
# Phase 1 deeply compared all five complete stdout objects before business
# repairs (runtime/event-probe-reuse-phase1/final-receipt.json). The fault
# declarations were then deliberately corrected; their business truth is tested
# by test_event_probe_source_settlement/local_settlement, rather than freezing
# the old global failures. Every normal scenario still compares its whole JSON
# object, including all events, numeric fields and quality declarations. The
# additive producer-boundary contract has a second full-object hash below;
# the original pre-boundary projection must still match these frozen hashes.
@pytest.mark.parametrize("args,expected", [
    (["tests/native_pc_frida_probe_fixture.js", "event_probe_native_pc.js"],
     "342a5a5367e9c1de59e7387cc5db456cd699a75d24020ee04cbe92d4ab32e6c7"),
    (["tests/event_probe_quality_fixture.js"],
     "8a9be77df713875c4995d6fa31b0660337d605006d2447a35629a4123d24a068"),
    (["tests/native_pc_settlement_order_fixture.js"],
     "4973f5562e5615228d0b236fa8288cedf71a022a364cfafa7a2856d2cc457d1d"),
    (["tests/event_probe_pipeline_fixture.js"],
     "c164421621daaca45d03a4342542e27612b30ce782cce80e2a787f9bf97963be"),
    (["tests/event_probe_retained_queue_fixture.js", "event_probe_android2791.js"],
     "f0b9644db28cde752b8a2060487163a3ab9e9d7fb3bdde3071cdf9ce8264d2d5"),
])
def test_shared_runtime_preserves_complete_frozen_normal_wire_objects(args, expected):
    result = subprocess.run(["node", *args], cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    if args == ["tests/native_pc_settlement_order_fixture.js"]:
        payload = {backend: {name: value for name, value in cases.items() if name != 'rejected'}
                   for backend, cases in payload.items()}
    elif args == ["tests/event_probe_pipeline_fixture.js"]:
        normal = ('target_return_before_apply_commit', 'nonlethal_cap', 'lethal_hit',
                  'full_block_unit_before_shield', 'full_block_unit_after_shield',
                  'late_amplification_130_after_unit', 'late_amplification_unrelated_after_unit',
                  'late_attenuation_after_unit', 'late_amplification_before_unit',
                  'late_amplification_during_unit', 'late_amplification_old_output')
        payload = {backend: {name: cases[name] for name in normal} for backend, cases in payload.items()}
    extended_hashes = {
        'tests/event_probe_quality_fixture.js': '5042a3c89ee5e2dd709f186c351463e355880461ca463f8bb3f7d6c825685987',
        'tests/native_pc_settlement_order_fixture.js': 'bc3792874c309d3aa292ae2b4d66cc6e17faf3348308424dcce31eabb7f7c6e2',
        'tests/event_probe_pipeline_fixture.js': '84776a0810f29b1dc79b2713f4a11cb84c045d316bf62f2eb6fbf9cb782d0592',
        'tests/event_probe_retained_queue_fixture.js': '3abb08e9fa139e8b24f2b3e5a71665d6075bfc066dbc6dcc8126f2b5727e5bed',
    }
    def digest(value):
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return hashlib.sha256(canonical).hexdigest()

    terminal_read_hashes = {
        'tests/event_probe_quality_fixture.js': 'aa6e7945b7173194caf0ee09dce54d082eab9c502f40d152c6816f9a27db0b4b',
        'tests/native_pc_settlement_order_fixture.js': '411c11b96b97f139b147d01058f37fda7b78d3d794532e0f2e0fcdaec4febb83',
        'tests/event_probe_pipeline_fixture.js': '5d5a88a72f80795992fac843be86506ab1bacfc3a3d7379e2e4b190633d65a12',
    }

    def before_terminal_read(value):
        if isinstance(value, dict):
            if 'terminal_read_version' in value:
                assert type(value['terminal_read_version']) is int and value['terminal_read_version'] == 1
            return {key: before_terminal_read(item) for key, item in value.items()
                    if key != 'terminal_read_version'}
        if isinstance(value, list):
            return [before_terminal_read(item) for item in value]
        return value

    assert digest(payload) == terminal_read_hashes.get(args[0], extended_hashes.get(args[0], expected))
    assert digest(before_terminal_read(payload)) == extended_hashes.get(args[0], expected)
    additions = {'initial_boundary', 'initial_boundary_diagnostics', 'terminal_boundary_diagnostics'}
    observed_additions = set()

    def assert_boundary_contract(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in additions:
                    observed_additions.add(key)
                if key == 'initial_boundary' and item is not None:
                    assert set(item) == {'version', 'source', 'method', 'phase', 'state', 'hooks_ready',
                        'native_identity', 'native_stage_id', 'native_generation', 'native_process_pid',
                        'native_frame', 'native_fixed_time', 'frame', 'game_time', 'sequence', 'epoch',
                        'active', 'damage_settlement_scope'}
                    assert item['version'] == 1 and item['source'] == 'native-pc-producer'
                    assert item['method'] == 'BattleController.set_state' and item['phase'] == 'leave'
                    assert item['state'] == 2 and item['hooks_ready'] is True
                    assert item['frame'] == item['native_frame'] == item['sequence'] == item['active'] == 0
                    assert item['game_time'] == 0 and item['native_fixed_time'] == '0'
                    assert set(item['native_identity']) == {'controller', 'native', 'logger', 'stats', 'level'}
                if key in ('initial_boundary_diagnostics', 'terminal_boundary_diagnostics'):
                    counters = ({'attempts', 'captured', 'rejected', 'invalidated'} if key.startswith('initial_')
                        else {'destroy_entered', 'destroy_left', 'candidates', 'captured', 'rejected', 'invalidated'})
                    assert set(item) == counters | {'last_rejection'}
                    assert all(type(item[name]) is int and 0 <= item[name] <= 1000000 for name in counters)
                    assert item['last_rejection'] is None or (
                        isinstance(item['last_rejection'], str) and 0 < len(item['last_rejection']) <= 64
                        and all(character in 'abcdefghijklmnopqrstuvwxyz_' for character in item['last_rejection']))
                assert_boundary_contract(item)
        elif isinstance(value, list):
            for item in value:
                assert_boundary_contract(item)

    assert_boundary_contract(payload)
    if args[0] in extended_hashes:
        assert 'terminal_boundary_diagnostics' in observed_additions

    def original_contract(value):
        if isinstance(value, dict):
            return {key: original_contract(item) for key, item in value.items() if key not in additions}
        if isinstance(value, list):
            return [original_contract(item) for item in value]
        return value

    assert digest(original_contract(before_terminal_read(payload))) == expected
