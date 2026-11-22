"""Stage the pinned official Windows x64 Gadget for a portable build.

This module performs no I/O at import. The caller supplies the final version
directory, normally ``collector/vendor/frida/17.26.1`` inside a fresh package.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
from pathlib import Path
import tempfile
import time
import urllib.request


VERSION = '17.26.1'
FILENAME = f'frida-gadget-{VERSION}-windows-x86_64.dll'
ARCHIVE_NAME = FILENAME + '.xz'
URL = f'https://github.com/frida/frida/releases/download/{VERSION}/{ARCHIVE_NAME}'
ARCHIVE_SHA256 = '9340980e7bb6a98ce6d9fd1554438593feedb0c1c01890dc1b72281ba6209e21'
DLL_SHA256 = 'e18e5869be6207fa79cdfcc05aa4e4fe3e304f1d8a28c6d7f1329276e84e5b67'
DLL_SIZE = 34576384
LICENSE_URL = f'https://github.com/frida/frida-core/blob/{VERSION}/COPYING'
MANIFEST_NAME = 'manifest.json'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
LZMA_MEMORY_LIMIT = 256 * 1024 * 1024
BLOCK_SIZE = 64 * 1024
DOWNLOAD_SECONDS = 120


def gadget_metadata():
    return dict(format=1, component='frida-gadget', version=VERSION,
                platform='windows-x86_64', filename=FILENAME, url=URL,
                compressed_sha256=ARCHIVE_SHA256, dll_sha256=DLL_SHA256,
                dll_size=DLL_SIZE, license_url=LICENSE_URL)


def _verify_file(path, expected_sha256, limit, *, expected_size=None):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular asset file: {path}')
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(BLOCK_SIZE):
            size += len(chunk)
            if size > limit:
                raise ValueError(f'Asset exceeds its size limit: {path}')
            digest.update(chunk)
    if (digest.hexdigest() != expected_sha256
            or expected_size is not None and size != expected_size):
        raise ValueError(f'Asset SHA-256 or size mismatch: {path}')


def _publish(temporary, destination, expected_sha256, limit, *, expected_size=None):
    # A same-directory hard link publishes the verified file atomically without
    # replacing a file that another build or the user created concurrently.
    try:
        os.link(temporary, destination)
    except FileExistsError:
        _verify_file(destination, expected_sha256, limit, expected_size=expected_size)


def _download_archive(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / ARCHIVE_NAME
    if destination.exists() or destination.is_symlink():
        _verify_file(destination, ARCHIVE_SHA256, MAX_ARCHIVE_BYTES)
        return destination
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_dir, prefix='.gadget-download-', delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            count = 0
            deadline = time.monotonic() + DOWNLOAD_SECONDS
            request = urllib.request.Request(URL, headers={'User-Agent': 'ArkDPS-portable-build'})
            with urllib.request.urlopen(request, timeout=30) as response:
                declared = response.headers.get('Content-Length')
                if declared is not None and int(declared) > MAX_ARCHIVE_BYTES:
                    raise ValueError('Gadget download exceeds its size limit')
                while chunk := response.read(BLOCK_SIZE):
                    count += len(chunk)
                    if count > MAX_ARCHIVE_BYTES:
                        raise ValueError('Gadget download exceeds its size limit')
                    if time.monotonic() > deadline:
                        raise TimeoutError('Gadget download exceeded its time limit')
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != ARCHIVE_SHA256:
                raise ValueError('Downloaded Gadget archive SHA-256 mismatch')
        _publish(temporary, destination, ARCHIVE_SHA256, MAX_ARCHIVE_BYTES)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-dir', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'vendor/frida' / VERSION)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--source-archive', type=Path)
    args = parser.parse_args()
    path = stage_gadget(args.target_dir, cache_dir=args.cache_dir, source_archive=args.source_archive)
    print(json.dumps({'path': str(path.resolve()), **gadget_metadata()}, ensure_ascii=False))


def _extract(archive, output):
    """Verify both compressed input and bounded decompressed output as read."""
    if archive.is_symlink() or not archive.is_file():
        raise ValueError(f'Expected a regular Gadget archive: {archive}')
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=LZMA_MEMORY_LIMIT)
    compressed = hashlib.sha256()
    decoded = hashlib.sha256()
    archive_size = dll_size = 0
    with archive.open('rb') as source:
        while chunk := source.read(BLOCK_SIZE):
            archive_size += len(chunk)
            if archive_size > MAX_ARCHIVE_BYTES:
                raise ValueError('Gadget archive exceeds its size limit')
            compressed.update(chunk)
            if decoder.eof:
                raise ValueError('Unexpected data after the Gadget XZ stream')
            data = chunk
            while True:
                block = decoder.decompress(data, max_length=BLOCK_SIZE)
                dll_size += len(block)
                if dll_size > DLL_SIZE:
                    raise ValueError('Expanded Gadget exceeds its pinned size')
                decoded.update(block)
                output.write(block)
                if decoder.eof or decoder.needs_input:
                    break
                data = b''
            if decoder.unused_data:
                raise ValueError('Unexpected data after the Gadget XZ stream')
    if (not decoder.eof or archive_size == 0 or compressed.hexdigest() != ARCHIVE_SHA256
            or dll_size != DLL_SIZE or decoded.hexdigest() != DLL_SHA256):
        raise ValueError('Gadget archive or DLL integrity mismatch')


def _manifest(path, metadata):
    encoded = (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
            raise ValueError(f'Unexpected Gadget manifest: {path}')
        if json.loads(path.read_text(encoding='utf-8')) != metadata:
            raise ValueError(f'Existing Gadget manifest differs: {path}')
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.gadget-manifest-', delete=False) as output:
            temporary = Path(output.name)
            output.write(encoded)
        _publish(temporary, path, hashlib.sha256(encoded).hexdigest(), len(encoded))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stage_gadget(target_dir, *, cache_dir=None, source_archive=None):
    """Return the verified DLL in a final package directory, preserving existing files.

    A supplied source archive is verified locally without downloading. Otherwise
    the build cache is used, with a bounded download from the pinned public URL.
    Unknown destination/cache files and mismatched manifests raise ``ValueError``.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / FILENAME
    metadata = gadget_metadata()
    manifest = target_dir / MANIFEST_NAME
    if manifest.exists() or manifest.is_symlink():
        _manifest(manifest, metadata)
    if target.exists() or target.is_symlink():
        _verify_file(target, DLL_SHA256, DLL_SIZE, expected_size=DLL_SIZE)
        _manifest(manifest, metadata)
        return target
    if source_archive is not None:
        archive = Path(source_archive)
    else:
        cache = (Path(cache_dir) if cache_dir is not None else
                 Path(__file__).resolve().parents[1] / 'runtime' / 'build-cache' / 'frida' / VERSION)
        archive = _download_archive(cache)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target_dir, prefix='.gadget-dll-', delete=False) as output:
            temporary = Path(output.name)
            _extract(archive, output)
        _publish(temporary, target, DLL_SHA256, DLL_SIZE, expected_size=DLL_SIZE)
        _manifest(manifest, metadata)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
