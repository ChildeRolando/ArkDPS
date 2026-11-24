"""Execute the real Windows loader logic against bounded WinAPI functions.

No real process is opened and no native code is executed. Unlike the runtime
sequence fakes, these tests exercise PSAPI enumeration, preparation, UTF-16
path ownership, thread submission, result capture and post-load verification.
"""
import ctypes
import hashlib
import json
import math
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


def test_loader_diagnostics_preserve_real_success_facts_without_paths_or_mock_budget_changes(api, tmp_path):
    api.gadget = tmp_path / '\u6587\u6863-\U0001f680' / module.GADGET_NAME
    loader = module.WindowsGadgetLoader(42)
    loader.load(api.gadget)
    try:
        before = loader.diagnostics()
        assert before['pid'] == 42 and before['created_filetime'] == api.birth
        assert before['verification_alive'] is True and before['process_exit_code'] == 259
        assert before['module_query_attempted'] is before['module_query_succeeded'] is before['module_found'] is True
        assert before['module_base'] == hex(api.gadget_base)
        assert before['module_base_low32'] == api.gadget_base and before['return_matches'] is True
        encoded = str(api.gadget).encode('utf-16-le')
        assert before['path_utf16_units'] == len(encoded) // 2
        assert before['path_utf16_bytes'] == len(encoded)
        assert before['path_buffer_bytes'] == len(encoded) + 2
        assert before['loader_module_basename'] == 'KernelBase.dll' and before['loader_export_rva'] == 0x1234
        assert api.clock == 0.0  # Observation timing is not the loader's policy clock.
        assert str(api.gadget) not in json.dumps(before) and str(tmp_path) not in json.dumps(before)
        assert all(type(value) is float and math.isfinite(value) and value >= 0
                   for key, value in before['timing_seconds'].items() if key != 'closed')
    finally:
        loader.close()
    after = loader.diagnostics()
    assert after['handle_closed'] and after['timing_seconds']['closed'] is not None
    assert {key: value for key, value in after.items() if key not in ('handle_closed', 'timing_seconds')} == {
        key: value for key, value in before.items() if key not in ('handle_closed', 'timing_seconds')}
    assert before['timing_seconds']['closed'] is None  # A detached diagnostic snapshot.


@pytest.mark.parametrize('found', [False, True])
def test_all_ones_thread_status_never_becomes_win32_error_or_false_success(api, found):
    api.result = 0xffffffff
    if not found:
        submit = api.kernel.CreateRemoteThread.call
        def missing_module(*arguments):
            result = submit(*arguments)
            api.loaded = False
            return result
        api.kernel.CreateRemoteThread.call = missing_module
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(module.RuntimeFailure) as error:
            loader.load(api.gadget)
        assert error.value.code == ('loader_result_mismatch' if found else 'loader_failed')
        diagnostic = loader.diagnostics()
        assert diagnostic['thread_exit_code'] == 0xffffffff
        assert diagnostic['verification_alive'] is True and diagnostic['process_exit_code'] == 259
        assert diagnostic['module_query_attempted'] is diagnostic['module_query_succeeded'] is True
        assert diagnostic['module_found'] is found
        assert diagnostic['return_matches'] is (False if found else None)
        assert diagnostic['module_base'] == (hex(api.gadget_base) if found else None)
        assert 'not a Win32 error' in str(error.value)
        assert api.count('submit') == 1
    finally:
        loader.close()
    closed = loader.diagnostics()
    assert closed['handle_closed'] and closed['thread_exit_code'] == 0xffffffff
    for key in ('verification_alive', 'process_exit_code', 'module_query_attempted',
                'module_query_succeeded', 'module_found', 'module_base', 'module_base_low32', 'return_matches'):
        assert closed[key] == diagnostic[key]


def test_process_exited_before_verification_is_not_reported_as_missing_module(api):
    submit = api.kernel.CreateRemoteThread.call
    def exited(*arguments):
        result = submit(*arguments)
        api.alive, api.result, api.memory = False, 0xffffffff, None
        return result
    api.kernel.CreateRemoteThread.call = exited
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(module.RuntimeFailure) as error:
            loader.load(api.gadget)
        assert error.value.code == 'loader_process_exited'
        diagnostic = loader.diagnostics()
        assert diagnostic['verification_alive'] is False and diagnostic['process_exit_code'] == 0
        assert diagnostic['module_query_attempted'] is False
        assert diagnostic['module_query_succeeded'] is diagnostic['module_found'] is diagnostic['return_matches'] is None
        assert diagnostic['thread_exit_code'] == 0xffffffff
    finally:
        loader.close()


@pytest.mark.parametrize('transient', [False, True])
def test_module_query_error_stays_unknown_not_false_even_if_retried(api, transient):
    api.fail_enum_after, api.enum_error = 1, 299 if transient else 5
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(TimeoutError if transient else module.RuntimeFailure):
            loader.load(api.gadget)
        diagnostic = loader.diagnostics()
        assert diagnostic['module_query_attempted'] is True and diagnostic['module_query_succeeded'] is False
        assert diagnostic['module_found'] is diagnostic['module_base'] is diagnostic['return_matches'] is None
        if transient:
            assert loader.poll() and loader.load_error is None
            assert loader.diagnostics()['module_found'] is loader.diagnostics()['return_matches'] is True
        else:
            assert loader.load_error.code == 'loader_verification_failed'
        assert api.count('submit') == 1
    finally:
        loader.close()


