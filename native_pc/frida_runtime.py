"""Own native PC Frida startup and authenticated, process-bound reconnects.

The verified Gadget is loaded through the ordinary Windows loader once per
new process. Its DLL reference lives until normal game exit. Observers retain
the shared MuMu event protocol and own their scripts and client sessions.
"""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
from threading import Condition, Event, Thread
import time
from types import SimpleNamespace

from event_observer import NativePcFridaObserver, ObserverStatus
from . import probe_native_pc as access


FRIDA_VERSION = '17.26.1'
GADGET_NAME = 'frida-gadget-17.26.1-windows-x86_64.dll'
GADGET_SHA256 = 'e18e5869be6207fa79cdfcc05aa4e4fe3e304f1d8a28c6d7f1329276e84e5b67'
SOURCE_BUILD = 'win-x64:' + access.KNOWN_FILES['GameAssembly.dll']
DEFAULT_ASSET = Path(__file__).resolve().parents[1] / 'vendor' / 'frida' / FRIDA_VERSION / GADGET_NAME
ADDRESS = '127.0.0.1'
PORT = 27382
POLL_SECONDS = .025
FILETIME_EPOCH = 116444736000000000
LOADER_PREPARATION_SECONDS = 10.0
LOADER_VERIFICATION_SECONDS = 1.0
LOADER_READ_RETRY_SECONDS = .01


