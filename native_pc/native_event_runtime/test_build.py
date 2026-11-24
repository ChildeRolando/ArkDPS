"""Build-input checks do not map a DLL or access a game process."""

from hashlib import sha256
import io
import tarfile

import pytest

from native_pc.native_event_runtime.build import dependency, extract_archive, require_windows_x64


def test_dependency_verifies_cached_bytes(tmp_path):
    path = tmp_path / 'owned.txt'
    path.write_bytes(b'owned-build-fixture')
    artifact = (path.name, 'https://example.invalid/not-contacted', sha256(path.read_bytes()).hexdigest())
    assert dependency(tmp_path, artifact, offline=True) == path.read_bytes()
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        dependency(tmp_path, artifact, offline=True)
    assert path.read_bytes() == b'changed'


def test_offline_dependency_never_downloads(tmp_path):
    with pytest.raises(ValueError, match='missing cached dependency'):
        dependency(tmp_path, ('missing', 'https://example.invalid/not-contacted', '0' * 64), offline=True)


def archive(*entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as stream:
        for name, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == 'file':
                info.size = 3
                stream.addfile(info, io.BytesIO(b'own'))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = 'outside'
                stream.addfile(info)
    return output.getvalue()


def test_archive_extracts_only_regular_owned_files(tmp_path):
    extract_archive(archive(('owned/file.txt', 'file')), tmp_path)
    assert (tmp_path / 'owned/file.txt').read_bytes() == b'own'


@pytest.mark.parametrize('name,kind', [('../outside', 'file'), ('/absolute', 'file'),
                                        ('C:/outside', 'file'), ('owned\\outside', 'file'), ('link', 'symlink')])
def test_archive_rejects_unsafe_members(tmp_path, name, kind):
    with pytest.raises(ValueError, match='unsafe dependency archive member'):
        extract_archive(archive((name, kind)), tmp_path)
    assert not list(tmp_path.iterdir())


def test_archive_rejects_duplicate_paths(tmp_path):
    with pytest.raises(ValueError, match='unsafe dependency archive member'):
        extract_archive(archive(('same', 'file'), ('same', 'file')), tmp_path)


def test_compiler_output_must_be_x64_dll(tmp_path):
    binary = tmp_path / 'owned.dll'
    binary.write_bytes(b'not-a-PE-file')
    with pytest.raises(ValueError, match='Windows x64 DLL'):
        require_windows_x64(binary)
