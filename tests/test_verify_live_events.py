import importlib.util
from copy import deepcopy
import json
from pathlib import Path

import pytest

from native_pc.probe_native_pc import BUILD_ID, BILIBILI_BUILD_ID


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


def test_friendly_damage_is_retained_but_not_compared_as_outgoing_counter_damage(tmp_path):
    records, path = evidence(tmp_path)
    first, last = records[0]['data'], records[2]['data']
    first['native_rows'] = last['native_rows'] = [dict(id='token_cathy', damage=100, healing=0)]
    records[1]['data']['events'].append(dict(sequence=3, frame=2, game_time=1, source='token_cathy',
        kind='damage', amount=10, target='char_cathy', damage_type=3))
    last['event_watermark']['sequence'] = 3
    path.write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    result = module.verify(tmp_path)
    assert result['events'] == 3
    assert result['native_counter_deltas']['token_cathy']['damage'] == dict(native_delta=0, observed_events=0)
    assert result['excluded_friendly_damage'] == dict(count=1, amount=10, sequences=[3])
    assert result['pc_settlement_mechanisms_verified'] is False


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


def pc_capture(directory, *, with_events=True, source_build=None):
    """Publish through the production session with controlled collector/observer inputs."""
    from dataclasses import replace
    from types import SimpleNamespace
    from capture_cli import json_value
    from collection_session import CollectionSession
    from native_pc.probe_native_pc import BUILD_ID, KNOWN_BUILDS

    source_build = source_build or BUILD_ID
    from test_lifecycle_events import native_connection

    runtime, collector, observer, snapshot, state, producer, _, _ = native_connection()
    details = dict(fixed_module_bytes_match=True, build=dict(known_build=True, files=[
        dict(file=name, sha256=digest, sha256_matches=True) for name, digest in KNOWN_BUILDS[source_build].items()]))
    collector.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True,
                                            version_actual=source_build, details=details)
    collector.connect = lambda **_: None
    collector.close = collector.locate_lifecycle = observer.start = observer.stop = lambda: None
    original_lifecycle, original_roster = collector.read_lifecycle, snapshot.read_roster
    def lifecycle():
        life = original_lifecycle()
        life['metadata']['source_build'] = source_build
        return life
    collector.read_lifecycle = lifecycle
    snapshot.read_roster = lambda: replace(original_roster(), local_player_side=0)
    original_mark, original_read = observer.mark, observer.read_through
    observer.mark = lambda: dict(original_mark(), source_build=source_build)
    observer.read_through = lambda seq: dict(original_read(seq), source_build=source_build,
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
    summary = dict(platform='native_pc', pid=10, process_instance=[10, 1234], game_version=source_build,
                   head='test-candidate', error=None)
    (directory / 'summary.json').write_text(json.dumps(summary))
    return records


@pytest.mark.parametrize('source_build', [BUILD_ID, BILIBILI_BUILD_ID])
def test_pc_four_families_are_reconciled_with_separate_damage_bases(tmp_path, source_build):
    records = pc_capture(tmp_path, source_build=source_build)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)))
    receipt = module.verify(tmp_path, pc_settlements=True)
    audit = receipt['native_pc_settlements']
    assert receipt['native_counter_deltas']['char_a']['damage']['native_delta'] == 13
    assert audit['settled_hp'] == {'char_a': 8} and audit['settled_enemy_shield'] == {'char_a': 1}
    assert receipt['shield_totals'] == {'char_a': 3}
    assert audit['absorbed_by_source'] == {'char_a': 3}
    assert audit['paired_skill_windows'] == {'char_a': 1} and audit['skill_seconds'] == {'char_a': 1}
    assert receipt['event_prefix_from_initial_boundary_complete'] is False
    assert receipt['native_cumulative_from_zero_complete'] is False


@pytest.mark.parametrize('source_build', [BUILD_ID, BILIBILI_BUILD_ID])
@pytest.mark.parametrize('fault', ['unknown_build', 'invalid_build', 'diagnostics', 'files', 'sample', 'batch'])
def test_pc_receipt_rejects_unknown_or_mixed_registered_builds(tmp_path, source_build, fault):
    from native_pc.probe_native_pc import KNOWN_BUILDS

    records = pc_capture(tmp_path, source_build=source_build)
    other = BILIBILI_BUILD_ID if source_build == BUILD_ID else BUILD_ID
    connection = next(r['data'] for r in records if r['kind'] == 'connected')
    observation = next(r['data'] for r in records if r['kind'] == 'lifecycle_observation'
                       and (r['data'].get('events') or {}).get('drain') is not None)
    if fault in ('unknown_build', 'invalid_build'):
        summary = json.loads((tmp_path / 'summary.json').read_text())
        summary['game_version'] = 'win-x64:unknown' if fault == 'unknown_build' else []
        (tmp_path / 'summary.json').write_text(json.dumps(summary))
    elif fault == 'diagnostics':
        connection['diagnostics']['version_actual'] = other
    elif fault == 'files':
        connection['diagnostics']['details']['build']['files'] = [
            dict(file=name, sha256=digest, sha256_matches=True)
            for name, digest in KNOWN_BUILDS[other].items()]
    elif fault == 'sample':
        observation['sample']['battle_metadata']['source_build'] = other
    else:
        observation['events']['drain']['source_build'] = other
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)))
    with pytest.raises(ValueError):
        module.verify(tmp_path, pc_settlements=True)


def test_basic_current_pc_record_verification_does_not_claim_strict_settlement_mechanisms(tmp_path):
    records = pc_capture(tmp_path)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    result = module.verify(tmp_path)
    assert result['events'] == 6
    assert result['verification_scope'] == 'single_capture_published_counter_window'
    assert result['pc_settlement_mechanisms_verified'] is False
    assert 'native_pc_settlements' not in result


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


def mechanism_events():
    base = dict(frame=3, game_time=2, source='char_a', source_character_uid=1,
                target='enemy_a', target_instance='0x6000')
    proof = dict(target_uid='21', parent_real_delta=0, parent_hp_before=0, parent_hp_after=0,
                 child_hp_before=20, child_hp_after=0, child_real_delta=-20,
                 ordering='child_before_parent_zero', parent_entry_hp_before=20)
    result = [
        dict(sequence=1, frame=2, game_time=1, source='char_a', source_character_uid=1,
             kind='shield_absorbed', amount=3, proof=dict(buff='shining_s_2', before=5, after=2, incoming=3)),
        dict(base, sequence=2, kind='elemental_bar', amount=10, target_uid='21',
             proof=dict(method='Entity.ApplyModifier', type=0, target=5)),
        dict(base, sequence=3, kind='damage_settlement', amount=20, target_uid='21', damage_type=5,
             shield_absorbed=0, settlement_complete=True, proof=dict(method='Entity.ApplyModifier')),
        dict(base, sequence=4, kind='elemental_burst', amount=30, real_delta=-20, hp_before=20, hp_after=0,
             target_type=0, damage_type=5, cancel_reason=0, killed=True, shield_absorbed=0,
             proof=dict(method='Entity.ApplyModifier', type=5, target=0)),
        dict(base, sequence=5, kind='damage_settlement', amount=0, target_uid='21', damage_type=1,
             shield_absorbed=0, settlement_complete=True, settled_after_nested_kill=True,
             proof=dict(method='Entity.ApplyModifier', nested_settlement=deepcopy(proof))),
        dict(base, sequence=6, kind='damage', amount=15, real_delta=0, hp_before=0, hp_after=0,
             target_type=0, damage_type=1, cancel_reason=0, killed=True, shield_absorbed=0,
             settled_after_nested_kill=True, proof=dict(method='Entity.ApplyModifier', type=1, target=0,
                                                       nested_settlement=deepcopy(proof))),
    ]
    for event in result[2:4]:
        event['source'] = None
        event.pop('source_character_uid')
    return result


def test_positive_shield_elemental_and_nested_parent_child_raw_witnesses_are_separate_gates():
    gates = module.pc_mechanism_evidence(mechanism_events())
    assert all(gate['passed'] for gate in gates.values())
    assert gates['shield']['positive_witnesses'] == [1]
    assert gates['elemental']['positive_witnesses'] == [dict(category='source_null_bar_burst',
                                                            bar_sequences=[2], burst_sequence=4, settlement_sequence=3)]
    assert gates['nested']['positive_witnesses'] == [dict(parent_sequence=6, parent_settlement_sequence=5,
                                                        ordering='child_before_parent_zero',
                                                        child_burst_sequence=4, child_settlement_sequence=3)]


def test_empty_or_zero_only_mechanism_samples_never_pass():
    assert all(gate['passed'] is False for gate in module.pc_mechanism_evidence([]).values())
    events = mechanism_events()
    for event in events:
        event['amount'] = 0
    assert all(gate['passed'] is False for gate in module.pc_mechanism_evidence(events).values())


@pytest.mark.parametrize('fault', ['missing_proof', 'old_parent_before_child', 'child_hp', 'parent_entry_hp',
                                  'child_settlement_missing', 'child_incomplete', 'wrong_target',
                                  'parent_settlement_missing', 'parent_not_flagged', 'parent_delta',
                                  'duplicate_child', 'reordered_parent', 'reused_parent_and_child',
                                  'unrelated_parent_method', 'unrelated_settlement_method', 'parent_damage_type',
                                  'parent_target_type', 'wrong_source', 'wrong_instance', 'small_nonzero_parent_hp'])
def test_nested_gate_requires_flag_and_complete_same_hit_parent_child_proof(fault):
    events = mechanism_events()
    parent, parent_settlement = events[-1], events[-2]
    if fault == 'missing_proof':
        parent['proof'].pop('nested_settlement')
    elif fault == 'old_parent_before_child':
        for event in (parent, parent_settlement):
            event['proof']['nested_settlement'].pop('ordering')
    elif fault == 'child_hp':
        events[3]['hp_before'] = 21
    elif fault == 'parent_entry_hp':
        for event in (parent, parent_settlement):
            event['proof']['nested_settlement']['parent_entry_hp_before'] = 21
    elif fault == 'child_settlement_missing':
        events.pop(2)
    elif fault == 'child_incomplete':
        events[2]['settlement_complete'] = False
    elif fault == 'wrong_target':
        events[2]['target_uid'] = '22'
    elif fault == 'parent_settlement_missing':
        events.pop(-2)
    elif fault == 'parent_not_flagged':
        parent.pop('settled_after_nested_kill')
    elif fault == 'parent_delta':
        parent['real_delta'] = -10
    elif fault == 'duplicate_child':
        parent_settlement['sequence'], parent['sequence'] = 6, 7
        events.insert(4, dict(events[3], sequence=5))
    elif fault == 'reordered_parent':
        parent_settlement['sequence'] = 2
    elif fault == 'reused_parent_and_child':
        events.append(dict(deepcopy(parent), sequence=7))
    elif fault == 'unrelated_parent_method':
        parent['proof']['method'] = 'Unrelated.Callback'
    elif fault == 'unrelated_settlement_method':
        parent_settlement['proof']['method'] = 'Unrelated.Callback'
    elif fault == 'parent_damage_type':
        parent['proof']['type'] = 2
    elif fault == 'parent_target_type':
        parent['proof']['target'] = 5
    elif fault == 'wrong_source':
        parent_settlement['source'] = 'char_other'
    elif fault == 'wrong_instance':
        parent_settlement['target_instance'] = '0x7000'
    else:
        parent['hp_before'] = .001
    assert module.pc_mechanism_evidence(events)['nested']['passed'] is False


@pytest.mark.parametrize('fault', ['zero_bar', 'missing_bar', 'wrong_pointer', 'wrong_uid', 'missing_settlement',
                                  'nominal_not_hp', 'wrong_delta', 'unverified_method', 'nominal_below_hp',
                                  'unowned_bar', 'bar_missing_uid', 'reuse_consumed_bar'])
def test_elemental_gate_needs_positive_bar_burst_and_bound_actual_hp_settlement(fault):
    events = mechanism_events()
    if fault == 'zero_bar':
        events[1]['amount'] = 0
    elif fault == 'missing_bar':
        events.pop(1)
    elif fault == 'wrong_pointer':
        events[1]['target_instance'] = '0x8888'
    elif fault == 'wrong_uid':
        events[1]['target_uid'] = '22'
    elif fault == 'missing_settlement':
        events.pop(2)
    elif fault == 'nominal_not_hp':
        events[2]['amount'] = 30
    elif fault == 'wrong_delta':
        events[3]['real_delta'] = -21
    elif fault == 'unverified_method':
        events[3]['proof']['method'] = 'synthetic_guess'
    elif fault == 'nominal_below_hp':
        events[3]['amount'] = 1
    elif fault == 'unowned_bar':
        events[1]['source'] = 'enemy_x'
    elif fault == 'bar_missing_uid':
        events[1].pop('source_character_uid')
    else:
        events.extend([dict(deepcopy(events[2]), sequence=7), dict(deepcopy(events[3]), sequence=8)])
    assert module.pc_mechanism_evidence(events)['elemental']['passed'] is False


def test_requested_pc_mechanisms_are_opt_in_and_absence_is_a_real_failure(tmp_path):
    records = pc_capture(tmp_path)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    result = module.verify(tmp_path, pc_mechanisms='shield')
    assert result['required_pc_mechanisms_passed'] is True
    assert result['pc_mechanism_coverage']['elemental']['passed'] is False
    assert result['pc_settlement_mechanisms_verified'] is False
    with pytest.raises(ValueError, match='elemental'):
        module.verify(tmp_path, pc_mechanisms='shield,elemental,nested')
    with pytest.raises(ValueError, match='unknown PC mechanism'):
        module.verify(tmp_path, pc_mechanisms='invented')


def test_source_completeness_uses_initial_certificate_without_relabeling_late_native_counters(tmp_path):
    from test_compare_pc_live_probes import add_initial_prefix, capture, event, write_records
    directory = tmp_path / 'capture'
    records = capture(directory, offset=1, first_frame=2)
    add_initial_prefix(records, [dict(event(), frame=0, game_time=0)])
    write_records(directory, records)
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    source = module.pc_source_completeness(directory, records, summary)
    assert source['event_prefix_from_initial_boundary_complete'] is True
    assert source['raw_terminal_verified'] is True
    assert source['native_cumulative_from_zero_complete'] is False
    assert source['host_first_mark']['frame'] == 2 and source['raw_initial_event_count'] == 1


def test_verifier_explicit_session_uses_selected_publications_not_global_last_summary(tmp_path):
    from test_compare_pc_live_probes import SESSION, SECOND_SESSION, multi_capture
    directory = tmp_path / 'capture'
    multi_capture(directory)
    with pytest.raises(ValueError, match='explicit native session'):
        module.verify(directory)
    first = module.verify(directory, native_session=SESSION)
    second = module.verify(directory, native_session=SECOND_SESSION)
    assert first['baseline_sequence'] == 0 and first['final_sequence'] == 2
    assert first['events'] == 2 and first['event_prefix_from_initial_boundary_complete'] is True
    assert first['source_selection_manifest']['original_summary_last_native_session'] == SECOND_SESSION
    assert first['source_selection_manifest']['lifecycle_generation'] == 1
    assert second['baseline_sequence'] == 2 and second['final_sequence'] == 4 and second['events'] == 2
    assert second['event_prefix_from_initial_boundary_complete'] is second['native_cumulative_from_zero_complete'] is False
    assert second['source_selection_manifest']['lifecycle_generation'] == 2


def f32_model_window():
    import struct
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    initial = dict(id='char_a', physical=0, arts=0, true=0, healing=0, elemental=0, damage=0)
    middle = dict(initial, physical=f32(.1), damage=f32(.1))
    final = dict(initial, physical=f32(f32(.1) + f32(.2)), damage=f32(f32(.1) + f32(.2)))
    publications = [dict(frame=index, game_time=index, event_watermark=dict(sequence=index), native_rows=[row])
                    for index, row in enumerate((initial, middle, final))]
    events = [dict(sequence=index, frame=index, game_time=index, kind='damage', source='char_a', amount=value,
                   target='enemy_a', damage_type=1) for index, value in enumerate((.1, .2), 1)]
    return events, publications


def test_pc_f32_model_checks_every_typed_endpoint_exactly_without_tolerance_fallback():
    events, publications = f32_model_window()
    result = module.check_pc_f32_counters(events, publications)
    assert result['exact'] is True and result['exact_typed_checks'] == 15
    assert result['applied_event_count'] == 2 and result['baseline_sequence'] == 0 and result['final_sequence'] == 2
    assert result['seeds']['char_a']['sequence'] == 0
    assert result['closed_source_update_instructions_verified'] is result['tolerance_fallback_used'] is False
    assert len(result['storage_dtype_evidence']['reader_sha256']) == 64


@pytest.mark.parametrize('fault', ['one_ulp', 'missing_event', 'event_order', 'unknown_type', 'missing_column',
                                  'not_binary32_seed', 'unknown_source', 'missing_final_source', 'wrong_typed_total',
                                  'one_ulp_intermediate', 'duplicate_row', 'event_clock'])
@pytest.mark.parametrize('reader_profile', ['native_pc', 'android'])
def test_pc_f32_model_rejects_any_exact_endpoint_gap_order_or_dtype_fault(fault, reader_profile):
    import struct
    events, publications = f32_model_window()
    final = publications[-1]['native_rows'][0]
    if fault in ('one_ulp', 'one_ulp_intermediate'):
        row = final if fault == 'one_ulp' else publications[1]['native_rows'][0]
        bits = struct.unpack('<I', struct.pack('<f', row['physical']))[0]
        row['physical'] = row['damage'] = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    elif fault == 'missing_event':
        events.pop(0)
    elif fault == 'event_order':
        events.reverse()
    elif fault == 'unknown_type':
        events[0]['damage_type'] = 8
    elif fault == 'missing_column':
        final.pop('arts')
    elif fault == 'not_binary32_seed':
        publications[0]['native_rows'][0].update(physical=.1, damage=.1)
    elif fault == 'unknown_source':
        events[0]['source'] = 'char_unknown'
    elif fault == 'missing_final_source':
        publications[-1]['native_rows'].clear()
    elif fault == 'wrong_typed_total':
        final['damage'] += 1
    elif fault == 'duplicate_row':
        publications[-1]['native_rows'].append(dict(final))
    else:
        events[-1]['frame'] = 0
    with pytest.raises(ValueError):
        (module.check_pc_f32_counters(events, publications) if reader_profile == 'native_pc' else
         module.check_native_f32_counters(events, publications, reader_profile='android'))


