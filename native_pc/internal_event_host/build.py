"""Build the local event host with a pinned nlohmann/json header and GNU C++."""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from shutil import which
from subprocess import CalledProcessError, run
from tempfile import TemporaryDirectory
from urllib.request import urlopen


SOURCE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SOURCE.parents[1] / 'runtime/native-pc/internal-event-host/ArkDPS.NativeEventHost.exe'
DEPENDENCY = 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/'
HEADER = ('single_include/nlohmann/json.hpp',
          '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6')
LICENSE = ('LICENSE.MIT',
           '86b998c792894ccb911a1cb7994f7a9652894e7a094c0b5e45be2f553f45cf14')


def dependency(cache, artifact, *, offline):
    relative, digest = artifact
    path = cache / Path(relative).name
    cached = path.is_file()
    if cached:
        contents = path.read_bytes()
    elif offline:
        raise ValueError(f'Missing cached nlohmann/json artifact: {path}')
    else:
        with urlopen(DEPENDENCY + relative, timeout=30) as response:
            contents = response.read(2 * 1024 * 1024 + 1)
    if sha256(contents).hexdigest() != digest:
        raise ValueError(f'nlohmann/json artifact digest mismatch: {path}')
    if not cached:
        cache.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='verified-', dir=cache) as temp:
            verified = Path(temp) / path.name
            verified.write_bytes(contents)
            verified.replace(path)
    return contents


def _require_windows_x64(binary):
    with binary.open('rb') as stream:
        dos = stream.read(64)
        if len(dos) == 64 and dos[:2] == b'MZ':
            stream.seek(int.from_bytes(dos[60:64], 'little'))
            pe = stream.read(26)
            # The COFF machine and Optional Header must say AMD64 and PE32+.
            if (len(pe) == 26 and pe[:4] == b'PE\0\0' and pe[4:6] == b'\x64\x86'
                    and pe[24:26] == b'\x0b\x02'):
                return
    raise ValueError('native event host compiler must produce a Windows x64 executable')


def build(output, *, compiler='g++', cache=None, offline=False):
    compiler_path = which(compiler)
    if compiler_path is None:
        raise ValueError(f'C++17 compiler not found: {compiler}')
    output = Path(output).resolve()
    cache = Path(cache).resolve() if cache else output.parent / 'nlohmann-json-3.11.3'
    header = dependency(cache, HEADER, offline=offline)
    license_text = dependency(cache, LICENSE, offline=offline)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='native-event-host-', dir=output.parent) as temp:
        stage = Path(temp)
        binary = stage / output.name
        (stage / 'json.hpp').write_bytes(header)
        (stage / 'LICENSE.MIT').write_bytes(license_text)
        run([compiler_path, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Wpedantic', '-Werror',
             '-static', '-I', str(stage), str(SOURCE / 'main.cpp'), '-o', str(binary)], check=True)
        _require_windows_x64(binary)
        (stage / 'LICENSE.MIT').replace(output.parent / 'nlohmann-json-LICENSE.MIT')
        binary.replace(output)
    return output


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--compiler', default='g++')
    parser.add_argument('--cache', type=Path, help='Directory containing json.hpp and LICENSE.MIT')
    parser.add_argument('--offline', action='store_true', help='Require already-cached build dependencies')
    args = parser.parse_args()
    try:
        print(build(args.output, compiler=args.compiler, cache=args.cache, offline=args.offline))
    except (OSError, ValueError, CalledProcessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
