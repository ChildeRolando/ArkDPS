"""The research gate must stop before every disallowed or unverifiable read."""

import hashlib
import importlib.util
from pathlib import Path
import struct

import pytest

from native_pc import probe_native_pc as build_gate


SPEC = importlib.util.spec_from_file_location(
    'native_pc_probe', Path(__file__).parents[1] / 'acceptance' / 'probe_native_pc.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeApi:
    def __init__(self, root):
        self.root = root
        self.candidates = [42]
        self.query_error = None
        self.read_open_error = None
        self.module_error = None
        self.read_error = None
        self.path = root / 'Arknights.exe'
        self.module_path = root / 'GameAssembly.dll'
        self.opens = []
        self.closes = []
        self.reads = []
        self.content = b'header'

    def named_processes(self):
        return self.candidates

    def open_process(self, pid, access):
        self.opens.append((pid, access))
        error = self.read_open_error if access == probe.READ_ACCESS else self.query_error
        if error is not None:
            raise probe.ApiFailure('OpenProcess', error)
        return access

    def image_path(self, handle):
        return self.path

    def close(self, handle):
        self.closes.append(handle)

    def modules(self, handle):
        if self.module_error:
            raise probe.ApiFailure('EnumProcessModulesEx', self.module_error)
        return [(str(self.module_path), 0x100000, 0x1000000)]

    def read(self, handle, address, size):
        self.reads.append((address, size))
        if self.read_error:
            raise probe.ApiFailure('ReadProcessMemory', self.read_error, 2)
        return self.content


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, 'verify_build', lambda root: (
        {'known_build': True}, [('pe_header', 0, b'header')]))
    return tmp_path, FakeApi(tmp_path)


def test_unknown_build_stops_before_process_discovery(tmp_path):
    report = probe.probe(tmp_path, object())
    assert report['status'] == 'unsupported_build'
    assert report['memory']['attempted'] is False
    assert report['read_access']['attempted'] is False


def test_hash_mismatch_is_explicit(monkeypatch, tmp_path):
    (tmp_path / 'test.bin').write_bytes(b'unknown')
    monkeypatch.setattr(probe, 'KNOWN_FILES', {'test.bin': hashlib.sha256(b'known').hexdigest()})
    build, samples = probe.verify_build(tmp_path)
    assert build['known_build'] is False
    assert build['files'][0]['sha256_matches'] is False
    assert samples is None


@pytest.mark.parametrize(('exe', 'metadata', 'expected_build'), [
    (b'official exe', b'official metadata', build_gate.BUILD_ID),
    (b'Bilibili exe', b'Bilibili metadata', 'win-x64:fixture-bilibili'),
    (b'official exe', b'Bilibili metadata', None),
])
def test_verify_build_matches_only_complete_tuples(monkeypatch, tmp_path, exe, metadata, expected_build):
    exe_path = 'Arknights.exe'
    dll_path = 'GameAssembly.dll'
    metadata_path = 'Arknights_Data/il2cpp_data/Metadata/global-metadata.dat'
    dll = b'shared GameAssembly'
    def hashes(exe_bytes, metadata_bytes):
        files = {exe_path: exe_bytes, dll_path: dll, metadata_path: metadata_bytes}
        return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}

    monkeypatch.setattr(build_gate, 'KNOWN_BUILDS', {
        build_gate.BUILD_ID: hashes(b'official exe', b'official metadata'),
        'win-x64:fixture-bilibili': hashes(b'Bilibili exe', b'Bilibili metadata'),
    })
    for name, content in {exe_path: exe, dll_path: dll, metadata_path: metadata}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    code = b'x' * build_gate.CODE_SIZE
    monkeypatch.setattr(build_gate, 'read_pe', lambda _: (0x8664, b'header', code))

    build, samples = build_gate.verify_build(tmp_path)

    assert build['known_build'] is (expected_build is not None)
    assert build.get('source_build') == expected_build
    if expected_build is None:
        assert samples is None
    else:
        assert samples == [('pe_header', 0, b'header'), ('known_code', build_gate.CODE_RVA, code)]


