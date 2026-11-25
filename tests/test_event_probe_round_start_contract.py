"""Verify the producer zero origin against independent current platform roots."""

from copy import deepcopy
from hashlib import sha256
import struct

import pytest

from autochess_context import normalize_native_autochess
from event_probe import (
    StreamGap, autochess_round_start_live_skill_uids, autochess_round_start_origin,
    autochess_round_start_skill_known, contract_for, ready_probe_modes, verified_autochess_round_start,
)
from event_stream import baseline_barrier


STAGE = 'Activities/ACT3AUTOCHESS/owned_test'
ROOTS = {key: hex(index * 0x10000) for index, key in enumerate(
    ('controller', 'native', 'logger', 'stats', 'level'), 1)}
SCOPE = ':'.join(ROOTS.values()) + ':autochess:3'


def raw_context(frame, when, cid):
    return dict(protocol_version=1, status='available', verified_same_frame=True,
                frame=frame, game_time=when, scope_key=SCOPE, phase=4, round=2,
                match_key='0x60000:0x70000', collection_mode='all', self_player_index=3,
                players=[dict(index=i, uid=f'00{i}', nick_name=f'fixture {i}') for i in range(1, 5)],
                memberships=[dict(cid=cid, data_uid=301, player_index=3, player_side=3,
                                  is_token=False, is_predefined=False, host_key=None, host_uid=None)])


def event(sequence, frame, when):
    return dict(sequence=sequence, frame=frame, game_time=when, source='char_zero',
                source_character_uid=301, amount=1, kind='skill_start',
                proof={'method': 'Character.SwitchToSkillState'},
                native_autochess_scope=dict(protocol_version=1, status='available', verified_same_frame=True,
                    scope_key=SCOPE, phase=4, round=2, collection_mode='all', frame=frame, game_time=when))


def inputs(platform='native_pc', *, events=None):
    events = [event(42, 0, 0), event(43, 13, .5)] if events is None else events
    pid, birth, serial = 77, 888, 3
    frame, when = 30, 1
    build = 'fixture-build'
    pointers = [int(value, 16) for value in ROOTS.values()]
    stamp = dict(native_identity=deepcopy(ROOTS), frame=frame, native_fixed_time=str(1 << 32), stage_id=STAGE)
    metadata = dict(platform=platform, stage_id=STAGE, source_build=build, outcome='later_only')
    if platform == 'native_pc':
        digest = sha256(struct.pack('<6Q', *pointers, serial)).hexdigest()
        session, identity = f'{pid}:{birth}:{digest}', [digest]
        physical = dict(native_window_guard=dict(version=1, source='native_pc_guarded_read',
                        before=stamp, after=deepcopy(stamp), source_build=build, battle_serial=serial,
                        process_instance_before=[pid, birth], process_instance_after=[pid, birth]))
    else:
        controller, native, logger, stats, level = pointers
        session, identity = f'{pid}:{controller}:{stats}:{level}', [controller, native]
        physical = dict(observer_logger=logger, guard=dict(start=frame, end=frame, complete=True))
    raw = raw_context(frame, when, 'char_later')
    context = normalize_native_autochess(raw, expected_frame=frame, expected_game_time=when,
                                         expected_scope_key=SCOPE)
    sample = dict(session='feed:20', native_session=session, battle_identity=identity,
                  state=2, frame=frame, game_time=when, battle_metadata=metadata,
                  native_counter_scope='events_only', autochess_context=context,
                  rows=[{'id': 'char_later', 'damage': 600}], global_total=600, global_heal=50,
                  enemy={'frame': frame}, roster={'frame': frame}, presence=[301],
                  autochess_snapshot_anchor=dict(frame=frame, game_time=when), **physical)
    common = dict(native_identity=deepcopy(ROOTS), native_generation=5, native_process_pid=pid,
                  native_stage_id=STAGE)
    mark = dict(**common, sequence=41 + len(events), epoch=100 + len(events), active=0, active_hooks=[],
                frame=frame, game_time=when, native_frame=frame, native_fixed_time=str(1 << 32),
                native_session=session, source_build=build, native_autochess_context=raw,
                damage_settlement_scope=5, error=None, stopped=False)
    quality = dict(quality_contract_version=1, source_attribution_version=1, partial_damage_version=7,
                   damage_settlement_quality_version=1, damage_settlement_scope=5, damage_unknown=False,
                   engagement_unknown=False, shield_unknown=False, skill_unknown=False, skill_ready=True,
                   mitigation_ready=True, mitigation_unknown=False, enemy_damage_ready=True,
                   enemy_damage_unknown=False, source_character_uid_ready=True, unknown_sources=[],
                   engagement_unknown_sources=[])
    point = dict(**deepcopy(common), version=1, source_method='BattleController.ClearRunTimeData',
                 native_fixed_time='0', frame=0, game_time=0, sequence=41, epoch=100, active=0,
                 native_autochess_context=raw_context(0, 0, 'char_zero'), quality=quality,
                 live_character_collection_empty=True)
    drained = dict(mark, events=[], autochess_round_start=point, autochess_round_start_events=events,
                   autochess_round_start_events_complete=True, probe_contract=contract_for(sample=sample).describe())
    return sample, drained, mark


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
def test_real_zero_roster_is_used_on_both_backends_and_later_read_is_only_an_identity_anchor(platform):
    sample, drained, mark = inputs(platform)
    originals = deepcopy((sample, drained, mark))
    zero, opening, empty, replay = verified_autochess_round_start(sample, drained, mark)
    assert autochess_round_start_origin(zero)
    assert autochess_round_start_skill_known(zero)
    assert baseline_barrier(opening, zero, opening, contract=contract_for(sample=zero)) == 41
    assert opening['frame'] == opening['native_frame'] == opening['game_time'] == 0
    assert opening['epoch'] == 100 and opening['native_fixed_time'] == '0'
    assert zero['rows'] == [] and zero['global_total'] is zero['global_heal'] is None
    assert zero['autochess_context']['memberships'][0]['cid'] == 'char_zero'
    assert zero['autochess_snapshot_anchor'] == sample['autochess_snapshot_anchor']
    assert zero.get('native_window_guard') == sample.get('native_window_guard')
    assert not any(key in zero for key in ('enemy', 'roster', 'presence'))
    assert 'outcome' not in zero['battle_metadata']
    assert empty['events'] == empty['baseline_discarded_skill_events'] == []
    assert empty['baseline_discarded_skill_events_complete'] is True
    assert replay['events'] == drained['autochess_round_start_events']
    assert 'autochess_round_start' not in replay and 'autochess_round_start_events' not in replay
    replay['events'][0]['proof']['method'] = 'owned mutation'
    zero['autochess_round_start']['quality']['skill_ready'] = False
    assert (sample, drained, mark) == originals


