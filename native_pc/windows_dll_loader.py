"""Ordinary Windows DLL loading with retained ownership on incomplete cleanup.

No instrumentation provider, privilege elevation or protection bypass lives here.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

from . import probe_native_pc as access

LOADER_PREPARATION_SECONDS = 10.0
LOADER_VERIFICATION_SECONDS = 1.0
LOADER_READ_RETRY_SECONDS = .01


class RuntimeFailure(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _transient_module_read(error):
    return ((isinstance(error, OSError) and getattr(error, 'winerror', None) in (24, 299))
            or isinstance(error, RuntimeFailure) and error.code == 'loader_not_ready')


def _bounded_module_read(read, *, cancelled, alive, timeout, timeout_code, label, on_error=None, clock=None):
    """Retry read-only initialization races without reissuing a Windows load."""
    clock = clock or time
    deadline = clock.monotonic() + timeout
    while True:
        if cancelled():
            raise InterruptedError('PC native observer startup cancelled')
        if not alive():
            raise RuntimeFailure('process_changed', 'PC process changed during module readiness checks')
        try:
            value = read()
            if cancelled():
                raise InterruptedError('PC native observer startup cancelled')
            if not alive():
                raise RuntimeFailure('process_changed', 'PC process changed before its module read completed')
            return value
        except (OSError, RuntimeFailure) as error:
            retrying = _transient_module_read(error) and clock.monotonic() < deadline
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
            clock.sleep(LOADER_READ_RETRY_SECONDS)


class WindowsDllLoader:
    """Keep the path allocation and loader thread until their use has ended."""

    def __init__(self, pid, *, access_rights=0x43A, clock=None):
        self._diagnostic_origin = time.perf_counter()
        self._timing_seconds = dict.fromkeys(('load_started', 'submitted', 'thread_completed',
            'verification_started', 'verification_completed', 'closed'))
        self._last_alive_exit_code = None
        self.verification_alive = self.process_exit_code = None
        self.module_query_attempted = False
        self.module_query_succeeded = self.module_found = None
        self.module_base = self.module_base_low32 = self.return_matches = None
        self.path_utf16_units = self.path_utf16_bytes = self.path_buffer_bytes = None
        self.loader_module_basename = self.loader_export_rva = None
        self.clock = clock or time
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
        self.on_submitted = None
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
        # Same short-circuit API order and liveness policy as before. Retain the
        # actual code when read; a skipped/failed read is not an invented zero.
        self._last_alive_exit_code = None
        if self.handle is None or self.api.process_times(self.handle)[0] != self.created_filetime:
            return False
        code = self.api.exit_code(self.handle)
        if type(code) is int and 0 <= code <= 0xffffffff:
            self._last_alive_exit_code = code
        return code == 259

    def _stamp(self, name):
        # Observation only: never use the policy/mock clock or change deadlines.
        self._timing_seconds[name] = max(0.0, time.perf_counter() - self._diagnostic_origin)

    def diagnostics(self):
        return {'stage': self.stage, 'operation': self.operation, 'check': self.check,
                'pid': self.pid, 'created_filetime': self.created_filetime,
                'submitted': self.submitted, 'thread_exit_code': self.thread_exit_code,
                'verification_complete': self.verification_complete,
                'preparation_retries': self.preparation_retries,
                'verification_retries': self.verification_retries,
                'last_error': dict(self.last_error) if self.last_error is not None else None,
                'verification_alive': self.verification_alive, 'process_exit_code': self.process_exit_code,
                'module_query_attempted': self.module_query_attempted,
                'module_query_succeeded': self.module_query_succeeded, 'module_found': self.module_found,
                'module_base': self.module_base, 'module_base_low32': self.module_base_low32,
                'return_matches': self.return_matches,
                'path_utf16_units': self.path_utf16_units, 'path_utf16_bytes': self.path_utf16_bytes,
                'path_buffer_bytes': self.path_buffer_bytes,
                'loader_module_basename': self.loader_module_basename, 'loader_export_rva': self.loader_export_rva,
                'timing_seconds': dict(self._timing_seconds),
                'handle_closed': self.handle is None}

    def _remember_error(self, error):
        self.last_error = {'stage': self.stage, 'operation': getattr(error, 'operation', self.operation),
                           'check': self.check, 'win32_error': getattr(error, 'winerror', None)}

    def _win_error(self, operation):
        self.operation = operation
        error = ctypes.WinError(ctypes.get_last_error())
        error.operation = operation
        self._remember_error(error)
        error.strerror += f' (PC native observer {self.stage}/{self.check or "-"}/{operation})'
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
        self.loader_module_basename = Path(path.value).name
        own = self.api.open_process(os.getpid(), 0x410)
        try:
            info = access.ModuleInfo()
            if not self.api.psapi.GetModuleInformation(own, owner, ctypes.byref(info), ctypes.sizeof(info)):
                self._win_error('GetModuleInformation(local)')
        finally:
            self.api.close(own)
        offset = int(address) - int(info.base)
        self.loader_export_rva = offset if 0 <= offset < info.size else None
        remote = self.find_module(path.value)
        if not 0 <= offset < info.size or remote is None or remote['size'] != info.size:
            raise RuntimeFailure('loader_not_ready', 'Matching public Windows loader module is not ready')
        return remote['base'] + offset

    def _prepare(self):
        self.stage = 'preparing'
        def read():
            self.check, self.operation = 'existing_component', 'find_module'
            if self.find_module(self.path) is not None:
                raise RuntimeFailure('existing_component', 'observer DLL is already loaded without this startup reference')
            self.check = 'loader_export'
            return self._loader_export()
        def failed(error, retrying):
            self._remember_error(error)
            if retrying:
                self.preparation_retries += 1
        return _bounded_module_read(read, cancelled=self.cancelled, alive=self.alive,
            timeout=LOADER_PREPARATION_SECONDS, timeout_code='loader_preparation_timeout',
            label='PC native observer Windows loader preparation', on_error=failed, clock=self.clock)

    def load(self, path):
        self._check_cancelled()
        if self.submitted:
            raise RuntimeFailure('loader_already_submitted', 'This process owner already submitted its Windows load')
        self.path = Path(path)
        self._stamp('load_started')
        encoded_path = str(self.path).encode('utf-16-le', errors='surrogatepass')
        self.path_utf16_bytes, self.path_utf16_units = len(encoded_path), len(encoded_path) // 2
        entry = self._prepare()
        self.before_mutation()
        self._check_cancelled()
        if not self.alive():
            raise RuntimeFailure('process_changed', 'PC process exited before Windows loader mutation')
        text = ctypes.create_unicode_buffer(str(self.path))
        self.path_buffer_bytes = ctypes.sizeof(text)
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
            raise RuntimeFailure('loader_failed', 'observer DLL path write was incomplete')
        def submit():
            self._check_cancelled()
            self.operation = 'CreateRemoteThread'
            thread = self.kernel.CreateRemoteThread(self.handle, None, 0, entry, self.path_address, 0, None)
            if not thread:
                self._win_error('CreateRemoteThread')
            self.pending_thread = thread
            self.submitted = True
            self.stage = 'submitted'
            self._stamp('submitted')
        self.commit(submit)
        if self.on_submitted is not None:
            self.on_submitted(self)
        if not self.poll():
            raise TimeoutError('Windows observer DLL loader is still completing')
        if self.load_error is not None:
            raise self.load_error

    def _check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('PC native observer startup cancelled')

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
            self._stamp('thread_completed')
            self.api.close(self.pending_thread)
            self.pending_thread = None
            self._verification_deadline = self.clock.monotonic() + LOADER_VERIFICATION_SECONDS
        self.stage, self.check = 'verifying', 'loaded_component'
        if self._timing_seconds['verification_started'] is None:
            self._stamp('verification_started')
        # The path is no longer in use once the Windows worker has returned.
        # Query failures after this point retain the result and only reverify.
        self.free_path()
        # These are the latest verification attempt's facts. Query errors and
        # an exited process must remain unknown, not become module_found=False.
        self.verification_alive = self.process_exit_code = None
        self.module_query_attempted = False
        self.module_query_succeeded = self.module_found = None
        self.module_base = self.module_base_low32 = self.return_matches = None
        try:
            self.operation = 'find_module'
            try:
                self.verification_alive = self.alive()
            finally:
                self.process_exit_code = self._last_alive_exit_code
            if self.verification_alive:
                self.module_query_attempted = True
                try:
                    self.module = self.find_module(self.path)
                except Exception:
                    self.module_query_succeeded = False
                    raise
                self.module_query_succeeded = True
                self.module_found = self.module is not None
                if self.module is not None:
                    self.module_base = hex(self.module['base'])
                    self.module_base_low32 = self.module['base'] & 0xffffffff
                    self.return_matches = self.module_base_low32 == self.thread_exit_code
            else:
                self.module = None
        except (OSError, RuntimeFailure) as error:
            self._remember_error(error)
            if self._read_not_ready(error) and self.alive():
                if self.clock.monotonic() < self._verification_deadline:
                    self.verification_retries += 1
                    return False
                self.load_error = RuntimeFailure('loader_verification_timeout',
                    f'PC native observer post-load verification timed out ({self.operation}, '
                    f'WinError {getattr(error, "winerror", None)}, Windows result {self.thread_exit_code:#x})')
            else:
                self.load_error = RuntimeFailure('loader_verification_failed',
                    f'PC native observer post-load verification failed ({self.operation}, '
                    f'WinError {getattr(error, "winerror", None)}, Windows result {self.thread_exit_code:#x})')
        else:
            if not self.verification_alive:
                exited = self.process_exit_code is not None and self.process_exit_code != 259
                self.load_error = RuntimeFailure('loader_process_exited' if exited else 'process_changed',
                    f'PC process was not alive at DLL verification (process status {self.process_exit_code}, '
                    f'thread status {self.thread_exit_code:#x}; not a Win32 error)')
            elif self.module is None:
                self.load_error = RuntimeFailure('loader_failed',
                    f'Observer DLL mapping was not confirmed (thread status {self.thread_exit_code:#x}; not a Win32 error)')
            elif not self.return_matches:
                self.load_error = RuntimeFailure('loader_result_mismatch',
                    f'Observer DLL was found but its module result did not match '
                    f'(thread status {self.thread_exit_code:#x}; not a Win32 error)')
        self.verification_complete = True
        self.stage = 'complete'
        self._stamp('verification_completed')
        return True

    def free_path(self):
        if self.path_address is None:
            return
        if self.pending_thread is not None:
            raise RuntimeFailure('cleanup_pending', 'observer DLL loader still owns its path buffer')
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
                    raise RuntimeFailure('cleanup_pending', 'observer DLL startup has not returned; cleanup remains owned')
            else:
                self.api.close(self.pending_thread)
                self.pending_thread = self.path_address = None
        self.free_path()
        self.api.close(self.handle)
        self.handle = None
        self._stamp('closed')
