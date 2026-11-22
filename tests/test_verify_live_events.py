import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    'verify_live_events', Path(__file__).resolve().parents[1] / 'acceptance' / 'verify_live_events.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def evidence(directory):
    source, owner = 'token_cathy', 'char_cathy'
    row = {'sources': [source], 'observed_damage': 0, 'observed_healing': 0,
           'shield_absorbed': 2, 'counter_snapshot_complete': False}
    first = {'session': 'battle', 'frame': 1, 'game_time': 0, 'rows': [], 'event_status': 'recording',
             'event_watermark': {'sequence': 0}}
    last = {'session': 'battle', 'frame': 2, 'game_time': 1, 'event_status': 'recording',
            'event_watermark': {'sequence': 2}, 'statistics': {'rows': {owner: row}}, 'rows': []}
    events = [{'sequence': 1, 'frame': 2, 'game_time': 1, 'source': source,
               'kind': 'shield_absorbed', 'amount': 2,
               'proof': {'buff': 'catsld_t_1[core]', 'before': 2, 'after': 0, 'incoming': 5}},
              {'sequence': 2, 'frame': 2, 'game_time': 1, 'source': source,
               'kind': 'shield_absorbed', 'amount': 0,
               'proof': {'buff': 'catsld_t_1[core]', 'before': 0, 'after': 0, 'incoming': 5}}]
    records = [{'kind': 'published', 'data': first},
               {'kind': 'drain', 'data': {'events': events}},
               {'kind': 'published', 'data': last}]
    (directory / 'summary.json').write_text(json.dumps({'error': None, 'head': 'candidate'}))
    path = directory / 'capture.jsonl'
    path.write_text('\n'.join(json.dumps(record) for record in records))
    return records, path


def test_product_event_proofs_reconcile_partial_zero_and_summon_owner(tmp_path):
    evidence(tmp_path)
    result = module.verify(tmp_path, source_owners=['token_cathy=char_cathy'],
                           require_partial=True, require_zero=True)
    assert result['events'] == 2 and result['shield_totals']['char_cathy'] == 2
    assert result['event_counts'] == {'shield_absorbed': 2}
    assert result['partial_break_sequences'] == [1] and result['zero_absorption_sequences'] == [2]


def test_missing_raw_event_fails_reconciliation(tmp_path):
    records, path = evidence(tmp_path)
    records[1]['data']['events'].pop()
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='missing'):
        module.verify(tmp_path)


def test_failed_run_can_only_be_reconciled_as_an_incomplete_prefix(tmp_path):
    evidence(tmp_path)
    (tmp_path / 'summary.json').write_text(json.dumps({'error': 'unstable clock', 'head': 'candidate'}))
    with pytest.raises(ValueError, match='failure'):
        module.verify(tmp_path)
    result = module.verify(tmp_path, allow_incomplete=True)
    assert result['events'] == 2 and result['complete'] is False
    assert result['capture_error'] == 'unstable clock'


def test_event_and_published_totals_cannot_hide_a_native_delta_mismatch(tmp_path):
    records, path = evidence(tmp_path)
    first, last = records[0]['data'], records[2]['data']
    first['rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    last['rows'] = [{'id': 'token_cathy', 'damage': 120, 'healing': 0}]
    records[1]['data']['events'][0].update(kind='damage', amount=19)
    row = last['statistics']['rows']['char_cathy']
    row.update(observed_damage=19, shield_absorbed=0)
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='amount mismatch'):
        module.verify(tmp_path)


def test_lifecycle_envelope_uses_native_sources_and_preserves_raw_proofs(tmp_path):
    records, path = evidence(tmp_path)
    first, last = records[0]['data'], records[2]['data']
    first['native_rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    last['native_rows'] = [{'id': 'token_cathy', 'damage': 100, 'healing': 0}]
    # GUI rows are an owner projection and cannot substitute for source totals.
    first['rows'] = last['rows'] = [{'id': 'char_cathy', 'damage': 999}]
    events = records[1]['data']
    records[1] = {'kind': 'lifecycle_observation', 'data': {
        'generation': 1, 'events': {'drain': events}}}
    path.write_text('\n'.join(json.dumps(record) for record in records))
    assert module.verify(tmp_path)['shield_totals']['char_cathy'] == 2
    last['native_rows'][0]['damage'] = 110
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='amount mismatch'):
        module.verify(tmp_path)


