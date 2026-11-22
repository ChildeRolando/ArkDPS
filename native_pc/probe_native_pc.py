"""Foreground, bounded, ordinary Windows read-access experiment for native PC.

The output contains checks and error codes. Addresses, file paths, and memory
contents are kept out of the diagnostic. Run with the official client already
open and provide its installation directory with --game-root.
"""

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from datetime import datetime, timezone


KNOWN_FILES = {
    'Arknights.exe':
        '5f03c3c7260668b9929b5e0fa85087d318f2529e87efedc77769ef7a91615993',
    'GameAssembly.dll':
        'cf7956bf88a61986859ef55c026d231713e2d12cf69ffd9a19779ab491cf76ff',
    'Arknights_Data/il2cpp_data/Metadata/global-metadata.dat':
        'e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5',
}
BUILD_ID = 'win-x64:' + KNOWN_FILES['GameAssembly.dll']
BILIBILI_FILES = {
    'Arknights.exe':
        '31f3c6f12a2ebee1260cadab4d7858b5e3f7000fcfaec0054fd93dd4bb79f444',
    'GameAssembly.dll':
        '6654b76f8eb32993513ed3b5e861be8e028a61c9cf85b7a696167358e9732e94',
    'Arknights_Data/il2cpp_data/Metadata/global-metadata.dat':
        'e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5',
}
BILIBILI_BUILD_ID = 'win-x64:' + BILIBILI_FILES['GameAssembly.dll']
# Keep each three-file set intact. The Bilibili build uses the existing probe
# layouts for the compatibility test and still needs live capture validation.
KNOWN_BUILDS = {BUILD_ID: KNOWN_FILES, BILIBILI_BUILD_ID: BILIBILI_FILES}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
READ_ACCESS = 0x0400 | 0x0010  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
STILL_ACTIVE = 259
CODE_RVA = 0x744680  # BattleLogger._CreateVerboseRecorder in this exact build.
CODE_SIZE = 32


class ApiFailure(Exception):
    def __init__(self, operation, code, bytes_read=0):
        self.operation = operation
        self.code = code
        self.bytes_read = bytes_read
        super().__init__(operation)

    def diagnostic(self):
        return {'api': self.operation, 'win32_error': self.code}


def same_path(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def read_pe(path):
    """Return just the disk bytes used by the two fixed memory comparisons."""
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise ValueError('invalid_pe')
        pe_offset = struct.unpack_from('<I', header, 60)[0]
        stream.seek(pe_offset)
        coff = stream.read(24)
        if len(coff) != 24 or coff[:4] != b'PE\0\0':
            raise ValueError('invalid_pe')
        machine, sections = struct.unpack_from('<HH', coff, 4)
        optional_size = struct.unpack_from('<H', coff, 20)[0]
        stream.seek(pe_offset + 24 + optional_size)
        table = stream.read(sections * 40)
        if len(table) != sections * 40:
            raise ValueError('invalid_pe')
        code = None
        for index in range(sections):
            _, rva, raw_size, raw_offset = struct.unpack_from(
                '<4I', table, index * 40 + 8)
            if rva <= CODE_RVA and CODE_RVA + CODE_SIZE <= rva + raw_size:
                stream.seek(raw_offset + CODE_RVA - rva)
                code = stream.read(CODE_SIZE)
                break
        return machine, header, code


def verify_build(root, *, known_files=None):
    checks = []
    files = KNOWN_FILES if known_files is None else known_files
    for relative in files:
        check = {'file': relative}
        try:
            digest = hashlib.sha256()
            with (root / relative).open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            check['sha256'] = digest.hexdigest()
        except OSError as error:
            check['file_error'] = error.errno
        checks.append(check)
    source_build = None
    if known_files is None:
        observed = {check['file']: check.get('sha256') for check in checks}
        source_build = next((key for key, fingerprints in KNOWN_BUILDS.items()
                             if observed == fingerprints), None)
        files = KNOWN_BUILDS.get(source_build, KNOWN_FILES)
    for check in checks:
        check['sha256_matches'] = check.get('sha256') == files[check['file']]
    build = {'files': checks, 'known_build': all(c['sha256_matches'] for c in checks)}
    if not build['known_build']:
        return build, None
    exe_machine, _, _ = read_pe(root / 'Arknights.exe')
    machine, header, code = read_pe(root / 'GameAssembly.dll')
    build['amd64'] = exe_machine == machine == 0x8664
    if not build['amd64'] or code is None or len(code) != CODE_SIZE:
        build['known_build'] = False
        return build, None
    if source_build is not None:
        build['source_build'] = source_build
    return build, [('pe_header', 0, header), ('known_code', CODE_RVA, code)]


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260),
    ]


class ModuleInfo(ctypes.Structure):
    _fields_ = [
        ('base', ctypes.c_void_p), ('size', wintypes.DWORD),
        ('entry', ctypes.c_void_p),
    ]


class WindowsApi:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.psapi = ctypes.WinDLL('psapi', use_last_error=True)
        signatures = [
            (self.kernel.CreateToolhelp32Snapshot, [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.Process32FirstW, [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            (self.kernel.Process32NextW, [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            (self.kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel.QueryFullProcessImageNameW,
             [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
             wintypes.BOOL),
            (self.kernel.GetProcessTimes,
             [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
              ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
              ctypes.POINTER(wintypes.FILETIME)], wintypes.BOOL),
            (self.kernel.GetExitCodeProcess,
             [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel.ReadProcessMemory,
             [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
              ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            (self.psapi.EnumProcessModulesEx,
             [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
              ctypes.POINTER(wintypes.DWORD), wintypes.DWORD], wintypes.BOOL),
            (self.psapi.GetModuleFileNameExW,
             [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
            (self.psapi.GetModuleInformation,
             [wintypes.HANDLE, wintypes.HMODULE, ctypes.POINTER(ModuleInfo), wintypes.DWORD],
             wintypes.BOOL),
        ]
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def named_processes(self):
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ApiFailure('CreateToolhelp32Snapshot', ctypes.get_last_error())
        try:
            entry = ProcessEntry(dwSize=ctypes.sizeof(ProcessEntry))
            ok = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            if not ok:
                code = ctypes.get_last_error()
                if code != 18:
                    raise ApiFailure('Process32FirstW', code)
                return []
            result = []
            while ok:
                if entry.szExeFile.casefold() == 'arknights.exe':
                    result.append(entry.th32ProcessID)
                ok = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
            code = ctypes.get_last_error()
            if code != 18:  # ERROR_NO_MORE_FILES
                raise ApiFailure('Process32NextW', code)
            return result
        finally:
            self.close(snapshot)

    def open_process(self, pid, access):
        handle = self.kernel.OpenProcess(access, False, pid)
        if not handle:
            raise ApiFailure('OpenProcess', ctypes.get_last_error())
        return handle

    def close(self, handle):
        self.kernel.CloseHandle(handle)

    def image_path(self, handle):
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ApiFailure('QueryFullProcessImageNameW', ctypes.get_last_error())
        return buffer.value

    def process_times(self, handle):
        """Return creation and exit FILETIMEs for the owned process handle.

        The exit time is undefined while running and must not establish exit.
        """
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                           ctypes.byref(kernel), ctypes.byref(user)):
            raise ApiFailure('GetProcessTimes', ctypes.get_last_error())
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime,
                (exited.dwHighDateTime << 32) | exited.dwLowDateTime)

    def exit_code(self, handle):
        """Return the documented termination status without widening access.

        STILL_ACTIVE does not prove liveness if the target itself exited with
        reserved code 259. In that case a later read error remains an error.
        """
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ApiFailure('GetExitCodeProcess', ctypes.get_last_error())
        return code.value

    def modules(self, handle):
        # Fixed generous bound. An incomplete list is a failed experiment.
        modules = (wintypes.HMODULE * 4096)()
        needed = wintypes.DWORD()
        if not self.psapi.EnumProcessModulesEx(
                handle, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
            raise ApiFailure('EnumProcessModulesEx', ctypes.get_last_error())
        if needed.value > ctypes.sizeof(modules):
            raise ApiFailure('EnumProcessModulesEx', 234)  # ERROR_MORE_DATA
        if needed.value % ctypes.sizeof(wintypes.HMODULE):
            raise ApiFailure('EnumProcessModulesEx', 13)  # ERROR_INVALID_DATA
        result = []
        for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            path = ctypes.create_unicode_buffer(32768)
            count = self.psapi.GetModuleFileNameExW(handle, module, path, len(path))
            if not count:
                raise ApiFailure('GetModuleFileNameExW', ctypes.get_last_error())
            if count >= len(path):
                raise ApiFailure('GetModuleFileNameExW', 122)
            if Path(path.value).name.casefold() != 'gameassembly.dll':
                continue
            info = ModuleInfo()
            if not self.psapi.GetModuleInformation(handle, module, ctypes.byref(info), ctypes.sizeof(info)):
                raise ApiFailure('GetModuleInformation', ctypes.get_last_error())
            result.append((path.value, info.base, info.size))
        return result

    def read(self, handle, address, size):
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not self.kernel.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(count)):
            raise ApiFailure('ReadProcessMemory', ctypes.get_last_error(), count.value)
        if count.value != size:
            raise ApiFailure('ReadProcessMemory', 299, count.value)
        return buffer.raw


def probe(game_root, api=None, *, handle_only=False, build_check=None):
    root = Path(game_root)
    report = {
        'schema_version': 1,
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'pending',
        'read_access': {'attempted': False, 'opened': False, 'requested_access': READ_ACCESS},
        'memory': {'attempted': False, 'reads_completed': 0, 'samples': []},
    }
    report['build'], samples = (build_check or verify_build)(root)
    if samples is None:
        report['status'] = 'unsupported_build'
        return report
    if api is None:
        if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
            report['status'] = 'requires_windows_x64_python'
            return report
        api = WindowsApi()
    processes = {'named': 0, 'path_matched': 0, 'query_failures': []}
    report['process'] = processes
    try:
        candidates = api.named_processes()
    except ApiFailure as error:
        report['status'] = 'process_enumeration_failed'
        report['error'] = error.diagnostic()
        return report
    processes['named'] = len(candidates)
    matched = []
    for pid in candidates:
        query = None
        try:
            query = api.open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
            if same_path(api.image_path(query), root / 'Arknights.exe'):
                matched.append(pid)
        except ApiFailure as error:
            processes['query_failures'].append(error.diagnostic())
        finally:
            if query is not None:
                api.close(query)
    processes['path_matched'] = len(matched)
    if len(matched) != 1:
        report['status'] = 'ambiguous_process' if matched else 'no_matching_process'
        return report
    handle = None
    try:
        report['read_access']['attempted'] = True
        handle = api.open_process(matched[0], READ_ACCESS)
        report['read_access']['opened'] = True
        if not same_path(api.image_path(handle), root / 'Arknights.exe'):
            report['status'] = 'process_identity_changed'
            return report
        if handle_only:
            report['status'] = 'read_handle_available'
            return report
        modules = api.modules(handle)
        report['module'] = {'named': len(modules), 'path_matches': False}
        if len(modules) != 1:
            report['status'] = 'module_not_found' if not modules else 'ambiguous_module'
            return report
        path, base, size = modules[0]
        if not same_path(path, root / 'GameAssembly.dll'):
            report['status'] = 'module_path_mismatch'
            return report
        report['module']['path_matches'] = True
        for name, rva, expected in samples:
            if not base or rva + len(expected) > size:
                report['status'] = 'module_layout_mismatch'
                return report
            sample = {'name': name, 'bytes_requested': len(expected), 'bytes_read': 0}
            report['memory']['samples'].append(sample)
            report['memory']['attempted'] = True
            try:
                actual = api.read(handle, base + rva, len(expected))
            except ApiFailure as error:
                sample['bytes_read'] = error.bytes_read
                sample.update(error.diagnostic())
                report['status'] = 'memory_read_failed'
                return report
            sample['bytes_read'] = len(actual)
            sample['matches_file_bytes'] = actual == expected
            report['memory']['reads_completed'] += 1
            if actual != expected:
                report['status'] = 'memory_content_mismatch'
                return report
        report['status'] = 'read_gate_passed'
    except ApiFailure as error:
        report['status'] = 'access_denied' if handle is None and error.code == 5 else 'process_access_failed'
        report['error'] = error.diagnostic()
    finally:
        if handle is not None:
            api.close(handle)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--handle-only', action='store_true',
                        help='check and close the handle without any process memory reads')
    args = parser.parse_args(argv)
    try:
        report = probe(args.game_root, handle_only=args.handle_only)
    except Exception as error:
        # Exception messages may contain a personal path. Retain only the type.
        report = {'schema_version': 1, 'status': 'probe_error', 'error_type': type(error).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] in ('read_gate_passed', 'read_handle_available') else 2


if __name__ == '__main__':
    raise SystemExit(main())