def add_pc_typed_rows(records):
    for record in records:
        data = record['data']
        if record['kind'] == 'lifecycle_observation':
            for key in ('before', 'after'):
                mark = (data.get('events') or {}).get(key)
                if mark is not None:
                    mark.update(damage_unknown=False, engagement_unknown=False, shield_unknown=False, skill_unknown=False)
        rows = data.get('native_rows') if record['kind'] == 'published' else (
            (data.get('sample') or {}).get('rows') if record['kind'] == 'lifecycle_observation' else None)
        if rows is not None:
            for row in rows:
                row.update(physical=row['damage'], arts=0, true=0, elemental=0)


def test_explicit_pc_f32_flag_uses_official_guarded_rows_and_composes_with_positive_mechanisms(tmp_path):
    records = pc_capture(tmp_path)
    add_pc_typed_rows(records)
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    result = module.verify(tmp_path, pc_f32_counters=True, pc_mechanisms='shield')
    assert result['pc_f32_counters_verified'] is True and result['required_pc_mechanisms_passed'] is True
    assert result['native_counter_arithmetic'] == 'pc_binary32_ordered_exact'
    assert result['pc_f32_counter_evidence']['exact_typed_checks'] > 0
    assert result['event_prefix_from_initial_boundary_complete'] is False


@pytest.mark.parametrize('fault', ['fingerprint', 'missing_column', 'unknown_coverage'])
def test_explicit_pc_f32_flag_never_uses_unverified_build_columns_or_coverage(tmp_path, fault):
    records = pc_capture(tmp_path)
    add_pc_typed_rows(records)
    if fault == 'fingerprint':
        next(r['data'] for r in records if r['kind'] == 'connected')['diagnostics']['details']['build']['known_build'] = False
    elif fault == 'missing_column':
        next(r['data'] for r in reversed(records) if r['kind'] == 'published')['native_rows'][0].pop('arts')
    else:
        next(r['data'] for r in records if r['kind'] == 'lifecycle_observation' and
             (r['data'].get('events') or {}).get('drain'))['events']['drain']['unknown_sources'] = ['char_unknown']
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    with pytest.raises(ValueError):
        module.verify(tmp_path, pc_f32_counters=True)


def parent_before_child_events(*, ordinary_child=False):
    events = mechanism_events()
    proof = dict(target_uid='21', parent_real_delta=-10, parent_hp_before=30, parent_hp_after=20,
                 child_hp_before=20, child_hp_after=0, child_real_delta=-20)
    if ordinary_child:
        proof.update(child_hp_after=5, child_real_delta=-15)
        events[2].update(amount=15, damage_type=2, killed=False)
        events[3].update(kind='damage', damage_type=2, real_delta=-15, hp_after=5, killed=False,
                         proof=dict(method='Entity.ApplyModifier', type=2, target=0))
        for event in events[2:4]:
            event.update(source='char_a', source_character_uid=1)
    for event in events[-2:]:
        event.pop('settled_after_nested_kill')
        event['proof']['nested_settlement'] = deepcopy(proof)
        event['killed'] = False
    events[-2]['amount'] = 10
    events[-1].update(real_delta=-10, hp_before=30, hp_after=20)
    return events


@pytest.mark.parametrize('ordinary_child', [False, True])
def test_general_nested_gate_supports_real_parent_before_child_certificate_without_afterkill_flag(ordinary_child):
    gates = module.pc_mechanism_evidence(parent_before_child_events(ordinary_child=ordinary_child))
    assert gates['nested']['passed'] is True
    witness = gates['nested']['positive_witnesses'][0]
    assert witness['ordering'] == 'parent_before_child' and witness['parent_sequence'] == 6
    assert witness['child_sequence'] == 4 and witness['child_kind'] == ('damage' if ordinary_child else 'elemental_burst')


@pytest.mark.parametrize('fault', ['parent_hp', 'child_hp', 'wrong_uid', 'wrong_instance', 'wrong_source',
                                  'missing_parent_settlement', 'missing_child_settlement', 'child_nominal',
                                  'parent_killed', 'mixed_ordering', 'reused_child'])
def test_parent_before_child_nested_certificate_cannot_hide_wrong_hp_binding_or_reuse(fault):
    events = parent_before_child_events()
    if fault == 'parent_hp':
        events[-1]['hp_after'] = 19
    elif fault == 'child_hp':
        events[3]['hp_before'] = 21
    elif fault == 'wrong_uid':
        events[2]['target_uid'] = '22'
    elif fault == 'wrong_instance':
        events[2]['target_instance'] = '0x7000'
    elif fault == 'wrong_source':
        events[2]['source'] = 'char_other'
    elif fault == 'missing_parent_settlement':
        events.pop(-2)
    elif fault == 'missing_child_settlement':
        events.pop(2)
    elif fault == 'child_nominal':
        events[3]['amount'] = 1
    elif fault == 'parent_killed':
        events[-1]['killed'] = True
    elif fault == 'mixed_ordering':
        for event in events[-2:]:
            event['proof']['nested_settlement']['ordering'] = 'child_before_parent_zero'
    else:
        events.append(dict(deepcopy(events[-1]), sequence=7))
    assert module.pc_mechanism_evidence(events)['nested']['passed'] is False


def supplement_window(*, explicit_source=False):
    import struct
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    evidence = mechanism_events()
    bar, settlement, burst = deepcopy(evidence[1:4])
    bar.update(sequence=2, frame=1, game_time=1)
    settlement.update(sequence=3, frame=2, game_time=2, source=None, nominal_amount=30)
    burst.update(sequence=4, frame=2, game_time=2, source=None)
    settlement.pop('source_character_uid', None)
    burst.pop('source_character_uid', None)
    initial = dict(id='char_a', physical=0, arts=0, true=0, healing=0, elemental=0, damage=0)
    middle = dict(initial, physical=f32(.1), damage=f32(.1))
    final = dict(middle)
    if explicit_source:
        for event in (settlement, burst):
            event.update(source='char_a', source_character_uid=1)
        final.update(elemental=30, damage=f32(.1) + 30)
    rows = []
    for index, native in enumerate((initial, middle, final)):
        supplement = 30 if index == 2 and not explicit_source else 0
        row = dict(sources=['char_a'], damage=native['damage'] + supplement, healing=0,
                   counter_snapshot_complete=True, burst_added_settlement=supplement or None)
        rows.append(dict(frame=index, game_time=index, native_session='10:1234:stage_a',
                         battle_metadata=dict(stage_id='stage_a'), native_rows=[native],
                         statistics=dict(rows=dict(char_a=row)), event_watermark=dict(sequence=(0, 2, 4)[index])))
    return [dict(sequence=1, frame=1, game_time=1, kind='damage', amount=.1, source='char_a',
                 target='enemy_a', damage_type=1), bar, settlement, burst], rows


def test_f32_owner_supplement_uses_nominal_source_null_burst_once_at_each_publication():
    events, publications = supplement_window()
    assert module.check_pc_f32_counters(events, publications)['exact'] is True
    result = module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})
    assert result['final_nominal_supplements'] == dict(char_a=30)
    assert result['source_null_burst_witnesses'][0]['actual_hp_damage'] == 20
    assert result['source_null_burst_witnesses'][0]['nominal_supplement'] == 30
    assert result['future_events_used'] is result['explicit_source_bursts_supplemented'] is False


def test_explicitly_sourced_burst_updates_native_elemental_counter_but_is_never_added_twice():
    events, publications = supplement_window(explicit_source=True)
    assert module.check_pc_f32_counters(events, publications)['applied_event_count'] == 2
    result = module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})
    assert not result['source_null_burst_witnesses'] and not any(result['final_nominal_supplements'].values())
    publications[-1]['statistics']['rows']['char_a']['burst_added_settlement'] = 30
    with pytest.raises(ValueError, match='supplement'):
        module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})


def single_settlement_supplement_window(*, explicit_source=False):
    """A new synthetic one-record wire; leave all old recording fixtures intact."""
    events, publications = supplement_window(explicit_source=explicit_source)
    ordinary, bar, settlement, burst = events
    ordinary = dict(ordinary, kind='damage_settlement', damage_type=1,
                    nominal_amount=ordinary['amount'], source_character_uid=1,
                    proof={'method': 'Entity.ApplyModifier', 'settlement_wire_version': 2, 'native_modifier_observation':
                           {'version': 1, 'method': 'Entity.ApplyModifier', 'amount': ordinary['amount']}})
    settlement.update({key: burst[key] for key in
                       ('hp_before', 'hp_after', 'real_delta', 'cancel_reason', 'killed')})
    settlement['proof']['native_modifier_observation'] = {
        'version': 1, 'method': 'Entity.ApplyModifier', 'amount': burst['amount']}
    settlement['proof']['settlement_wire_version'] = 2
    publications[-1]['event_watermark']['sequence'] = settlement['sequence']
    return [ordinary, bar, settlement], publications


@pytest.mark.parametrize('explicit_source', (False, True))
def test_single_settlement_supplement_preserves_nominal_and_real_sequence_without_synthetic_burst(explicit_source):
    events, publications = single_settlement_supplement_window(explicit_source=explicit_source)
    original = deepcopy(events)
    result = module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})
    assert events == original
    assert module._single_settlement_wire(events)
    if explicit_source:
        assert result['source_null_burst_witnesses'] == []
        assert not any(result['final_nominal_supplements'].values())
    else:
        witness, = result['source_null_burst_witnesses']
        assert witness['nominal_supplement'] == 30 and witness['actual_hp_damage'] == 20
        assert witness['bar_sequence'] == 2 and witness['settlement_sequence'] == 3
        assert 'burst_sequence' not in witness
        assert result['final_nominal_supplements'] == {'char_a': 30}
    assert result['nominal_supplements_by_watermark'][:2] == [
        {'sequence': 0, 'by_owner': {}}, {'sequence': 2, 'by_owner': {}}]


