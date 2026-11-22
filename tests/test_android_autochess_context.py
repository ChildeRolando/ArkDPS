"""Real shared JS producer with separately reflected, non-PC Android layouts."""
import json
from pathlib import Path
import subprocess


def test_android_autochess_typed_metadata_scope_and_mode_contract():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ['node', str(root / 'tests/android_autochess_context_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['status'] == 'verified'
    assert len(result['checks']) == 9