def test_enemy_entries_reconcile_without_becoming_operator_damage(tmp_path):
    records, path = evidence(tmp_path)
    last = records[2]['data']
    last['event_watermark']['sequence'] = 3
    enemy = {'sequence': 3, 'frame': 2, 'game_time': 1, 'source': None,
             'kind': 'enemy_enter', 'amount': 0, 'target': 'enemy_a',
             'target_instance': '12', 'enemy_entry_attributes': {'max_hp': 300, 'def': 0}}
    records[1]['data']['events'].extend([enemy, dict(enemy)])
    last['statistics']['enemy_entry'] = {
        'complete': True,
        'start_frame': 1,
        'events': [{'instance_uid': '12', 'eid': 'enemy_a', 'frame': 2, 'game_time': 1,
                    'enemy_entry_attributes': {'max_hp': 300, 'def': 0}}],
    }
    path.write_text('\n'.join(json.dumps(record) for record in records))
    result = module.verify(tmp_path)
    assert result['enemy_entry_count'] == 1
    assert result['enemy_entry_complete'] is True
    assert result['shield_totals'] == {'char_cathy': 2}

    records[1]['data']['events'][-1]['sequence'] = 4
    last['event_watermark']['sequence'] = 4
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='enemy entry was emitted more than once'):
        module.verify(tmp_path)

    records[1]['data']['events'].pop()
    last['event_watermark']['sequence'] = 3
    last['statistics']['enemy_entry']['events'].clear()
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='published enemy entries differ'):
        module.verify(tmp_path)


def test_enemy_phases_reconcile_without_becoming_operator_damage(tmp_path):
    records, path = evidence(tmp_path)
    last = records[2]['data']
    last['event_watermark']['sequence'] = 3
    phase = {'sequence': 3, 'frame': 2, 'game_time': 1, 'source': None,
             'kind': 'enemy_reborn', 'amount': 60_000, 'target': 'enemy_1506_patrt',
             'target_instance': '12', 'hp_before': 0, 'hp_after': 60_000,
             'phase_total_hp': 120_000, 'phase_count': 2, 'phase_from_birth': True}
    records[1]['data']['events'].append(phase)
    last['statistics']['enemy_phases'] = {
        'complete': True,
        'instances': {'12': {'eid': 'enemy_1506_patrt', 'frame': 2, 'game_time': 1,
                             'phase_total_hp': 120_000, 'phase_count': 2,
                             'phase_from_birth': True}},
    }
    path.write_text('\n'.join(json.dumps(record) for record in records))
    result = module.verify(tmp_path)
    assert result['enemy_phases'] == last['statistics']['enemy_phases']
    assert result['shield_totals'] == {'char_cathy': 2}

    phase['amount'] = 70_000
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='amount mismatch'):
        module.verify(tmp_path)


def test_enemy_hp_and_finish_reconcile_as_ordered_enemy_state(tmp_path):
    records, path = evidence(tmp_path)
    last = records[2]['data']
    last['event_watermark']['sequence'] = 5
    raw_events = records[1]['data']['events']
    common = dict(frame=2, game_time=1, source=None, target='enemy_a', target_instance='12', amount=0)
    raw_events.extend([
        dict(common, sequence=3, kind='enemy_hp', hp_before=50, hp_after=50, enemy_max_hp=100),
        dict(common, sequence=4, kind='enemy_fake_death', hp_after=0),
        dict(common, sequence=5, kind='enemy_finish', hp_after=0, finish_reason=2),
    ])
    shared = dict(frame=2, game_time=1, eid='enemy_a', instance_uid='12', hp_after=0)
    projected = [
        dict(shared, sequence=3, kind='enemy_hp', hp_before=50, hp_after=50, enemy_max_hp=100),
        dict(shared, sequence=4, kind='enemy_fake_death'),
        dict(shared, sequence=5, kind='enemy_finish', finish_reason=2),
    ]
    last['statistics']['enemy_state'] = {'complete': True, 'start_frame': 1, 'events': projected}
    path.write_text('\n'.join(json.dumps(record) for record in records))
    result = module.verify(tmp_path)
    assert result['enemy_state_complete'] and result['enemy_state_events'] == 3
    assert result['shield_totals'] == {'char_cathy': 2}

    projected[-1]['instance_uid'] = '13'
    path.write_text('\n'.join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match='published enemy state differs'):
        module.verify(tmp_path)



@pytest.mark.parametrize('value', [False, -10, 0, float('inf'), float('nan')])
def test_verifier_rejects_invalid_enemy_max_hp(value):
    event = dict(sequence=1, kind='enemy_hp', frame=2, game_time=1, source=None, amount=0,
                 target='enemy_a', target_instance='12', hp_after=10, enemy_max_hp=value)
    with pytest.raises(ValueError, match='invalid enemy max HP'):
        module.enemy_state_event(event)


