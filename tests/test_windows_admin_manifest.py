"""Elevation and embedded manifest checks; no executable, UI or UAC is started."""
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

import desktop_launcher as launcher


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('arkdps_windows_manifest', ROOT / 'tools/windows_manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
SOURCE = (ROOT / 'desktop/ArkDPS.Desktop/app.manifest').read_bytes()


def test_source_manifest_requires_admin_and_preserves_dpi_and_long_paths():
    assert manifest.verify_manifest_xml(SOURCE) == dict(level='requireAdministrator', ui_access=False,
        dpi_awareness='PerMonitorV2', long_path_aware=True)
    project = ElementTree.parse(ROOT / 'desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj')
    assert project.findtext('./PropertyGroup/ApplicationManifest') == 'app.manifest'


def test_only_the_compiled_internal_candidate_runs_without_elevation():
    path = ROOT / 'desktop/ArkDPS.Desktop/app.internal-events.manifest'
    assert manifest.verify_manifest_xml(path.read_bytes(), internal_backend=True) == dict(
        level='asInvoker', ui_access=False, dpi_awareness='PerMonitorV2', long_path_aware=True)
    with pytest.raises(ValueError, match='requireAdministrator'):
        manifest.verify_manifest_xml(path.read_bytes())
    with pytest.raises(ValueError, match='asInvoker'):
        manifest.verify_manifest_xml(SOURCE, internal_backend=True)


@pytest.mark.parametrize('before,after', [
    (b'requireAdministrator', b'asInvoker'), (b'uiAccess="false"', b'uiAccess="true"'),
    (b'PerMonitorV2', b'System'), (b'>true</longPathAware>', b'>false</longPathAware>'),
])
def test_wrong_or_reverted_manifest_is_rejected(before, after):
    assert before in SOURCE
    with pytest.raises(ValueError):
        manifest.verify_manifest_xml(SOURCE.replace(before, after))


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def windows_error(code):
    error = OSError(code, 'controlled Windows API failure')
    error.winerror = code
    return error


@pytest.fixture
def resource_api(tmp_path, monkeypatch):
    """Real ctypes buffer behind the documented PE resource API boundary."""
    resource = ctypes.create_string_buffer(SOURCE)
    opened, closed, queries = [], [], []
    path = tmp_path / 'never-executed.exe'
    path.write_bytes(b'controlled PE resource API input')
    def open_image(name, handle, flags):
        opened.append((name, handle, flags))
        return 0x101
    def find(module, name, kind):
        queries.append((module, name, kind))
        return 0x202
    api = SimpleNamespace(
        LoadLibraryExW=Function(open_image), FindResourceW=Function(find),
        SizeofResource=Function(lambda *_: len(SOURCE)), LoadResource=Function(lambda *_: 0x303),
        LockResource=Function(lambda *_: ctypes.addressof(resource)),
        FreeLibrary=Function(lambda handle: closed.append(handle) or True))
    monkeypatch.setattr(manifest.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 1813, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', windows_error, raising=False)
    return SimpleNamespace(path=path, api=api, opened=opened, closed=closed, queries=queries)


def test_embedded_resource_is_read_as_data_and_freed_without_execution(resource_api):
    value = resource_api
    assert manifest.verify_executable_manifest(value.path)['level'] == 'requireAdministrator'
    assert value.opened == [(str(value.path.resolve()), None, 0x22)]
    assert value.queries == [(0x101, 1, 24)]
    assert value.closed == [0x101]


@pytest.mark.parametrize('failure', ['FindResourceW', 'LoadResource', 'LockResource', 'SizeofResource'])
def test_missing_or_invalid_pe_resource_fails_and_releases_mapping(resource_api, failure):
    value = resource_api
    setattr(value.api, failure, Function(lambda *_: 0))
    with pytest.raises((OSError, ValueError)):
        manifest.verify_executable_manifest(value.path)
    assert value.closed == [0x101]


def test_embedded_as_invoker_is_rejected_even_when_source_is_admin(resource_api):
    wrong = ctypes.create_string_buffer(SOURCE.replace(b'requireAdministrator', b'asInvoker'))
    resource_api.api.SizeofResource = Function(lambda *_: len(wrong.value))
    resource_api.api.LockResource = Function(lambda *_: ctypes.addressof(wrong))
    with pytest.raises(ValueError, match='requireAdministrator'):
        manifest.verify_executable_manifest(resource_api.path)
    assert resource_api.closed == [0x101]


def test_launcher_preserves_normal_process_and_exit_ownership(monkeypatch, tmp_path):
    commands = []
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=17)
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    monkeypatch.setattr(launcher, '_elevated_launch', lambda *_: pytest.fail('unnecessary UAC request'))
    args = ['ArkDPS.Desktop.exe', '--platform', 'native_pc']
    assert launcher.run_desktop(args, tmp_path) == 17
    assert commands == [(args, dict(cwd=tmp_path))]


def test_launcher_only_elevates_for_windows_740(monkeypatch, tmp_path):
    observed = []
    error = windows_error(740)
    def run(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    monkeypatch.setattr(launcher, '_elevated_launch', lambda *args: observed.append(args) or 23)
    args = ['ArkDPS.Desktop.exe', '--native-game-root', 'E:/游戏/明日方舟']
    assert launcher.run_desktop(args, tmp_path) == 23
    assert observed == [(args, tmp_path)]
    error.winerror = 5
    with pytest.raises(OSError) as failure:
        launcher.run_desktop(args, tmp_path)
    assert failure.value is error
    assert len(observed) == 1


@pytest.fixture
def shell_api(monkeypatch):
    starts, waits, closed = [], [], []
    def start(pointer):
        request = pointer._obj
        starts.append(dict(verb=request.lpVerb, file=request.lpFile, args=request.lpParameters,
                           directory=request.lpDirectory, mask=request.fMask))
        request.hProcess = 0x1234
        return True
    def exit_code(handle, pointer):
        pointer._obj.value = 31
        return True
    api = SimpleNamespace(ShellExecuteExW=Function(start),
        WaitForSingleObject=Function(lambda *args: waits.append(args) or 0),
        GetExitCodeProcess=Function(exit_code), CloseHandle=Function(lambda handle: closed.append(handle) or True))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 1223, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', windows_error, raising=False)
    return SimpleNamespace(api=api, starts=starts, waits=waits, closed=closed)


def test_elevation_preserves_unicode_and_quoted_argv_and_waits_for_own_handle(shell_api, tmp_path):
    command = [str(tmp_path / '中文 ArkDPS.exe'), '--repo', 'E:\\my repo\\',
               '--native-game-root', 'E:\\游戏\\明日 方舟', '--serial', 'value"quoted']
    assert launcher._elevated_launch(command, tmp_path) == 31
    assert shell_api.starts == [dict(verb='runas', file=command[0],
        args=launcher.subprocess.list2cmdline(command[1:]), directory=str(tmp_path), mask=0x140)]
    assert shell_api.waits == [(0x1234, 0xffffffff)]
    assert shell_api.closed == [0x1234]


def test_uac_cancel_does_not_report_a_started_process(shell_api, tmp_path):
    shell_api.api.ShellExecuteExW = Function(lambda *_: False)
    with pytest.raises(RuntimeError, match='已取消管理员权限请求'):
        launcher._elevated_launch(['never-started.exe'], tmp_path)
    assert shell_api.waits == shell_api.closed == []


@pytest.mark.parametrize('function', ['WaitForSingleObject', 'GetExitCodeProcess'])
def test_elevated_process_handle_is_closed_when_wait_or_exit_query_fails(shell_api, tmp_path, function):
    setattr(shell_api.api, function, Function(lambda *_: 0xffffffff if function == 'WaitForSingleObject' else False))
    with pytest.raises(OSError):
        launcher._elevated_launch(['controlled.exe'], tmp_path)
    assert shell_api.closed == [0x1234]


def test_acceptance_rejects_non_admin_without_triggering_elevation(monkeypatch):
    api = SimpleNamespace(IsUserAnAdmin=Function(lambda: False))
    monkeypatch.setattr(manifest.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_args, **_kwargs: api, raising=False)
    with pytest.raises(RuntimeError, match='administrator test shell'):
        manifest.require_admin_test_host()
    api.IsUserAnAdmin = Function(lambda: True)
    assert manifest.require_admin_test_host() is None