@pytest.mark.parametrize('field,value', [('version', True), ('source_method', 'phase setter'),
    ('frame', False), ('game_time', False), ('native_fixed_time', '00'), ('sequence', 44),
    ('epoch', 103), ('active', False), ('native_process_pid', 78), ('native_generation', 4),
    ('native_stage_id', 'unrelated_stage'), ('native_identity', {'controller': '0x90000'}),
    ('live_character_collection_empty', 1), ('quality', {})])
def test_malformed_origin_never_creates_a_new_baseline(field, value):
    sample, drained, mark = inputs()
    drained['autochess_round_start'][field] = value
    with pytest.raises(StreamGap):
        verified_autochess_round_start(sample, drained, mark)


@pytest.mark.parametrize('fault', ['foreign_scope', 'wrong_mode', 'unknown_stamp', 'backwards',
    'future_time', 'missing_sequence', 'gap', 'duplicate_after', 'completion_bool', 'account_swap',
    'self_swap', 'partial_input', 'bad_mark_quality_scope', 'bad_point_quality_scope'])
def test_corrupt_prefix_or_source_binding_is_rejected_before_any_replay(fault):
    sample, drained, mark = inputs()
    start = drained['autochess_round_start']
    raw = drained['autochess_round_start_events']
    if fault == 'foreign_scope':
        raw[0]['native_autochess_scope']['scope_key'] = SCOPE + '1'
    elif fault == 'wrong_mode':
        raw[0]['native_autochess_scope']['collection_mode'] = 'local'
    elif fault == 'unknown_stamp':
        raw[0]['native_autochess_scope']['status'] = 'unknown'
    elif fault == 'backwards':
        raw[0] = event(42, 17, .75)
    elif fault == 'future_time':
        raw[1] = event(43, 13, 2)
    elif fault == 'missing_sequence':
        raw[0].pop('sequence')
    elif fault == 'gap':
        raw.pop(0)
    elif fault == 'duplicate_after':
        raw[1] = deepcopy(raw[0])
    elif fault == 'completion_bool':
        drained['autochess_round_start_events_complete'] = 1
    elif fault == 'account_swap':
        start['native_autochess_context']['players'][0]['uid'] = '099'
    elif fault == 'self_swap':
        start['native_autochess_context']['self_player_index'] = 2
    elif fault == 'partial_input':
        drained.pop('autochess_round_start')
    elif fault == 'bad_mark_quality_scope':
        mark['damage_settlement_scope'] = 5.0
    elif fault == 'bad_point_quality_scope':
        start['quality']['damage_settlement_scope'] = True
    original = deepcopy((sample, drained, mark))
    with pytest.raises(StreamGap):
        verified_autochess_round_start(sample, drained, mark)
    assert (sample, drained, mark) == original