def pc_capture(directory, *, with_events=True):
    """Publish through the production session with controlled collector/observer inputs."""
    from dataclasses import replace
    from types import SimpleNamespace
    from capture_cli import json_value
    from collection_session import CollectionSession
    from native_pc.probe_native_pc import KNOWN_FILES
    from native_pc_collector import BUILD_ID
    from test_lifecycle_events import native_connection

    runtime, collector, observer, snapshot, state, producer, _, _ = native_connection()
    details = dict(fixed_module_bytes_match=True, build=dict(known_build=True, files=[
        dict(file=name, sha256=digest, sha256_matches=True) for name, digest in KNOWN_FILES.items()]))
    collector.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True,
                                            version_actual=BUILD_ID, details=details)
    collector.connect = lambda **_: None
    collector.close = collector.locate_lifecycle = observer.start = observer.stop = lambda: None
    original_lifecycle, original_roster = collector.read_lifecycle, snapshot.read_roster
    def lifecycle():
        life = original_lifecycle()
        life['metadata']['source_build'] = BUILD_ID
        return life
    collector.read_lifecycle = lifecycle
    snapshot.read_roster = lambda: replace(original_roster(), local_player_side=0)
    original_mark, original_read = observer.mark, observer.read_through
    observer.mark = lambda: dict(original_mark(), source_build=BUILD_ID)
    observer.read_through = lambda seq: dict(original_read(seq), source_build=BUILD_ID,
        partial_damage_version=7, source_attribution_version=1, damage_settlement_ready=True,
        damage_settlement_unknown=False)
    base = dict(frame=2, game_time=1, source='char_a', source_character_uid=1)
    skill = dict(skill_key='skill_1', amount=1)
    events = [
        dict(base, sequence=1, kind='skill_start', **skill, proof={'method': 'Character.SwitchToSkillState'}),
        dict(base, sequence=2, kind='damage', amount=13, target='enemy_a', damage_type=1, target_type=0,
             real_delta=-8, hp_before=8, hp_after=0, shield_absorbed=1, killed=True),
        dict(base, sequence=3, kind='damage_settlement', amount=8, settlement_complete=True,
             target='enemy_a', target_uid='21', damage_type=1, shield_absorbed=1, nominal_amount=13, killed=True),
        dict(base, sequence=4, kind='healing', amount=4),
        dict(base, sequence=5, kind='shield_absorbed', amount=3,
             proof=dict(buff='shining_s_2', before=5, after=2, incoming=3)),
        dict(base, frame=3, game_time=2, sequence=6, kind='skill_end', **skill,
             proof={'method': 'Character.SwitchOutFromSkillState'}),
    ]
    records = []
    def audit(kind, data):
        records.append(json.loads(json.dumps(dict(kind=kind, data=data), default=json_value)))
    session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                observer_factory=lambda *_: observer, audit=audit)
    session.enable_lifecycle(runtime)
    session.connect()
    try:
        session.read()
        snapshot.frame, snapshot.game_time = 3, 2
        if with_events:
            snapshot.damage, state.healing = 13, 4
            producer.queue.extend(events)
        session.read()
    finally:
        session.close()
    summary = dict(platform='native_pc', pid=10, process_instance=[10, 1234], game_version=BUILD_ID,
                   head='test-candidate', error=None)
    (directory / 'summary.json').write_text(json.dumps(summary))
    return records


def test_pc_four_families_are_reconciled_with_separate_damage_bases(tmp_path):
    records = pc_capture(tmp_path)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)))
    receipt = module.verify(tmp_path, pc_settlements=True)
    audit = receipt['native_pc_settlements']
    assert receipt['native_counter_deltas']['char_a']['damage']['native_delta'] == 13
    assert audit['settled_hp'] == {'char_a': 8} and audit['settled_enemy_shield'] == {'char_a': 1}
    assert receipt['shield_totals'] == {'char_a': 3}
    assert audit['absorbed_by_source'] == {'char_a': 3}
    assert audit['paired_skill_windows'] == {'char_a': 1} and audit['skill_seconds'] == {'char_a': 1}


def test_continuous_empty_pc_window_cannot_attest_to_settlement(tmp_path):
    records = pc_capture(tmp_path, with_events=False)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)))
    with pytest.raises(ValueError, match='no positive native PC damage'):
        module.verify(tmp_path, pc_settlements=True)


@pytest.mark.parametrize('fault', ['unbound', 'coverage', 'watermark', 'empty', 'heal', 'shield', 'hp',
                                   'targetless', 'snapshot', 'skill'])
def test_pc_four_family_receipt_requires_bound_positive_witnesses(tmp_path, fault):
    records = pc_capture(tmp_path)
    publication = [r['data'] for r in records if r['kind'] == 'published'][-1]
    obs = [r['data'] for r in records if r['kind'] == 'lifecycle_observation' and
           (r['data'].get('events') or {}).get('drain')][-1]
    drain = obs['events']['drain']
    events = drain['events']
    if fault == 'unbound':
        drain['native_session'] = 'another-battle'
    elif fault == 'coverage':
        del drain['shield_unknown']
    elif fault == 'watermark':
        obs['events']['before']['epoch'] += 1
    elif fault == 'empty':
        events.clear()
    elif fault == 'heal':
        events[3]['amount'] = 0
    elif fault == 'shield':
        events[4]['amount'] = 0
    elif fault == 'hp':
        publication['statistics']['rows']['char_a']['settled_damage']['known_hp_damage'] += 1
    elif fault == 'targetless':
        del events[2]['target_uid']
    elif fault == 'snapshot':
        publication.update(event_watermark=None, event_status='unavailable')
    else:
        events[-1]['skill_key'] = 'different-skill'
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)))
    with pytest.raises(ValueError):
        module.verify(tmp_path, pc_settlements=True)
