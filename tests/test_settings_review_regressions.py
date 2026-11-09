"""ARK-11 review: cleanup ownership and fresh, trustworthy restore evidence."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from collection_session import CollectionSession
from event_observer import FridaEventObserver
from layout_store import LayoutStore
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'acceptance'))
import verify_settings as verify


@pytest.mark.parametrize('stage', ['startup', 'new_battle'])
def test_retired_observer_handles_are_retried_on_close(stage):
    failures = {'unload', 'detach'}
    calls = []

    def release(name):
        calls.append(name)
        if name in failures:
            raise RuntimeError(name + ' refused')

    collector = SimpleNamespace(close=lambda: calls.append('collector'))
    old = FridaEventObserver(collector, 'unused')
    script = SimpleNamespace(is_destroyed=False, unload=lambda: release('unload'))
    native = SimpleNamespace(is_detached=False, detach=lambda: release('detach'))
    old.script, old.session = script, native

    def start():
        # Frida start itself already attempts release before reporting failure.
        old._release()
        raise RuntimeError('attach failed')

    old.start = start
    replacement = SimpleNamespace(start=lambda: None, stop=lambda: calls.append('replacement'))
    factory = lambda *_: old if stage == 'startup' else replacement
    current = CollectionSession('adb', 'serial', collector=collector, observer_factory=factory)
    if stage == 'startup':
        current._attach_observer()
        assert current.event_status == 'failed'
        assert current.observer is None  # snapshot-only consumers see no failed observer
    else:
        current.observer = old
        current.event_status = 'gap'
        current._observer_generation = 1
        retried, active = current._prepare_battle_observer(SimpleNamespace(generation=2, events={}))
        assert retried and active is replacement
    failures.clear()
    current.close()
    assert old.script is old.session is None
    assert calls.count('unload') >= 2 and calls.count('detach') >= 2
    assert calls[-1] == 'collector'
    # Successful retries do not erase the historical cleanup failure.
    assert current.cleanup_errors.get('observer')


@pytest.mark.parametrize('payload', ['{broken', '{"version": 99, "panels": []}'])
def test_backup_failure_protects_original_even_after_lock_clears(tmp_path, monkeypatch, payload):
    path = tmp_path / 'layout.json'
    path.write_text(payload, encoding='utf-8')
    store = LayoutStore(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', lambda *_: (_ for _ in ()).throw(OSError('locked')))
        loaded = store.load()
    assert not loaded.ok
    assert '备份失败' in loaded.warning
    with pytest.raises(OSError, match='备份失败'):
        store.save(loaded.layout)
    assert path.read_text(encoding='utf-8') == payload
    assert not list(tmp_path.glob('.layout-*'))


@pytest.mark.parametrize('returncode', [0, 1])
def test_phase_main_does_not_accept_stale_restore_receipt(tmp_path, monkeypatch, returncode):
    child_path = tmp_path / 'restore-receipt.json'
    child_path.write_text(json.dumps({'status': 'PASS'}), encoding='utf-8')
    root = SimpleNamespace(destroy=lambda: None)

    class FakeApp:
        def __init__(self, *_args, **_kwargs):
            # Stop before driving real windows; still exercise real restart block.
            self.panels = []
            self.stop = SimpleNamespace(set=lambda: None)

    monkeypatch.setattr(verify.tk, 'Tk', lambda: root)
    monkeypatch.setattr(verify, 'App', FakeApp)
    monkeypatch.setattr(verify, 'enable_dpi_awareness', lambda: True)
    monkeypatch.setattr(verify, 'settle', lambda *_: None)
    monkeypatch.setattr(verify, 'monitors', lambda: [])
    monkeypatch.setattr(verify.subprocess, 'check_output', lambda *_a, **_k: 'candidate-sha')
    monkeypatch.setattr(verify.subprocess, 'run', lambda *_a, **_k: SimpleNamespace(
        returncode=returncode, stdout='', stderr='restore failed before receipt'))
    assert verify.phase_main(tmp_path) == 1
    receipt = json.loads((tmp_path / 'receipt.json').read_text(encoding='utf-8'))
    assert receipt['restart']['status'] == 'FAIL'


@pytest.mark.parametrize('outcome', ['pass', 'nonzero', 'wrong_run', 'wrong_head',
                                     'wrong_phase', 'fail', 'invalid', 'missing', 'timeout'])
def test_restore_accepts_only_fresh_success_for_current_candidate(tmp_path, monkeypatch, outcome):
    child_path = tmp_path / 'restore-receipt.json'
    child_path.write_text('{"status": "PASS"}', encoding='utf-8')

    def run(command, **kwargs):
        assert not child_path.exists()  # deletion precedes process launch
        run_id = command[command.index('--run-id') + 1]
        if outcome == 'timeout':
            raise verify.subprocess.TimeoutExpired(command, 300)
        result = dict(status='PASS', head='candidate', phase='restore', run_id=run_id)
        if outcome == 'wrong_run':
            result['run_id'] = 'previous-run'
        elif outcome == 'wrong_head':
            result['head'] = 'old-candidate'
        elif outcome == 'wrong_phase':
            result['phase'] = 'main'
        elif outcome == 'fail':
            result['status'] = 'FAIL'
        if outcome != 'missing':
            child_path.write_text('{not-json' if outcome == 'invalid' else json.dumps(result),
                                  encoding='utf-8')
        return SimpleNamespace(returncode=7 if outcome == 'nonzero' else 0, stderr='child error')

    monkeypatch.setattr(verify.subprocess, 'run', run)
    result = verify.run_restore(tmp_path / 'state', tmp_path, 'candidate')
    assert (result['status'] == 'PASS') == (outcome == 'pass')
    if outcome == 'nonzero':
        assert result['returncode'] == 7


def test_dpi_awareness_is_checked_before_creating_tk(monkeypatch):
    calls = []
    monkeypatch.setattr(verify, 'enable_dpi_awareness', lambda: calls.append('dpi') or True)
    monkeypatch.setattr(verify.tk, 'Tk', lambda: calls.append('tk') or 'root')
    assert verify.create_root() == 'root'
    assert calls == ['dpi', 'tk']
    monkeypatch.setattr(verify, 'enable_dpi_awareness', lambda: False)
    with pytest.raises(RuntimeError, match='DPI awareness'):
        verify.create_root()
    assert calls == ['dpi', 'tk']


@pytest.mark.parametrize('already_aware', [True, False])
def test_failed_dpi_api_call_requires_effective_awareness(monkeypatch, already_aware):
    import ctypes
    import app

    def get_context():
        return -4

    user = SimpleNamespace(SetProcessDpiAwarenessContext=lambda *_: 0,
                           GetThreadDpiAwarenessContext=get_context,
                           AreDpiAwarenessContextsEqual=lambda *_: already_aware)
    monkeypatch.setattr(app.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=user), raising=False)
    assert app.enable_dpi_awareness() is already_aware


def test_pending_observers_survive_repeated_close_failures_without_starving_other_cleanup():
    failures = {0, 1}
    calls = []
    current = CollectionSession('adb', 'serial', collector=SimpleNamespace(
        close=lambda: calls.append('collector')))
    observers = []
    for number in range(2):
        def stop(number=number):
            calls.append(number)
            if number in failures:
                raise RuntimeError(f'detach {number} failed')
        observer = SimpleNamespace(stop=stop)
        observers.append(observer)
        current.observer = observer
        current._retire_observer()
    current.observer = SimpleNamespace(stop=lambda: calls.append('active'))
    with pytest.raises(RuntimeError):
        current.close()
    assert calls[-4:] == [0, 1, 'active', 'collector']
    assert current._pending_observers == observers
    failures.clear()
    current.close()
    assert calls[-4:] == [0, 1, 'active', 'collector']
    assert not current._pending_observers
