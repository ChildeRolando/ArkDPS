import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'arkdps_frida_gadget_asset_tests', Path(__file__).resolve().parents[1] / 'tools/frida_gadget_asset.py')
asset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asset)


@pytest.fixture
def fixture_asset(monkeypatch, tmp_path):
    dll = b'MZ\0ArkDPS owned synthetic Gadget asset\0' * 8
    archive = lzma.compress(dll, format=lzma.FORMAT_XZ)
    monkeypatch.setattr(asset, 'DLL_SIZE', len(dll))
    monkeypatch.setattr(asset, 'DLL_SHA256', hashlib.sha256(dll).hexdigest())
    monkeypatch.setattr(asset, 'ARCHIVE_SHA256', hashlib.sha256(archive).hexdigest())
    source = tmp_path / 'source.xz'
    source.write_bytes(archive)
    return dll, archive, source


def no_network(*args, **kwargs):
    raise AssertionError('Unexpected network request')


def test_pins_official_asset_and_has_no_import_io(monkeypatch):
    assert asset.VERSION == '17.26.1'
    assert asset.FILENAME == 'frida-gadget-17.26.1-windows-x86_64.dll'
    assert asset.ARCHIVE_SHA256 == '9340980e7bb6a98ce6d9fd1554438593feedb0c1c01890dc1b72281ba6209e21'
    assert asset.DLL_SHA256 == 'e18e5869be6207fa79cdfcc05aa4e4fe3e304f1d8a28c6d7f1329276e84e5b67'
    monkeypatch.setattr(asset.urllib.request, 'urlopen', no_network)
    monkeypatch.setattr(Path, 'mkdir', no_network)
    spec = importlib.util.spec_from_file_location('gadget_asset_no_io', asset.__file__)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    assert loaded.gadget_metadata()['license_url'].endswith('/17.26.1/COPYING')


def test_local_archive_stages_canonical_dll_manifest_and_reuses_valid_output(
        fixture_asset, tmp_path, monkeypatch):
    dll, _, source = fixture_asset
    monkeypatch.setattr(asset.urllib.request, 'urlopen', no_network)
    target_dir = tmp_path / 'collector/vendor/frida/17.26.1'
    target = asset.stage_gadget(target_dir, source_archive=source)
    assert target == target_dir / asset.FILENAME
    assert target.read_bytes() == dll
    metadata = json.loads((target_dir / asset.MANIFEST_NAME).read_text(encoding='utf-8'))
    assert metadata == asset.gadget_metadata()
    assert metadata['url'].endswith(asset.ARCHIVE_NAME)
    previous = target.stat().st_mtime_ns
    assert asset.stage_gadget(target_dir) == target
    assert target.stat().st_mtime_ns == previous
    assert not list(target_dir.glob('.gadget-*'))


def test_download_is_bounded_public_verified_and_cached(fixture_asset, tmp_path, monkeypatch):
    dll, archive, _ = fixture_asset
    requests = []

    class Response(io.BytesIO):
        headers = {'Content-Length': str(len(archive))}

    def open_asset(request, *, timeout):
        requests.append((request, timeout))
        return Response(archive)

    monkeypatch.setattr(asset.urllib.request, 'urlopen', open_asset)
    cache = tmp_path / 'build-cache'
    target = asset.stage_gadget(tmp_path / 'first', cache_dir=cache)
    assert target.read_bytes() == dll
    request, timeout = requests[0]
    assert request.full_url == asset.URL and timeout == 30
    assert request.get_header('Authorization') is None
    assert (cache / asset.ARCHIVE_NAME).read_bytes() == archive
    monkeypatch.setattr(asset.urllib.request, 'urlopen', no_network)
    assert asset.stage_gadget(tmp_path / 'second', cache_dir=cache).read_bytes() == dll
    assert len(requests) == 1
    assert not list(cache.glob('.gadget-*'))


@pytest.mark.parametrize('location', ['target', 'cache'])
def test_unknown_existing_files_are_preserved(fixture_asset, tmp_path, monkeypatch, location):
    _, _, source = fixture_asset
    monkeypatch.setattr(asset.urllib.request, 'urlopen', no_network)
    directory = tmp_path / location
    directory.mkdir()
    existing = directory / (asset.FILENAME if location == 'target' else asset.ARCHIVE_NAME)
    existing.write_bytes(b'unknown earlier user file')
    with pytest.raises(ValueError, match='mismatch'):
        asset.stage_gadget(directory if location == 'target' else tmp_path / 'output',
                           cache_dir=directory, source_archive=source if location == 'target' else None)
    assert existing.read_bytes() == b'unknown earlier user file'
    assert not (directory / asset.MANIFEST_NAME).exists()