def test_host_accepts_a_reassembled_prefix_larger_than_the_single_source_read_limit():
    events = [event(sequence, 13, .5) for sequence in range(42, 2092)]
    sample, drained, mark = inputs(events=events)
    _, _, _, replay = verified_autochess_round_start(sample, drained, mark)
    assert replay['events'] == events and len(replay['events']) == 2050


@pytest.mark.parametrize('reason', ['prefix_already_acknowledged', 'invalid_prefix_scope_or_clock'])
def test_unavailable_round_start_prefix_uses_ordinary_legacy_acquisition(reason):
    sample, drained, mark = inputs()
    for field in ('autochess_round_start', 'autochess_round_start_events',
                  'autochess_round_start_events_complete'):
        drained.pop(field)
    drained['autochess_round_start_unavailable'] = reason
    assert verified_autochess_round_start(sample, drained, mark) is None


def test_round_start_installation_diagnostics_are_typed():
    available = dict(autochess_round_start_ready=True, autochess_round_start_error=None)
    unsupported = dict(autochess_round_start_ready=False, autochess_round_start_error='unsupported signature')
    malformed = dict(autochess_round_start_ready=1, autochess_round_start_error={})
    assert ready_probe_modes(available) == available
    assert ready_probe_modes(unsupported) == unsupported
    assert ready_probe_modes(malformed) == {}


def test_internal_origin_cannot_forge_skill_proof_from_shallow_coverage_booleans():
    zero, _, _, _ = verified_autochess_round_start(*inputs())
    assert autochess_round_start_skill_known(zero)
    point = zero['autochess_round_start']
    point['live_character_collection_empty'] = False
    assert autochess_round_start_origin(zero) and not autochess_round_start_skill_known(zero)
    point['live_character_collection_empty'] = True
    point['quality'] = {'skill_ready': True, 'skill_unknown': False}
    assert not autochess_round_start_origin(zero) and not autochess_round_start_skill_known(zero)


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('uids,empty,expected', [
    ([], True, frozenset()), ([301, 0xffffffff], False, frozenset({301, 0xffffffff})),
    (None, False, None), (None, None, None),
])
def test_round_start_live_uid_evidence_keeps_existence_separate_from_counter_rows(platform, uids, empty, expected):
    sample, drained, mark = inputs(platform)
    point = drained['autochess_round_start']
    point.update(version=2, live_character_collection_empty=empty, live_character_data_uids=uids)
    zero, _, _, _ = verified_autochess_round_start(sample, drained, mark)
    assert autochess_round_start_origin(zero)
    assert autochess_round_start_live_skill_uids(zero) == expected
    zero['autochess_round_start']['quality']['skill_ready'] = False
    assert autochess_round_start_origin(zero)
    assert autochess_round_start_live_skill_uids(zero) is None


@pytest.mark.parametrize('uids,empty', [
    ([1, 1], False), ([2, 1], False), ([True], False), ([0], False), ([-1], False),
    ([0x100000000], False), ([1], True), ([1], None), ([], False),
    (list(range(1, 130)), False), ({'uid': 1}, False),
])
def test_bad_live_uid_declaration_cannot_certify_round_start(uids, empty):
    sample, drained, mark = inputs()
    drained['autochess_round_start'].update(
        version=2, live_character_collection_empty=empty, live_character_data_uids=uids)
    with pytest.raises(StreamGap):
        verified_autochess_round_start(sample, drained, mark)
