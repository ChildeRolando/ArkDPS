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
        if row['native_profile'].get('cpp_io_counters_available',True):
            assert row['native_profile']['point_calls']>0
            assert row['native_profile']['scalar_calls']>0
        else:
            assert row['native_profile']['point_calls'] is None
            assert row['native_profile']['scalar_calls'] is None
            # Routed requests only; no assertion pretends to count native lease.
            assert sum(row['native_request_counts'].values())>0
    assert report['readiness'].startswith('NOT_FULL_WORKER_ACCEPTANCE')

def test_prepared_full_scope_publication_and_owned_queue(tmp_path):
    stage=os.environ.get('ARKDPS_NATIVE_QJS_STAGE')
    if not stage:
        pytest.skip('requires existing verified native build cache; no download')
    report=run(Path(stage),tmp_path,32,full_scope=True,pub_every=8571,queue_rate=1000,pause_ms=0)
    queue=report['queue']
    assert queue['failure_code']==0
    assert queue['reserved_records']==queue['consumed_records']==64
    assert queue['raw_prefix_contiguous'] is True
    assert queue['entry_compact_min']>128
    assert queue['leave_compact_max']>6000
    assert queue['max_queue_age_ms']>=0