def test_failed_process_exit_read_remains_none_without_fabricating_a_process_result(api):
    exit_code = api.exit_code
    def unreadable(handle):
        if api.loaded and api.memory is None:
            raise WinError(5)
        return exit_code(handle)
    api.exit_code = unreadable
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(module.RuntimeFailure) as error:
            loader.load(api.gadget)
        assert error.value.code == 'loader_verification_failed'
        diagnostic = loader.diagnostics()
        assert diagnostic['verification_alive'] is diagnostic['process_exit_code'] is None
        assert diagnostic['module_query_attempted'] is False
        assert diagnostic['module_query_succeeded'] is diagnostic['module_found'] is None
    finally:
        loader.close()


def test_full_x64_module_base_and_thread_low32_are_separate_facts(api):
    api.gadget_base = 0x00007ffa87650000
    api.result = api.gadget_base & 0xffffffff
    loader = module.WindowsGadgetLoader(42)
    try:
        loader.load(api.gadget)
        diagnostic = loader.diagnostics()
        assert diagnostic['module_base'] == '0x7ffa87650000'
        assert diagnostic['module_base_low32'] == diagnostic['thread_exit_code'] == 0x87650000
        assert diagnostic['return_matches'] is True and loader.load_error is None
    finally:
        loader.close()


def test_changed_birth_is_not_a_fabricated_process_exit_code(api):
    submit = api.kernel.CreateRemoteThread.call
    def replaced(*arguments):
        result = submit(*arguments)
        api.process_times = lambda handle: (api.birth + 1, 0)
        api.memory = None
        return result
    api.kernel.CreateRemoteThread.call = replaced
    loader = module.WindowsGadgetLoader(42)
    try:
        with pytest.raises(module.RuntimeFailure) as error:
            loader.load(api.gadget)
        assert error.value.code == 'process_changed'
        diagnostic = loader.diagnostics()
        assert diagnostic['verification_alive'] is False and diagnostic['process_exit_code'] is None
        assert diagnostic['module_query_attempted'] is False and diagnostic['module_found'] is None
    finally:
        loader.close()


@pytest.mark.parametrize('exit_code', [0, 1, 0xffffffff])
def test_confirmed_exit_requires_owned_birth_and_real_typed_exit_code_without_new_handles(api, exit_code):
    loader = module.WindowsGadgetLoader(42)
    reads = []
    original_times = api.process_times
    api.process_times = lambda handle: reads.append(('times', handle)) or original_times(handle)
    api.exit_code = lambda handle: reads.append(('exit', handle)) or exit_code
    opened = list(api.opened)
    try:
        assert loader.confirmed_process_exit((42, 100)) is True
        assert [op for op, _ in reads] == ['times', 'exit', 'times']
        assert all(handle == loader.handle for _, handle in reads) and api.opened == opened
        assert not loader.submitted and not api.count('submit')
    finally:
        loader.close()


@pytest.mark.parametrize('problem', ['active', 'unknown_exit', 'boolean_exit', 'negative_exit', 'large_exit',
    'first_read_error', 'second_read_error', 'before_birth', 'after_birth', 'wrong_pid', 'wrong_birth',
    'missing_handle', 'invalid_expected', 'boolean_expected', 'swapped_handle'])
def test_unconfirmed_exit_is_never_inferred_from_missing_identity_or_failed_queries(api, problem):
    loader = module.WindowsGadgetLoader(42)
    handle, expected = loader.handle, (42, 100)
    calls = [0]
    original_times = api.process_times
    def times(owned):
        calls[0] += 1
        if problem == 'first_read_error' and calls[0] == 1 or problem == 'second_read_error' and calls[0] == 2:
            raise WinError(5)
        if problem == 'before_birth' and calls[0] == 1 or problem == 'after_birth' and calls[0] == 2:
            return 101, 0
        return original_times(owned)
    api.process_times = times
    def exit_code(owned):
        if problem == 'swapped_handle':
            loader.handle = (43, 99, 100)
        return dict(active=259, unknown_exit=None, boolean_exit=False, negative_exit=-1,
                    large_exit=0x100000000).get(problem, 0)
    api.exit_code = exit_code
    if problem == 'wrong_pid':
        expected = (43, 100)
    elif problem == 'wrong_birth':
        expected = (42, 101)
    elif problem == 'missing_handle':
        loader.handle = None
    elif problem == 'invalid_expected':
        expected = None
    elif problem == 'boolean_expected':
        expected = (42, True)
    try:
        assert loader.confirmed_process_exit(expected) is False
        assert not loader.submitted and not api.count('submit')
    finally:
        loader.handle = handle
        loader.close()
