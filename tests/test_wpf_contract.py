"""Build/distribution and display contract checks that run without WPF or ADB."""
import ast
import importlib.util
from pathlib import Path
import re
import zipfile

import pytest
from desktop_launcher import worker_python

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_windows', ROOT/'tools/package_windows.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def test_pythonw_launcher_uses_console_python_with_redirectable_stdio():
    assert Path(worker_python('C:/Python/pythonw.exe')).name == 'python.exe'
    assert worker_python('C:/Python/python.exe') == str(Path('C:/Python/python.exe'))


def test_package_input_hash_mismatch_rejects_before_unpack(tmp_path):
    source = tmp_path/'python.zip'
    source.write_bytes(b'not a runtime')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        package.check(source, package.PYTHON_SHA256)


@pytest.mark.parametrize('member', ['../escape.py', '/absolute.py', 'nested/../../escape.py'])
def test_runtime_unpack_refuses_traversal(tmp_path, member):
    source = tmp_path/'input.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr(member, 'never execute me')
    with pytest.raises(ValueError, match='Unsafe archive'):
        package.unpack(source, tmp_path/'extract')
    assert not (tmp_path/'escape.py').exists()


def bridge_import_closure():
    """Repo-root modules a portable worker loads when it imports desktop_bridge."""
    pending, seen = ['desktop_bridge'], set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        tree = ast.parse((ROOT/(module + '.py')).read_text('utf-8'))
        for node in tree.body:  # module scope only: that is what an import runs
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or '']
            else:
                continue
            for name in names:
                top = name.split('.')[0]
                if (ROOT/(top + '.py')).is_file():
                    pending.append(top)
    return seen


def test_portable_collector_ships_every_module_the_worker_imports():
    # The portable package seeds collector/ from BACKEND alone and its embedded
    # interpreter cannot see the repository, so a missing repo-root module fails
    # the package smoke (`python -I -c "import desktop_bridge"`) and then the
    # installed worker. Derive the requirement from the imports themselves so a
    # new helper module cannot be forgotten here again.
    missing = sorted(name + '.py' for name in bridge_import_closure()
                     if name + '.py' not in package.BACKEND)
    assert missing == []
    for name in package.BACKEND:
        assert (ROOT/name).is_file(), name


def contrast(a, b):
    def luminance(color):
        values = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(v*w for v, w in zip(linear, (.2126, .7152, .0722)))
    x, y = sorted([luminance(a), luminance(b)])
    return (y+.05)/(x+.05)


def test_actual_wpf_theme_text_pairs_have_aa_contrast():
    source = (ROOT/'desktop/ArkDPS.Desktop/App.xaml.cs').read_text('utf-8')
    palettes = re.findall(r'\["Bg"\].*?\["Signal"\]="#[0-9A-F]+"', source, re.S)
    assert len(palettes) == 2
    for raw in palettes:
        colors = dict(re.findall(r'\["([A-Za-z]+)"\]="(#[0-9A-F]+)"', raw))
        for foreground, background in [('Ink','Bg'), ('Ink','Card'), ('Muted','Card'),
                ('Muted','Inset'), ('ConsoleInk','Console'), ('ConsoleMuted','Console'),
                ('ConsoleInk','ConsoleCard'), ('ConsoleMuted','ConsoleCard'), ('AccentText','Card'), ('AccentInk','Accent')]:
            assert contrast(colors[foreground], colors[background]) >= 4.5, (foreground, background, colors)


def test_ui_has_no_game_memory_or_adb_process_api():
    source = '\n'.join(p.read_text('utf-8') for p in (ROOT/'desktop/ArkDPS.Desktop').glob('*.cs'))
    for forbidden in ('ReadProcessMemory', 'WriteProcessMemory', 'OpenProcess(', 'adb.exe', 'Process.GetProcessesByName'):
        assert forbidden not in source
    assert 'desktop_bridge.py' in source


def test_legacy_ui_acceptance_aborts_before_tk_on_dpi_failure(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT/'acceptance'))
    import verify_ui_themes as verify
    monkeypatch.setattr(verify, 'enable_dpi_awareness', lambda: False)
    monkeypatch.setattr(verify.tk, 'Tk', lambda: pytest.fail('created Tk without DPI awareness'))
    with pytest.raises(RuntimeError, match='DPI awareness'):
        verify.run(tmp_path/'evidence', False)