def test_pe_sample_uses_rva_mapping_instead_of_file_offset(tmp_path):
    binary = bytearray(512)
    binary[:2] = b'MZ'
    struct.pack_into('<I', binary, 60, 64)
    binary[64:68] = b'PE\0\0'
    struct.pack_into('<HH', binary, 68, 0x8664, 1)
    # The section containing CODE_RVA is physically at byte 256.
    struct.pack_into('<4I', binary, 64 + 24 + 8, 64, probe.CODE_RVA, 64, 256)
    binary[256:256 + probe.CODE_SIZE] = b'x' * probe.CODE_SIZE
    path = tmp_path / 'GameAssembly.dll'
    path.write_bytes(binary)
    machine, header, code = probe.read_pe(path)
    assert machine == 0x8664
    assert header == binary[:64]
    assert code == b'x' * probe.CODE_SIZE


@pytest.mark.parametrize('case', ['absent', 'wrong_path', 'query_denied', 'ambiguous'])
def test_unverified_process_never_gets_read_handle(gate, case):
    root, api = gate
    if case == 'absent':
        api.candidates = []
    elif case == 'wrong_path':
        api.path = root / 'other' / 'Arknights.exe'
    elif case == 'query_denied':
        api.query_error = 5
    else:
        api.candidates = [42, 43]
    report = probe.probe(root, api)
    assert report['status'] in ('no_matching_process', 'ambiguous_process')
    assert all(access == probe.PROCESS_QUERY_LIMITED_INFORMATION for _, access in api.opens)
    assert api.reads == []
    assert report['read_access']['attempted'] is False


def test_access_denied_is_one_attempt_and_zero_memory_reads(gate):
    root, api = gate
    api.read_open_error = 5
    report = probe.probe(root, api)
    assert report['status'] == 'access_denied'
    assert report['error'] == {'api': 'OpenProcess', 'win32_error': 5}
    assert report['read_access']['attempted'] is True
    assert report['read_access']['opened'] is False
    assert api.opens.count((42, probe.READ_ACCESS)) == 1
    assert api.reads == []


@pytest.mark.parametrize('case', ['wrong_path', 'query_failed'])
def test_unverified_module_stops_before_memory_read(gate, case):
    root, api = gate
    if case == 'wrong_path':
        api.module_path = root / 'other' / 'GameAssembly.dll'
    else:
        api.module_error = 5
    report = probe.probe(root, api)
    assert report['status'] in ('module_path_mismatch', 'process_access_failed')
    assert api.reads == []
    assert api.closes == [probe.PROCESS_QUERY_LIMITED_INFORMATION, probe.READ_ACCESS]


def test_failed_read_reports_partial_count_and_stops(gate):
    root, api = gate
    api.read_error = 299
    report = probe.probe(root, api)
    assert report['status'] == 'memory_read_failed'
    assert report['memory']['attempted'] is True
    assert report['memory']['reads_completed'] == 0
    assert report['memory']['samples'][0]['bytes_read'] == 2
    assert report['memory']['samples'][0]['win32_error'] == 299
    assert len(api.reads) == 1
    assert api.closes[-1] == probe.READ_ACCESS


def test_changed_memory_content_stops(gate):
    root, api = gate
    api.content = b'change'
    report = probe.probe(root, api)
    assert report['status'] == 'memory_content_mismatch'
    assert report['memory']['samples'][0]['matches_file_bytes'] is False


def test_success_closes_handles_and_keeps_raw_data_out(gate):
    root, api = gate
    report = probe.probe(root, api)
    assert report['status'] == 'read_gate_passed'
    assert report['memory']['reads_completed'] == 1
    assert api.closes == [probe.PROCESS_QUERY_LIMITED_INFORMATION, probe.READ_ACCESS]
    text = repr(report)
    assert str(root) not in text
    assert '0x100000' not in text
    assert "b'header'" not in text


def test_success_requires_both_fixed_comparisons(gate, monkeypatch):
    root, api = gate
    monkeypatch.setattr(probe, 'verify_build', lambda root: (
        {'known_build': True},
        [('pe_header', 0, b'header'), ('known_code', probe.CODE_RVA, b'header')]))
    report = probe.probe(root, api)
    assert report['status'] == 'read_gate_passed'
    assert api.reads == [(0x100000, 6), (0x100000 + probe.CODE_RVA, 6)]
    assert report['memory']['reads_completed'] == 2


def test_handle_only_does_not_enumerate_modules_or_read_memory(gate):
    root, api = gate
    api.module_error = 5
    report = probe.probe(root, api, handle_only=True)
    assert report['status'] == 'read_handle_available'
    assert api.closes == [probe.PROCESS_QUERY_LIMITED_INFORMATION, probe.READ_ACCESS]
    assert report['memory']['attempted'] is False
    assert api.reads == []
