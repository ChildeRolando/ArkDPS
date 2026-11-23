"""ArkDPS entry point: the WPF product UI, plus device-free headless capture.

The Tk panel is sealed (see ``archive/legacy-tk`` and
``docs/adr/0005-seal-legacy-tk.md``). There is exactly one product UI,
``desktop/ArkDPS.Desktop``; this module only routes command-line launches to it.
Removed legacy flags fail loudly through argparse instead of silently falling
back to a different interface.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector import DEFAULT_ADB, DEFAULT_SERIAL, ROOT
from collection_session import DEFAULT_FRIDA_REMOTE
from connection import ConnectionConfigStore

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
    parser.add_argument('--platform', choices=('mumu', 'native_pc'),
                        help='select the game platform for this launch')
    parser.add_argument('--native-game-root',
                        help='set the directory containing the official PC client Arknights.exe')
    parser.add_argument('--arkframework-root', type=Path,
                        help='官服 PC 采集时从此 ArkFramework 目录单独记录 BetterBattle 的逐笔持续回复')
    parser.add_argument('--capture-seconds', type=float, default=0)
    parser.add_argument('--internal-capture', metavar='SCENARIO',
                        help='run a controlled PC event capture and print its receipt')
    parser.add_argument('--state-dir', type=Path,
                        help='separate state directory for --internal-capture')
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


def main(*, native_pc_observer_factory=None):
    _utf8_streams()
    parser = parser_for()
    args = parser.parse_args()
    from native_pc.internal_entry import portable_manifest
    manifest = portable_manifest(ROOT)
    if manifest and manifest.get('internal_event_backend') and args.internal_capture is None:
        parser.error('内部候选需要显式选择 --internal-capture，启动方式见随包说明。')
    if args.internal_capture is not None:
        if (any(value is not None for value in (args.adb, args.serial, args.platform,
                args.native_game_root, args.arkframework_root, args.frida_remote, args.output_dir,
                args.stop_file, args.expect_head, args.maa_job, args.maa_log, args.stage))
                or args.capture_seconds or args.require_progress or args.require_events):
            parser.error('--internal-capture cannot be combined with live capture options')
        from native_pc.internal_capture import run_internal_capture
        from native_pc.internal_entry import internal_host, internal_state_dir
        try:
            host = internal_host(ROOT)
            directory = internal_state_dir(ROOT, args.state_dir)
            result = run_internal_capture(host, args.internal_capture, directory)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False, default=str))
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0 if result.get('status') == 'ok' else 2
    if args.state_dir is not None:
        parser.error('--state-dir requires --internal-capture')
    if args.arkframework_root is not None:
        if args.platform == 'mumu' or args.frida_remote:
            parser.error('--arkframework-root 仅用于官服 PC')
        args.platform = 'native_pc'
        args.arkframework_root = args.arkframework_root.expanduser().resolve()
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
    saved = ConnectionConfigStore(ROOT / 'runtime' / 'connection.json').load().config
    args.platform = args.platform or ('native_pc' if args.native_game_root else saved.platform)
    capture_options = {}
    if args.platform == 'native_pc':
        args.native_game_root = args.native_game_root or saved.native_game_root
        if not args.native_game_root:
            parser.error('官服 PC 采集需要 --native-game-root，或已保存的游戏目录')
        if args.frida_remote:
            parser.error('--frida-remote 只支持 MuMu')
        if native_pc_observer_factory is not None and args.frida_remote is None:
            capture_options['native_pc_observer_factory'] = native_pc_observer_factory
        args.collect_native_actions = args.frida_remote is None
        if args.frida_remote == '':
            args.arkframework_root = None
        args.frida_remote = '' if args.frida_remote == '' else None
        args.adb, args.serial = None, None
    elif args.frida_remote is None:
        # Headless capture has no settings window; keep the documented default.
        args.frida_remote = DEFAULT_FRIDA_REMOTE
    elif args.frida_remote == '':
        args.frida_remote = None
    if args.require_events and args.platform == 'mumu' and not args.frida_remote:
        parser.error('--require-events cannot be combined with --no-events')
    if args.platform == 'mumu':
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
    from capture_cli import run_capture
    if args.platform == 'native_pc':
        try:
            run_capture(args, **capture_options)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        finally:
            instance_lock.close()
    else:
        from log_context import LogContext

        state_dir = ROOT / 'runtime'
        path = state_dir / f'app-{timestamp():%Y%m%d-%H%M%S-%f}.log'
        try:
            with path.open('x', encoding='utf-8', buffering=1) as log:
                context = LogContext(state_dir,
                    connection_override=dict(platform='mumu', adb_path=args.adb, serial=args.serial, auto_detect=False),
                    layout_override=dict(event_observation=bool(args.frida_remote)))
                log.write(json.dumps(context.header('capture_console', debug_mode=True), ensure_ascii=False) + '\n')
                with redirect_stdout(log), redirect_stderr(log):
                    run_capture(args)
        finally:
            instance_lock.close()
    return None


if __name__ == '__main__':
    raise SystemExit(main())
