"""Source-checkout launcher for the C#/WPF UI (no Tk or device access)."""
from pathlib import Path
import ctypes
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / 'desktop' / 'ArkDPS.Desktop' / 'ArkDPS.Desktop.csproj'
EXECUTABLE = PROJECT.parent / 'bin' / 'Release' / 'net10.0-windows' / 'ArkDPS.Desktop.exe'


def worker_python(executable):
    path = Path(executable)
    return str(path.with_name('python.exe') if path.name.lower() == 'pythonw.exe' else path)


def launch(args=None):
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    log = runtime / 'wpf-build.log'
    try:
        sources = [p for p in PROJECT.parent.iterdir() if p.suffix in ('.cs', '.xaml', '.csproj', '.manifest')]
        if not EXECUTABLE.exists() or any(p.stat().st_mtime > EXECUTABLE.stat().st_mtime for p in sources):
            dotnet = os.environ.get('ARKDPS_DOTNET') or shutil.which('dotnet')
            if not dotnet:
                raise RuntimeError('首次从源码启动需要 .NET 10 SDK；安装后重新运行 start.cmd。')
            with log.open('w', encoding='utf-8') as output:
                result = subprocess.run([dotnet, 'build', str(PROJECT), '-c', 'Release', '--nologo'],
                    cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(f'WPF 构建失败（退出码 {result.returncode}）；详情见 runtime/wpf-build.log。')
        command = [str(EXECUTABLE), '--repo', str(ROOT), '--python', worker_python(sys.executable)]
        if args is not None:
            for name in ('adb', 'serial'):
                value = getattr(args, name, None)
                if value:
                    command += ['--' + name, value]
            endpoint = getattr(args, 'frida_remote', None)
            if endpoint == '':
                command.append('--no-events')
            elif endpoint:
                command += ['--frida-remote', endpoint]
        # Keep the launcher foreground-owned; WPF owns the independent Python
        # process, keeps it alive when hidden, and awaits cleanup on explicit Quit.
        return subprocess.run(command, cwd=ROOT).returncode
    except (OSError, RuntimeError) as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'ArkDPS 启动失败', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(launch())
