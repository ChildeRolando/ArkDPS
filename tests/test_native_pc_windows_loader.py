"""Execute the real Windows loader logic against bounded WinAPI functions.

No real process is opened and no native code is executed. Unlike the runtime
sequence fakes, these tests exercise PSAPI enumeration, preparation, UTF-16
path ownership, thread submission, result capture and post-load verification.
"""
import ctypes
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from native_pc import frida_runtime as module


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *arguments):
        return self.call(*arguments)


class WinError(OSError):
    def __init__(self, code):
        super().__init__(code, 'controlled Win32 failure')
        self.winerror = code


class Api:
    local_base, remote_base, gadget_base = 0x10000000, 0x70000000, 0x60000000
    allocation, thread = 0xA00000, 0xB00000

    def __init__(self, gadget):
        self.gadget = gadget
        self.system_path = str(gadget.parent / 'system' / 'KernelBase.dll')
        self.image = str(gadget.parent / 'game' / 'Arknights.exe')
        self.birth, self.alive = 100, True
        self.loaded = False
        self.signalled = True
        self.result = self.gadget_base
        self.last_error = 0
        self.fail_enum_before = self.fail_enum_after = 0
        self.fail_enum_calls = set()
        self.fail_filename_before = 0
        self.enum_error = 299
        self.calls, self.opened, self.closed = [], [], []
        self.memory = None
        self.clock = 0.0
        self.on_sleep = lambda: None
        self.kernel = SimpleNamespace(**{name: Function(method) for name, method in {
            'IsWow64Process2': self.architecture,
            'GetModuleHandleW': lambda _: self.local_base,
            'GetProcAddress': lambda *_: self.local_base + 0x1234,
            'GetModuleHandleExW': self.owner,
            'GetModuleFileNameW': self.owner_path,
            'VirtualAllocEx': self.allocate,
            'VirtualFreeEx': self.free,
            'WriteProcessMemory': self.write,
            'CreateRemoteThread': self.submit,
            'WaitForSingleObject': self.wait,
            'GetExitCodeThread': self.exit_code_thread,
        }.items()})
        self.psapi = SimpleNamespace(EnumProcessModulesEx=self.enumerate,
            GetModuleFileNameExW=self.module_path, GetModuleInformation=self.module_info)

    def open_process(self, pid, rights):
        handle = (pid, len(self.opened), self.birth)
        self.opened.append(handle)
        return handle

    def close(self, handle):
        assert handle not in self.closed
        self.closed.append(handle)
        self.calls.append(('close', handle))

    def process_times(self, handle):
        return handle[2], 0

    def exit_code(self, handle):
        return 259 if self.alive and handle[2] == self.birth else 0

    def image_path(self, handle):
        return self.image

    def architecture(self, handle, machine, native):
        machine._obj.value, native._obj.value = 0, 0x8664
        return True

    def owner(self, flags, address, result):
        assert flags == 6 and address == self.local_base + 0x1234
        result._obj.value = self.local_base
        return True

    def owner_path(self, handle, buffer, count):
        buffer.value = self.system_path
        return len(buffer.value)

    def enumerate(self, handle, modules, size, needed, flags):
        self.calls.append(('enumerate', self.loaded))
        if self.count('enumerate') in self.fail_enum_calls:
            self.last_error = self.enum_error
            return False
        counter = 'fail_enum_after' if self.loaded else 'fail_enum_before'
        if getattr(self, counter):
            setattr(self, counter, getattr(self, counter) - 1)
            self.last_error = self.enum_error
            return False
        bases = [self.remote_base] + ([self.gadget_base] if self.loaded else [])
        needed._obj.value = len(bases) * ctypes.sizeof(module.wintypes.HMODULE)
        for index, base in enumerate(bases):
            modules[index] = base
        return True

    def module_path(self, handle, base, buffer, size):
        if not self.loaded and self.fail_filename_before:
            self.fail_filename_before -= 1
            self.last_error = 299
            return 0
        buffer.value = self.system_path if base == self.remote_base else str(self.gadget)
        return len(buffer.value)

    def module_info(self, handle, base, result, size):
        result._obj.base = self.local_base if handle[0] == os.getpid() else int(base)
        result._obj.size = 0x100000
        return True

    def allocate(self, *arguments):
        self.calls.append(('allocate',))
        assert self.memory is None
        self.memory = b''
        return self.allocation

    def write(self, handle, address, value, length, written):
        assert address == self.allocation
        self.calls.append(('write',))
        self.memory = ctypes.string_at(value, length)
        written._obj.value = length
        return True

    def submit(self, handle, attributes, stack, address, argument, flags, thread_id):
        assert address == self.remote_base + 0x1234 and argument == self.allocation
        self.calls.append(('submit',))
        self.loaded = True
        return self.thread

    def wait(self, handle, duration):
        assert handle == self.thread and duration == 0
        return 0 if self.signalled else 258

    def exit_code_thread(self, handle, result):
        result._obj.value = self.result
        self.calls.append(('thread_result', self.result))
        return True

    def free(self, handle, address, size, flags):
        assert address == self.allocation and self.memory is not None
        self.calls.append(('free',))
        self.memory = None
        return True

    def sleep(self, duration):
        self.clock += duration
        self.on_sleep()

    def count(self, kind):
        return sum(call[0] == kind for call in self.calls)