def test_unknown_manifest_is_rejected_before_staging(fixture_asset, tmp_path, monkeypatch):
    _, _, source = fixture_asset
    monkeypatch.setattr(asset.urllib.request, 'urlopen', no_network)
    target = tmp_path / 'target'
    target.mkdir()
    manifest = target / asset.MANIFEST_NAME
    manifest.write_text('{"component":"someone else"}', encoding='utf-8')
    with pytest.raises(ValueError, match='manifest differs'):
        asset.stage_gadget(target, source_archive=source)
    assert manifest.read_text(encoding='utf-8') == '{"component":"someone else"}'
    assert not (target / asset.FILENAME).exists()


@pytest.mark.parametrize('failure', ['compressed_hash', 'dll_hash', 'expanded_size', 'truncated'])
def test_invalid_local_inputs_publish_no_dll(fixture_asset, tmp_path, monkeypatch, failure):
    _, archive, source = fixture_asset
    if failure == 'compressed_hash':
        monkeypatch.setattr(asset, 'ARCHIVE_SHA256', '0' * 64)
    elif failure == 'dll_hash':
        monkeypatch.setattr(asset, 'DLL_SHA256', '0' * 64)
    elif failure == 'expanded_size':
        monkeypatch.setattr(asset, 'DLL_SIZE', 10)
    else:
        source.write_bytes(archive[:-8])
        monkeypatch.setattr(asset, 'ARCHIVE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    target = tmp_path / 'target'
    with pytest.raises((ValueError, lzma.LZMAError)):
        asset.stage_gadget(target, source_archive=source)
    assert not list(target.iterdir())


@pytest.mark.parametrize('failure', ['declared_size', 'stream_size', 'hash', 'connection', 'deadline'])
def test_failed_download_preserves_empty_cache_for_retry(fixture_asset, tmp_path, monkeypatch, failure):
    _, archive, _ = fixture_asset

    class Response(io.BytesIO):
        headers = {'Content-Length': str(asset.MAX_ARCHIVE_BYTES + 1)} if failure == 'declared_size' else {}

    def open_asset(*args, **kwargs):
        if failure == 'connection':
            raise OSError('connection reset')
        return Response(b'wrong archive' if failure == 'hash' else archive)

    if failure == 'stream_size':
        monkeypatch.setattr(asset, 'MAX_ARCHIVE_BYTES', 8)
    if failure == 'deadline':
        clock = iter((0, asset.DOWNLOAD_SECONDS + 1))
        monkeypatch.setattr(asset.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(asset.urllib.request, 'urlopen', open_asset)
    cache = tmp_path / 'cache'
    with pytest.raises((ValueError, OSError, TimeoutError)):
        asset.stage_gadget(tmp_path / 'target', cache_dir=cache)
    assert not list(cache.iterdir())
    assert not list((tmp_path / 'target').iterdir())


@pytest.mark.parametrize('valid', [False, True])
def test_concurrent_destination_is_never_overwritten(fixture_asset, tmp_path, monkeypatch, valid):
    dll, _, source = fixture_asset
    original = asset.os.link

    def competing_writer(temporary, destination):
        if destination.name == asset.FILENAME:
            destination.write_bytes(dll if valid else b'concurrent unknown file')
            raise FileExistsError(destination)
        original(temporary, destination)

    monkeypatch.setattr(asset.os, 'link', competing_writer)
    directory = tmp_path / 'target'
    if valid:
        assert asset.stage_gadget(directory, source_archive=source).read_bytes() == dll
        assert (directory / asset.MANIFEST_NAME).exists()
    else:
        with pytest.raises(ValueError):
            asset.stage_gadget(directory, source_archive=source)
        assert (directory / asset.FILENAME).read_bytes() == b'concurrent unknown file'
        assert not (directory / asset.MANIFEST_NAME).exists()
    assert not list(directory.glob('.gadget-*'))
