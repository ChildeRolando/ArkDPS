from pathlib import Path
import subprocess


def test_canonical_event_store_is_the_only_retention_and_ack_owner():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(['node', str(root/'tests/event_store_owner_fixture.js')],
                         cwd=root, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout+run.stderr
    assert 'PASS_CANONICAL_EVENT_STORE' in run.stdout
