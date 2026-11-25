"""Owned callback proof through both shared capture and real enemy projection."""
import json
from pathlib import Path
import subprocess

from event_capture import LiveEventCapture
from enemy_telemetry import EnemyTelemetry, NativeEnemyObservations
from test_enemy_lifecycle_stream import ingest


def test_real_r5_hp_fragment_compaction_preserves_shared_consumers():
    fixture = Path(__file__).with_name('event_probe_hp_performance_fixture.js')
    run = subprocess.run(['node', str(fixture)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    pairs = json.loads(run.stdout)['pairs']
    for pair in pairs:
        projections = []
        for mode in ('raw', 'compact'):
            batch = pair[mode]
            capture = LiveEventCapture(None)
            ingest(capture, 0, 0)
            result = ingest(capture, 1560, batch['events'][-1]['sequence'], *batch['events'])
            state = result['enemy_state']
            assert state['complete']
            telemetry = EnemyTelemetry(None)
            entities = [dict(instance_uid=int(e['target_instance']), enemy_key=e['target'],
                inited=True, borned=True, is_disappeared=False, native_object_present=True,
                hp=e['hp_after'], hp_status='available') for e in batch['events'] if e['kind'] == 'enemy_enter']
            seed = NativeEnemyObservations().observe({'enemies_status': 'available', 'enemies': {
                'scope': 'unit_manager_enemies_membership', 'collection_count': len(entities),
                'entities': entities}}, frame=0, game_time=0, catalog={})
            telemetry.sample_native_snapshot(seed, frame=0, game_time=0,
                enemy_state={'complete': True, 'start_frame': 0, 'events': []})
            view = telemetry.sample_native_snapshot(None, frame=1560, game_time=52,
                enemy_state=state)
            # Different raw sequence counts are expected. Business values and
            # unknown/coverage boundaries must be identical, not recomputed by
            # a copied test-only healing formula.
            projections.append({'rows': view['rows'], 'totals': view['totals'],
                                'complete': view['enemy_state_complete']})
        assert projections[0] == projections[1]
