"""Headless capture does not consume GUI-only layout recovery diagnostics."""
import json
from types import SimpleNamespace

import pytest

import app
import capture_cli
from layout_store import LayoutStore


@pytest.mark.parametrize('contents', ['{broken json', json.dumps({'version': 999})])
@pytest.mark.parametrize('event_args', [[], ['--no-events'], ['--frida-remote', 'host:1234']])
def test_capture_leaves_bad_layout_for_next_gui_diagnostic(tmp_path, monkeypatch,
                                                          contents, event_args):
    path = tmp_path / 'layout.json'
    path.write_text(contents, encoding='utf-8')
    store = LayoutStore(path)
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    # Headless capture must never touch GUI-only layout state; inject a guard
    # even though the WPF-only entry no longer defines it.
    monkeypatch.setattr(app, 'LayoutStore',
                        lambda: pytest.fail('capture loaded GUI layout'), raising=False)
    monkeypatch.setattr(app.sys, 'argv', ['app.py', '--capture-seconds', '1', *event_args])
    monkeypatch.setattr(app.socket, 'socket', lambda: SimpleNamespace(
        bind=lambda address: None, listen=lambda count: None))
    captured = []
    monkeypatch.setattr(capture_cli, 'run_capture', captured.append)
    app.main()
    assert len(captured) == 1
    assert captured[0].frida_remote == (None if event_args == ['--no-events'] else
                                         'host:1234' if event_args else
                                         app.DEFAULT_FRIDA_REMOTE)
    assert path.exists() and path.read_text(encoding='utf-8') == contents
    assert not list(tmp_path.glob('layout.json.corrupt-*'))
    # The next GUI load still diagnoses and backs up the original bad file.
    loaded = store.load()
    assert loaded.warning and not loaded.ok
    assert len(loaded.layout.panels) == 1
    assert list(tmp_path.glob('layout.json.corrupt-*'))