@pytest.mark.parametrize('fault', ('hp', 'actual', 'nominal', 'uid', 'source', 'clock', 'stage',
                                  'cancel', 'shield', 'missing_endpoint', 'incomplete', 'output_source_null',
                                  'reused_certificate', 'future_supplement', 'missing_format'))
def test_single_settlement_supplement_rejects_bad_raw_evidence_without_legacy_fallback(fault):
    events, publications = single_settlement_supplement_window()
    certificate = events[-1]
    if fault == 'hp': certificate['hp_before'] += 1
    elif fault == 'actual': certificate['amount'] += 1
    elif fault == 'nominal': certificate['nominal_amount'] = 1
    elif fault == 'uid': certificate['target_uid'] = '00021'
    elif fault == 'source': certificate.update(source='char_other', source_character_uid=1)
    elif fault == 'clock': events[1].update(frame=3, game_time=3)
    elif fault == 'stage': certificate['native_stage_id'] = 'other_stage'
    elif fault == 'cancel': certificate['cancel_reason'] = True
    elif fault == 'shield': certificate['shield_absorbed'] = None
    elif fault == 'missing_endpoint': certificate.pop('hp_after')
    elif fault == 'incomplete': certificate['settlement_complete'] = False
    elif fault == 'output_source_null': certificate['proof']['method'] = 'Entity.OnAfterOutputDamage'
    elif fault == 'reused_certificate':
        events.append(dict(deepcopy(certificate), sequence=4, frame=3, game_time=3))
        publications.append(dict(deepcopy(publications[-1]), frame=3, game_time=3,
                                 event_watermark={'sequence': 4}))
        publications[-1]['statistics']['rows']['char_a']['burst_added_settlement'] = 60
    elif fault == 'future_supplement':
        publications[1]['statistics']['rows']['char_a']['burst_added_settlement'] = 30
    elif fault == 'missing_format': certificate['proof'].pop('settlement_wire_version')
    with pytest.raises(ValueError):
        module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})


def test_non_enemy_observation_cannot_disable_same_prefix_single_certificate_supplement():
    events, publications = single_settlement_supplement_window()
    events.append(dict(sequence=4, frame=2, game_time=2, kind='damage', source='char_a',
                       amount=99, damage_type=1, target='char_ally', target_type=1))
    publications[-1]['event_watermark']['sequence'] = 4
    result = module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})
    assert result['final_nominal_supplements'] == {'char_a': 30}
    assert result['source_null_burst_witnesses'][0]['settlement_sequence'] == 3


@pytest.mark.parametrize('version', (True, '2', 1, 3, None))
def test_unknown_single_certificate_format_cannot_fall_back_to_empty_legacy_observations(version):
    events, publications = single_settlement_supplement_window()
    events[-1]['proof']['settlement_wire_version'] = version
    with pytest.raises(ValueError, match='wire version'):
        module.check_pc_owner_counter_supplements(events, publications, {'char_a': 'char_a'})


@pytest.mark.parametrize('fault', ['wrong_uid', 'wrong_instance', 'missing_owner', 'wrong_stage', 'future_bar',
                                  'nominal_not_settlement', 'actual_hp_as_nominal', 'fake_published_supplement',
                                  'future_supplement', 'reused_burst', 'stale_duplicate_bar', 'explicit_double_credit'])
def test_f32_supplement_cannot_cross_bind_reuse_or_credit_future_or_actual_hp_values(fault):
    events, publications = supplement_window(explicit_source=fault == 'explicit_double_credit')
    ownership = {'char_a': 'char_a'}
    if fault == 'wrong_uid':
        events[2]['target_uid'] = '22'
    elif fault == 'wrong_instance':
        for event in events[2:]:
            event['target_instance'] = '0x7000'
    elif fault == 'missing_owner':
        ownership.clear()
    elif fault == 'wrong_stage':
        events[1]['native_stage_id'] = 'other_stage'
    elif fault == 'future_bar':
        events[1].update(frame=3, game_time=3)
    elif fault == 'nominal_not_settlement':
        events[2]['nominal_amount'] = 20
    elif fault == 'actual_hp_as_nominal':
        publications[-1]['statistics']['rows']['char_a'].update(burst_added_settlement=20,
                                                              damage=publications[-1]['native_rows'][0]['damage'] + 20)
    elif fault == 'fake_published_supplement':
        publications[-1]['statistics']['rows']['char_a']['burst_added_settlement'] = 31
    elif fault == 'future_supplement':
        publications[1]['statistics']['rows']['char_a'].update(burst_added_settlement=30,
                                                             damage=publications[1]['native_rows'][0]['damage'] + 30)
    elif fault in ('reused_burst', 'stale_duplicate_bar'):
        if fault == 'stale_duplicate_bar':
            events.append(dict(deepcopy(events[1]), sequence=5))
        events.extend([dict(deepcopy(events[2]), sequence=len(events) + 1, frame=3, game_time=3),
                       dict(deepcopy(events[3]), sequence=len(events) + 2, frame=3, game_time=3)])
        publications.append(dict(deepcopy(publications[-1]), frame=3, game_time=3,
                                 event_watermark=dict(sequence=events[-1]['sequence'])))
        publications[-1]['statistics']['rows']['char_a'].update(burst_added_settlement=60,
                                                              damage=publications[-1]['native_rows'][0]['damage'] + 60)
    else:
        publications[-1]['statistics']['rows']['char_a']['burst_added_settlement'] = 30
    with pytest.raises(ValueError):
        module.check_pc_owner_counter_supplements(events, publications, ownership)


def test_terminal_tail_source_evidence_keeps_counter_window_at_the_last_real_publication(tmp_path):
    from test_compare_pc_live_probes import add_initial_prefix, add_terminal_tail, capture, write_records
    directory = tmp_path / 'capture'
    records = capture(directory, terminal=False)
    add_initial_prefix(records)
    add_terminal_tail(records)
    write_records(directory, records)
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    source = module.pc_source_completeness(directory, records, summary)
    assert source['event_prefix_from_initial_boundary_complete'] is True
    assert source['native_cumulative_from_zero_complete'] is False
    assert source['native_counter_watermark']['sequence'] == 2
    assert source['event_source_final_watermark']['sequence'] == 3
    assert source['source_verified_event_count'] == 3


