"""Player/UID isolation at the shared ordered receiver, using a real failure.

The focused fixture preserves original actor evidence. Its selected records
use a consecutive test transport; it does not declare production coverage.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from event_stream import StreamGap
from roster import CharacterEntry, RosterSnapshot
from snapshot_adapter import BattleStatisticsAdapter


FIXTURE = Path(__file__).with_name('fixtures') / 'event_probe_business' / (
    '20261122-autochess-grey-throat-player-isolation.json')
FIXTURE_SHA256 = 'f43990208fb6c760664421b6c547ac469b27553e80354125f120047be373bbbf'
SOURCE = 'char_367_swllow'
LOCAL_UID, FOREIGN_UID = 3145802, 4194395


def scene():
    data = FIXTURE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == FIXTURE_SHA256
    return json.loads(data)


def actor(cid=SOURCE, uid=LOCAL_UID, side=3, *, host=None):
    return CharacterEntry(cid, uid, host is not None, False,
                          host.cid if host else None, host.unique_id if host else None, side)


def counter(cid=SOURCE, damage=0, healing=0):
    return dict(id=cid, damage=damage, healing=healing, physical=damage,
                arts=0, true=0, elemental=0, raw_output=damage)


def setup(entries=None, *, platform='native_pc', end_frame=3000, end_time=100,
          damage=0, healing=0, source_events=()):
    entries = entries or (actor(), actor(uid=FOREIGN_UID, side=4))
    roster = RosterSnapshot(0, tuple(entries), frozenset(), True, 3)
    metadata = {'platform': platform}
    sample = dict(session='test-player-isolation', frame=0, game_time=0,
                  rows=[counter()], battle_metadata=metadata)
    adapter = BattleStatisticsAdapter()
    options = dict(source_attribution=True, settlement_version=7)
    adapter.update(sample, roster, **options)
    adapter.start_events(session=sample['session'], frame=0, sequence=0)
    stats = adapter.statistics
    stats.settlement_enabled = stats.partial_damage_enabled = stats.reconcile_damage_scope = True
    stats.damage_settlement_version = 7
    final = dict(sample, frame=end_frame, game_time=end_time,
                 rows=[counter(damage=damage, healing=healing)])
    adapter.update(final, replace(roster, frame=end_frame), source_events=source_events, **options)
    return adapter


def deliver(adapter, record, *, sequence=None):
    original = deepcopy(record)
    payload = {k: v for k, v in original.items() if k != 'proof'}
    if payload['kind'] in ('skill_start', 'skill_end'):
        payload['skill_method'] = original.get('proof', {}).get('method')
    if sequence is not None:
        payload['sequence'] = sequence
    adapter.accept_event(session=adapter.session, replay_record=original, **payload)


def finish(adapter):
    stream = adapter.event_stream
    stream.watermark(session=adapter.session, sequence=stream.sequence,
                     frame=adapter.last_frame, game_time=adapter.last_game_time)
    return adapter.statistics.result()


def event(kind, *, uid=LOCAL_UID, sequence=1, amount=1, **fields):
    return dict(sequence=sequence, frame=1, game_time=1, source=SOURCE,
                source_character_uid=uid, kind=kind, amount=amount, **fields)


def hit(**fields):
    return event('damage_settlement', amount=10, target='enemy_1071_dftman',
                 target_uid='77', target_instance='test-target', damage_type=1,
                 shield_absorbed=0, settlement_complete=True, nominal_amount=10,
                 killed=False, **fields)


def test_frozen_foreign_cast_keeps_all_72_local_skill_classifications():
    captured = scene()
    records = [r['event'] for r in captured['original_records']]
    checkpoint = captured['checkpoint']
    adapter = setup([CharacterEntry(**r) for r in captured['roster_entries']],
                    damage=checkpoint['local_nominal_damage'], source_events=records)
    before_cast = None
    for number, record in enumerate(records, 1):
        if record['sequence'] == checkpoint['foreign_cast_sequence']:
            meter = adapter.statistics.settled[SOURCE]
            before_cast = (meter.hits, meter.raw_skill_hits)
        deliver(adapter, record, sequence=number)
    row = finish(adapter)['rows'][SOURCE]
    lane = row['settled_damage']
    assert before_cast == (26, 26)
    assert lane['observed_hits'] == 72 and lane['unverified_hits'] == 0
    assert lane['effective_damage'] == pytest.approx(checkpoint['local_hp_damage'])
    assert lane['overkill_matched_hits'] == lane['mitigation_matched_hits'] == 72
    assert lane['skill_damage_unverified_share'] == 0, lane['skill_damage_unverified_share']
    assert lane['skill_effective_damage_unverified_share'] == 0
    assert lane['skill_split_complete'] and lane['skill_split_effective_complete']
    assert lane.get('skill_split_reason') is None
    assert SOURCE not in adapter.statistics.skill_incomplete
    assert SOURCE not in adapter.statistics.settlement_skill_unassigned
    assert row['observed_damage'] == pytest.approx(checkpoint['local_nominal_damage'])
    assert row['observed_damage'] == lane['observed_damage'], 'modern observations come only from local certificates'
    assert not adapter.statistics.incoming_damage_sources, 'foreign HP evidence cannot mark a local gap'
    assert adapter.source_observed_uids[SOURCE] == {LOCAL_UID}
    assert not adapter.event_stream.closed


@pytest.mark.parametrize('kind', ['damage', 'healing', 'hp_damage', 'shield_absorbed',
                                  'elemental_bar', 'elemental_burst'])
def test_same_cid_foreign_legacy_records_never_enter_local_accounts(kind):
    own = event(kind, amount=10)
    if kind in ('damage', 'hp_damage', 'elemental_burst'):
        own.update(target='enemy_1071_dftman', target_instance='test-target',
                   real_delta=-10, hp_before=100, hp_after=90, killed=False,
                   damage_type=5 if kind == 'elemental_burst' else 1,
                   target_type=0, cancel_reason=0, shield_absorbed=0)
    if kind == 'elemental_bar':
        own.update(target_instance='test-target', target_uid='77')
    foreign = dict(own, source_character_uid=FOREIGN_UID, sequence=2, amount=1000)
    adapter = setup(damage=10 if kind in ('damage', 'elemental_burst') else 0,
                    healing=10 if kind == 'healing' else 0, source_events=[own, foreign])
    deliver(adapter, own)
    before = deepcopy(adapter.statistics.events)
    deliver(adapter, foreign)
    result = finish(adapter)
    row = result['rows'][SOURCE]
    assert adapter.statistics.events == before
    assert row['observed_damage'] is None, 'legacy output cannot fabricate a complete modern settlement'
    assert row['settled_damage']['observed_hits'] == 0
    assert row['observed_healing'] == (10 if kind == 'healing' else 0)
    assert row['burst_count'] == (1 if kind == 'elemental_burst' else 0)
    assert row['shield_absorbed'] == (10 if kind == 'shield_absorbed' else 0)
    assert adapter.source_observed_uids[SOURCE] == {LOCAL_UID}
    assert adapter.event_stream.sequence == 2 and not adapter.event_stream.closed
    assert row.get('damage_scope_reason') is None


@pytest.mark.parametrize('uid', [None, 999999])
@pytest.mark.parametrize('kind', ['damage', 'healing', 'hp_damage', 'elemental_burst'])
def test_unknown_uid_is_neither_foreign_nor_credited_by_shared_cid(uid, kind):
    record = event(kind, uid=uid, amount=10)
    if kind != 'healing':
        record.update(target='enemy_1071_dftman', target_instance='test-target', damage_type=1)
    adapter = setup(source_events=[record])
    assert adapter._settlement_identity(SOURCE, uid)[0] == 'unknown'
    deliver(adapter, record)
    row = finish(adapter)['rows'][SOURCE]
    assert row['observed_damage'] in (0, None) and row['observed_healing'] in (0, None)
    assert row['burst_count'] == 0
    assert not adapter.statistics.events
    assert not adapter.statistics.incoming_damage_sources
    assert adapter.event_stream.sequence == 1 and not adapter.event_stream.closed


@pytest.mark.parametrize('uid', [LOCAL_UID, None, 999999])
def test_own_or_unknown_unmatched_waiting_cast_retains_skill_gap(uid):
    adapter = setup()
    deliver(adapter, event('skill_end', uid=uid, skill_key='skchr_swllow_2',
                           skill_reason=1, skill_wait_for_end=True,
                           proof={'method': 'BasicSkill.OnCastFinish'}))
    deliver(adapter, hit(sequence=2))
    lane = finish(adapter)['rows'][SOURCE]['settled_damage']
    assert lane['skill_damage_unverified_share'] == 1
    assert not lane['skill_split_complete']
    assert lane['skill_split_reason'] == '技能事件的角色归属未确认'
    assert not adapter.event_stream.closed


def test_unknown_settlement_uid_remains_visible_as_unassigned_evidence():
    adapter = setup()
    deliver(adapter, hit(uid=999999))
    result = finish(adapter)
    assert result['settlement_unassigned']['hits'] == 1
    assert result['settlement_unassigned']['hp'] == 10
    assert result['rows'][SOURCE]['settled_damage']['effective_damage'] == 0


def test_local_and_foreign_summons_use_their_verified_hosts():
    local, foreign = actor(), actor(uid=FOREIGN_UID, side=4)
    token, other_token = actor('token_test', 10, host=local), actor('token_test', 11, 4, host=foreign)
    adapter = setup([local, foreign, token, other_token], damage=10)
    for number, uid in enumerate((10, 11), 1):
        record = event('damage', uid=uid, sequence=number, amount=10,
                       target='enemy_1071_dftman', real_delta=-10, hp_before=100,
                       hp_after=90, killed=False, target_type=0, damage_type=1,
                       cancel_reason=0, shield_absorbed=0)
        deliver(adapter, dict(record, source='token_test'))
    deliver(adapter, dict(event('skill_start', uid=10, sequence=3, skill_key='summon_skill'),
                          source='token_test'))
    for number, uid in enumerate((10, 11), 4):
        deliver(adapter, dict(hit(uid=uid, sequence=number), source='token_test'))
    result = finish(adapter)
    lane = result['rows'][SOURCE]['settled_damage']
    assert lane['effective_damage'] == 10 and lane['observed_hits'] == 1
    assert lane['observed_non_skill_damage'] == 10 and lane['skill_damage_unverified_share'] == 0
    assert result['rows'][SOURCE]['observed_damage'] == 10
    assert SOURCE not in adapter.statistics.skill_active
    assert not adapter.statistics.settlement_skill_active
    assert result['settlement_unassigned']['hits'] == 0
    assert adapter._settlement_identity('token_test', 11) == ('foreign', None, None)
    assert adapter._settlement_identity('token_test', None) == ('unknown', None, None)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_single_player_keeps_local_legacy_and_strict_damage_and_skill_evidence(platform):
    adapter = setup([actor()], platform=platform, damage=10)
    deliver(adapter, event('skill_start', skill_key='skchr_swllow_2',
                           proof={'method': 'Character.SwitchToSkillState'}))
    deliver(adapter, event('skill_end', sequence=2, skill_key='skchr_swllow_2',
                           skill_reason=1, skill_wait_for_end=True,
                           proof={'method': 'BasicSkill.OnCastFinish'}))
    deliver(adapter, event('damage', sequence=3, amount=10, target='enemy_1071_dftman',
                           real_delta=-10, hp_before=100, hp_after=90, killed=False,
                           target_type=0, damage_type=1, cancel_reason=0, shield_absorbed=0))
    deliver(adapter, hit(sequence=4))
    row = finish(adapter)['rows'][SOURCE]
    assert row['observed_damage'] == 10 and row.get('damage_scope_reason') is None
    assert row['settled_damage']['observed_skill_damage'] == 10
    assert row['settled_damage']['skill_split_complete']
    assert row['settled_damage']['complete'] and not adapter.event_stream.closed


def test_foreign_payload_advances_original_sequence_and_still_enforces_replay_and_watermark():
    adapter = setup()
    record = event('skill_end', uid=FOREIGN_UID, skill_key='skchr_swllow_2',
                   skill_reason=1, skill_wait_for_end=True,
                   proof={'method': 'BasicSkill.OnCastFinish'})
    deliver(adapter, record)
    before = deepcopy(adapter.statistics.result())
    deliver(adapter, record)
    assert adapter.statistics.result() == before
    assert adapter.event_stream.received[1] == record
    finish(adapter)
    with pytest.raises(StreamGap, match='conflicts'):
        deliver(adapter, dict(record, amount=2))


def test_shared_receiver_retires_foreign_cast_before_local_skill_validation():
    adapter = setup()
    stream = adapter.event_stream
    before = deepcopy(adapter.statistics.result())
    stream.accept(session=adapter.session, sequence=1, frame=1, game_time=1,
                  source=SOURCE, source_character_uid=FOREIGN_UID,
                  kind='skill_end', amount=1, skill_method='BasicSkill.OnCastFinish',
                  skill_key='skchr_swllow_2', skill_wait_for_end=True, skill_reason=1,
                  settlement_identity=('foreign', None, None))
    assert adapter.statistics.result() == before
    local_hit = {k: v for k, v in hit(sequence=2).items()}
    stream.accept(session=adapter.session, settlement_identity=('local', SOURCE, LOCAL_UID), **local_hit)
    assert stream.sequence == 2 and stream.frame == 1 and stream.game_time == 1
    assert finish(adapter)['rows'][SOURCE]['settled_damage']['observed_hits'] == 1


def test_source_null_elemental_burst_keeps_existing_local_target_binding():
    adapter = setup()
    deliver(adapter, event('elemental_bar', target_instance='test-target', target_uid='77'))
    burst = event('elemental_burst', sequence=2, amount=10, target='enemy_1071_dftman',
                  target_instance='test-target', damage_type=5, real_delta=-10,
                  hp_before=100, hp_after=90, killed=False, shield_absorbed=0)
    burst.update(source=None, source_character_uid=None)
    deliver(adapter, burst)
    row = finish(adapter)['rows'][SOURCE]
    assert row['burst_settlement'] == 10 and row['burst_count'] == 1


def test_foreign_elemental_bar_replaces_local_target_witness_before_sourceless_burst():
    adapter = setup()
    bar = event('elemental_bar', target_instance='test-target', target_uid='77')
    deliver(adapter, bar)
    deliver(adapter, dict(bar, sequence=2, source_character_uid=FOREIGN_UID))
    assert adapter.statistics.settlement_bars['77'][2:] == ('foreign', None, None)
    assert 'test-target' not in adapter.statistics.last_elemental
    burst = dict(hit(sequence=3), source=None, source_character_uid=None, damage_type=5)
    deliver(adapter, burst)
    result = finish(adapter)
    assert result['rows'][SOURCE]['settled_damage']['effective_damage'] == 0
    assert result['settlement_unassigned']['hits'] == 0


@pytest.mark.parametrize('kind', ['elemental_burst', 'damage_settlement'])
def test_explicit_foreign_burst_consumes_previous_local_target_witness(kind):
    adapter = setup()
    deliver(adapter, event('elemental_bar', target_instance='test-target', target_uid='77'))
    if kind == 'damage_settlement':
        burst = dict(hit(uid=FOREIGN_UID, sequence=2), damage_type=5)
    else:
        burst = event(kind, uid=FOREIGN_UID, sequence=2, amount=10,
                      target='enemy_1071_dftman', target_instance='test-target',
                      real_delta=-10, hp_before=100, hp_after=90, killed=False, damage_type=5)
    deliver(adapter, burst)
    assert not adapter.statistics.settlement_bars
    assert 'test-target' not in adapter.statistics.last_elemental
    assert finish(adapter)['rows'][SOURCE]['burst_count'] == 0


@pytest.mark.parametrize('change,reason', [({'sequence': 2}, 'sequence gap'),
                                          ({'frame': -1}, 'clock is out of order'),
                                          ({'game_time': -1}, 'clock is out of order')])
def test_foreign_actor_cannot_bypass_global_stream_validation(change, reason):
    adapter = setup()
    record = event('damage', uid=FOREIGN_UID)
    with pytest.raises(StreamGap, match=reason):
        deliver(adapter, dict(record, **change))
    assert adapter.event_stream.closed

