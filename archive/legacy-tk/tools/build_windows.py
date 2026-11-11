"""Build a traceable portable candidate, never publish it. Run from a clean HEAD."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_head(expected=None):
    head = git('rev-parse', 'HEAD')
    if expected and head != expected:
        raise RuntimeError(f'candidate mismatch: expected {expected}, got {head}')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('release builds require a clean committed worktree')
    return head


def toolchain_versions():
    packages = {}
    for line in (ROOT / 'requirements-windows.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, expected = line.split('==')
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f'toolchain mismatch: {name} requires {expected}, got {actual}')
        packages[name] = actual
    return packages


def tracked_data():
    """Explicit tracked resources only: never captures, settings, caches or secrets."""
    paths = git('ls-files', 'vendor', 'assets', 'docs').splitlines()
    paths += ['event_probe_android2791.js', 'README.md', 'THIRD_PARTY.md',
              'LICENSE', 'CHANGELOG.md', 'release.json', 'requirements-windows.txt']
    resources = [(ROOT / path, Path(path).parent.as_posix()) for path in paths]
    # Frozen imported tools.* modules have __file__ at <bundle>/tools/,
    # not at their source pathex vendor/ark-source/tools/. Mirror only data
    # into that import layout; keep the original vendor subtree for provenance.
    prefix = 'vendor/ark-source/'
    for path in paths:
        if (path.startswith((prefix + 'tools/', prefix + 'game_data/'))
                and not path.endswith('.py')):
            resources.append((ROOT / path, Path(path[len(prefix):]).parent.as_posix()))
    return resources


def copy_runtime_licenses(destination):
    destination.mkdir()
    base = Path(sys.base_prefix)
    # The CPython Windows distribution includes its dependency notices here.
    for name, path in [('Python.txt', base / 'LICENSE.txt'),
                       ('Tcl.txt', ROOT / 'assets/licenses/Tcl.txt'),
                       ('Tk.txt', base / 'tcl/tk8.6/license.terms')]:
        if not path.is_file():
            raise RuntimeError(f'missing runtime license: {path}')
        shutil.copyfile(path, destination / name)
    for package in ('frida', 'typing_extensions', 'pyinstaller'):
        dist = importlib.metadata.distribution(package)
        licenses = [p for p in dist.files or [] if 'license' in p.name.lower()
                    or p.name.lower().startswith('copying')]
        if not licenses:
            raise RuntimeError(f'missing distribution license: {package}')
        for index, path in enumerate(licenses):
            shutil.copyfile(dist.locate_file(path), destination / f'{package}-{index}.txt')


def inventory(folder):
    return {p.relative_to(folder).as_posix(): sha256(p)
            for p in sorted(folder.rglob('*')) if p.is_file()}


def build(output, expected=None):
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('build on Windows x64 with CPython 3.10.10')
    if platform.python_version() != '3.10.10':
        raise RuntimeError('the candidate toolchain requires CPython 3.10.10')
    head = candidate_head(expected)
    config = json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))
    packages = toolchain_versions()
    if packages['frida'] != config['frida_version']:
        raise RuntimeError('event client version does not match release.json')
    # A new directory is a guard against publishing stale PyInstaller output.
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--windowed', '--noupx', '--contents-directory', '.',
               '--name', 'ArkDPS', '--icon', str(ROOT / 'assets/arkdps.ico'),
               '--paths', str(ROOT / 'vendor/ark-source'),
               '--hidden-import', 'frida', '--copy-metadata', 'frida',
               '--exclude-module', 'numpy', '--exclude-module', 'PIL',
               '--exclude-module', 'pytest',
               '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
               '--specpath', str(output)]
    # Preserve the layout used by __file__-relative data and runtime paths.
    # --contents-directory . is intentional; a default _internal layout would
    # move persisted history/settings inside a replaceable support directory.
    for source, destination in tracked_data():
        command += ['--add-data', f'{source};{destination}']
    command.append(str(ROOT / 'app.py'))
    with (output / 'build.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    folder = output / 'dist/ArkDPS'
    copy_runtime_licenses(folder / 'licenses')
    candidate_head(head)  # Reject changes made while PyInstaller was running.
    manifest = dict(schema_version=1, head=head, release=config,
                    python=platform.python_version(), build_platform=platform.platform(),
                    packages=packages, files=inventory(folder))
    (folder / 'build-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    archive = output / f"ArkDPS-{config['version']}-windows-x64-{head[:12]}.zip"
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                bundle.write(path, 'ArkDPS/' + path.relative_to(folder).as_posix())
    receipt = dict(head=head, artifact=archive.name, sha256=sha256(archive),
                   bytes=archive.stat().st_size, build_manifest=sha256(folder / 'build-manifest.json'),
                   public_release_authorized=False)
    (output / 'artifact.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--expect-head')
    args = parser.parse_args()
    build(args.output, args.expect_head)
