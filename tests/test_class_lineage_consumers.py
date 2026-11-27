from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_source_lifecycle_and_presence_use_complete_class_lineage():
    result = subprocess.run(
        ['node', str(ROOT / 'tests/class_lineage_consumers_fixture.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_COMPLETE_CLASS_LINEAGE_CONSUMERS' in result.stdout