@pytest.fixture
def api(tmp_path, monkeypatch):
    instance = Api(tmp_path / module.GADGET_NAME)
    monkeypatch.setattr(module.access, 'WindowsApi', lambda: instance)
    monkeypatch.setattr(module.ctypes, 'get_last_error', lambda: instance.last_error, raising=False)
    monkeypatch.setattr(module.ctypes, 'WinError', WinError, raising=False)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: instance.clock,
        time_ns=lambda: int(instance.clock * 1_000_000_000), sleep=instance.sleep))
    yield instance
    assert instance.memory is None, 'the UTF-16 path allocation lost its owner'
    assert all(handle in instance.closed for handle in instance.opened)


@pytest.mark.parametrize('failure', ['enum', 'filename', 'export_enum'])
def test_preparation_retries_real_module_reads_before_any_mutation(api, failure):
    if failure == 'enum':
        api.fail_enum_before = 1
    elif failure == 'filename':
        api.fail_filename_before = 1
    else:
        api.fail_enum_calls.add(2)
    def still_readonly():
        assert api.count('allocate') == api.count('submit') == 0
    api.on_sleep = still_readonly
    loader = module.WindowsGadgetLoader(42)
    try:
        loader.load(api.gadget)
        assert loader.submitted and loader.verification_complete and loader.load_error is None
        assert loader.preparation_retries == 1 and api.count('submit') == 1
        assert loader.thread_exit_code == api.result
        assert loader.diagnostics()['last_error']['win32_error'] == 299
    finally:
        loader.close()


def test_preparation_timeout_has_stage_api_and_no_allocations(api):
    api.fail_enum_before = 100000
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(module.RuntimeFailure) as failure:
            loader.load(api.gadget)
        assert failure.value.code == 'loader_preparation_timeout'
        assert loader.stage == 'preparing' and loader.operation == 'EnumProcessModulesEx'
        assert not loader.submitted and api.count('allocate') == api.count('submit') == 0
        assert loader.diagnostics()['last_error']['check'] == 'existing_component'
    finally:
        loader.close()


@pytest.mark.parametrize('interrupt', ['cancel', 'exit', 'replace'])
def test_cancel_or_process_exit_during_preparation_never_mutates(api, interrupt):
    api.fail_enum_before = 1
    loader = module.WindowsGadgetLoader(42)
    cancelled = [False]
    loader.cancelled = lambda: cancelled[0]
    def stop():
        cancelled[0] = interrupt == 'cancel'
        api.alive = interrupt != 'exit'
        if interrupt == 'replace':
            api.birth += 1
    api.on_sleep = stop
    try:
        with pytest.raises((InterruptedError, module.RuntimeFailure)) as failure:
            loader.load(api.gadget)
        if interrupt != 'cancel':
            assert failure.value.code == 'process_changed'
        assert not loader.submitted and api.count('allocate') == api.count('submit') == 0
    finally:
        loader.close()


def test_cancel_at_windows_commit_releases_only_its_path_without_submitting(api):
    loader = module.WindowsGadgetLoader(42)
    cancelled = [False]
    loader.cancelled = lambda: cancelled[0]
    def commit(operation):
        cancelled[0] = True
        operation()
    loader.commit = commit
    try:
        with pytest.raises(InterruptedError):
            loader.load(api.gadget)
        assert not loader.submitted and api.count('allocate') == api.count('write') == 1
        assert api.count('submit') == 0
    finally:
        loader.close()
    assert api.count('free') == 1


@pytest.mark.parametrize('boundary,expected_writes', [('VirtualAllocEx', 0), ('WriteProcessMemory', 1)])
def test_cancellation_after_allocation_or_path_write_prevents_next_mutation(api, boundary, expected_writes):
    loader = module.WindowsGadgetLoader(42)
    cancelled = [False]
    loader.cancelled = lambda: cancelled[0]
    function = getattr(api.kernel, boundary)
    original = function.call
    def finish_then_cancel(*arguments):
        result = original(*arguments)
        cancelled[0] = True
        return result
    function.call = finish_then_cancel
    try:
        with pytest.raises(InterruptedError):
            loader.load(api.gadget)
        assert not loader.submitted and loader.pending_thread is None
        assert api.count('allocate') == 1 and api.count('write') == expected_writes
        assert api.count('submit') == 0 and api.memory is not None
    finally:
        loader.close()
    assert api.count('free') == 1 and api.memory is None


def test_hard_preparation_errors_are_not_retried(api):
    api.fail_enum_before, api.enum_error = 1, 5
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(OSError) as failure:
            loader.load(api.gadget)
        assert failure.value.winerror == 5 and loader.preparation_retries == 0
        assert api.count('allocate') == api.count('submit') == 0
    finally:
        loader.close()


