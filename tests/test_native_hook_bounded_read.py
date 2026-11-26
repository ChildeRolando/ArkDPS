"""Bounded native payload reads use owned files only; no DLL is executed."""
from io import BytesIO
from pathlib import Path
import tracemalloc

import pytest

from native_pc import native_hook_source as module
from test_native_hook_portable import portable


@pytest.mark.parametrize('size', [0, 1, 15, 16])
def test_portable_read_returns_complete_bytes_through_its_limit(tmp_path, size):
    data = bytes(range(size))
    (tmp_path / 'payload').write_bytes(data)
    assert module._portable_bytes(tmp_path, 'payload', limit=16) == data


def test_portable_read_rejects_the_first_byte_over_its_limit(tmp_path):
    (tmp_path / 'payload').write_bytes(b'x' * 17)
    with pytest.raises(RuntimeError, match='大小限制'):
        module._portable_bytes(tmp_path, 'payload', limit=16)


def test_zero_limit_allows_only_empty_files_and_negative_limit_rejects(tmp_path):
    path = tmp_path / 'payload'
    path.write_bytes(b'')
    assert module._portable_bytes(tmp_path, 'payload', limit=0) == b''
    with pytest.raises(RuntimeError, match='大小限制'):
        module._portable_bytes(tmp_path, 'payload', limit=-1)
    path.write_bytes(b'x')
    with pytest.raises(RuntimeError, match='大小限制'):
        module._portable_bytes(tmp_path, 'payload', limit=0)


def test_payload_larger_than_one_block_is_returned_in_full(tmp_path):
    data = bytes(range(251)) * 400
    (tmp_path / 'payload').write_bytes(data)
    assert module._portable_bytes(tmp_path, 'payload', limit=len(data)) == data


def test_portable_read_retains_path_containment_check(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    (tmp_path / 'outside').write_bytes(b'outside')
    with pytest.raises(RuntimeError, match='路径越界'):
        module._portable_bytes(package, '../outside')


def test_small_payload_does_not_allocate_its_64_mib_allowance(tmp_path):
    (tmp_path / 'payload').write_bytes(b'x' * 1024)
    tracemalloc.start()
    try:
        assert module._portable_bytes(tmp_path, 'payload') == b'x' * 1024
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


class ShortRead(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []

    def read(self, size=-1):
        self.requests.append(size)
        return super().read(min(size, 3))


def test_short_reads_are_not_mistaken_for_eof_or_allowed_to_exceed_limit(monkeypatch):
    streams = []

    def opened(_path, mode):
        assert mode == 'rb'
        stream = ShortRead(b'complete payload')
        streams.append(stream)
        return stream

    monkeypatch.setattr(Path, 'open', opened)
    assert module._bounded_file_bytes(Path('owned'), limit=16) == b'complete payload'
    with pytest.raises(ValueError, match='exceeds limit'):
        module._bounded_file_bytes(Path('owned'), limit=15)
    assert all(0 < size <= 64 * 1024 for stream in streams for size in stream.requests)
    assert all(stream.closed for stream in streams)


class ChangedRead(BytesIO):
    def __init__(self, before, after):
        super().__init__(before)
        self.after = after
        self.changed = False

    def read(self, size=-1):
        data = super().read(min(size, 3))
        if not self.changed:
            position = self.tell()
            self.seek(0)
            self.write(self.after)
            self.truncate()
            self.seek(position)
            self.changed = True
        return data


def test_a_growing_stream_is_read_through_eof_not_the_original_size(monkeypatch):
    monkeypatch.setattr(Path, 'open', lambda *_: ChangedRead(b'old', b'old plus tail'))
    assert module._bounded_file_bytes(Path('owned'), limit=20) == b'old plus tail'
    with pytest.raises(ValueError, match='exceeds limit'):
        module._bounded_file_bytes(Path('owned'), limit=3)


@pytest.mark.parametrize('change', ['grow', 'truncate'])
def test_changed_stream_still_fails_the_portable_digest_gate(portable, monkeypatch, change):
    binary = portable['binary']
    original = binary.read_bytes()
    replacement = original + b'appended tail' if change == 'grow' else original[:3]
    opened = Path.open

    def changed(path, mode='r', *args, **kwargs):
        if path == binary and mode == 'rb':
            return ChangedRead(original, replacement)
        return opened(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', changed)
    with pytest.raises(RuntimeError, match='不一致'):
        module.native_hook_binary(portable['root'])


@pytest.mark.parametrize('capture', [False, True])
def test_development_validation_uses_small_reads_and_preserves_capture(portable, tmp_path, monkeypatch, capture):
    root = tmp_path / 'development'
    binary = root / 'runtime/native-pc/native-event-runtime' / module.DLL_NAME
    binary.parent.mkdir(parents=True)
    binary.write_bytes(portable['binary'].read_bytes())
    build_path = binary.with_suffix('.build.json')
    build_path.write_bytes(portable['build_path'].read_bytes())
    source_dir = root / 'native_pc/native_event_runtime'
    source_dir.mkdir(parents=True)
    for name in module.RUNTIME_SOURCES:
        (source_dir / name).write_bytes((portable['source_dir'] / name).read_bytes())
    observed = []
    original = module._bounded_file_bytes

    def read(path, **kwargs):
        observed.append(path)
        return original(path, **kwargs)

    captured = {} if capture else None
    monkeypatch.setattr(module, '_bounded_file_bytes', read)
    assert module.native_hook_binary(root, _read_capture=captured) == binary
    expected = {binary, build_path, *(source_dir / name for name in module.RUNTIME_SOURCES)}
    assert set(observed) == expected
    if capture:
        assert captured == {path.resolve(): path.read_bytes() for path in expected}


def test_default_payload_validator_also_uses_bounded_reads(portable, monkeypatch):
    original = module._bounded_file_bytes
    read_paths = []

    def read(path, **kwargs):
        read_paths.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(module, '_bounded_file_bytes', read)
    assert module.validate_native_hook_payload(portable['binary'], source_dir=portable['source_dir'],
        license_dir=portable['bundle'] / 'licenses') == portable['build_data']
    assert portable['binary'] in read_paths
    assert portable['build_path'] in read_paths
    assert len(read_paths) == 2 + len(module.RUNTIME_SOURCES) + len(module.NATIVE_HOOK_LICENSES)
