"""Demand contracts exercise actual producer/reducer source on synthetic owned memory."""
from pathlib import Path
import shutil
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('fixture,marker', [
    ('formula_demand_semantics_fixture.js', 'formula_demand'),
    ('formula_demand_consumer_contract_fixture.js', 'PASS_FORMULA_CONSUMER_CONTRACT'),
    ('damage_demand_semantics.js', 'damage_demand_owned_semantics'),
    ('damage_demand_owned_trace.js', 'missingParentRejected'),
])
def test_capture_demand_source_contracts(fixture, marker):
    if not shutil.which('node'):
        pytest.skip('Node.js is required for owned source fixtures')
    result = subprocess.run(['node', str(ROOT / 'tests' / fixture)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout
