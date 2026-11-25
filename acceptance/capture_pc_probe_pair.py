"""Foreground same-battle native/Frida comparison; never controls the game UI.

Start before restarting the client. Wait for BOTH_READY before entering a
no-cost practice battle. Two independent collection sessions write separate
journals and histories. Hook coexistence itself remains part of acceptance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import parser_for
from capture_cli import run_capture
from acceptance.source_prefix_capture import PrefixAuditSession
from native_pc.native_hook_source import NativeHookEventProbe, native_hook_binary


ROOT = Path(__file__).resolve().parents[1]
_RETAINED_PREPARATIONS = []


def _prepared_native_hooks(game_root, *, root, runtime_dir):
    from native_pc.native_hook_source import PreparedNativeHooks
    return PreparedNativeHooks(game_root, root=root, runtime_dir=runtime_dir)


def candidate_identity():
    def git(*arguments):
        return subprocess.check_output(['git', *arguments], cwd=ROOT)
    files = git('ls-files', '-z', '--modified', '--others', '--exclude-standard').decode('utf-8').split('\0')
    return dict(head=git('rev-parse', 'HEAD').decode().strip(),
                branch=git('branch', '--show-current').decode().strip(),
                tracked_diff_sha256=sha256(git('diff', '--binary', 'HEAD')).hexdigest(),
                working_files={name: sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in files if name and (ROOT / name).is_file()})


def capture_arguments(game_root, output, backend, *, seconds, wait_seconds, stop_file, stage):
    args = parser_for().parse_args([
        '--platform', 'native_pc', '--native-game-root', str(game_root),
        '--native-pc-event-backend', backend, '--capture-seconds', str(seconds),
        '--wait-battle-seconds', str(wait_seconds), '--baseline-timeout', str(wait_seconds),
        '--output-dir', str(output), '--stop-file', str(stop_file),
        '--require-events', '--require-progress',
    ] + (['--stage', stage] if stage else []))
    args.collect_native_actions = True
    return args


def _wait_for_game_exit(process_instance):
    """Fail closed and keep the Frida client alive until this exact game exits."""
    from native_pc.probe_native_pc import ApiFailure, WindowsApi
    pid, created = process_instance
    api = WindowsApi()
    while True:
        handle = None
        try:
            handle = api.open_process(pid, 0x1000)
            if api.process_times(handle)[0] != created or api.exit_code(handle) != 259:
                return
        except ApiFailure as exc:
            if exc.code == 87:  # The original process no longer exists.
                return
        except KeyboardInterrupt:
            print('保护等待仍在继续：请先从游戏界面正常退出PC，不要结束采集器。', flush=True)
        except Exception:
            pass  # A failed query is not proof of exit.
        finally:
            if handle is not None:
                try:
                    api.close(handle)
                except Exception:
                    pass
        try:
            time.sleep(.5)
        except KeyboardInterrupt:
            print('保护等待仍在继续：请先正常退出游戏，不要结束采集器。', flush=True)


class _CloseOrder:
    """Native wraps Frida: release the outer patch before the inner patch."""

    def __init__(self, stop_file, *, on_unsafe_wait):
        self.stop_file = stop_file
        self.on_unsafe_wait = on_unsafe_wait
        self.native_done = Event()
        self.sampling_ended = {'native': Event(), 'frida': Event()}
        self.native_observers = []
        self.sessions = {}
        self.native_safe = True
        self.game_exit_confirmed = False
        self.unsafe_wait_required = False
        self.process_instance = None

    def before(self, label, session):
        self.sessions[label] = session
        self.sampling_ended[label].set()
        self.stop_file.touch(exist_ok=True)
        if label == 'frida':
            # Remain inside run_capture's open journal scope, retaining the
            # observer, client and startup owner. Returning/raising here could
            # let interpreter shutdown detach Frida underneath an active patch.
            self.await_native_release()
        return True

    def await_native_release(self):
        self.native_done.wait()
        if not self.native_safe and not self.game_exit_confirmed:
            self.unsafe_wait_required = True
            print('FATAL_KEEPALIVE 原生 Hook 卸载未获确认；Frida 保持加载。'
                  '请从游戏界面正常退出PC；本采集器将保护等待，不能先关闭。', flush=True)
            self.on_unsafe_wait(self.process_instance)
            self.game_exit_confirmed = True

    def wrap_frida_observer(self, observer):
        # CollectionSession can retire a failed observer on a new generation,
        # before the capture-finally gate. Guard that stop path as well.
        stop = observer.stop
        def guarded_stop():
            self.stop_file.touch(exist_ok=True)
            self.await_native_release()
            return stop()
        observer.stop = guarded_stop
        return observer

    def after(self, label, session, succeeded):
        if label == 'native':
            self.native_safe = succeeded and all(
                getattr(observer, 'hook_disable_confirmed', False) is True
                for observer in self.native_observers)
            self.native_done.set()

    def task_ended(self, label):
        # A failure before run_capture enters its try/finally has no session
        # callback. With no created observer there cannot be a native patch.
        self.sampling_ended[label].set()
        if label == 'native' and not self.native_done.is_set():
            self.native_safe = not self.native_observers
            self.native_done.set()

    def native_not_scheduled(self):
        if not self.native_observers:
            self.native_safe = True
            self.native_done.set()


def _wait_for_cleanup(workers, executor):
    # Ctrl+C requests STOP but must not terminate the Frida client while the
    # outer native patch remains unconfirmed. The worker emits the exit prompt.
    while not all(task.done() for _, task in workers):
        try:
            time.sleep(.1)
        except KeyboardInterrupt:
            print('正在按逆序释放 Hook；如出现保护提示，请先正常退出游戏。', flush=True)
    while True:
        try:
            executor.shutdown(wait=True)
            return
        except KeyboardInterrupt:
            continue


def run_pair(args, *, runtime_factory=None, capture=run_capture, on_unsafe_wait=None):
    # Resolve/verify the experimental DLL before touching a live process.
    binary = native_hook_binary(ROOT)
    if not args.allow_dual_hooks:
        raise ValueError('同局对账会同时安装两套 Hook；请显式传入 --allow-dual-hooks。')
    bootstrap_dir = getattr(args, 'bootstrap_dir', None)
    preload_native = bool(getattr(args, 'preload_native', False))
    if preload_native and bootstrap_dir is not None:
        raise ValueError('--preload-native 不能与 --bootstrap-dir 同时使用；启动期对照必须使用新游戏实例。')
    if bootstrap_dir is not None:
        bootstrap_dir = Path(bootstrap_dir).resolve()
        if not (bootstrap_dir / 'native-pc-frida/lease.json').is_file():
            raise ValueError('--bootstrap-dir 必须是含 native-pc-frida/lease.json 的旧运行时根目录。')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stop_file = output / 'STOP'
    identity = candidate_identity()
    (output / 'candidate.json').write_text(json.dumps(dict(identity,
        native_dll_sha256=sha256(binary.read_bytes()).hexdigest(),
        bootstrap_reused=bootstrap_dir is not None,
        bootstrap_directory=str(bootstrap_dir) if bootstrap_dir is not None else None,
        native_preload_requested=preload_native, native_preloaded=False,
        native_preload_mechanism='ordinary_loader' if preload_native else None,
        native_preload_requires_frida_initialization=False,
        game_ui_controlled=False, real_client_acceptance='pending'),
        ensure_ascii=False, indent=2), encoding='utf-8')
    if runtime_factory is None:
        from native_pc.frida_runtime import NativePcFridaRuntime
        runtime_factory = NativePcFridaRuntime
    prepared = (_prepared_native_hooks(args.game_root, root=ROOT, runtime_dir=output / 'native-preload')
                if preload_native else None)
    runtime_options = dict(runtime_dir=bootstrap_dir or output / 'frida-runtime')
    if prepared is not None:
        def prepare_selected_process(instance):
            # Bind the provider-neutral ordinary loader to the parent's actual
            # cancellation and mutation admission gate before it opens a target.
            prepared.cancelled = lambda: stop_file.exists() or runtime._cancel.is_set()
            prepared.commit = runtime._commit
            if prepared.cancelled():
                raise InterruptedError('native preload cancelled before process preparation')
            result = prepared.prepare(instance)
            if prepared.cancelled():
                raise InterruptedError('native preload cancelled after process preparation')
            return result
        runtime_options['prepare_process'] = prepare_selected_process
    runtime = runtime_factory(args.game_root, **runtime_options)
    close_order = _CloseOrder(stop_file, on_unsafe_wait=on_unsafe_wait or _wait_for_game_exit)
    results, failure = {}, None
    workers = []
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ArkDPS pair')
    try:
        runtime.start()
        if bootstrap_dir is None:
            print('WAITING_GAME 请正常重启PC；两套采集器均就绪前不要开始演习。', flush=True)
        else:
            print('RECONNECTING_GAME 正在核对旧运行时与当前游戏进程实例；无需再次重启，两套就绪前不要开始演习。', flush=True)
        lease = runtime.wait_ready(args.wait_seconds, cancelled=stop_file.exists)
        if (not isinstance(lease, dict) or type(lease.get('pid')) is not int or lease['pid'] <= 0
                or type(lease.get('created')) is not int or lease['created'] <= 0):
            raise RuntimeError('Frida startup did not provide a verified process instance')
        close_order.process_instance = (lease['pid'], lease['created'])

        def frida_factory(collector, remote):
            return close_order.wrap_frida_observer(runtime.observer_factory(collector, remote))

        def native_factory(collector, remote):
            options = dict(runtime_dir=output / 'native-runtime')
            if prepared is not None:
                options['prepared'] = prepared
            observer = NativeHookEventProbe(collector, remote, **options)
            close_order.native_observers.append(observer)
            # Retain the independently checked process birth before collector.close.
            close_order.process_instance = tuple(collector.process.process_instance)
            return observer

        def collect(label, options, factory):
            try:
                return capture(options, native_pc_observer_factory=factory,
                    session_factory=PrefixAuditSession, baseline_poll_seconds=.01,
                    state_dir=output / f'{label}-state',
                    native_pc_bootstrap=runtime,
                    before_session_close=lambda session: close_order.before(label, session),
                    after_session_close=lambda session, success: close_order.after(label, session, success))
            finally:
                close_order.task_ended(label)

        for label, backend, factory in (
            ('frida', 'frida', frida_factory),
            ('native', 'native', native_factory),
        ):
            options = capture_arguments(args.game_root, output / label, backend,
                seconds=args.seconds, wait_seconds=args.wait_seconds, stop_file=stop_file, stage=args.stage)
            task = executor.submit(collect, label, options, factory)
            workers.append((label, task))
            # Serialize installation: Frida first, then native around it. Do
            # not patch the same target concurrently during initialization.
            deadline = time.monotonic() + args.wait_seconds
            while not (output / label / 'READY.json').exists():
                if task.done():
                    task.result()
                    raise RuntimeError(f'{label} capture ended before readiness')
                if close_order.sampling_ended[label].is_set():
                    raise RuntimeError(f'{label} capture entered cleanup before readiness')
                if time.monotonic() >= deadline or stop_file.exists():
                    raise TimeoutError(f'{label} readiness timed out or was cancelled')
                time.sleep(.1)
        print('BOTH_READY 两套探针已就绪。请手动完成选定关卡；结束后先停结算，按提示返回大厅以保留真实销毁封口。', flush=True)
        while not all(task.done() for _, task in workers):
            for label, task in workers:
                if task.done() and task.exception() is not None:
                    raise RuntimeError(f'{label} capture failed: {task.exception()}')
            time.sleep(.2)
        for label, task in workers:
            results[label] = task.result()
    except BaseException as exc:
        failure = f'{type(exc).__name__}: {exc}'
    finally:
        stop_file.touch(exist_ok=True)
        if not any(label == 'native' for label, _ in workers):
            close_order.native_not_scheduled()
        # Each capture owns and closes its own observer before the shared
        # Frida startup owner can release authentication/startup resources.
        _wait_for_cleanup(workers, executor)
        for label, task in workers:
            if label not in results:
                try:
                    results[label] = task.result()
                except BaseException as exc:
                    results[label] = {'error': f'{type(exc).__name__}: {exc}'}
        preparation_diagnostics = None
        preparation_cleanup_errors = None
        native_preloaded = False
        if prepared is not None:
            for _ in range(2):
                try:
                    prepared.close()
                except Exception as exc:
                    preparation_cleanup_errors = str(exc)
                else:
                    preparation_cleanup_errors = None
                    break
            if preparation_cleanup_errors is not None:
                _RETAINED_PREPARATIONS.append(prepared)
                failure = failure or f'native preparation cleanup pending: {preparation_cleanup_errors}'
            diagnostic = getattr(prepared, 'diagnostics', None)
            if callable(diagnostic):
                try:
                    preparation_diagnostics = diagnostic()
                except Exception:
                    preparation_diagnostics = {'status': 'diagnostics_unavailable'}
            try:
                native_preloaded = runtime.describe().get('prepared_process_instance') is not None
            except Exception:
                # Selecting a birth, retaining a loader, or merely having a
                # preparation object is not evidence that ordinary load finished.
                native_preloaded = isinstance(preparation_diagnostics, dict) and preparation_diagnostics.get('prepared') is True
        if close_order.native_safe or close_order.game_exit_confirmed:
            try:
                runtime.close()
            except Exception as exc:
                failure = failure or f'cleanup: {exc}'
        else:
            # Normally the Frida worker performs this protected wait itself.
            # Cover a caller-supplied capture that failed before invoking its gate.
            close_order.unsafe_wait_required = True
            print('FATAL_KEEPALIVE 请先正常退出PC，采集器保留 Frida 所有权。', flush=True)
            close_order.on_unsafe_wait(close_order.process_instance)
            close_order.game_exit_confirmed = True
            runtime.close()
        if close_order.unsafe_wait_required:
            failure = failure or 'native hook disable was not confirmed; retained Frida until the game exited'
        receipt = dict(status='failed' if failure else 'captured', error=failure,
            game_ui_controlled=False, live_parity_verified=False,
            bootstrap_reused=bootstrap_dir is not None,
            bootstrap_directory=str(bootstrap_dir) if bootstrap_dir is not None else None,
            native_preload_requested=preload_native,
            native_preloaded=native_preloaded,
            native_preload_mechanism='ordinary_loader' if prepared is not None else None,
            native_preload_requires_frida_initialization=False,
            native_preload_diagnostics=preparation_diagnostics,
            native_preload_cleanup_error=preparation_cleanup_errors,
            native_hook_disable_confirmed=close_order.native_safe,
            protected_game_exit_wait=close_order.unsafe_wait_required,
            game_exit_confirmed=close_order.game_exit_confirmed,
            source_types={'native': 'native-hooks', 'frida': 'frida'}, candidate=identity,
            captures={label: dict(error=result.get('error'), samples=result.get('samples'),
                                  process_instance=result.get('process_instance'))
                      for label, result in results.items()})
        (output / 'pair-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    if failure:
        raise RuntimeError(failure)
    print('CAPTURED 请运行 compare_pc_live_probes 对两份独立日志逐笔对账；captured 不是验收通过。', flush=True)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True, help='new, nonexistent evidence directory')
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--wait-seconds', type=float, default=300)
    parser.add_argument('--stage')
    parser.add_argument('--bootstrap-dir', type=Path,
                        help='Reuse a closed owner\'s runtime root containing native-pc-frida/lease.json; keep new capture output separate')
    parser.add_argument('--preload-native', action='store_true',
                        help='Ordinarily load the native component in the selected new process before Gadget; authenticate hooks only after VM readiness')
    parser.add_argument('--allow-dual-hooks', action='store_true')
    args = parser.parse_args(argv)
    if args.preload_native and args.bootstrap_dir is not None:
        parser.error('--preload-native and --bootstrap-dir are mutually exclusive')
    if not (0 < args.seconds <= 3600 and 0 < args.wait_seconds <= 600):
        parser.error('seconds must be 0..3600 and wait-seconds 0..600, excluding zero')
    try:
        run_pair(args)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