def test_wpf_quit_is_bounded_and_never_modal():
    bridge = (ROOT/'desktop/ArkDPS.Desktop/BridgeClient.cs').read_text('utf-8')
    # WaitForExitAsync() also waits for EOF on the redirected stderr pipe, so a
    # surviving grandchild that inherited the handle can block it indefinitely.
    assert 'await process.WaitForExitAsync()' not in bridge
    assert 'WaitForExitBoundedAsync' in bridge and 'ExitSeconds' in bridge and 'QuitSeconds' in bridge
    window = (ROOT/'desktop/ArkDPS.Desktop/MainWindow.xaml.cs').read_text('utf-8')
    quit_body = window[window.index('internal async Task Quit'):]
    assert 'MessageBox' not in quit_body
    assert 'Task.WhenAny' in quit_body and 'WriteQuitLog' in quit_body


def test_history_page_entry_auto_refreshes_and_keeps_manual_button():
    window = (ROOT/'desktop/ArkDPS.Desktop/MainWindow.xaml.cs').read_text('utf-8')
    navigate = window[window.index('internal async Task Navigate'):]
    navigate = navigate[:navigate.index('private async void AddPanel')]
    assert 'history_refresh' in navigate
    xaml = (ROOT/'desktop/ArkDPS.Desktop/MainWindow.xaml').read_text('utf-8')
    assert 'Click="RefreshHistory"' in xaml


def test_engagement_unavailable_has_reason_and_recovery_entry_in_wpf():
    xaml = (ROOT/'desktop/ArkDPS.Desktop/StatisticPanel.xaml').read_text('utf-8')
    assert 'x:Name="RateNotice"' in xaml and 'x:Name="RateAction"' in xaml
    panel = (ROOT/'desktop/ArkDPS.Desktop/StatisticPanel.xaml.cs').read_text('utf-8')
    # The compact rate text now comes from the model, including '无接敌' and '—',
    # so the UI cannot append a unit to an unknown value.
    assert 'Entry.Text("rate_text")' in panel
    assert 'rate_display") + "/s"' not in panel
    for field in ('rate_reason', 'rate_action', 'rate_action_label', 'rate_note'):
        assert field in panel
    assert 'Rate.ToolTip' in panel and 'RateNoticePanel.Visibility' in panel
    inspector = (ROOT/'desktop/ArkDPS.Desktop/Inspector.cs').read_text('utf-8')
    assert 'rate_reason' in inspector and 'rate_hint' in inspector
    assert '未建立事件覆盖' in inspector and '不补零' in inspector
    window = (ROOT/'desktop/ArkDPS.Desktop/MainWindow.xaml.cs').read_text('utf-8')
    assert 'HandleRateAction' in window
    assert 'Command("rescan")' in window and 'Navigate("settings")' in window


def test_model_view_carries_explicit_event_rate_contract_fields():
    source = (ROOT/'panel_model.py').read_text('utf-8')
    for field in ('rate_available', 'rate_reason', 'rate_hint', 'rate_action',
                  'rate_action_label', 'rate_state', 'rate_text', 'rate_note'):
        assert field in source
    assert '不以 0 或墙钟替代' in source


def test_wpf_surfaces_event_derived_overkill_without_redefining_damage():
    inspector = (ROOT/'desktop/ArkDPS.Desktop/Inspector.cs').read_text('utf-8')
    screenshot = (ROOT/'desktop/ArkDPS.Desktop/Screenshot.cs').read_text('utf-8')
    assert '"伤害 · 含过量"' in inspector
    assert '过量伤害 · 事件派生' in inspector and 'overkill_damage' in inspector
    assert '总伤害含过量' in screenshot and 'overkill_damage' in screenshot


def test_wpf_user_facing_numbers_use_at_most_one_decimal():
    bridge = (ROOT/'desktop/ArkDPS.Desktop/BridgeClient.cs').read_text('utf-8')
    inspector = (ROOT/'desktop/ArkDPS.Desktop/Inspector.cs').read_text('utf-8')
    screenshot = (ROOT/'desktop/ArkDPS.Desktop/Screenshot.cs').read_text('utf-8')
    panel = (ROOT/'desktop/ArkDPS.Desktop/StatisticPanel.xaml.cs').read_text('utf-8')
    assert 'public static string Ui(' in bridge and 'ToString("0.#"' in bridge
    assert '.Exact(' not in inspector
    assert 'Json.UiText(entry.Text("exact_value"))' in screenshot
    assert '0.###' not in screenshot
    assert 'Entry.Exact("rate")' not in panel and 'Entry.Exact("share")' not in panel
    assert 'Json.UiText(Entry.Text("exact_value"))' in panel
