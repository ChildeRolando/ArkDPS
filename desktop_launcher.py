"""Source-checkout launcher for the C#/WPF UI (no Tk or device access)."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import shutil
import subprocess
import sys

from log_context import LogContext

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / 'desktop' / 'ArkDPS.Desktop' / 'ArkDPS.Desktop.csproj'
EXECUTABLE = PROJECT.parent / 'bin' / 'Release' / 'net10.0-windows' / 'ArkDPS.Desktop.exe'


def worker_python(executable):
    path = Path(executable)
    return str(path.with_name('python.exe') if path.name.lower() == 'pythonw.exe' else path)


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
               ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
               ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
               ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
               ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
               ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
               ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
               ('hProcess', wintypes.HANDLE)]


def _elevated_launch(command, directory):
    """The EXE manifest requires elevation; keep ownership of the launched app."""
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    request = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo),
        fMask=0x40 | 0x100,  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        lpVerb='runas', lpFile=str(command[0]),
        lpParameters=subprocess.list2cmdline([str(value) for value in command[1:]]),
        lpDirectory=str(directory), nShow=1)
    if not shell.ShellExecuteExW(ctypes.byref(request)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise RuntimeError('已取消管理员权限请求，ArkDPS 尚未启动。')
        raise ctypes.WinError(error)
    if not request.hProcess:
        raise RuntimeError('Windows 未返回 ArkDPS 进程句柄，无法确认启动结果。')
    try:
        if kernel.WaitForSingleObject(request.hProcess, 0xffffffff) != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(request.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel.CloseHandle(request.hProcess)


def run_desktop(command, directory):
    try:
        return subprocess.run(command, cwd=directory).returncode
    except OSError as error:
        if getattr(error, 'winerror', None) != 740:
            raise
        return _elevated_launch(command, directory)


def launch(args=None):
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    log = runtime / 'wpf-build.log'
    try:
        from native_pc.internal_entry import portable_manifest
        packaged = portable_manifest(ROOT) is not None
        sources = [] if packaged else [
            p for p in PROJECT.parent.iterdir() if p.suffix in ('.cs', '.xaml', '.csproj', '.manifest')]
        if not packaged and (not EXECUTABLE.exists()
                             or any(p.stat().st_mtime > EXECUTABLE.stat().st_mtime for p in sources)):
            dotnet = os.environ.get('ARKDPS_DOTNET') or shutil.which('dotnet')
            if not dotnet:
                raise RuntimeError('首次从源码启动需要 .NET 10 SDK；安装后重新运行 start.cmd。')
            with log.open('w', encoding='utf-8') as output:
                overrides = {key: value for key, arg in (('platform', 'platform'), ('adb_path', 'adb'),
                    ('serial', 'serial'), ('native_game_root', 'native_game_root'))
                    if (value := getattr(args, arg, None)) is not None}
                if overrides.get('serial'):
                    overrides['auto_detect'] = False
                if overrides.get('native_game_root'):
                    overrides.setdefault('platform', 'native_pc')
                endpoint = getattr(args, 'frida_remote', None)
                log_context = LogContext(runtime, root=ROOT, connection_override=overrides or None,
                    layout_override={'event_observation': bool(endpoint)} if endpoint is not None else None)
                header = log_context.header('wpf_build')
                output.write(json.dumps(header, ensure_ascii=False) + '\n')
                output.flush()
                result = subprocess.run([dotnet, 'build', str(PROJECT), '-c', 'Release', '--nologo'],
                    cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(f'WPF 构建失败（退出码 {result.returncode}）；详情见 runtime/wpf-build.log。')
        command = ([str(ROOT.parent / 'ArkDPS.Desktop.exe')] if packaged else
                   [str(EXECUTABLE), '--repo', str(ROOT), '--python', worker_python(sys.executable)])
        if args is not None:
            for name in ('adb', 'serial', 'platform', 'native_game_root', 'native_pc_event_backend',
                         'native_pc_event_feed'):
                value = getattr(args, name, None)
                if value:
                    command += ['--' + name.replace('_', '-'), value]
            framework_root = getattr(args, 'arkframework_root', None)
            if framework_root:
                command += ['--arkframework-root', str(Path(framework_root).absolute())]
            endpoint = getattr(args, 'frida_remote', None)
            if endpoint == '':
                command.append('--no-events')
            elif endpoint:
                command += ['--frida-remote', endpoint]
        # Keep the launcher foreground-owned; WPF owns the independent Python
        # process, keeps it alive when hidden, and awaits cleanup on explicit Quit.
        return run_desktop(command, ROOT)
    except (OSError, RuntimeError) as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'ArkDPS 启动失败', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(launch())