class RuntimeFailure(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.frida-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _transient_module_read(error):
    return ((isinstance(error, OSError) and getattr(error, 'winerror', None) in (24, 299))
            or isinstance(error, RuntimeFailure) and error.code == 'loader_not_ready')


def _bounded_module_read(read, *, cancelled, alive, timeout, timeout_code, label, on_error=None):
    """Retry read-only initialization races without reissuing a Windows load."""
    deadline = time.monotonic() + timeout
    while True:
        if cancelled():
            raise InterruptedError('PC Frida startup cancelled')
        if not alive():
            raise RuntimeFailure('process_changed', 'PC process changed during module readiness checks')
        try:
            value = read()
            if cancelled():
                raise InterruptedError('PC Frida startup cancelled')
            if not alive():
                raise RuntimeFailure('process_changed', 'PC process changed before its module read completed')
            return value
        except (OSError, RuntimeFailure) as error:
            retrying = _transient_module_read(error) and time.monotonic() < deadline
            if on_error is not None:
                on_error(error, retrying)
            if not _transient_module_read(error):
                raise
            if not alive():
                raise RuntimeFailure('process_changed', 'PC process exited while its module list initialized') from error
            if not retrying:
                raise RuntimeFailure(timeout_code,
                    f'{label} timed out ({getattr(error, "operation", "module query")}, '
                    f'WinError {getattr(error, "winerror", None)})') from error
            time.sleep(LOADER_READ_RETRY_SECONDS)


class WindowsGadgetLoader:
    """Keep the path allocation and loader thread until their use has ended."""

    def __init__(self, pid, *, access_rights=0x43A):
        self.api = access.WindowsApi()
        self.kernel = self.api.kernel
        self.handle = self.api.open_process(pid, access_rights)
        self.pid = pid
        self.created_filetime = None
        self.pending_thread = None
        self.path_address = None
        self.module = None
        self.load_error = None
        self.path = None
        self.stage, self.operation, self.check = 'initialized', None, None
        self.submitted = False
        self.thread_exit_code = None
        self.verification_complete = False
        self.preparation_retries = self.verification_retries = 0
        self.last_error = None
        self._verification_deadline = None
        self.cancelled = lambda: False
        self.commit = lambda operation: operation()
        self.before_mutation = lambda: None
        for name, arguments, result in (
            ('IsWow64Process2', [wintypes.HANDLE, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.WORD)], wintypes.BOOL),
            ('GetModuleHandleW', [wintypes.LPCWSTR], wintypes.HMODULE),
            ('GetProcAddress', [wintypes.HMODULE, ctypes.c_char_p], ctypes.c_void_p),
            ('GetModuleHandleExW', [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HMODULE)], wintypes.BOOL),
            ('GetModuleFileNameW', [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
            ('VirtualAllocEx', [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD], ctypes.c_void_p),
            ('VirtualFreeEx', [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD], wintypes.BOOL),
            ('WriteProcessMemory', [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            ('CreateRemoteThread', [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.HANDLE),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('GetExitCodeThread', [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        try:
            self.created_filetime = self.api.process_times(self.handle)[0]
            machine, native = wintypes.WORD(), wintypes.WORD()
            if not self.kernel.IsWow64Process2(self.handle, ctypes.byref(machine), ctypes.byref(native)):
                raise ctypes.WinError(ctypes.get_last_error())
            if machine.value != 0 or native.value != 0x8664 or ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeFailure('unsupported_environment', 'Native Windows x64 is required')
        except BaseException:
            self.api.close(self.handle)
            self.handle = None
            raise

    def alive(self):
        return (self.handle is not None and self.api.process_times(self.handle)[0] == self.created_filetime
                and self.api.exit_code(self.handle) == 259)

    def diagnostics(self):
        return {'stage': self.stage, 'operation': self.operation, 'check': self.check,
                'submitted': self.submitted, 'thread_exit_code': self.thread_exit_code,
                'verification_complete': self.verification_complete,
                'preparation_retries': self.preparation_retries,
                'verification_retries': self.verification_retries,
                'last_error': dict(self.last_error) if self.last_error is not None else None,
                'handle_closed': self.handle is None}

    def _remember_error(self, error):
        self.last_error = {'stage': self.stage, 'operation': getattr(error, 'operation', self.operation),
                           'check': self.check, 'win32_error': getattr(error, 'winerror', None)}

    def _win_error(self, operation):
        self.operation = operation
        error = ctypes.WinError(ctypes.get_last_error())
        error.operation = operation
        self._remember_error(error)
        error.strerror += f' (PC Frida {self.stage}/{self.check or "-"}/{operation})'
        raise error

    @staticmethod
    def _read_not_ready(error):
        return _transient_module_read(error)

    def find_module(self, path):
        modules = (wintypes.HMODULE * 4096)()
        needed = wintypes.DWORD()
        self.operation = 'EnumProcessModulesEx'
        if not self.api.psapi.EnumProcessModulesEx(self.handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
            self._win_error('EnumProcessModulesEx')
        if needed.value > ctypes.sizeof(modules) or needed.value % ctypes.sizeof(wintypes.HMODULE):
            raise RuntimeFailure('module_query_failed', 'Incomplete PC module list')
        matches = []
        for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            name = ctypes.create_unicode_buffer(32768)
            self.operation = 'GetModuleFileNameExW'
            count = self.api.psapi.GetModuleFileNameExW(self.handle, module, name, len(name))
            if not count or count >= len(name):
                self._win_error('GetModuleFileNameExW')
            if access.same_path(name.value, path):
                info = access.ModuleInfo()
                self.operation = 'GetModuleInformation'
                if not self.api.psapi.GetModuleInformation(self.handle, module, ctypes.byref(info), ctypes.sizeof(info)):
                    self._win_error('GetModuleInformation')
                matches.append({'path': name.value, 'base': int(info.base), 'size': info.size})
        if len(matches) > 1:
            raise RuntimeFailure('module_query_failed', 'Ambiguous exact PC module path')
        return matches[0] if matches else None

    def _loader_export(self):
        address = self.kernel.GetProcAddress(self.kernel.GetModuleHandleW('kernel32.dll'), b'LoadLibraryW')
        owner = wintypes.HMODULE()
        if not address or not self.kernel.GetModuleHandleExW(6, address, ctypes.byref(owner)):
            self._win_error('GetProcAddress/GetModuleHandleExW')
        path = ctypes.create_unicode_buffer(32768)
        if not self.kernel.GetModuleFileNameW(owner, path, len(path)):
            self._win_error('GetModuleFileNameW')
        own = self.api.open_process(os.getpid(), 0x410)
        try:
            info = access.ModuleInfo()
            if not self.api.psapi.GetModuleInformation(own, owner, ctypes.byref(info), ctypes.sizeof(info)):
                self._win_error('GetModuleInformation(local)')
        finally:
            self.api.close(own)
        offset = int(address) - int(info.base)
        remote = self.find_module(path.value)
        if not 0 <= offset < info.size or remote is None or remote['size'] != info.size:
            raise RuntimeFailure('loader_not_ready', 'Matching public Windows loader module is not ready')
        return remote['base'] + offset

    def _prepare(self):
        self.stage = 'preparing'
        def read():
            self.check, self.operation = 'existing_component', 'find_module'
            if self.find_module(self.path) is not None:
                raise RuntimeFailure('existing_component', 'Gadget is already loaded without this startup reference')
            self.check = 'loader_export'
            return self._loader_export()
        def failed(error, retrying):
            self._remember_error(error)
            if retrying:
                self.preparation_retries += 1
        return _bounded_module_read(read, cancelled=self.cancelled, alive=self.alive,
            timeout=LOADER_PREPARATION_SECONDS, timeout_code='loader_preparation_timeout',
            label='PC Frida Windows loader preparation', on_error=failed)

    def load(self, path):
        self._check_cancelled()
        if self.submitted:
            raise RuntimeFailure('loader_already_submitted', 'This process owner already submitted its Windows load')
        self.path = Path(path)
        entry = self._prepare()
        self.before_mutation()
        self._check_cancelled()
        if not self.alive():
            raise RuntimeFailure('process_changed', 'PC process exited before Windows loader mutation')
        text = ctypes.create_unicode_buffer(str(self.path))
        self.stage, self.check, self.operation = 'allocating', None, 'VirtualAllocEx'
        self.path_address = self.kernel.VirtualAllocEx(self.handle, None, ctypes.sizeof(text), 0x3000, 0x04)
        if not self.path_address:
            self._win_error('VirtualAllocEx')
        self._check_cancelled()
        written = ctypes.c_size_t()
        self.stage, self.operation = 'writing_path', 'WriteProcessMemory'
        if not self.kernel.WriteProcessMemory(self.handle, self.path_address, text, ctypes.sizeof(text), ctypes.byref(written)):
            self._win_error('WriteProcessMemory')
        if written.value != ctypes.sizeof(text):
            raise RuntimeFailure('loader_failed', 'Gadget path write was incomplete')
        def submit():
            self._check_cancelled()
            self.operation = 'CreateRemoteThread'
            thread = self.kernel.CreateRemoteThread(self.handle, None, 0, entry, self.path_address, 0, None)
            if not thread:
                self._win_error('CreateRemoteThread')
            self.pending_thread = thread
            self.submitted = True
            self.stage = 'submitted'
        self.commit(submit)
        if not self.poll():
            raise TimeoutError('Windows Gadget loader is still completing')
        if self.load_error is not None:
            raise self.load_error

    def _check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('PC Frida startup cancelled')

    def poll(self, *, wait_ms=0):
        if self.verification_complete or not self.submitted:
            return True
        if self.pending_thread is not None:
            self.operation = 'WaitForSingleObject'
            status = self.kernel.WaitForSingleObject(self.pending_thread, wait_ms)
            if status == 258:
                return False
            if status != 0:
                self._win_error('WaitForSingleObject')
            code = wintypes.DWORD()
            self.operation = 'GetExitCodeThread'
            if not self.kernel.GetExitCodeThread(self.pending_thread, ctypes.byref(code)):
                self._win_error('GetExitCodeThread')
            self.thread_exit_code = code.value
            self.api.close(self.pending_thread)
            self.pending_thread = None
            self._verification_deadline = time.monotonic() + LOADER_VERIFICATION_SECONDS
        self.stage, self.check = 'verifying', 'loaded_component'
        # The path is no longer in use once the Windows worker has returned.
        # Query failures after this point retain the result and only reverify.
        self.free_path()
        try:
            self.operation = 'find_module'
            self.module = self.find_module(self.path) if self.alive() else None
        except (OSError, RuntimeFailure) as error:
            self._remember_error(error)
            if self._read_not_ready(error) and self.alive():
                if time.monotonic() < self._verification_deadline:
                    self.verification_retries += 1
                    return False
                self.load_error = RuntimeFailure('loader_verification_timeout',
                    f'PC Frida post-load verification timed out ({self.operation}, '
                    f'WinError {getattr(error, "winerror", None)}, Windows result {self.thread_exit_code:#x})')
            else:
                self.load_error = RuntimeFailure('loader_verification_failed',
                    f'PC Frida post-load verification failed ({self.operation}, '
                    f'WinError {getattr(error, "winerror", None)}, Windows result {self.thread_exit_code:#x})')
        else:
            if self.module is None or (self.module['base'] & 0xFFFFFFFF) != self.thread_exit_code:
                self.load_error = RuntimeFailure('loader_failed',
                    f'Windows did not load Gadget (thread result {self.thread_exit_code:#x})')
        self.verification_complete = True
        self.stage = 'complete'
        return True

    def free_path(self):
        if self.path_address is None:
            return
        if self.pending_thread is not None:
            raise RuntimeFailure('cleanup_pending', 'Gadget loader still owns its path buffer')
        if self.alive() and not self.kernel.VirtualFreeEx(self.handle, self.path_address, 0, 0x8000):
            self._win_error('VirtualFreeEx')
        self.path_address = None

    def close(self):
        if self.handle is None:
            return
        if self.pending_thread is not None:
            if self.alive():
                self.poll()
                if self.pending_thread is not None:
                    raise RuntimeFailure('cleanup_pending', 'Gadget startup has not returned; cleanup remains owned')
            else:
                self.api.close(self.pending_thread)
                self.pending_thread = self.path_address = None
        self.free_path()
        self.api.close(self.handle)
        self.handle = None


class RuntimeState(str, Enum):
    STOPPED = 'stopped'
    STARTING = 'starting'
    WAITING_ENDPOINT = 'waiting_for_endpoint'
    WAITING_PROCESS = 'waiting_for_process'
    LOADING = 'loading'
    AUTHENTICATING = 'authenticating'
    READY = 'ready'
    PROCESS_FAILED = 'process_failed'
    FAILED = 'failed'
    STOPPING = 'stopping'
    CLEANUP_PENDING = 'cleanup_pending'
    CLOSED = 'closed'


# Environment/ownership failures need explicit retry; a failed DLL attempt
# waits for that process birth to exit. Only authentication retries itself.
_TRANSITIONS = {
    RuntimeState.STOPPED: {RuntimeState.STARTING},
    RuntimeState.STARTING: {RuntimeState.WAITING_ENDPOINT, RuntimeState.WAITING_PROCESS,
                           RuntimeState.AUTHENTICATING, RuntimeState.PROCESS_FAILED, RuntimeState.FAILED},
    RuntimeState.WAITING_ENDPOINT: {RuntimeState.WAITING_PROCESS, RuntimeState.STARTING, RuntimeState.FAILED},
    RuntimeState.WAITING_PROCESS: {RuntimeState.LOADING, RuntimeState.STARTING, RuntimeState.FAILED},
    RuntimeState.LOADING: {RuntimeState.AUTHENTICATING, RuntimeState.PROCESS_FAILED,
                         RuntimeState.WAITING_PROCESS, RuntimeState.FAILED},
    RuntimeState.AUTHENTICATING: {RuntimeState.READY, RuntimeState.WAITING_PROCESS,
                                RuntimeState.STARTING, RuntimeState.PROCESS_FAILED, RuntimeState.FAILED},
    RuntimeState.READY: {RuntimeState.AUTHENTICATING, RuntimeState.WAITING_PROCESS,
                        RuntimeState.STARTING, RuntimeState.FAILED},
    RuntimeState.PROCESS_FAILED: {RuntimeState.WAITING_PROCESS, RuntimeState.STARTING,
                                 RuntimeState.AUTHENTICATING, RuntimeState.FAILED},
    RuntimeState.FAILED: {RuntimeState.STARTING, RuntimeState.WAITING_PROCESS},
    RuntimeState.STOPPING: {RuntimeState.CLEANUP_PENDING, RuntimeState.CLOSED},
    RuntimeState.CLEANUP_PENDING: {RuntimeState.CLOSED},
    RuntimeState.CLOSED: set(),
}
for _state_name in _TRANSITIONS:
    _TRANSITIONS[_state_name].add(_state_name)
    if _state_name != RuntimeState.CLOSED:
        _TRANSITIONS[_state_name].add(RuntimeState.STOPPING)

_KEEP = object()
_AUTH_RETRY_SECONDS = .1
_TRANSIENT_ENDPOINT_CODES = frozenset(('endpoint_unavailable', 'endpoint_cleanup_pending'))


class NativePcFridaRuntime:
    """Own startup, the process-lifetime DLL and all client registrations.

    Start is idempotent while running. Cancellation is terminal for this owner.
    Explicit retry reconciles repaired prerequisites without forgetting births,
    changing an armed watch window or superseding an in-flight Windows load.
    """

    pending_states = frozenset(state.value for state in (
        RuntimeState.STARTING, RuntimeState.WAITING_ENDPOINT, RuntimeState.WAITING_PROCESS,
        RuntimeState.LOADING, RuntimeState.AUTHENTICATING))
    failure_states = frozenset(state.value for state in (
        RuntimeState.FAILED, RuntimeState.PROCESS_FAILED, RuntimeState.CLEANUP_PENDING))

    def __init__(self, game_root, *, runtime_dir, gadget_path=None, api=None, loader_factory=None,
                 build_check=None, admin_check=None):
        self.game_root = Path(game_root).resolve()
        self.runtime_dir = Path(runtime_dir).resolve() / 'native-pc-frida'
        self.lease_path = self.runtime_dir / 'lease.json'
        self.asset_path = DEFAULT_ASSET if gadget_path is None else None
        self.gadget_path = (self.runtime_dir / FRIDA_VERSION / GADGET_NAME if gadget_path is None
                            else Path(gadget_path).resolve())
        self.config_path = self.gadget_path.with_suffix('.config')
        self.api = api
        self.loader_factory = loader_factory or WindowsGadgetLoader
        self.build_check = build_check or access.verify_build
        self.admin_check = admin_check or (lambda: bool(ctypes.windll.shell32.IsUserAnAdmin()))
        self.status, self.code, self.error = RuntimeState.STOPPED, None, None
        self._condition = Condition()
        self._cancel = Event()
        self._thread = None
        self._rpc_cancellations = set()
        self._lease = None
        self._attempted = set()
        self._pending_loader = None
        self._armed = None
        self._retry_requested = True
        self._module_loaded = False
        self._authenticated_birth = None
        self._endpoint_owner = None
        self._endpoint_needs_cleanup = False
        self._observers = set()
        self._next_auth = 0
        self._build_signature = None
        self._asset_signature = None
        self._ready_generation = 0
        self._loader_diagnostic = None
        self._module_query_diagnostic = None

    @property
    def is_pending(self):
        with self._condition:
            return not self._cancel.is_set() and self.status.value in self.pending_states

    @property
    def is_failed(self):
        with self._condition:
            return self.status.value in self.failure_states

    def describe(self):
        with self._condition:
            self._assert_invariants()
            recovery = ('none' if self.status == RuntimeState.CLOSED else
                        'cleanup' if self.status == RuntimeState.CLEANUP_PENDING else
                        'explicit_retry' if self.status == RuntimeState.FAILED else
                        'next_process' if self.status == RuntimeState.PROCESS_FAILED else 'automatic')
            return {'status': self.status.value, 'code': self.code, 'error': self.error,
                    'lease': dict(self._lease) if self._lease is not None else None,
                    'armed_filetime': self._armed, 'expected_resident': self._module_loaded,
                    'pending_loader': self._pending_loader is not None,
                    'loader': self._loader_snapshot(),
                    'module_query': dict(self._module_query_diagnostic) if self._module_query_diagnostic is not None else None,
                    'endpoint_owned': self._endpoint_owner is not None,
                    'ready_generation': self._ready_generation,
                    'active_observers': len(self._observers), 'cancelled': self._cancel.is_set(),
                    'thread_alive': self._thread is not None and self._thread.is_alive(), 'recovery': recovery}

    def _loader_snapshot(self, loader=None):
        loader = self._pending_loader if loader is None else loader
        diagnostics = getattr(loader, 'diagnostics', None)
        if callable(diagnostics):
            return diagnostics()
        return dict(self._loader_diagnostic) if self._loader_diagnostic is not None else None

    def _remember_loader(self, loader):
        diagnostics = self._loader_snapshot(loader)
        if diagnostics is not None:
            self._loader_diagnostic = diagnostics

    @staticmethod
    def _birth(record):
        return record['pid'], record['created']

    def _assert_invariants(self):
        if self.status == RuntimeState.READY:
            if (self._cancel.is_set() or self._lease is None or self._lease['status'] != 'ready'
                    or not self._module_loaded or self._pending_loader is not None
                    or self._authenticated_birth != self._birth(self._lease)
                    or self._endpoint_owner is None or self._endpoint_needs_cleanup
                    or self._endpoint_owner.birth != self._birth(self._lease)):
                raise RuntimeError('Invalid ready PC Frida ownership state')
        if self.status == RuntimeState.AUTHENTICATING:
            if self._lease is None or not self._module_loaded or self._pending_loader is not None:
                raise RuntimeError('PC Frida authentication requires a completed owned component')
        if self.status == RuntimeState.LOADING and self._lease is None:
            raise RuntimeError('PC Frida loading requires an owned lease')
        if self._pending_loader is not None and self.status not in (
                RuntimeState.LOADING, RuntimeState.STOPPING, RuntimeState.CLEANUP_PENDING):
            raise RuntimeError('PC Frida loader ownership escaped its loading state')
        if self.status == RuntimeState.CLOSED:
            if self._pending_loader is not None or self._endpoint_owner is not None or self._observers:
                raise RuntimeError('PC Frida closed while client resources remain owned')

    def _state(self, state, code=None, error=None, *, lease=_KEEP, loaded=_KEEP):
        with self._condition:
            state = RuntimeState(state)
            if state not in _TRANSITIONS[self.status]:
                raise RuntimeError(f'Invalid PC Frida transition {self.status.value} -> {state.value}')
            if self._cancel.is_set() and state not in (
                    RuntimeState.STOPPING, RuntimeState.CLEANUP_PENDING, RuntimeState.CLOSED):
                raise InterruptedError('PC Frida startup cancelled')
            previous = (self.status, self.code, self.error, self._lease,
                        self._module_loaded, self._authenticated_birth)
            if lease is not _KEEP:
                self._lease = dict(lease) if lease is not None else None
            if loaded is not _KEEP:
                self._module_loaded = loaded
            if state != RuntimeState.READY:
                self._authenticated_birth = None
            self.status, self.code, self.error = state, code, error
            try:
                self._assert_invariants()
            except BaseException:
                (self.status, self.code, self.error, self._lease,
                 self._module_loaded, self._authenticated_birth) = previous
                raise
            self._condition.notify_all()

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise InterruptedError('PC Frida startup cancelled')

    def _commit(self, operation):
        # Cancellation and the beginning of a Windows operation share this lock.
        with self._condition:
            self._check_cancelled()
            return operation()

    def _new_thread(self):
        self._thread = Thread(target=self._run, name='ArkDPS PC Frida startup', daemon=False)
        try:
            self._thread.start()
        except Exception:
            if not self._thread.is_alive():
                self._thread = None
                self._state(RuntimeState.FAILED, 'watcher_start_failed', 'PC Frida watcher could not start; retry')
            raise

    def start(self):
        with self._condition:
            if self._cancel.is_set() or self.status == RuntimeState.CLOSED:
                raise RuntimeError('PC Frida runtime is cancelled or closed; create a new owner')
            if self._thread is not None:
                if self._thread.is_alive():
                    return self
                raise RuntimeError('PC Frida watcher stopped; use explicit retry')
            self._state(RuntimeState.STARTING)
            self._new_thread()
        return self

    def retry(self):
        with self._condition:
            if self._cancel.is_set():
                raise RuntimeError('PC Frida runtime is cancelled; create a new owner')
            if self.status in (RuntimeState.STOPPING, RuntimeState.CLEANUP_PENDING, RuntimeState.CLOSED):
                raise RuntimeError('PC Frida runtime is stopping; finish cleanup first')
            if self._pending_loader is not None:
                return False
            self._retry_requested = True
            if not self._observers and self.status.value not in self.pending_states:
                self._state(RuntimeState.STARTING)
            self._condition.notify_all()
            if self._thread is None or not self._thread.is_alive():
                self._state(RuntimeState.STARTING)
                self._new_thread()
            return True

    def cancel(self):
        with self._condition:
            if self.status == RuntimeState.CLOSED:
                return
            self._cancel.set()
            if self.status not in (RuntimeState.STOPPING, RuntimeState.CLEANUP_PENDING):
                self._state(RuntimeState.STOPPING)
            cancellations = tuple(self._rpc_cancellations)
            observers = tuple(self._observers)
            self._condition.notify_all()
        for cancellation in cancellations:
            cancellation.cancel()
        for observer in observers:
            callback = getattr(observer, '_runtime_cancelled', None)
            if callback is not None:
                callback()

    def wait_ready(self, timeout, cancelled=None):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('PC Frida readiness timeout must be finite and nonnegative')
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                self._check_cancelled()
                if cancelled is not None and cancelled():
                    raise InterruptedError('PC Frida startup cancelled')
                self._assert_invariants()
                if self.status == RuntimeState.READY:
                    return dict(self._lease)
                if self.status in (RuntimeState.FAILED, RuntimeState.PROCESS_FAILED, RuntimeState.CLEANUP_PENDING):
                    raise RuntimeFailure(self.code or 'startup_failed', self.error or 'PC Frida startup failed')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('等待 PC 事件组件就绪超时；请先启动 ArkDPS，再重新启动 PC 游戏')
                self._condition.wait(min(.1, remaining))

    @staticmethod
    def _file_token(path):
        try:
            item = path.stat()
            return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns
        except FileNotFoundError:
            return None

    def _verify_build_cached(self):
        signature = tuple(self._file_token(self.game_root / name) for name in access.KNOWN_FILES)
        if signature == self._build_signature:
            return
        build, _ = self.build_check(self.game_root)
        if build.get('known_build') is not True:
            raise RuntimeFailure('native_unsupported_build', 'PC 客户端构建不匹配；请等待适配此版本')
        if signature != tuple(self._file_token(self.game_root / name) for name in access.KNOWN_FILES):
            raise RuntimeFailure('native_build_changed', 'PC 客户端文件正在变化；请完成更新后重试')
        self._build_signature = signature

    def _verify_assets(self):
        source = self.asset_path or self.gadget_path
        signature = self._file_token(source), self._file_token(self.gadget_path)
        if signature == self._asset_signature:
            return
        if not source.is_file():
            raise RuntimeFailure('gadget_missing', '缺少随 ArkDPS 提供的 PC Frida 事件组件，请重新解压完整安装包')
        if _digest(source) != GADGET_SHA256:
            raise RuntimeFailure('gadget_hash_mismatch', 'PC Frida 组件校验失败，请重新解压完整安装包')
        if self.asset_path is not None and not self.gadget_path.exists():
            self.gadget_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.gadget_path.parent, prefix='.gadget-',
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(source, temporary)
                if _digest(temporary) != GADGET_SHA256:
                    raise RuntimeFailure('gadget_hash_mismatch', 'Cached PC Frida Gadget hash did not match')
                self._commit(lambda: os.replace(temporary, self.gadget_path))
            finally:
                temporary.unlink(missing_ok=True)
        if _digest(self.gadget_path) != GADGET_SHA256:
            raise RuntimeFailure('gadget_hash_mismatch', 'Cached PC Frida Gadget hash did not match')
        self._asset_signature = self._file_token(source), self._file_token(self.gadget_path)

    def _preflight(self):
        self._check_cancelled()
        if not self.admin_check():
            raise RuntimeFailure('native_permission_denied', '请以管理员权限启动 ArkDPS，才能启用 PC Frida 事件采集')
        self._verify_build_cached()
        self._verify_assets()
        self.api = self.api or access.WindowsApi()

    def _instances(self):
        matches = []
        for pid in self.api.named_processes():
            self._check_cancelled()
            handle = None
            try:
                handle = self.api.open_process(pid, 0x1000)
                if (self.api.exit_code(handle) == 259
                        and access.same_path(self.api.image_path(handle), self.game_root / 'Arknights.exe')):
                    matches.append((pid, self.api.process_times(handle)[0]))
            except access.ApiFailure as exc:
                if exc.code not in (5, 87, 299):
                    raise
            finally:
                if handle is not None:
                    self.api.close(handle)
        return matches

    def _alive(self, record):
        handle = None
        try:
            handle = self.api.open_process(record['pid'], 0x1000)
            return (self.api.process_times(handle)[0] == record['created'] and self.api.exit_code(handle) == 259
                    and access.same_path(self.api.image_path(handle), self.game_root / 'Arknights.exe'))
        except access.ApiFailure as exc:
            if exc.code == 87:
                return False
            raise
        finally:
            if handle is not None:
                self.api.close(handle)

    def _save_lease(self, record, *, ready=False):
        self._check_cancelled()
        disk = json.loads(self.lease_path.read_text(encoding='utf-8')) if self.lease_path.exists() else None
        if disk != self._lease:
            raise RuntimeFailure('lease_conflict', 'A changed PC Frida lease was preserved')
        with self._condition:
            self._check_cancelled()
            if ready and (self._endpoint_owner is None or self._endpoint_needs_cleanup
                          or self._endpoint_owner.birth != self._birth(record)):
                raise RuntimeFailure('endpoint_unavailable', 'PC Frida authentication changed before readiness commit')
            _write_json(self.lease_path, record)
            if ready:
                self._authenticated_birth = self._birth(record)
                self._ready_generation += 1
                self._state(RuntimeState.READY, lease=record)
            else:
                self._lease = dict(record)

    def _read_lease(self):
        if not self.lease_path.exists():
            return None
        try:
            record = json.loads(self.lease_path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise RuntimeFailure('lease_conflict', 'PC Frida lease could not be validated; it was preserved') from exc
        required = {'version', 'pid', 'created', 'game_root', 'source_build', 'gadget_path',
                    'gadget_sha256', 'config_sha256', 'status'}
        if (not isinstance(record, dict) or set(record) != required
                or type(record['version']) is not int or record['version'] != 1
                or any(type(record[key]) is not int or record[key] <= 0 for key in ('pid', 'created'))
                or any(not isinstance(record[key], str) for key in ('game_root', 'source_build', 'gadget_path', 'gadget_sha256'))
                or not access.same_path(record['game_root'], self.game_root)
                or record['source_build'] != SOURCE_BUILD
                or record['gadget_sha256'] != GADGET_SHA256 or record['status'] not in ('loading', 'ready', 'failed')
                or not isinstance(record['config_sha256'], str) or len(record['config_sha256']) != 64
                or any(ch not in '0123456789abcdef' for ch in record['config_sha256'])):
            raise RuntimeFailure('lease_conflict', 'PC Frida lease does not match this verified client and component')
        return record

    def _credentials(self, record):
        try:
            raw = self.config_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record['config_sha256']:
                raise ValueError('credential hash differs')
            configuration = json.loads(raw)
            interaction = configuration.get('interaction')
            if (not isinstance(interaction, dict)
                    or any(interaction.get(key) != value for key, value in
                           (('type', 'listen'), ('address', ADDRESS), ('port', PORT), ('on_load', 'resume')))
                    or not isinstance(interaction.get('token'), str) or len(interaction['token']) < 32):
                raise ValueError('credential shape differs')
            return interaction['token']
        except (ValueError, OSError, AttributeError) as exc:
            raise RuntimeFailure('credential_conflict', 'PC Frida credential file changed or is unavailable; it was preserved') from exc

    def _module_present(self, record):
        loader = self.loader_factory(record['pid'], access_rights=0x410)
        diagnostic = {'stage': 'checking', 'retries': 0, 'win32_error': None, 'operation': None}
        self._module_query_diagnostic = diagnostic
        loader.cancelled = self._cancel.is_set
        def same_birth():
            return loader.created_filetime == record['created'] and self._alive(record)
        def read():
            return loader.find_module(self.gadget_path) is not None
        def failed(error, retrying):
            diagnostic.update(operation=getattr(error, 'operation', 'find_module'),
                              win32_error=getattr(error, 'winerror', None))
            diagnostic['retries'] += int(retrying)
        try:
            # The query owner is separate from the submitted load owner. Its
            # retries must never overwrite submitted/result evidence.
            loader.stage, loader.check = 'checking', 'presence'
            present = _bounded_module_read(read, cancelled=self._cancel.is_set, alive=same_birth,
                timeout=LOADER_VERIFICATION_SECONDS, timeout_code='module_read_timeout',
                label='PC Frida module presence verification', on_error=failed)
            diagnostic['stage'] = 'complete'
            return present
        finally:
            loader.close()

    @contextmanager
    def _rpc(self, frida):
        cancellation = frida.Cancellable()
        with self._condition:
            self._check_cancelled()
            self._rpc_cancellations.add(cancellation)
        try:
            with cancellation:
                self._check_cancelled()
                yield
                self._check_cancelled()
        finally:
            with self._condition:
                self._rpc_cancellations.discard(cancellation)

    def _release_endpoint(self):
        with self._condition:
            owner = self._endpoint_owner
            if owner is None:
                return
            if self._observers:
                raise RuntimeFailure('endpoint_cleanup_pending', 'PC observer resources must be released before reconnect')
            if self.status == RuntimeState.READY:
                self._state(RuntimeState.AUTHENTICATING)
        try:
            owner.manager.remove_remote_device(owner.address)
        except Exception as exc:
            self._endpoint_needs_cleanup = True
            raise RuntimeFailure('endpoint_cleanup_pending', 'PC Frida endpoint cleanup remains owned; retry cleanup') from exc
        with self._condition:
            if self._endpoint_owner is owner:
                self._endpoint_owner = None
                self._endpoint_needs_cleanup = False

    def _connect(self, frida, record):
        self._check_cancelled()
        token = self._credentials(record)
        if self._endpoint_needs_cleanup:
            self._release_endpoint()
        address = f'{ADDRESS}:{PORT}'
        try:
            owner = self._endpoint_owner
            if owner is None:
                manager = frida.get_device_manager()
                device = manager.add_remote_device(address, token=token)
                owner = SimpleNamespace(manager=manager, device=device, address=address, birth=self._birth(record))
                with self._condition:
                    # Retain ownership before validation, including cancellation.
                    self._endpoint_owner = owner
            elif owner.birth != self._birth(record):
                raise RuntimeFailure('endpoint_mismatch', 'PC Frida registration belongs to a different process birth')
            self._check_cancelled()
            processes = owner.device.enumerate_processes()
            self._check_cancelled()
            if len(processes) != 1 or processes[0].pid != record['pid'] or not self._alive(record):
                raise RuntimeFailure('endpoint_mismatch', 'Authenticated PC Frida endpoint identifies a different process')
            return owner.manager, owner.device, owner.address
        except BaseException as exc:
            if self._endpoint_owner is not None:
                with self._condition:
                    if self.status == RuntimeState.READY:
                        self._state(RuntimeState.AUTHENTICATING)
                    self._endpoint_needs_cleanup = True
                if not self._observers:
                    self._release_endpoint()
            if isinstance(exc, (RuntimeFailure, InterruptedError)) or not isinstance(exc, Exception):
                raise
            permission_error = getattr(frida, 'PermissionDeniedError', ())
            if isinstance(exc, permission_error):
                raise RuntimeFailure('endpoint_auth_failed', 'PC Frida 本机端点认证失败；请释放旧会话后重试') from exc
            raise RuntimeFailure('endpoint_unavailable', 'PC Frida 本机认证端点尚未就绪，正在重试连接') from exc

    def _verify_endpoint(self, record):
        import frida
        if frida.__version__ != FRIDA_VERSION:
            raise RuntimeFailure('frida_version_mismatch', 'Matching Frida 17.26.1 client required')
        with self._rpc(frida):
            self._connect(frida, record)

    def _remove_credentials(self, record):
        with self._condition:
            # Waiting means cleanup has committed. Keep disk ownership, lease
            # revocation and its public state in one transition so describe()
            # cannot publish waiting with credentials still awaiting deletion.
            if not self._cancel.is_set():
                self._state(RuntimeState.STARTING, loaded=False)
            else:
                self._module_loaded = False
            try:
                if self.lease_path.exists() and json.loads(self.lease_path.read_text(encoding='utf-8')) != record:
                    raise RuntimeFailure('lease_conflict', 'Changed PC Frida lease was preserved')
                if self.config_path.exists():
                    if _digest(self.config_path) != record['config_sha256']:
                        raise RuntimeFailure('credential_conflict', 'Changed PC Frida credentials were preserved')
                    self.config_path.unlink()
                if self.lease_path.exists():
                    self.lease_path.unlink()
            except Exception as exc:
                failure = exc if isinstance(exc, RuntimeFailure) else RuntimeFailure(
                    'credential_cleanup_failed', f'PC Frida 旧进程凭据清理失败；请重试（{exc}）')
                self._state(RuntimeState.CLEANUP_PENDING if self._cancel.is_set() else RuntimeState.FAILED,
                            failure.code, str(failure))
                if failure is exc:
                    raise
                raise failure from exc
            self._lease = None
            self._authenticated_birth = None
            # Preserve the watch window across OWN exits, including births
            # between polls. Only the initial foreign-port wait delays arming.
            if not self._cancel.is_set():
                self._state(RuntimeState.WAITING_PROCESS, loaded=False)
            else:
                self._assert_invariants()
                self._condition.notify_all()

    def _ready(self, record):
        self._verify_endpoint(record)
        self._check_cancelled()
        if not self._alive(record):
            raise RuntimeFailure('process_changed', 'PC process exited during endpoint authentication')
        self._save_lease(dict(record, status='ready'), ready=True)

    def _port_occupied(self):
        with socket.socket() as stream:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                stream.bind((ADDRESS, PORT))
            except OSError as exc:
                if exc.errno in (98, 10013, 10048) or getattr(exc, 'winerror', None) in (10013, 10048):
                    return True
                raise
        return False

    def _wait_endpoint_step(self):
        if self._port_occupied():
            self._state(RuntimeState.WAITING_ENDPOINT, 'endpoint_in_use',
                        'PC Frida 本机端口仍被占用；释放后将自动等待新启动的游戏')
            return
        with self._condition:
            self._check_cancelled()
            if self._armed is None:
                self._armed = time.time_ns() // 100 + FILETIME_EPOCH
            self._state(RuntimeState.WAITING_PROCESS)

    def _load_once(self, instance):
        self._verify_build_cached()
        self._verify_assets()
        with self._condition:
            self._check_cancelled()
            if instance in self._attempted:
                raise RuntimeFailure('restart_required', '当前游戏进程已尝试加载；请正常重启游戏')
            self._attempted.add(instance)
        record = {'version': 1, 'pid': instance[0], 'created': instance[1], 'game_root': str(self.game_root),
                  'source_build': SOURCE_BUILD, 'gadget_path': str(self.gadget_path),
                  'gadget_sha256': GADGET_SHA256, 'status': 'loading'}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._condition:
                self._check_cancelled()
                with self.config_path.open('x', encoding='utf-8') as stream:
                    json.dump({'interaction': {'type': 'listen', 'address': ADDRESS, 'port': PORT,
                                              'token': secrets.token_urlsafe(32), 'on_load': 'resume', 'on_port_conflict': 'fail'},
                               'teardown': 'full'}, stream, separators=(',', ':'))
        except FileExistsError as exc:
            raise RuntimeFailure('credential_conflict', 'An existing PC Frida credential file was preserved') from exc
        record['config_sha256'] = _digest(self.config_path)
        try:
            self._save_lease(record)
        except BaseException:
            if _digest(self.config_path) == record['config_sha256']:
                self.config_path.unlink()
            raise
        self._state(RuntimeState.LOADING)
        self._loader_diagnostic = {'stage': 'initializing', 'operation': 'OpenProcess', 'submitted': False}
        loader = self.loader_factory(instance[0])
        self._pending_loader = loader
        loader.cancelled, loader.commit = self._cancel.is_set, self._commit
        loader.before_mutation = lambda: (self._verify_build_cached(), self._verify_assets())
        try:
            self._check_cancelled()
            if loader.created_filetime != instance[1] or not self._alive(record):
                raise RuntimeFailure('process_changed', 'PC process identity changed before Frida startup')
            self._check_cancelled()
            loader.load(self.gadget_path)
        except TimeoutError:
            self._state(RuntimeState.LOADING, 'loader_pending', 'Windows PC Frida startup is still completing')
            return
        except BaseException:
            if loader.pending_thread is None:
                loader.close()
                self._pending_loader = None
            raise
        finally:
            self._remember_loader(loader)
        self._finish_loader()

    def _finish_loader(self):
        loader = self._pending_loader
        if loader is None:
            return False
        try:
            if not loader.poll():
                return False
            error = loader.load_error
            loader.close()
        finally:
            self._remember_loader(loader)
        self._pending_loader = None
        self._check_cancelled()
        if error is not None:
            raise error
        if not self._alive(self._lease) or not self._module_present(self._lease):
            raise RuntimeFailure('loader_failed', 'Windows PC Frida component was not present after startup')
        self._state(RuntimeState.AUTHENTICATING, loaded=True)
        self._next_auth = 0
        return True

    def _reconcile(self):
        self._state(RuntimeState.STARTING)
        self._preflight()
        record = self._read_lease()
        if self._lease is not None and record != self._lease:
            raise RuntimeFailure('lease_conflict', 'A changed PC Frida lease was preserved')
        if record is None:
            if self._armed is None:
                self._wait_endpoint_step()
            else:
                self._state(RuntimeState.WAITING_PROCESS)
            return
        with self._condition:
            self._lease = record
            self._attempted.add(self._birth(record))
            if self._armed is None:
                self._armed = time.time_ns() // 100 + FILETIME_EPOCH
        if not self._alive(record):
            self._release_endpoint()
            self._remove_credentials(record)
            return
        if not access.same_path(record['gadget_path'], self.gadget_path) or not self._module_present(record):
            self._state(RuntimeState.PROCESS_FAILED, 'restart_required',
                        '当前 PC 游戏没有此路径可重连的组件；请保持 ArkDPS 开启，再正常重启游戏', loaded=False)
            return
        self._credentials(record)
        self._state(RuntimeState.AUTHENTICATING, loaded=True)
        self._next_auth = 0

    def _authenticate_step(self):
        with self._condition:
            if self._observers or time.monotonic() < self._next_auth:
                return
        self._ready(self._lease)

    def _scan_step(self):
        matches = self._instances()
        self._check_cancelled()
        if len(matches) > 1:
            self._state(RuntimeState.WAITING_PROCESS, 'ambiguous_process', '请保留一个相同路径的 PC 游戏实例')
        elif matches and matches[0][1] > self._armed and matches[0] not in self._attempted:
            self._load_once(matches[0])
        else:
            self._state(RuntimeState.WAITING_PROCESS, 'restart_required' if matches else None,
                        '请保持 ArkDPS 开启，再重新启动 PC 游戏以启用逐次战斗事件' if matches else None)

    def _step(self):
        self._check_cancelled()
        if self._pending_loader is not None:
            self._finish_loader()
            return
        if self.status == RuntimeState.FAILED and not self._retry_requested:
            return
        if self._lease is not None and not self._alive(self._lease):
            self._state(RuntimeState.STARTING, loaded=False)
            if self._observers:
                return
            with self._condition:
                self._check_cancelled()
                # One explicit retry authorizes one cleanup attempt. Retain
                # its intent while observers still own resources, then consume
                # it before any operation that can fail again.
                self._retry_requested = False
            self._release_endpoint()
            self._remove_credentials(self._lease)
            return
        if self._retry_requested and not self._observers:
            self._retry_requested = False
            self._reconcile()
            return
        handlers = {
            RuntimeState.WAITING_ENDPOINT: self._wait_endpoint_step,
            RuntimeState.WAITING_PROCESS: self._scan_step,
            RuntimeState.AUTHENTICATING: self._authenticate_step,
        }
        handler = handlers.get(self.status)
        if handler is not None:
            handler()

    def _run(self):
        while not self._cancel.is_set():
            try:
                self._step()
            except Exception as exc:
                with self._condition:
                    if self._cancel.is_set():
                        break
                    code = getattr(exc, 'code', 'startup_interrupted' if isinstance(exc, InterruptedError)
                                   else 'startup_failed')
                    if self._pending_loader is not None:
                        self._state(RuntimeState.LOADING, 'loader_pending',
                                    'Windows PC Frida startup or cleanup is still owned; waiting for completion')
                    elif code in _TRANSIENT_ENDPOINT_CODES and self._module_loaded and self._lease is not None:
                        self._next_auth = time.monotonic() + _AUTH_RETRY_SECONDS
                        self._state(RuntimeState.AUTHENTICATING, code, str(exc))
                    elif self._lease is not None and (self.status == RuntimeState.LOADING
                            or code in ('loader_failed', 'loader_not_ready', 'process_changed')):
                        self._state(RuntimeState.PROCESS_FAILED, code, str(exc))
                    else:
                        self._state(RuntimeState.FAILED, code, str(exc))
            self._cancel.wait(POLL_SECONDS)

    def report_transport_failure(self, error=None):
        with self._condition:
            if self._cancel.is_set() or self.status not in (RuntimeState.READY, RuntimeState.AUTHENTICATING):
                return
            self._endpoint_needs_cleanup = self._endpoint_owner is not None
            self._next_auth = 0
            self._state(RuntimeState.AUTHENTICATING, 'endpoint_unavailable',
                        'PC Frida 会话已断开；释放旧观察者后将重新认证')

    def _reserve_observer(self, observer, record):
        with self._condition:
            self._check_cancelled()
            if self.status != RuntimeState.READY or self._lease != record:
                raise RuntimeFailure('endpoint_unavailable', 'PC Frida readiness changed before observer attachment')
            self._observers.add(observer)

    def _release_observer(self, observer):
        with self._condition:
            self._observers.discard(observer)
            self._condition.notify_all()

    def observer_factory(self, collector, remote):
        return _GadgetObserver(collector, remote, runtime=self)

    def close(self):
        if self.status == RuntimeState.CLOSED:
            return
        self.cancel()
        try:
            if self._thread is not None:
                self._thread.join(timeout=32)
                if self._thread.is_alive():
                    raise RuntimeFailure('cleanup_pending', 'PC Frida startup worker has not finished cleanup')
            if self._pending_loader is not None:
                try:
                    self._pending_loader.close()
                finally:
                    self._remember_loader(self._pending_loader)
                self._pending_loader = None
            if self._observers:
                raise RuntimeFailure('cleanup_pending', 'PC observers still own scripts or sessions')
            self._release_endpoint()
            record = self._lease
            if record is not None and not self._alive(record):
                self._remove_credentials(record)
        except Exception as exc:
            self._state(RuntimeState.CLEANUP_PENDING, getattr(exc, 'code', 'cleanup_failed'), str(exc))
            raise
        self._state(RuntimeState.CLOSED)


class _GadgetObserver(NativePcFridaObserver):
    native_clock_busy_retry = True

    def __init__(self, collector, remote, *, runtime):
        super().__init__(collector, remote)
        self.runtime = runtime
        self._reserved = False
        self._detach_intent = None
        self.script_error = None
        self._request_timing_audit = None

    def set_request_timing_audit(self, sink=None):
        self._request_timing_audit = sink

    def _request(self, operation, *, timeout=None, **arguments):
        sink = self._request_timing_audit
        if sink is None:
            return super()._request(operation, timeout=timeout, **arguments)
        started = time.monotonic()
        started_perf = time.perf_counter()
        error_type = None
        reply = None
        try:
            # Include both transport and NativePcBinding validation. The audit
            # write below is outside the measured request wall time.
            reply = super()._request(operation, timeout=timeout, **arguments)
            return reply
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            try:
                receipt = dict(
                    op=operation if operation in ('mark', 'read', 'ack') else 'other',
                    started_monotonic=started, started_perf_counter=started_perf,
                    elapsed_ms=(time.perf_counter() - started_perf) * 1000,
                    outcome='success' if error_type is None else 'error', error_type=error_type)
                sequence = arguments.get('sequence')
                if operation == 'ack' and type(sequence) is int:
                    acknowledged = reply.get('acknowledged') if isinstance(reply, dict) else None
                    confirmed = (type(acknowledged) is int and acknowledged == sequence
                                 and not reply.get('error'))
                    receipt['acknowledgement'] = ('unknown' if error_type else
                                                  'confirmed' if confirmed else 'rejected')
                    receipt['ack_sequence'] = sequence
                sink('native_pc_request_timing', receipt)
            except BaseException:
                pass  # Optional diagnostics cannot replace a result or exception.

    def start(self):
        if self.status != ObserverStatus.STOPPED or self.session is not None or self.script is not None or self._reserved:
            raise RuntimeError('observer is already started or still owns resources')
        self.script_error = None
        self.runtime.wait_ready(self.request_timeout)
        import frida
        try:
            # Include create_script/load, not only attachment. Frida's current
            # Cancellable interrupts every startup RPC within this scope.
            with self.runtime._rpc(frida):
                super().start()
        except BaseException as exc:
            if self._reserved or self.script is not None or self.session is not None:
                self.error = str(exc)
                self.status = ObserverStatus.FAILED
                self._release()
            raise

    def _message(self, message, data):
        if message.get('type') == 'error':
            self.script_error = {
                key: message[key]
                for key in ('description', 'stack', 'fileName', 'lineNumber', 'columnNumber')
                if key in message and (isinstance(message[key], str) if key in ('description', 'stack', 'fileName')
                                       else type(message[key]) is int)
            }
        super()._message(message, data)

    def _runtime_cancelled(self):
        self._expect_detach()
        if self.status != ObserverStatus.STOPPED:
            self.error = 'PC Frida observer cancelled'
            self.status = ObserverStatus.FAILED
            self.ready.set()
            self._wake_request()

    def _probe_source(self):
        self.runtime._check_cancelled()
        return super()._probe_source()

    def _attach_session(self, frida):
        record = self.runtime.wait_ready(self.request_timeout)
        process = self.collector.process
        if (process.process_instance != (record['pid'], record['created'])
                or process.pid != record['pid'] or not process.process_alive()
                or not self.runtime._module_present(record)):
            raise RuntimeFailure('process_changed', 'PC Frida lease does not match the connected collector process')
        self.runtime._reserve_observer(self, record)
        self._reserved = True
        try:
            with self.runtime._rpc(frida):
                _, device, _ = self.runtime._connect(frida, record)
                self._detach_intent = SimpleNamespace(intentional=False)
                # Own the returned session before the cancellation checkpoint
                # on RPC-scope exit can reject publishing the attachment.
                self.session = device.attach(process.pid, realm='native')
                return self.session
        except Exception:
            self.runtime.report_transport_failure()
            raise

    @property
    def _detached(self):
        # The base observer registers this callback on its current physical
        # session. Capture that session's intent, so delayed callbacks cannot
        # affect a subsequently attached session on the same observer object.
        intent = self._detach_intent
        return lambda reason, *args: self._session_detached(intent, reason, *args)

    def _session_detached(self, intent, reason, *args):
        if (intent is None or intent is not self._detach_intent or intent.intentional
                or self.status == ObserverStatus.STOPPED):
            # A retired physical session must not alter the next session's
            # error, ready signal, pending request or authentication generation.
            return
        super()._detached(reason, *args)
        self.runtime.report_transport_failure()

    def _expect_detach(self):
        if self._detach_intent is not None:
            self._detach_intent.intentional = True

    def stop(self):
        # exports.stop may itself cause a detach before the base finally block
        # changes ObserverStatus. Intent must precede all owned shutdown work.
        intent = self._detach_intent
        self._expect_detach()
        try:
            return super().stop()
        finally:
            if intent is self._detach_intent and self.status == ObserverStatus.STOPPED:
                self.ready.set()
                self._wake_request()

    def _release(self):
        self._expect_detach()
        try:
            super()._release()
        finally:
            if self._reserved and self.script is None and self.session is None:
                self.runtime._release_observer(self)
                self._reserved = False