def test_both_nested_orderings_match_the_actual_owned_synthetic_producer_callbacks():
    """Contract integration only: these callback fixtures are not live evidence."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    before = json.loads(subprocess.check_output(
        ['node', str(root / 'tests' / 'event_probe_nested_settlement_fixture.js')], cwd=root, text=True, encoding='utf-8'))
    after = json.loads(subprocess.check_output(
        ['node', str(root / 'tests' / 'event_probe_nested_after_kill_fixture.js')], cwd=root, text=True, encoding='utf-8'))
    for case in before['native_pc']['valid']:
        gate = module.pc_mechanism_evidence(case['batch']['events'])['nested']
        assert gate['passed'] is True and gate['positive_witnesses'][0]['ordering'] == 'parent_before_child'
    gate = module.pc_mechanism_evidence(after['native_pc']['valid']['batch']['events'])['nested']
    assert gate['passed'] is True and gate['positive_witnesses'][0]['ordering'] == 'child_before_parent_zero'


def ordinary_child_first_events(*, child_after=75, parent_after=40):
    def settlement(sequence, damage_type, before, after, nominal):
        return dict(sequence=sequence, kind='damage_settlement', frame=30, game_time=1.0,
                    source='char_a', source_character_uid=1, target='enemy_a', target_uid='12',
                    target_instance='0x1000', target_type=0, damage_type=damage_type,
                    amount=before - after, nominal_amount=nominal, settlement_complete=True,
                    cancel_reason=0, shield_absorbed=0, real_delta=after - before,
                    hp_before=before, hp_after=after, killed=after == 0,
                    proof=dict(method='Entity.ApplyModifier', settlement_wire_version=2,
                               native_modifier_observation=dict(version=1, method='Entity.ApplyModifier', amount=nominal),
                               target_callback_checkpoint=dict(version=1, method='Unit.OnTakeDamage', returned=True,
                                                               source='char_a', source_character_uid=1, target_uid='12',
                                                               frame=30, game_time=1.0, hp_before=100)))

    child = settlement(10, 3, 100, child_after, 120 if child_after == 0 else 25)
    parent = settlement(11, 1, child_after, parent_after, 80)
    ordering = 'child_before_parent'
    if child_after == 0:
        parent['settled_after_nested_kill'] = True
        parent.pop('killed')
        ordering = 'child_before_parent_zero'
    parent['proof']['nested_settlement'] = dict(target_uid='12', ordering=ordering, parent_entry_hp_before=100,
                                               parent_real_delta=parent['real_delta'], parent_hp_before=child_after,
                                               parent_hp_after=parent_after, child_real_delta=child['real_delta'],
                                               child_hp_before=100, child_hp_after=child_after)
    return [child, parent]


@pytest.mark.parametrize(('child_after', 'parent_after', 'ordering'), [
    (75, 40, 'child_before_parent'), (75, 0, 'child_before_parent'), (0, 0, 'child_before_parent_zero')])
def test_ordinary_child_first_gate_distinguishes_parent_damage_from_full_overkill(child_after, parent_after, ordering):
    events = ordinary_child_first_events(child_after=child_after, parent_after=parent_after)
    gate = module.pc_mechanism_evidence(events)['nested']
    assert gate['passed'] is True
    witness, = gate['positive_witnesses']
    assert witness['ordering'] == ordering
    assert (witness['child_settlement_sequence'], witness['parent_settlement_sequence']) == (10, 11)


@pytest.mark.parametrize('fault', ['parent_entry', 'child_checkpoint', 'child_modifier_amount', 'child_type',
                                  'parent_boundary', 'parent_killed', 'duplicate_child'])
def test_ordinary_child_first_requires_independent_and_bound_hp_debits(fault):
    events = ordinary_child_first_events()
    child, parent = events
    proof = parent['proof']['nested_settlement']
    if fault == 'parent_entry':
        proof['parent_entry_hp_before'] = parent['proof']['target_callback_checkpoint']['hp_before'] = 99
    elif fault == 'child_checkpoint':
        child['proof']['target_callback_checkpoint']['returned'] = False
    elif fault == 'child_modifier_amount':
        child['proof']['native_modifier_observation']['amount'] = 20
    elif fault == 'child_type':
        child['damage_type'] = True
    elif fault == 'parent_boundary':
        parent['hp_before'] = proof['parent_hp_before'] = 76
        parent['amount'] = 36
        parent['real_delta'] = proof['parent_real_delta'] = -36
    elif fault == 'parent_killed':
        parent['killed'] = True
    else:
        events.insert(0, dict(deepcopy(child), sequence=9))
    gate = module.pc_mechanism_evidence(events)['nested']
    assert gate['passed'] is False
    assert gate['invalid_sequences'] == [11]


def explicit_ep_events():
    events = mechanism_events()[2:4]
    for event in events:
        event.update(source='char_a', source_character_uid=1)
    return events


def test_native_explicit_ep_is_valid_without_a_fresh_bar_but_does_not_prove_a_source_null_burst():
    gate = module.pc_mechanism_evidence(explicit_ep_events())['elemental']
    assert gate['invalid_sequences'] == []
    assert len(gate['native_elemental_contribution_witnesses']) == 1
    assert gate['source_null_bar_burst_witnesses'] == []
    assert gate['passed'] is False and gate['reason'] == 'no_positive_bar_burst_witness'
    assert 'bar_sequences' not in gate['native_elemental_contribution_witnesses'][0]


@pytest.mark.parametrize('fault', ['actor_uid_missing', 'actor_uid_mismatch', 'unowned_actor', 'target_uid',
                                  'instance', 'clock', 'method', 'nominal', 'hp', 'settlement_missing',
                                  'settlement_incomplete', 'duplicate_settlement', 'boolean_native_type'])
def test_explicit_native_ep_subcategory_still_requires_unique_complete_same_hit_actor_and_hp_proof(fault):
    events = explicit_ep_events()
    settlement, contribution = events
    if fault == 'actor_uid_missing':
        for event in events:
            event.pop('source_character_uid')
    elif fault == 'actor_uid_mismatch':
        settlement['source_character_uid'] = 2
    elif fault == 'unowned_actor':
        for event in events:
            event['source'] = 'enemy_a'
    elif fault == 'target_uid':
        contribution['target_uid'] = '22'
    elif fault == 'instance':
        settlement['target_instance'] = '0x7000'
    elif fault == 'clock':
        settlement['frame'] += 1
    elif fault == 'method':
        contribution['proof']['method'] = 'Unrelated.Callback'
    elif fault == 'nominal':
        contribution['amount'] = 1
    elif fault == 'hp':
        contribution['hp_before'] += 1
    elif fault == 'settlement_missing':
        events.pop(0)
    elif fault == 'settlement_incomplete':
        settlement['settlement_complete'] = False
    elif fault == 'duplicate_settlement':
        events.insert(0, dict(deepcopy(settlement), sequence=2))
    else:
        contribution['target_type'] = False
    gate = module.pc_mechanism_evidence(events)['elemental']
    assert gate['native_elemental_contribution_witnesses'] == [] and gate['invalid_sequences']


def test_explicit_native_ep_classification_does_not_consume_a_real_bar_for_a_later_source_null_burst():
    events = mechanism_events()[1:4]
    bar = events[0]
    for event in events[1:]:
        event.update(source='char_a', source_character_uid=1)
    settlement, burst = deepcopy(events[1:])
    settlement.update(source=None, sequence=5)
    burst.update(source=None, sequence=6)
    for event in (settlement, burst):
        event.pop('source_character_uid')
    # Distinct HP endpoints keep each same-hit pair unambiguous.
    settlement['amount'] = 5
    burst.update(hp_before=5, hp_after=0, real_delta=-5)
    events.extend((settlement, burst))
    gate = module.pc_mechanism_evidence(events)['elemental']
    assert gate['passed'] is True and len(gate['source_null_bar_burst_witnesses']) == 1
    assert len(gate['native_elemental_contribution_witnesses']) == 1
    assert gate['source_null_bar_burst_witnesses'][0]['bar_sequences'] == [bar['sequence']]


def parent_before_elemental_nonlethal_events():
    events = parent_before_child_events()
    events[2].update(amount=15, killed=False)
    events[3].update(real_delta=-15, hp_after=5, killed=False)
    for event in events[-2:]:
        event['proof']['nested_settlement'].update(child_hp_after=5, child_real_delta=-15)
    return events


def test_complete_source_null_nonlethal_elemental_child_has_a_distinct_seven_field_parent_certificate():
    gates = module.pc_mechanism_evidence(parent_before_elemental_nonlethal_events())
    assert gates['nested']['passed'] is True
    assert gates['nested']['positive_witnesses'][0]['child_kind'] == 'elemental_burst'
    assert gates['nested']['positive_witnesses'][0]['ordering'] == 'parent_before_child'
    assert gates['elemental']['passed'] is True


def test_nonlethal_nested_hp_certificate_alone_does_not_fabricate_fresh_bar_burst_evidence():
    events = [event for event in parent_before_elemental_nonlethal_events() if event['kind'] != 'elemental_bar']
    gates = module.pc_mechanism_evidence(events)
    assert gates['nested']['passed'] is True
    assert gates['elemental']['passed'] is False
    assert gates['elemental']['source_null_bar_burst_witnesses'] == []


@pytest.mark.parametrize('fault', ['explicit_source_uid_mismatch', 'child_uid', 'child_hp', 'child_delta', 'child_cancel',
                                  'child_shield', 'child_killed_true', 'zero_hp_false_killed', 'missing_child_burst',
                                  'missing_child_settlement', 'child_incomplete', 'wrong_target_uid', 'wrong_instance',
                                  'cross_clock', 'parent_delta', 'parent_uses_child_final_hp', 'reused_parent',
                                  'duplicate_child', 'nested_child', 'afterkill_flag_misuse'])
def test_nonlethal_elemental_parent_requires_complete_unique_same_hit_evidence(fault):
    events = parent_before_elemental_nonlethal_events()
    settlement, child, parent_settlement, parent = events[2:]
    if fault == 'explicit_source_uid_mismatch':
        settlement.update(source='char_a', source_character_uid=1)
        child.update(source='char_a', source_character_uid=2)
    elif fault == 'child_uid':
        child['source_character_uid'] = 2
    elif fault == 'child_hp':
        child['hp_after'] = 6
    elif fault == 'child_delta':
        child['real_delta'] = -14
    elif fault == 'child_cancel':
        child['cancel_reason'] = 1
    elif fault == 'child_shield':
        child['shield_absorbed'] = settlement['shield_absorbed'] = 1
    elif fault == 'child_killed_true':
        child['killed'] = True
    elif fault == 'zero_hp_false_killed':
        child.update(hp_after=0, real_delta=-20)
        settlement['amount'] = 20
        for event in (parent_settlement, parent):
            event['proof']['nested_settlement'].update(child_hp_after=0, child_real_delta=-20)
    elif fault == 'missing_child_burst':
        events.remove(child)
    elif fault == 'missing_child_settlement':
        events.remove(settlement)
    elif fault == 'child_incomplete':
        settlement['settlement_complete'] = False
    elif fault == 'wrong_target_uid':
        settlement['target_uid'] = '22'
    elif fault == 'wrong_instance':
        settlement['target_instance'] = '0x7000'
    elif fault == 'cross_clock':
        child['game_time'] += 1
    elif fault == 'parent_delta':
        parent['real_delta'] = -9
    elif fault == 'parent_uses_child_final_hp':
        parent['hp_after'] = 5
    elif fault == 'reused_parent':
        events.append(dict(deepcopy(parent), sequence=7))
    elif fault == 'duplicate_child':
        parent_settlement['sequence'], parent['sequence'] = 6, 7
        events.insert(4, dict(deepcopy(child), sequence=5))
    elif fault == 'nested_child':
        child['proof']['nested_settlement'] = deepcopy(parent['proof']['nested_settlement'])
    else:
        parent['settled_after_nested_kill'] = True
    assert module.pc_mechanism_evidence(events)['nested']['passed'] is False


def test_frozen_original_ten_unknown_parents_are_not_retroactively_promoted_by_new_nonlethal_support():
    import hashlib
    path = Path(__file__).parent / 'fixtures' / 'event_probe_business' / '20261124-native-elemental-nonlethal-fields.json'
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == '81838508235e7d9ec8934737c36dc2414d7bc4fad168ebd75d4c884d2dfe0836'
    frozen = json.loads(data)
    assert len(frozen['elemental_nonlethal_groups']) == 10
    for group in frozen['elemental_nonlethal_groups']:
        events = [deepcopy(group[key]['event']) for key in ('raw_elemental_bar', 'raw_child_settlement',
                  'raw_child_burst', 'original_incomplete_parent_settlement', 'original_incomplete_parent_damage')]
        events.sort(key=lambda event: event['sequence'])
        assert events[-2]['settlement_complete'] is False
        assert not events[-1]['proof'].get('nested_settlement')
        assert module.pc_mechanism_evidence(events)['nested']['passed'] is False


@pytest.fixture(scope='module')
def nonlethal_callback_replay():
    """Owned callbacks with frozen live scalars, not a new live receipt."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/event_probe_nested_elemental_nonlethal_fixture.js'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('index', range(10))
