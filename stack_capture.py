"""Bounded WPR recording for the desktop settings button. EOF also requests a save."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from uuid import uuid4


DURATION = 45
PROFILE = Path(__file__).with_suffix('.wprp')


def wpr(*arguments, timeout=30):
    executable = Path(os.environ['SystemRoot']) / 'System32' / 'wpr.exe'
    result = subprocess.run([str(executable), *map(str, arguments)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
    if result.returncode:
        detail = result.stdout.decode('mbcs', errors='replace').replace('\x00', '').strip()
        code = result.returncode & 0xffffffff
        if code in (0xC5585011, 0x80070005):
            detail = 'Windows 未授予系统性能采样权限。请以管理员身份启动 ArkDPS。'
        raise RuntimeError(f'{detail} (0x{code:08X})')


def capture(output, stop, *, run=wpr, notify=lambda state: None):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    instance = ('-instancename', 'ArkDPS-' + uuid4().hex)
    # Each invocation owns exactly one named session, including its failure cleanup.
    started = False
    try:
        run('-start', str(PROFILE) + '!ArkDPS', *instance)
        started = True
        notify('ready')
        stop.wait(DURATION)
        notify('saving')
        run('-stop', output, *instance, timeout=150)
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError('Windows 未生成采样文件。')
    except Exception as error:
        # A start timeout may also leave a running session. A rejected start did not acquire it.
        if started or isinstance(error, subprocess.TimeoutExpired):
            try:
                run('-cancel', *instance)
            except Exception as cleanup:
                raise RuntimeError(f'{error}\n清理采样会话时 Windows 返回：{cleanup}') from error
        raise


if __name__ == '__main__':
    stop = Event()

    def wait_for_desktop():
        try:
            os.read(sys.stdin.fileno(), 1)
        finally:
            stop.set()

    def notify(state):
        try:
            print(state, flush=True)
        except OSError:
            # The desktop can exit while WPR is starting. Save on its EOF.
            stop.set()

    Thread(target=wait_for_desktop, daemon=True).start()
    try:
        capture(Path(sys.argv[1]), stop, notify=notify)
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
