"""Source filtering uses the real JS producer and bounded owned-memory data."""
import json
from pathlib import Path
import subprocess


def test_native_autochess_collection_preference_and_conservative_source_filter():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ['node', str(root / 'tests/native_pc_autochess_collection_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['status'] == 'verified'
    assert len(result['checks']) == 11
    assert [(row['mode'], row['retained']) for row in result['counts']] == [('all', 8), ('local', 2)]