def test_nonlethal_gate_accepts_the_shared_producers_ten_frozen_field_callback_replays(
        nonlethal_callback_replay, platform, index):
    report = nonlethal_callback_replay[platform]['valid'][index]
    assert report['scope'] == 'frozen-native-fields-owned-callback-replay-not-live'
    events = report['batch']['events']
    nested = module.pc_mechanism_evidence(events)['nested']
    assert nested['passed'] is True and len(nested['positive_witnesses']) == 1
    witness = nested['positive_witnesses'][0]
    assert witness['ordering'] == 'parent_before_child' and witness['child_kind'] == 'damage_settlement'
    child = next(event for event in events if event['sequence'] == witness['child_sequence'])
    assert child['source'] is None and child['hp_after'] > 0 and child['killed'] is False
    # A valid HP parent certificate alone is not evidence of fresh bar coverage.
    no_bar = [event for event in events if event['kind'] != 'elemental_bar']
    gates = module.pc_mechanism_evidence(no_bar)
    assert gates['nested']['passed'] is True and gates['elemental']['passed'] is False
    assert gates['elemental']['source_null_bar_burst_witnesses'] == []


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_nonlethal_gate_rejects_all_shared_callback_faults_without_inventing_a_parent_certificate(
        nonlethal_callback_replay, platform):
    faults = nonlethal_callback_replay[platform]['invalid']
    assert len(faults) == 29
    for name, report in faults.items():
        gate = module.pc_mechanism_evidence(report['batch']['events'])['nested']
        assert gate['passed'] is False, name
        assert not gate['positive_witnesses'], name


def owned_f32_model_window():
    import struct
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    zero = dict(physical=0, arts=0, true=0, healing=0, elemental=0, damage=0)
    publications = []
    for index, (physical, healing, through) in enumerate(((0, 0, 0), (f32(.1), f32(.3), 3),
            (f32(f32(.1) + f32(.2)), f32(f32(.3) + f32(.4)), 5))):
        publications.append(dict(frame=index, game_time=index, event_watermark=dict(sequence=through), native_rows=[
            dict(zero, id='char_a', physical=physical, damage=physical),
            dict(zero, id='token_a', healing=healing),
            dict(zero, id='trap_generic_owned_test', true=(index + 1) * 500, damage=(index + 1) * 500,
                 raw_output=-(index + 1) * 500)]))
    events = [dict(sequence=1, frame=1, game_time=1, kind='damage', source='char_a', amount=.1,
                   target='enemy_a', damage_type=1),
              dict(sequence=2, frame=1, game_time=1, kind='healing', source='token_a', amount=.3),
              dict(sequence=3, frame=1, game_time=1, kind='skill_start', source='trap_generic_owned_test', amount=1),
              dict(sequence=4, frame=2, game_time=2, kind='damage', source='char_a', amount=.2,
                   target='enemy_a', damage_type=1),
              dict(sequence=5, frame=2, game_time=2, kind='healing', source='token_a', amount=.4)]
    return events, publications


def test_explicit_owned_domain_preserves_every_event_models_all_char_and_token_rows_and_reports_exclusions():
    events, publications = owned_f32_model_window()
    originals = deepcopy((events, publications))
    with pytest.raises(ValueError, match='trap_generic_owned_test/true'):
        module.check_pc_f32_counters(events, publications)
    with pytest.raises(ValueError, match='trap_generic_owned_test/true'):
        module.check_pc_f32_counters(events, publications, source_domain='all')
    result = module.check_pc_f32_counters(events, publications, source_domain='owned')
    assert result['source_domain'] == 'owned' and result['exact'] is True
    assert result['modeled_native_sources'] == ['char_a', 'token_a']
    assert result['all_native_sources_verified'] is result['full_native_row_counter_model_verified'] is False
    assert result['original_event_order_preserved'] is True and result['ordered_raw_event_count'] == 5
    assert result['applied_event_count'] == 4 and result['exact_typed_checks'] == 30
    assert result['all_native_rows_format_validated'] is True and result['validated_native_row_count'] == 9
    excluded = result['excluded_native_sources']['trap_generic_owned_test']
    assert excluded['classification'] == 'map_trap'
    assert excluded['seed']['native_row']['true'] == 500 and excluded['final']['native_row']['true'] == 1500
    assert excluded['observation_count'] == excluded['change_point_count'] == 3
    assert excluded['final_publication_present'] is True
    assert [point['sequence'] for point in excluded['change_points']] == [0, 3, 5]
    assert len(result['source_domain_evidence']['shared_runtime_sha256']) == 64
    assert len(result['source_domain_evidence']['classification_sha256']) == 64
    assert (events, publications) == originals


@pytest.mark.parametrize('fault', ['missing_char_intermediate', 'missing_token_final', 'unknown_event_source',
    'nonowned_counter_event', 'unknown_native_namespace', 'excluded_nan', 'excluded_raw_nan', 'excluded_missing_column',
    'excluded_wrong_total', 'excluded_duplicate', 'char_one_ulp', 'char_no_event_changed', 'token_no_event_changed',
    'no_owned_updates', 'missing_noncounter_event', 'reordered_events'])
@pytest.mark.parametrize('reader_profile', ['native_pc', 'android'])
def test_owned_domain_cannot_hide_unknowns_invalid_excluded_rows_or_missing_eligible_counter_evidence(fault, reader_profile):
    events, publications = owned_f32_model_window()
    if fault == 'missing_char_intermediate':
        publications[1]['native_rows'].pop(0)
    elif fault == 'missing_token_final':
        publications[-1]['native_rows'].pop(1)
    elif fault == 'unknown_event_source':
        events[0]['source'] = 'mystery_source'
    elif fault == 'nonowned_counter_event':
        events[2].update(kind='damage', amount=500, damage_type=3, target='enemy_a')
    elif fault == 'unknown_native_namespace':
        publications[-1]['native_rows'][-1]['id'] = 'mystery_source'
    elif fault == 'excluded_nan':
        publications[1]['native_rows'][-1]['true'] = float('nan')
    elif fault == 'excluded_raw_nan':
        publications[1]['native_rows'][-1]['raw_output'] = float('nan')
    elif fault == 'excluded_missing_column':
        publications[1]['native_rows'][-1].pop('arts')
    elif fault == 'excluded_wrong_total':
        publications[1]['native_rows'][-1]['damage'] += 1
    elif fault == 'excluded_duplicate':
        publications[1]['native_rows'].append(deepcopy(publications[1]['native_rows'][-1]))
    elif fault == 'char_one_ulp':
        import struct
        row = publications[-1]['native_rows'][0]
        bits = struct.unpack('<I', struct.pack('<f', row['physical']))[0]
        row['physical'] = row['damage'] = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
    elif fault in ('char_no_event_changed', 'token_no_event_changed'):
        for event in events:
            if event['source'] == ('char_a' if fault.startswith('char') else 'token_a'):
                event.update(kind='skill_start', amount=1)
    elif fault == 'no_owned_updates':
        for event in events:
            event.update(kind='skill_start', amount=1)
        for publication in publications:
            for row in publication['native_rows'][:-1]:
                row.update(physical=0, damage=0, healing=0)
    elif fault == 'missing_noncounter_event':
        events.pop(2)
    else:
        events[1], events[2] = events[2], events[1]
    with pytest.raises(ValueError):
        (module.check_pc_f32_counters(events, publications, source_domain='owned') if reader_profile == 'native_pc' else
         module.check_native_f32_counters(events, publications, source_domain='owned', reader_profile='android'))


def test_owned_domain_does_not_call_last_seen_excluded_counter_a_missing_final_publication():
    events, publications = owned_f32_model_window()
    publications[-1]['native_rows'].pop()
    excluded = module.check_pc_f32_counters(events, publications, source_domain='owned')['excluded_native_sources']['trap_generic_owned_test']
    assert excluded['final'] is None and excluded['final_publication_present'] is False
    assert excluded['last_observed']['sequence'] == 3 and excluded['last_observed']['native_row']['true'] == 1000


@pytest.mark.parametrize('domain', ['invented', '', False, 0, None])
def test_counter_helper_never_guesses_an_invalid_source_domain(domain):
    events, publications = f32_model_window()
    with pytest.raises(ValueError, match='source domain'):
        module.check_pc_f32_counters(events, publications, source_domain=domain)


@pytest.mark.parametrize('domain', ['all', 'owned'])
def test_verify_requires_the_f32_flag_for_any_explicit_source_domain(tmp_path, domain):
    with pytest.raises(ValueError, match='requires --pc-f32-counters'):
        module.verify(tmp_path, pc_f32_source_domain=domain)


