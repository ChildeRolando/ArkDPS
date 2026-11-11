"""ArkDPS entry point: the WPF product UI, plus device-free headless capture.

The Tk panel is sealed (see ``archive/legacy-tk`` and
``docs/adr/0005-seal-legacy-tk.md``). There is exactly one product UI,
``desktop/ArkDPS.Desktop``; this module only routes command-line launches to it.
Removed legacy flags fail loudly through argparse instead of silently falling
back to a different interface.
"""
import argparse
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector import DEFAULT_ADB, DEFAULT_SERIAL, ROOT
from collection_session import DEFAULT_FRIDA_REMOTE

WPF_LAUNCH = 'python app.py（或 start.cmd）启动 WPF'
WPF_ACCEPTANCE = 'python acceptance/verify_wpf.py'
WPF_PACKAGE_ACCEPTANCE = 'python acceptance/verify_wpf_package.py <zip>'

REMOVED_FLAGS = {
    '--legacy-tk': ('Tk 面板已封存，产品 UI 只有 WPF；请直接运行 ' + WPF_LAUNCH
                    + '，UI 验收使用 ' + WPF_ACCEPTANCE + '。'),
    '--acceptance-output': ('旧 Tk GUI 测量入口已封存；UI 验收请运行 '
                            + WPF_ACCEPTANCE + '，打包验收请运行 ' + WPF_PACKAGE_ACCEPTANCE + '。'),
    '--self-test': ('旧 Tk 便携包自检已封存；UI 验收请运行 ' + WPF_ACCEPTANCE
                    + '，打包验收请运行 ' + WPF_PACKAGE_ACCEPTANCE + '。'),
    '--acceptance-seconds': '旧 Tk GUI 测量入口已封存；UI 验收请运行 ' + WPF_ACCEPTANCE + '。',
    '--acceptance-panels': '旧 Tk GUI 测量入口已封存；UI 验收请运行 ' + WPF_ACCEPTANCE + '。',
}


class RemovedFlag(argparse.Action):
    """Reject a sealed legacy flag before anything starts; never fall back."""

    def __init__(self, option_strings, dest, message='', **kwargs):
        kwargs.setdefault('nargs', 0)
        kwargs.setdefault('default', argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)
        self.message = message

    def __call__(self, parser, namespace, values, option_string=None):
        # Keep the diagnostic readable on UTF-8 and non-UTF-8 consoles; capsys
        # replaces sys.stderr with a text stream that has no usable buffer.
        text = ((option_string + ': ') if option_string else '') + self.message + chr(10)
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except UnicodeEncodeError:
            sys.stderr.buffer.write(text.encode('utf-8'))
            sys.stderr.buffer.flush()
        raise SystemExit(2)


def timestamp():
    return datetime.now(timezone(timedelta(hours=8)))


def parser_for():
    parser = argparse.ArgumentParser(
        description='ArkDPS：WPF 产品界面；无界面采集使用 --capture-seconds。')
    for flag, message in REMOVED_FLAGS.items():
        parser.add_argument(flag, action=RemovedFlag, message=message,
                            help='已移除（Tk 已封存）：使用时会明确报错并退出')
    parser.add_argument('--adb', default=None,
                        help='override the saved adb path for this launch')
    parser.add_argument('--serial', default=None,
                        help='override the saved instance address for this launch')
    parser.add_argument('--capture-seconds', type=float, default=0)
    events = parser.add_mutually_exclusive_group()
    events.add_argument('--frida-remote', default=None,
                        help='Frida 17.26.1 endpoint; default comes from the saved '
                             'event-observation setting (initially %s); no server is installed or started'
                             % DEFAULT_FRIDA_REMOTE)
    events.add_argument('--no-events', dest='frida_remote', action='store_const', const='',
                        help='disable event observation; retain native snapshots')
    parser.add_argument('--wait-battle-seconds', type=float, default=180)
    parser.add_argument('--baseline-timeout', type=float, default=90)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--expect-head')
    parser.add_argument('--maa-job', type=Path)
    parser.add_argument('--maa-log', type=Path)
    parser.add_argument('--stage')
    parser.add_argument('--require-progress', action='store_true')
    parser.add_argument('--require-events', action='store_true',
                        help='fail acceptance capture if event coverage is unavailable')
    return parser


def _utf8_streams():
    """Keep CLI diagnostics readable on non-Chinese Windows consoles and CI."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass


def main():
    _utf8_streams()
    parser = parser_for()
    args = parser.parse_args()
    if args.require_events and args.frida_remote == '':
        parser.error('--require-events cannot be combined with --no-events')
    if (args.capture_seconds < 0 or args.wait_battle_seconds < 0
            or args.baseline_timeout <= 0):
        parser.error('capture/wait durations must be nonnegative and baseline timeout positive')
    if not args.capture_seconds:
        # The WPF launcher owns the product UI, the Python worker and the
        # saved event-observation default; this entry never opens a window.
        from desktop_launcher import launch
        return launch(args)
    if args.frida_remote is None:
        # Headless capture has no settings window; keep the documented default.
        args.frida_remote = DEFAULT_FRIDA_REMOTE
    elif args.frida_remote == '':
        args.frida_remote = None
    if args.require_events and not args.frida_remote:
        parser.error('--require-events cannot be combined with --no-events')
    args.adb = args.adb or DEFAULT_ADB
    args.serial = args.serial or DEFAULT_SERIAL
    (ROOT / 'runtime').mkdir(exist_ok=True)
    # One owner for the device channel. A second launch cannot tear it down.
    instance_lock = socket.socket()
    try:
        instance_lock.bind(('127.0.0.1', 27380))
        instance_lock.listen(1)
    except OSError:
        instance_lock.close()
        raise SystemExit('统计窗已在运行，请先关闭后进行命令行采集')
    log = (ROOT / 'runtime' / f'app-{timestamp():%Y%m%d-%H%M%S}.log').open(
        'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    from capture_cli import run_capture
    run_capture(args)
    return None


if __name__ == '__main__':
    raise SystemExit(main())
