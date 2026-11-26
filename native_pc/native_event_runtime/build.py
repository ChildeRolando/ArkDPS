"""Build the standalone Windows hook runtime from verified upstream sources."""

from argparse import ArgumentParser
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
from shutil import which
from subprocess import CalledProcessError, run
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlopen


SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
DEFAULT_OUTPUT = ROOT / 'runtime/native-pc/native-event-runtime/ArkDPS.NativeEvents.dll'
ARCHIVES = (
    ('quickjs-2025-09-13.tar.xz', 'https://bellard.org/quickjs/quickjs-2025-09-13.tar.xz',
     '6f1f322aea3bb3a90858db85c9fe717013fde4df7dfcafe2f57e78f5bb4b4a0c'),
    ('minhook-1.3.4.tar.gz', 'https://codeload.github.com/TsudaKageyu/minhook/tar.gz/refs/tags/v1.3.4',
     '1aebeae4ca898330c507860acc2fca2eb335fe446a3a2b8444c3bf8b2660a14e'),
)
JSON_FILES = (
    ('json.hpp', 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp',
     '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'),
    ('nlohmann-json-LICENSE.MIT', 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/LICENSE.MIT',
     '86b998c792894ccb911a1cb7994f7a9652894e7a094c0b5e45be2f553f45cf14'),
)
MAX_DOWNLOAD = 16 * 1024 * 1024


def dependency(cache, artifact, *, offline=False):
    """Verify cached bytes too; extracted trees are never trusted as cache."""
    name, url, digest = artifact
    path = Path(cache) / name
    if path.is_file():
        if path.stat().st_size > MAX_DOWNLOAD:
            raise ValueError(f'oversize dependency: {name}')
        data = path.read_bytes()
    elif offline:
        raise ValueError(f'missing cached dependency: {name}')
    else:
        with urlopen(url, timeout=60) as response:
            data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD or sha256(data).hexdigest() != digest:
        raise ValueError(f'dependency SHA-256 mismatch: {name}')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='verified-', dir=path.parent) as temporary:
            staged = Path(temporary) / name
            staged.write_bytes(data)
            staged.replace(path)
    return data


def extract_archive(data, destination):
    """Extract only bounded regular files/directories, never links or traversal."""
    destination = Path(destination).resolve()
    total = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or '\\' in member.name
                    or ':' in member.name or member.name in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe dependency archive member')
            seen.add(member.name)
            total += member.size
            if total > 64 * 1024 * 1024 or member.size > MAX_DOWNLOAD:
                raise ValueError('dependency archive exceeds extraction limit')
            path = destination.joinpath(*name.parts)
            if not path.resolve().is_relative_to(destination):
                raise ValueError('dependency archive escaped extraction directory')
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    contents = stream.read(member.size + 1)
                if len(contents) != member.size:
                    raise ValueError('truncated dependency archive')
                path.write_bytes(contents)


def require_windows_x64(binary):
    with Path(binary).open('rb') as stream:
        header = stream.read(64)
        if len(header) == 64 and header[:2] == b'MZ':
            stream.seek(int.from_bytes(header[60:64], 'little'))
            pe = stream.read(26)
            if (len(pe) == 26 and pe[:6] == b'PE\0\0\x64\x86'
                    and pe[24:26] == b'\x0b\x02'
                    and int.from_bytes(pe[22:24], 'little') & 0x2000):
                return
    raise ValueError('compiler did not produce a Windows x64 DLL')


