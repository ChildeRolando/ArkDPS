"""Production source + real QuickJS/native owned provider; explicit cached stage only."""
import os
from pathlib import Path
import pytest
from run_production_hp_quickjs import run

def test_actual_hp_fused_quickjs_semantics(tmp_path):
    stage=os.environ.get('ARKDPS_NATIVE_QJS_STAGE')
    if not stage:
        pytest.skip('set ARKDPS_NATIVE_QJS_STAGE to verified existing build cache; no download')
    report=run(Path(stage),tmp_path,100)
    rows={r['branch']:r for r in report['results']}
    assert rows['unchanged']['sequence_delta']==0
    assert rows['changed_damage']['event_kinds']=={'enemy_hp':100}
    assert rows['heal_coalesced']['event_kinds']=={'enemy_hp':2}
    assert rows['maxhp_dirty']['dirty']==1
    for branch,row in rows.items():
        if branch=='input_only_baseline':
            continue
        assert row['native_profile']['edges']==200
        assert row['native_profile']['point_calls']>0
        assert row['native_profile']['scalar_calls']>0
    assert report['readiness'].startswith('NOT_FULL_WORKER_ACCEPTANCE')
