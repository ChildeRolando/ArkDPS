"""Validate the desktop's actual RT_MANIFEST without starting its executable."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
from xml.etree import ElementTree


def verify_manifest_xml(data):
    root = ElementTree.fromstring(data)
    namespace = {'v3': 'urn:schemas-microsoft-com:asm.v3'}
    levels = root.findall('v3:trustInfo/v3:security/v3:requestedPrivileges/v3:requestedExecutionLevel', namespace)
    if len(levels) != 1 or levels[0].attrib != {'level': 'requireAdministrator', 'uiAccess': 'false'}:
        raise ValueError('ArkDPS manifest must requireAdministrator with uiAccess=false')
    settings = root.find('v3:application/v3:windowsSettings', namespace)
    if settings is None:
        raise ValueError('ArkDPS manifest is missing Windows settings')
    dpi = settings.findall('{http://schemas.microsoft.com/SMI/2016/WindowsSettings}dpiAwareness')
    paths = settings.findall('{http://schemas.microsoft.com/SMI/2016/WindowsSettings}longPathAware')
    if len(dpi) != 1 or dpi[0].text != 'PerMonitorV2' or len(paths) != 1 or paths[0].text != 'true':
        raise ValueError('ArkDPS manifest must preserve PerMonitorV2 and longPathAware')
    return dict(level='requireAdministrator', ui_access=False, dpi_awareness='PerMonitorV2', long_path_aware=True)


def read_executable_manifest(path):
    if sys.platform != 'win32':
        raise RuntimeError('Reading Windows RT_MANIFEST requires Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    api.LoadLibraryExW.restype = wintypes.HMODULE
    api.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    api.FindResourceW.restype = wintypes.HANDLE
    api.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    api.SizeofResource.restype = wintypes.DWORD
    api.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    api.LoadResource.restype = wintypes.HANDLE
    api.LockResource.argtypes = [wintypes.HANDLE]
    api.LockResource.restype = ctypes.c_void_p
    api.FreeLibrary.argtypes = [wintypes.HMODULE]
    api.FreeLibrary.restype = wintypes.BOOL
    # DATAFILE | IMAGE_RESOURCE maps resources only: no imports, EXE entry point
    # or UAC prompt. RT_MANIFEST=24, CREATEPROCESS_MANIFEST_RESOURCE_ID=1.
    module = api.LoadLibraryExW(str(Path(path).resolve(strict=True)), None, 0x2 | 0x20)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        resource = api.FindResourceW(module, 1, 24)
        if not resource:
            raise ctypes.WinError(ctypes.get_last_error())
        size = api.SizeofResource(module, resource)
        if not 0 < size <= 1024 * 1024:
            raise ValueError('Invalid RT_MANIFEST resource size')
        loaded = api.LoadResource(module, resource)
        if not loaded:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = api.LockResource(loaded)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(pointer, size)
    finally:
        if not api.FreeLibrary(module):
            raise ctypes.WinError(ctypes.get_last_error())


def verify_executable_manifest(path):
    return verify_manifest_xml(read_executable_manifest(path))


def require_admin_test_host():
    """Acceptance must inherit elevation; it must never prompt during a test."""
    if sys.platform != 'win32':
        raise RuntimeError('Native Windows acceptance requires Windows')
    api = ctypes.WinDLL('shell32', use_last_error=True)
    api.IsUserAnAdmin.argtypes = []
    api.IsUserAnAdmin.restype = wintypes.BOOL
    if not api.IsUserAnAdmin():
        raise RuntimeError('ArkDPS desktop acceptance requires an administrator test shell; no UI was started')