def build(output=DEFAULT_OUTPUT, *, compiler='g++', c_compiler=None, cache=None, offline=False):
    cpp = which(compiler)
    if cpp is None:
        raise ValueError(f'C++ compiler not found: {compiler}')
    cc = which(c_compiler) if c_compiler else str(Path(cpp).with_name('gcc.exe' if os.name == 'nt' else 'gcc'))
    if not cc or not Path(cc).is_file():
        raise ValueError('matching C compiler not found')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(cache).resolve() if cache else output.parent / 'dependencies'
    artifacts = {item[0]: dependency(cache, item, offline=offline) for item in ARCHIVES + JSON_FILES}
    env = dict(os.environ)
    env['PATH'] = str(Path(cpp).parent) + os.pathsep + env.get('PATH', '')
    with TemporaryDirectory(prefix='native-runtime-', dir=output.parent) as temporary:
        stage = Path(temporary)
        source_files = ('engine.cpp', 'engine.h', 'engine_worker.h', 'engine_capture_lane.h',
                        'engine_capture_plan.h', 'capture_queue.h', 'native_capture.cpp', 'native_capture.h',
                        'server.cpp', 'hooks.S', 'bridge.js', 'publication.js', 'build.py')
        snapshots = {name: (SOURCE / name).read_bytes() for name in source_files}
        for name, contents in snapshots.items():
            (stage / name).write_bytes(contents)
        for artifact in ARCHIVES:
            extract_archive(artifacts[artifact[0]], stage)
        quickjs = stage / 'quickjs-2025-09-13'
        minhook = stage / 'minhook-1.3.4'
        # Windows is case-insensitive: QuickJS's VERSION shadows C++ <version>
        # if its source root is exposed as an angle-bracket include directory.
        public_headers = stage / 'quickjs-headers'
        public_headers.mkdir()
        for header in quickjs.glob('*.h'):
            (public_headers / header.name).write_bytes(header.read_bytes())
        (stage / 'json.hpp').write_bytes(artifacts['json.hpp'])
        bridge = snapshots['bridge.js'].decode('utf-8')
        if ')ARKDPS_BRIDGE"' in bridge:
            raise ValueError('bridge source conflicts with embedding delimiter')
        (stage / 'bridge_source.h').write_text(
            'static constexpr const char* arkdps_bridge_source = R"ARKDPS_BRIDGE('
            + bridge + ')ARKDPS_BRIDGE";\n', encoding='utf-8')
        publication = snapshots['publication.js'].decode('utf-8')
        if ')ARKDPS_PUB"' in publication:
            raise ValueError('publication source conflicts with embedding delimiter')
        (stage / 'publication_source.h').write_text(
            'static constexpr const char* arkdps_publication_source = R"ARKDPS_PUB('
            + publication + ')ARKDPS_PUB";\n', encoding='utf-8')
        objects = []
        cflags = ['-O2', '-fwrapv', '-D_GNU_SOURCE', '-D__USE_MINGW_ANSI_STDIO',
                  '-DCONFIG_VERSION="2025-09-13"', '-I', str(quickjs), '-I', str(minhook / 'include')]
        c_sources = [quickjs / (name + '.c') for name in ('quickjs', 'dtoa', 'libregexp', 'libunicode', 'cutils')]
        c_sources += [minhook / 'src' / name for name in ('buffer.c', 'hook.c', 'trampoline.c', 'hde/hde64.c')]
        for index, source in enumerate(c_sources + [stage / 'hooks.S']):
            obj = stage / f'object-{index}.o'
            run([cc, *cflags, '-c', str(source), '-o', str(obj)], env=env, check=True)
            objects.append(str(obj))
        binary = stage / output.name
        run([cpp, '-std=c++17', '-O2', '-Wall', '-Wextra', '-shared', '-static',
             '-I', str(stage), '-isystem', str(public_headers), '-I', str(minhook / 'include'),
             str(stage / 'engine.cpp'), str(stage / 'native_capture.cpp'), str(stage / 'server.cpp'), *objects,
             '-lpsapi', '-ladvapi32', '-lwinpthread', '-lm', '-o', str(binary)], env=env, check=True)
        require_windows_x64(binary)
        licenses = {
            'quickjs-LICENSE': (quickjs / 'LICENSE').read_bytes(),
            'minhook-LICENSE.txt': (minhook / 'LICENSE.txt').read_bytes(),
            'nlohmann-json-LICENSE.MIT': artifacts['nlohmann-json-LICENSE.MIT'],
        }
        manifest = dict(protocol=1, provider='arkdps-native-hooks', sha256=sha256(binary.read_bytes()).hexdigest(),
                        dependencies=[dict(file=name, url=url, sha256=digest) for name, url, digest in ARCHIVES + JSON_FILES],
                        licenses={name: sha256(data).hexdigest() for name, data in licenses.items()},
                        sources={name: sha256(contents).hexdigest() for name, contents in snapshots.items()})
        for name, data in licenses.items():
            (output.parent / name).write_bytes(data)
        output.with_suffix('.build.json').write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        binary.replace(output)
    return output


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--compiler', default='g++')
    parser.add_argument('--c-compiler')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    try:
        print(build(args.output, compiler=args.compiler, c_compiler=args.c_compiler, cache=args.cache, offline=args.offline))
    except (OSError, ValueError, CalledProcessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