def test_post_commit_299_retains_result_and_only_retries_verification(api):
    api.fail_enum_after = 1
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(TimeoutError):
            loader.load(api.gadget)
        assert loader.stage == 'verifying' and loader.submitted
        assert loader.pending_thread is None and loader.thread_exit_code == api.result
        assert not loader.verification_complete and api.memory is None
        assert loader.poll() and loader.verification_complete and loader.load_error is None
        with pytest.raises(module.RuntimeFailure) as duplicate:
            loader.load(api.gadget)
        assert duplicate.value.code == 'loader_already_submitted'
        assert api.count('submit') == api.count('thread_result') == api.count('free') == 1
    finally:
        loader.close()


def test_verification_timeout_keeps_thread_result_without_replaying_load(api):
    api.fail_enum_after = 100000
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(TimeoutError):
            loader.load(api.gadget)
        api.clock += module.LOADER_VERIFICATION_SECONDS
        assert loader.poll()
        assert loader.load_error.code == 'loader_verification_timeout'
        assert loader.thread_exit_code == api.result and loader.submitted
        assert api.count('submit') == api.count('thread_result') == api.count('free') == 1
        assert loader.diagnostics()['last_error']['stage'] == 'verifying'
    finally:
        loader.close()


def test_pending_worker_and_verification_have_distinct_cleanup_ownership(api):
    api.signalled, api.fail_enum_after = False, 1
    loader = module.WindowsGadgetLoader(42)
    with pytest.raises(TimeoutError):
        loader.load(api.gadget)
    with pytest.raises(module.RuntimeFailure) as pending:
        loader.close()
    assert pending.value.code == 'cleanup_pending'
    assert loader.pending_thread == api.thread and loader.thread_exit_code is None
    assert api.memory is not None and api.thread not in api.closed
    api.signalled = True
    # Finishing the physical worker allows cleanup even if its module query
    # is temporarily unavailable. No verification result is fabricated.
    loader.close()
    assert loader.handle is None and loader.pending_thread is None
    assert loader.thread_exit_code == api.result and not loader.verification_complete
    assert api.memory is None and api.count('submit') == api.count('free') == 1


def runtime_for(api, tmp_path, monkeypatch):
    api.gadget.write_bytes(b'controlled bytes, no native execution')
    monkeypatch.setattr(module, 'GADGET_SHA256', hashlib.sha256(api.gadget.read_bytes()).hexdigest())
    return module.NativePcFridaRuntime(Path(api.image).parent, runtime_dir=tmp_path / 'state',
        gadget_path=api.gadget, api=api,
        build_check=lambda _: ({'known_build': True, 'source_build': module.access.BUILD_ID}, []),
        admin_check=lambda: True)


def test_real_windows_loader_and_runtime_retry_final_presence_then_reconnect_without_reloading(api, tmp_path, monkeypatch):
    runtime = runtime_for(api, tmp_path, monkeypatch)
    runtime._state(module.RuntimeState.STARTING)
    runtime._state(module.RuntimeState.WAITING_PROCESS)
    # Guard, loader-export, and owned post-thread verification succeed. The
    # separate Runtime presence query then sees the same initialization race.
    api.fail_enum_calls.add(4)
    try:
        runtime._load_once((42, api.birth))
        assert runtime.status == module.RuntimeState.AUTHENTICATING
        state = runtime.describe()
        assert state['module_query']['retries'] == 1
        assert state['loader']['submitted'] and state['loader']['thread_exit_code'] == api.result
        assert state['loader']['stage'] == 'complete' and state['loader']['handle_closed']
        assert api.count('submit') == api.count('thread_result') == api.count('free') == 1
    finally:
        runtime.close()
    # A new owner adopts only the validated lease and retries the same readonly
    # presence path. It must not issue another Windows load into this birth.
    second = runtime_for(api, tmp_path, monkeypatch)
    second._state(module.RuntimeState.STARTING)
    api.fail_enum_calls.add(api.count('enumerate') + 1)
    try:
        second._reconcile()
        assert second.status == module.RuntimeState.AUTHENTICATING
        assert second.describe()['module_query']['retries'] == 1
        assert api.count('submit') == 1
    finally:
        second.close()


@pytest.mark.parametrize('event', ['cancel', 'replace', 'hard_error'])
def test_runtime_presence_read_revalidates_birth_and_cancels_without_replay(api, tmp_path, monkeypatch, event):
    runtime = runtime_for(api, tmp_path, monkeypatch)
    runtime._state(module.RuntimeState.STARTING)
    record = {'pid': 42, 'created': api.birth}
    api.loaded = True
    api.fail_enum_after = 1
    if event == 'hard_error':
        api.enum_error = 5
    def interrupt():
        if event == 'cancel':
            runtime.cancel()
        elif event == 'replace':
            api.birth += 1
    api.on_sleep = interrupt
    try:
        with pytest.raises((InterruptedError, module.RuntimeFailure, OSError)) as failure:
            runtime._module_present(record)
        if event == 'cancel':
            assert isinstance(failure.value, InterruptedError)
        elif event == 'replace':
            assert failure.value.code == 'process_changed'
        else:
            assert failure.value.winerror == 5
        assert api.count('allocate') == api.count('write') == api.count('submit') == 0
    finally:
        runtime.close()
