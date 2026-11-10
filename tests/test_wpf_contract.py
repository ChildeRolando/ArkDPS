"""Build/distribution and display contract checks that run without WPF or ADB."""
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