def test_verifier_owned_result_cannot_be_mistaken_for_all_native_row_counter_acceptance(tmp_path):
    records = pc_capture(tmp_path)
    add_pc_typed_rows(records)
    for record in records:
        rows = (record['data']['native_rows'] if record['kind'] == 'published' else
                (record['data'].get('sample') or {}).get('rows') if record['kind'] == 'lifecycle_observation' else None)
        if rows is not None:
            rows.append(dict(id='trap_test', physical=0, arts=0, true=500, elemental=0, healing=0, damage=500))
    (tmp_path / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    result = module.verify(tmp_path, pc_f32_counters=True, pc_f32_source_domain='owned')
    assert result['pc_f32_counters_verified'] is True and result['pc_f32_source_domain'] == 'owned'
    assert result['full_native_row_counter_model_verified'] is False
    assert result['complete_scope'] == 'published_capture_window_continuity_not_all_source_counter_acceptance'
    with pytest.raises(ValueError, match='unknown PC f32 source domain'):
        module.verify(tmp_path, pc_f32_counters=True, pc_f32_source_domain='invented')


def same_clock_explicit_burst_pairs():
    settlement, burst = explicit_ep_events()
    settlement.update(sequence=1, amount=10, nominal_amount=10, killed=False)
    burst.update(sequence=2, amount=10, real_delta=-10, hp_before=30, hp_after=20, killed=False)
    second_settlement, second_burst = deepcopy(settlement), deepcopy(burst)
    second_settlement['sequence'], second_burst['sequence'] = 3, 4
    second_burst.update(hp_before=20, hp_after=10)
    return [settlement, burst, second_settlement, second_burst]


def explicit_pair_supplement_publications(events):
    return [dict(frame=frame, game_time=frame, native_session='10:1234:stage_a',
        battle_metadata=dict(stage_id='stage_a'), native_rows=[], statistics=dict(rows={}),
        event_watermark=dict(sequence=sequence)) for frame, sequence in ((0, 0), (2, events[-1]['sequence']))]


def test_two_equal_same_clock_explicit_elemental_hits_consume_two_certificates_once_without_fresh_bar_credit():
    events = same_clock_explicit_burst_pairs()
    gate = module.pc_mechanism_evidence(events)['elemental']
    assert gate['invalid_sequences'] == [] and gate['passed'] is False
    assert gate['reason'] == 'no_positive_bar_burst_witness'
    assert [(w['settlement_sequence'], w['burst_sequence']) for w in gate['native_elemental_contribution_witnesses']] == [(1, 2), (3, 4)]
    assert gate['source_null_bar_burst_witnesses'] == []
    supplements = module.check_pc_owner_counter_supplements(events, explicit_pair_supplement_publications(events), {'char_a': 'char_a'})
    assert supplements['source_null_burst_witnesses'] == []
    assert supplements['final_nominal_supplements'] == {}


@pytest.mark.parametrize('fault', ['two_unused_before_first', 'second_missing', 'duplicate_burst', 'actor_uid',
    'source', 'target_uid', 'instance', 'clock', 'nominal', 'incomplete', 'method', 'future_certificate'])
def test_pair_consumption_still_rejects_true_ambiguity_reuse_and_cross_bound_certificates(fault):
    events = same_clock_explicit_burst_pairs()
    if fault == 'two_unused_before_first':
        events.insert(0, dict(deepcopy(events[0]), sequence=0))
    elif fault == 'second_missing':
        events.pop(2)
    elif fault == 'duplicate_burst':
        events.append(dict(deepcopy(events[-1]), sequence=5))
    elif fault == 'actor_uid':
        events[2]['source_character_uid'] = 2
    elif fault == 'source':
        events[2]['source'] = 'char_other'
    elif fault == 'target_uid':
        events[-1]['target_uid'] = '99'
    elif fault == 'instance':
        events[2]['target_instance'] = '0xdead'
    elif fault == 'clock':
        events[2]['frame'] += 1
    elif fault == 'nominal':
        events[-1]['amount'] = 1
    elif fault == 'incomplete':
        events[2]['settlement_complete'] = False
    elif fault == 'method':
        events[2]['proof']['method'] = 'Unrelated.Callback'
    else:
        events[2]['sequence'] = 5
    gate = module.pc_mechanism_evidence(events)['elemental']
    assert gate['invalid_sequences']
    with pytest.raises(ValueError):
        module.check_pc_owner_counter_supplements(events, explicit_pair_supplement_publications(events), {'char_a': 'char_a'})


@pytest.mark.parametrize('source', ['char_unseen', 'token_unseen'])
def test_owned_late_positive_seed_without_covered_raw_source_contribution_is_not_silently_accepted(source):
    events, publications = f32_model_window()
    for publication in publications[1:]:
        publication['native_rows'].append(dict(publication['native_rows'][0], id=source, physical=500, damage=500))
    with pytest.raises(ValueError, match='positive late seed'):
        module.check_pc_f32_counters(events, publications, source_domain='owned')


def test_legal_nonzero_actual_window_seed_does_not_claim_a_modelled_zero_origin():
    events, publications = f32_model_window()
    for publication in publications:
        publication['native_rows'].append(dict(id='token_baseline', physical=500, damage=500,
            arts=0, true=0, elemental=0, healing=0))
    result = module.check_pc_f32_counters(events, publications, source_domain='owned')
    assert result['all_native_sources_verified'] is True and result['native_counter_from_zero_verified'] is False
    assert result['seeds']['token_baseline']['values']['physical'] == 500
    interval = result['seeds']['token_baseline']['unmodeled_prefix']
    assert interval['after_sequence'] == interval['through_sequence'] == 0
    assert interval['counter_event_sequences'] == []


def test_owned_zero_amount_applications_cannot_use_excluded_trap_changes_as_a_positive_numeric_window():
    events, publications = owned_f32_model_window()
    for event in events:
        if event['kind'] != 'skill_start':
            event['amount'] = 0
    for publication in publications:
        for row in publication['native_rows'][:-1]:
            row.update(physical=0, healing=0, damage=0)
    with pytest.raises(ValueError, match='no changed owned counter update'):
        module.check_pc_f32_counters(events, publications, source_domain='owned')


@pytest.mark.parametrize('domain', ['all', 'owned', 'invalid'])
def test_cli_source_domain_is_explicit_and_never_activates_f32_implicitly(tmp_path, domain):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(root / 'acceptance/verify_live_events.py'), str(tmp_path),
               '--pc-f32-source-domain', domain]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert ('invalid choice' if domain == 'invalid' else 'requires --pc-f32-counters') in result.stderr


@pytest.fixture(scope='module')
def mumu_output_elemental_pairs():
    from hashlib import sha256
    path = Path(__file__).parent / 'fixtures/event_probe_business/20261124-mumu-native-elemental-output-fields.json'
    raw = path.read_bytes()
    assert sha256(raw).hexdigest() == '0ca8b6764a699a6ce9d8868f9ffd0fbc8e9e99c7d83ec77624ab33d250e509c3'
    frozen = json.loads(raw)
    assert frozen['provenance']['source_capture_sha256'] == 'e01644d1c8c6fc4b5006e1389f0800d1f745d146f5d4ebc52c406c0666603b31'
    assert frozen['provenance']['accepted_window'] == dict(after_sequence=3, through_sequence=2511)
    assert frozen['evidence_class'] == 'frozen_actual_live_fields_not_a_new_live_receipt'
    assert len(frozen['explicit_native_elemental_output_pairs']) == 24
    return frozen['explicit_native_elemental_output_pairs']


@pytest.mark.parametrize('index', range(24))
def test_frozen_mumu_output_final_certificates_share_the_native_elemental_pair_contract(mumu_output_elemental_pairs, index):
    case = mumu_output_elemental_pairs[index]
    settlement, burst = case['settlement']['event'], case['burst']['event']
    for role in ('settlement', 'burst'):
        assert case[role]['line'] > 0 and len(case[role]['carrier_line_sha256']) == 64
    assert settlement['proof']['method'] == 'Entity.OnAfterOutputDamage'
    assert burst['proof']['method'] == 'Entity.ApplyModifier'
    events = [settlement, burst]
    original = deepcopy(events)
    gate = module.pc_mechanism_evidence(events)['elemental']
    assert not gate['invalid_sequences'] and len(gate['native_elemental_contribution_witnesses']) == 1
    assert gate['passed'] is False and gate['source_null_bar_burst_witnesses'] == []
    witness = gate['native_elemental_contribution_witnesses'][0]
    assert witness['settlement_sequence'] == settlement['sequence'] and witness['burst_sequence'] == burst['sequence']
    publications = explicit_pair_supplement_publications(events)
    publications[-1].update(frame=burst['frame'], game_time=burst['game_time'])
    result = module.check_native_owner_counter_supplements(events, publications, {burst['source']: burst['source']})
    assert result['final_nominal_supplements'] == {} and result['source_null_burst_witnesses'] == []
    assert events == original  # Never rewrite output-final proof.method to ApplyModifier.
    assert module.check_native_owner_counter_supplements is module.check_pc_owner_counter_supplements


@pytest.mark.parametrize('fault', ['unrelated_method', 'unit_output_method', 'burst_output_method', 'null_source',
    'enemy_source', 'missing_actor_uid', 'zero_actor_uid', 'negative_actor_uid', 'overflow_actor_uid',
    'boolean_actor_uid', 'string_actor_uid', 'actor_mismatch', 'incomplete', 'wrong_type', 'boolean_type',
    'missing_nominal', 'wrong_nominal', 'zero_nominal', 'large_nominal', 'boolean_nominal', 'wrong_killed', 'mixed_unused_emitters',
    'burst_only_boolean_uid',
    'wrong_instance', 'cross_clock', 'wrong_hp', 'wrong_delta', 'reused_burst'])
