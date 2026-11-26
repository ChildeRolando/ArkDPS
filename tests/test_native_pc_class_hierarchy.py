import subprocess
from pathlib import Path

def test_complete_class_hierarchy():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "tests/native_pc_class_hierarchy_fixture.js"], cwd=root, capture_output=True, text=True, check=True)
    assert "PASS_CLASS_HIERARCHY" in result.stdout
