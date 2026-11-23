"""Own an early, process-bound native DLL preparation without a Frida runtime.

Preparation is not IL2CPP readiness or event coverage. CollectionSession still
performs its guarded VM checks before the observer authenticates and runs JS.
"""

import ctypes
from enum import Enum
import math
from pathlib import Path
from threading import Condition, Event, Thread
import time

from native_pc import probe_native_pc as access
from event_observer import ObserverStatus
from native_pc.native_hook_source import NativeHookEventProbe, PreparedNativeHooks
from native_pc.windows_dll_loader import RuntimeFailure


FILETIME_EPOCH = 116444736000000000


class RuntimeState(str, Enum):
    STOPPED = 'stopped'
    STARTING = 'starting'
    WAITING_PROCESS = 'waiting_for_process'
    LOADING = 'loading'
    READY = 'ready'
    PROCESS_FAILED = 'process_failed'
    FAILED = 'failed'
    STOPPING = 'stopping'
    CLEANUP_PENDING = 'cleanup_pending'
    CLOSED = 'closed'


class NativeHookRuntime:
    """One early-load owner; never resubmit a preparation to the same birth."""

    poll_seconds = .025
    pending_states = frozenset(('starting', 'waiting_for_process', 'loading'))
    failure_states = frozenset(('failed', 'process_failed', 'cleanup_pending'))

    def __init__(self, game_root, *, runtime_dir, collector_root=None, api=None, admin_check=None,
                 clock=None, prepared_factory=None, build_check=None, startup_trace=None):
        self.game_root = Path(game_root).expanduser().resolve()
        self.runtime_dir = Path(runtime_dir).resolve() / 'native-hook-sessions'
        self.collector_root = Path(collector_root).resolve() if collector_root else Path(__file__).resolve().parents[1]
        self.api = api
        self.admin_check = admin_check or (lambda: bool(ctypes.windll.shell32.IsUserAnAdmin()))
        self.clock = clock or time
        self.prepared_factory = prepared_factory or PreparedNativeHooks
        self.build_check = build_check or access.verify_build
        self.startup_trace = startup_trace
        self.status, self.code, self.error = RuntimeState.STOPPED, None, None
        self._condition, self._cancel = Condition(), Event()
        self._thread = self._prepared = self._lease = self._armed = None
        self._attempted, self._observers = set(), set()
        self._retry_requested = True
        self._build_signature = None
        self._ready_generation = 0
        self._last_preparation = None

    @property
    def is_pending(self):
        with self._condition:
            return not self._cancel.is_set() and self.status.value in self.pending_states

    @property
    def is_failed(self):
        with self._condition:
            return self.status.value in self.failure_states

    def _state(self, status, code=None, error=None):
        with self._condition:
            if self._cancel.is_set() and status not in (RuntimeState.STOPPING, RuntimeState.CLEANUP_PENDING, RuntimeState.CLOSED):
                raise InterruptedError('自研 PC 启动已取消')
            self.status, self.code, self.error = status, code, error
            self._condition.notify_all()

    def describe(self):
        with self._condition:
            prepared = self._prepared
            result = dict(status=self.status.value, code=self.code, error=self.error,
                lease=dict(self._lease) if self._lease else None, armed_filetime=self._armed,
                ready_generation=self._ready_generation, active_observers=len(self._observers),
                expected_resident=self.status == RuntimeState.READY, cancelled=self._cancel.is_set(),
                thread_alive=self._thread is not None and self._thread.is_alive(),
                provider='arkdps-native-hooks', frida_required=False,
                recovery='cleanup' if self.status == RuntimeState.CLEANUP_PENDING else
                         'next_process' if self.status == RuntimeState.PROCESS_FAILED else
                         'explicit_retry' if self.status == RuntimeState.FAILED else 'automatic')
        # Prepared.prepare holds its own lock while entering our commit gate.
        # Never acquire that lock while holding this runtime's condition.
        result['preparation'] = (prepared.diagnostics() if prepared is not None and result['status'] != 'loading'
                                 else self._last_preparation)
        loader = (result['preparation'] or {}).get('loader') or {}
        result['pending_loader'] = (result['status'] == 'loading'
                                   or bool(loader.get('submitted') and not loader.get('verification_complete')))
        return result

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise InterruptedError('自研 PC 启动已取消')

    def _files(self):
        result = []
        for relative in access.KNOWN_FILES:
            try:
                item = (self.game_root / relative).stat()
                result.append((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns))
            except FileNotFoundError:
                result.append(None)
        return tuple(result)

    def _preflight(self):
        self._check_cancelled()
        if not self.admin_check():
            raise RuntimeFailure('native_permission_denied', '请以管理员权限启动 ArkDPS；不会自动提权')
        before = self._files()
        build, _ = self.build_check(self.game_root)
        if (build.get('known_build') is not True or build.get('amd64') is not True
                or build.get('source_build') != access.BUILD_ID):
            raise RuntimeFailure('native_unsupported_build', '自研实时采集只接受指纹匹配的官服 Windows x64 构建')
        if self._files() != before:
            raise RuntimeFailure('native_build_changed', 'PC 客户端文件正在变化，请完成更新后重试')
        self._build_signature = before
        self.api = self.api or access.WindowsApi()
        self._check_cancelled()

    def _instances(self):
        matches = []
        for pid in self.api.named_processes():
            self._check_cancelled()
            handle = None
            try:
                handle = self.api.open_process(pid, access.PROCESS_QUERY_LIMITED_INFORMATION)
                if (self.api.exit_code(handle) == access.STILL_ACTIVE
                        and access.same_path(self.api.image_path(handle), self.game_root / 'Arknights.exe')):
                    created = self.api.process_times(handle)[0]
                    if type(pid) is not int or type(created) is not int or pid <= 0 or created <= 0:
                        raise RuntimeFailure('process_identity_invalid', 'PC 进程实例无效')
                    matches.append((pid, created))
            except access.ApiFailure as error:
                if error.code == 87:  # Process disappeared between enumeration and query.
                    continue
                if error.code in (5, 299):
                    raise RuntimeFailure('process_query_pending', '尚不能核验全部同名 PC 进程，暂不加载') from error
                raise
            finally:
                if handle is not None:
                    self.api.close(handle)
        return matches

    def _alive(self, record):
        if (not isinstance(record, dict) or any(type(record.get(key)) is not int or record[key] <= 0
                                              for key in ('pid', 'created'))):
            return False
        handle = None
        try:
            handle = self.api.open_process(record['pid'], access.PROCESS_QUERY_LIMITED_INFORMATION)
            return (self.api.process_times(handle)[0] == record['created']
                    and self.api.exit_code(handle) == access.STILL_ACTIVE
                    and access.same_path(self.api.image_path(handle), self.game_root / 'Arknights.exe'))
        except access.ApiFailure as error:
            if error.code == 87:
                return False
            raise
        finally:
            if handle is not None:
                self.api.close(handle)

    def _commit(self, operation):
        with self._condition:
            self._check_cancelled()
            if (self._lease is None or not self._alive(self._lease)
                    or self._instances() != [(self._lease['pid'], self._lease['created'])]):
                raise RuntimeFailure('process_changed', '原生组件提交前进程实例、路径或唯一性已变化')
            if self._files() != self._build_signature:
                raise RuntimeFailure('native_build_changed', '原生组件提交前客户端文件已变化')
            return operation()

    def start(self):
        with self._condition:
            self._check_cancelled()
            if self.status == RuntimeState.CLOSED:
                raise RuntimeError('自研 PC 启动所有者已关闭')
            if self._thread is not None:
                if self._thread.is_alive():
                    return self
                raise RuntimeError('启动监视器已结束，请显式重试')
            self._armed = self.clock.time_ns() // 100 + FILETIME_EPOCH
            self._state(RuntimeState.STARTING)
            self._thread = Thread(target=self._run, name='ArkDPS native PC startup', daemon=False)
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._state(RuntimeState.FAILED, 'watcher_start_failed', '自研 PC 监视器未能启动')
                raise
        return self

    def retry(self):
        with self._condition:
            self._check_cancelled()
            if self.status in (RuntimeState.STOPPING, RuntimeState.CLOSED):
                raise RuntimeError('自研 PC 启动正在关闭')
            self._retry_requested = True
            self._condition.notify_all()
            return not self._observers

    def cancel(self):
        with self._condition:
            if self.status == RuntimeState.CLOSED:
                return
            self._cancel.set()
            self._state(RuntimeState.STOPPING)

    def wait_ready(self, timeout, cancelled=None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('readiness timeout must be finite and nonnegative')
        deadline = self.clock.monotonic() + timeout
        with self._condition:
            while True:
                self._check_cancelled()
                if cancelled is not None and cancelled():
                    raise InterruptedError('自研 PC 启动等待已取消')
                if self.status == RuntimeState.READY and self._lease is not None:
                    if not self._alive(self._lease):
                        raise RuntimeFailure('process_changed', '已准备的 PC 进程实例已退出或变化')
                    return dict(self._lease)
                if self.is_failed:
                    raise RuntimeFailure(self.code or 'startup_failed', self.error or '自研 PC 启动失败')
                remaining = deadline - self.clock.monotonic()
                if remaining <= 0:
                    raise TimeoutError('请保持 ArkDPS 开启，再正常启动新的官服 PC 游戏实例')
                self._condition.wait(min(.1, remaining))

    def _discard_unclaimed(self):
        with self._condition:
            if self._observers:
                raise RuntimeFailure('cleanup_pending', '采集观察者仍持有原生组件资源')
            prepared = self._prepared
        if prepared is not None:
            prepared.close()
            with self._condition:
                if self._prepared is prepared:
                    self._prepared = None

    def _prepare(self, birth):
        with self._condition:
            self._check_cancelled()
            self._attempted.add(birth)
            self._lease = dict(pid=birth[0], created=birth[1], source_build=access.BUILD_ID)
            candidate = self.prepared_factory(self.game_root, root=self.collector_root, runtime_dir=self.runtime_dir)
            self._prepared = candidate  # Publish ownership before any mutation-capable call.
            candidate.cancelled, candidate.commit = self._cancel.is_set, self._commit
            self._state(RuntimeState.LOADING)
        try:
            candidate.prepare(birth)
            self._last_preparation = candidate.diagnostics()
            with self._condition:
                self._check_cancelled()
                if not self._alive(self._lease):
                    raise RuntimeFailure('process_changed', 'PC 进程在原生组件准备期间变化')
                self._ready_generation += 1
                self._state(RuntimeState.READY)
        except BaseException:
            self._last_preparation = candidate.diagnostics()
            try:
                self._discard_unclaimed()
            except Exception:
                if not self._cancel.is_set():
                    self._state(RuntimeState.CLEANUP_PENDING, 'cleanup_pending', '原生预加载器尚未清理，保留所有权')
            raise

    def _step(self):
        self._check_cancelled()
        with self._condition:
            retry, observed, state = self._retry_requested, bool(self._observers), self.status
        if state == RuntimeState.CLEANUP_PENDING:
            if not retry or observed:
                return
            self._discard_unclaimed()
            self._state(RuntimeState.PROCESS_FAILED if self._lease else RuntimeState.WAITING_PROCESS,
                        'restart_required' if self._lease else None)
        if retry and not observed:
            self._preflight()
            with self._condition:
                self._retry_requested = False
            if self._lease is None:
                self._state(RuntimeState.WAITING_PROCESS)
        elif state == RuntimeState.FAILED:
            return
        record = self._lease
        if record is not None:
            if self._alive(record):
                prepared = self._prepared
                expires = getattr(prepared, 'expires_at', None)
                if (self.status == RuntimeState.READY and not self._observers and expires is not None
                        and self.clock.monotonic() >= expires):
                    self._discard_unclaimed()
                    self._state(RuntimeState.PROCESS_FAILED, 'preparation_expired', '原生准备已过期，请正常重启游戏')
                return
            if self._observers:
                self._state(RuntimeState.PROCESS_FAILED, 'observer_cleanup_pending', '旧 PC 观察者尚待采集会话释放')
                return
            self._discard_unclaimed()
            with self._condition:
                self._lease = None
        matches = self._instances()
        if len(matches) > 1:
            self._state(RuntimeState.WAITING_PROCESS, 'ambiguous_process', '请保留一个相同路径的官服 PC 实例')
        elif matches and matches[0][1] > self._armed and matches[0] not in self._attempted:
            self._prepare(matches[0])
        else:
            self._state(RuntimeState.WAITING_PROCESS, 'restart_required' if matches else None,
                        '请保持 ArkDPS 开启，再正常重启官服 PC 游戏' if matches else None)

    def _run(self):
        while not self._cancel.is_set():
            try:
                self._step()
            except Exception as error:
                if self._cancel.is_set():
                    break
                code = getattr(error, 'code', 'startup_failed')
                if self.status == RuntimeState.CLEANUP_PENDING:
                    pass
                elif code == 'process_query_pending':
                    self._state(RuntimeState.WAITING_PROCESS, code, str(error))
                else:
                    self._state(RuntimeState.PROCESS_FAILED if self._lease else RuntimeState.FAILED, code, str(error))
            self._cancel.wait(self.poll_seconds)

    def observer_factory(self, collector, remote=None):
        with self._condition:
            self._check_cancelled()
            if self.status != RuntimeState.READY or self._prepared is None or self._observers:
                raise RuntimeFailure('native_not_ready', '原生准备尚未就绪或观察者仍拥有该输入')
            observer = _RuntimeNativeHookProbe(collector, remote, runtime=self, prepared=self._prepared,
                                              root=self.collector_root, runtime_dir=self.runtime_dir)
            self._observers.add(observer)
            return observer

    def close(self):
        if self.status == RuntimeState.CLOSED:
            return
        self.cancel()
        try:
            if self._thread is not None:
                self._thread.join(timeout=32)
                if self._thread.is_alive():
                    raise RuntimeFailure('cleanup_pending', '原生预加载监视器仍在清理')
            self._discard_unclaimed()
        except Exception as error:
            self._state(RuntimeState.CLEANUP_PENDING, 'cleanup_pending', str(error))
            raise
        self._state(RuntimeState.CLOSED)


class _RuntimeNativeHookProbe(NativeHookEventProbe):
    """The collection session, not startup, owns an adopted observer."""

    def __init__(self, collector, remote, *, runtime, **options):
        super().__init__(collector, remote, **options)
        self.runtime = runtime

    def start(self):
        with self.runtime._condition:
            self.runtime._check_cancelled()
            if (self not in self.runtime._observers or self.runtime.status != RuntimeState.READY
                    or not self.runtime._alive(self.runtime._lease)
                    or self.runtime._instances() != [(self.runtime._lease['pid'], self.runtime._lease['created'])]):
                raise RuntimeFailure('process_changed', '观察者准入时原生准备已失效')
        try:
            super().start()
            self.runtime._check_cancelled()
        except BaseException:
            try:
                self.stop()
            except Exception:
                pass  # Keep ownership when cleanup or the quiet fence failed.
            raise

    def stop(self):
        super().stop()
        with self.runtime._condition:
            if not self.hook_disable_confirmed and not self.runtime._alive(self.runtime._lease):
                self.hook_disable_confirmed = True  # Verified process exit destroys every resident hook.
            if self.status != ObserverStatus.STOPPED or self.cleanup_errors or not self.hook_disable_confirmed:
                raise RuntimeFailure('cleanup_pending', '原生观察者尚未确认 Hook 停用排空，保留所有权')
            self.runtime._observers.discard(self)
            if not self.runtime._cancel.is_set() and getattr(self.prepared, 'claimed', False):
                self.runtime._state(RuntimeState.PROCESS_FAILED, 'restart_required', '此进程的原生输入已关闭，请正常重启游戏')
            self.runtime._condition.notify_all()