def test_output_emitter_recognition_never_weakens_real_pair_identity_or_method_boundaries(mumu_output_elemental_pairs, fault):
    case = deepcopy(mumu_output_elemental_pairs[0])
    settlement, burst = case['settlement']['event'], case['burst']['event']
    events = [settlement, burst]
    if fault in ('unrelated_method', 'unit_output_method'):
        settlement['proof']['method'] = 'Unrelated.Callback' if fault == 'unrelated_method' else 'Unit.OnAfterOutputDamage'
    elif fault == 'burst_output_method':
        burst['proof']['method'] = 'Entity.OnAfterOutputDamage'
    elif fault in ('null_source', 'enemy_source'):
        for event in events:
            event['source'] = None if fault == 'null_source' else 'enemy_other'
            if fault == 'null_source':
                event.pop('source_character_uid')
    elif fault == 'missing_actor_uid':
        for event in events:
            event.pop('source_character_uid')
    elif fault in ('zero_actor_uid', 'negative_actor_uid', 'overflow_actor_uid', 'boolean_actor_uid', 'string_actor_uid'):
        value = dict(zero_actor_uid=0, negative_actor_uid=-1, overflow_actor_uid=0x100000000,
                     boolean_actor_uid=True, string_actor_uid='1')[fault]
        for event in events:
            event['source_character_uid'] = value
    elif fault == 'actor_mismatch':
        settlement['source_character_uid'] += 1
    elif fault == 'burst_only_boolean_uid':
        settlement['source_character_uid'], burst['source_character_uid'] = 1, True
    elif fault == 'incomplete':
        settlement['settlement_complete'] = False
    elif fault in ('wrong_type', 'boolean_type'):
        settlement['damage_type'] = 1 if fault == 'wrong_type' else True
    elif fault == 'missing_nominal':
        settlement.pop('nominal_amount')
    elif fault in ('wrong_nominal', 'zero_nominal', 'large_nominal', 'boolean_nominal'):
        settlement['nominal_amount'] = dict(wrong_nominal=1, zero_nominal=0, large_nominal=9999, boolean_nominal=True)[fault]
    elif fault == 'wrong_killed':
        settlement['killed'] = not burst['killed']
    elif fault == 'mixed_unused_emitters':
        other = deepcopy(settlement)
        other['sequence'] -= 1
        other['proof']['method'] = 'Entity.ApplyModifier'
        events.insert(0, other)
    elif fault == 'wrong_instance':
        settlement['target_instance'] = '0xdead'
    elif fault == 'cross_clock':
        settlement['frame'] += 1
    elif fault == 'wrong_hp':
        burst['hp_after'] += 1
    elif fault == 'wrong_delta':
        burst['real_delta'] -= 1
    else:
        events.append(dict(deepcopy(burst), sequence=burst['sequence'] + 1))
    assert module.pc_mechanism_evidence(events)['elemental']['invalid_sequences']
    with pytest.raises(ValueError):
        module.check_native_owner_counter_supplements(events, explicit_pair_supplement_publications(events),
            {'char_4247_oxi': 'char_4247_oxi'})


@pytest.mark.parametrize('parent_role', ['damage', 'settlement'])
def test_elemental_emitter_recognition_does_not_expand_nested_parent_output_methods(parent_role):
    events = parent_before_elemental_nonlethal_events()
    events[-1 if parent_role == 'damage' else -2]['proof']['method'] = 'Entity.OnAfterOutputDamage'
    assert module.pc_mechanism_evidence(events)['nested']['passed'] is False


def test_generic_native_f32_profiles_share_identical_math_but_bind_the_actual_reader_evidence():
    events, publications = owned_f32_model_window()
    pc = module.check_pc_f32_counters(events, publications, source_domain='owned')
    assert pc == module.check_native_f32_counters(events, publications, reader_profile='native_pc', source_domain='owned')
    android = module.check_native_f32_counters(events, publications, reader_profile='android', source_domain='owned')
    different_evidence = {'scope', 'reader_profile', 'storage_dtype_evidence'}
    assert {k:v for k,v in pc.items() if k not in different_evidence} == {k:v for k,v in android.items() if k not in different_evidence}
    assert android['reader_profile'] == 'android' and android['scope'] == 'independently_audited_android_published_counter_window'
    assert Path(android['storage_dtype_evidence']['reader_path']).name == 'collector.py'
    assert Path(pc['storage_dtype_evidence']['reader_path']).name == 'probe_native_pc_rows.py'
    assert pc['storage_dtype_evidence']['reader_sha256'] != android['storage_dtype_evidence']['reader_sha256']
    for result in (pc, android):
        assert result['source_identity_verified'] is result['native_counter_from_zero_verified'] is False
        assert result['independent_input_guard_required'] is True
        assert result['closed_source_update_instructions_verified'] is result['tolerance_fallback_used'] is False


@pytest.mark.parametrize('profile', ['mumu', 'unknown', None, True, []])
def test_generic_native_f32_rejects_unknown_reader_profiles_without_guessing(profile):
    events, publications = f32_model_window()
    with pytest.raises(ValueError, match='unknown native f32 reader profile'):
        module.check_native_f32_counters(events, publications, reader_profile=profile)


@pytest.mark.parametrize('profile', ['native_pc', 'android'])
def test_single_certificate_f32_uses_native_modifier_observation_and_preserves_raw(profile):
    from copy import deepcopy
    events, publications = f32_model_window()
    for event in events:
        observed = event['amount']
        event.update(kind='damage_settlement', amount=0, nominal_amount=400,
            proof={'settlement_wire_version': 2, 'native_modifier_observation': {'version': 1, 'method': 'Entity.ApplyModifier', 'amount': observed}})
    frozen = deepcopy(events)
    result = module.check_native_f32_counters(events, publications, reader_profile=profile)
    assert result['exact'] and result['applied_event_count'] == 2
    assert events == frozen
    observations = list(module.native_counter_observations(events))
    assert observations[0][0] is events[0]
    assert observations[0][2] == .1


@pytest.mark.parametrize('fault', ['absent', 'amount_absent', 'nan', 'negative', 'wrong_method', 'wrong_version', 'extra'])
def test_single_certificate_f32_refuses_missing_or_malformed_native_modifier_observation(fault):
    events, publications = f32_model_window()
    for event in events:
        event.update(kind='damage_settlement', nominal_amount=event['amount'],
            proof={'settlement_wire_version': 2, 'native_modifier_observation': {'version': 1, 'method': 'Entity.ApplyModifier', 'amount': event['amount']}})
    witness = events[0]['proof']['native_modifier_observation']
    if fault == 'absent':
        events[0]['proof'].pop('native_modifier_observation')
    elif fault == 'amount_absent':
        witness.pop('amount')
    elif fault in ('nan', 'negative'):
        witness['amount'] = float('nan') if fault == 'nan' else -1
    elif fault == 'wrong_method':
        witness['method'] = 'Entity.OnAfterOutputDamage'
    elif fault == 'wrong_version':
        witness['version'] = True
    else:
        witness['guessed'] = True
    with pytest.raises(ValueError, match='modifier observation|observation amount'):
        module.check_native_f32_counters(events, publications)


def test_single_certificate_full_shield_keeps_zero_native_observation_distinct_from_nominal():
    events, publications = f32_model_window()
    for index, event in enumerate(events):
        amount = 0 if index == 0 else 10
        event.update(kind='damage_settlement', amount=0, nominal_amount=40,
            shield_absorbed=40, proof={'settlement_wire_version': 2, 'native_modifier_observation': {
                'version': 1, 'method': 'Entity.ApplyModifier', 'amount': amount}})
    publications[1]['native_rows'][0].update(physical=0, damage=0)
    publications[2]['native_rows'][0].update(physical=10, damage=10)
    result = module.check_native_f32_counters(events, publications)
    assert result['applied_event_count'] == 2 and result['changed_typed_update_count'] == 1
    assert result['final_typed_values']['char_a']['physical'] == 10


def test_dual_wire_f32_prefers_legacy_counter_record_without_double_counting_certificate():
    events, publications = f32_model_window()
    certificate = dict(events[0], kind='damage_settlement', sequence=2,
                       amount=99, nominal_amount=99, proof={})
    events[1].update(sequence=3, frame=3, game_time=3)
    events.insert(1, certificate)
    publications[-1].update(frame=3, game_time=3, event_watermark={'sequence': 3})
    result = module.check_native_f32_counters(events, publications)
    assert result['applied_event_count'] == 2 and result['final_sequence'] == 3


def test_f32_mixed_v2_enemy_certificate_and_outside_legacy_observation_keep_distinct_counter_events():
    events, publications = f32_model_window()
    events[0].update(kind='damage_settlement', amount=0, nominal_amount=40,
        proof={'settlement_wire_version': 2, 'native_modifier_observation': {
            'version': 1, 'method': 'Entity.ApplyModifier', 'amount': .1}})
    events[1].update(damage_type=2, target='trap_outside')
    publications[-1]['native_rows'][0].update(physical=publications[1]['native_rows'][0]['physical'],
        arts=module.struct.unpack('<f', module.struct.pack('<f', .2))[0])
    row = publications[-1]['native_rows'][0]
    row['damage'] = row['physical'] + row['arts']
    result = module.check_native_f32_counters(events, publications)
    assert result['exact'] and result['applied_event_count'] == 2
    assert result['final_typed_values']['char_a']['physical'] == row['physical']
    assert result['final_typed_values']['char_a']['arts'] == row['arts']
