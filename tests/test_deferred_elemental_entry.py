"""Actual owned Born/tile/bar/burst callbacks; not a replay of R14 missing raw guards."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_probe import event_processing_payload, validated_enemy_birth_proof
from event_stream import EventStream
from stats_core import Statistics

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def producer():
    return json.loads(subprocess.check_output(['node', str(ROOT / 'tests/event_probe_deferred_elemental_entry_fixture.js')],
        cwd=ROOT, text=True, encoding='utf8'))


def replay(packet, *, legacy=False, foreign=False, gap=False):
    stats = Statistics('deferred', 0, event_coverage=True)
    stats.register_source('char_elemental_fixture', 'owner')
    stats.snapshot(0, {'char_elemental_fixture': dict(damage=0, healing=0)})
    stats.settlement_enabled = True
    stats.damage_settlement_version = 7
    stream = EventStream(stats, session='deferred', baseline_frame=0, baseline_sequence=0)
    for raw in packet['batch']['events']:
        stats.advance(raw['game_time'])
        if gap and raw['kind'] == 'enemy_enter':
            stats.invalidate_events(2)
        payload = event_processing_payload(raw)
        if legacy:
            payload.pop('enemy_birth_proof', None)
        identity = (('foreign', None, None) if foreign else ('local', 'owner', 1010)) if raw.get('source') == 'char_elemental_fixture' else ('unknown', None, None)
        stream.accept(session='deferred', settlement_identity=identity, replay_record=raw, **payload)
    stream.watermark(session='deferred', sequence=stream.sequence, frame=120, game_time=4)
    return stats, stream


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_real_deferred_admission_keeps_only_proved_post_born_bar_and_replays_once(producer, platform):
    packet = deepcopy(producer[platform]['valid'])
    original = deepcopy(packet)
    before, _ = replay(packet, legacy=True)
    assert before.settlement_unassigned['hits'] == 1 and not before.settled
    stats, stream = replay(packet)
    assert stats.settlement_unassigned['hits'] == 0
    lane = stats.result()['rows']['owner']['settled_damage']
    assert lane['observed_damage'] == lane['known_hp_damage'] == 50
    assert lane['observed_hits'] == 1 and lane['unverified_hits'] == 0
    assert not stats.settlement_bars and not stats.settlement_bar_origins
    for raw in packet['batch']['events']:
        identity = ('local', 'owner', 1010) if raw.get('source') == 'char_elemental_fixture' else ('unknown', None, None)
        stream.accept(session='deferred', settlement_identity=identity, replay_record=raw, **event_processing_payload(raw))
    assert stats.result()['rows']['owner']['settled_damage'] == lane
    assert packet == original


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('mode', ['before_born', 'reborn', 'cycle_before_entry', 'new_born_cycle', 'uid_changed', 'inside_born'])
def test_old_bar_or_real_rebirth_cannot_borrow_initial_born_certificate(producer, platform, mode):
    stats, _ = replay(producer[platform][mode])
    assert stats.settlement_unassigned['hits'] == 1
    assert not stats.settled


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_new_bar_after_real_rebirth_recovers_future_owner(producer, platform):
    stats, _ = replay(producer[platform]['reborn_new_bar'])
    assert stats.settlement_unassigned['hits'] == 0
    assert stats.result()['rows']['owner']['settled_damage']['observed_damage'] == 50


@pytest.mark.parametrize('fault', ['missing', 'pointer', 'uid', 'clock', 'sequence', 'scope'])
def test_malformed_or_other_body_certificate_does_not_promote_bar_owner(producer, fault):
    packet = deepcopy(producer['native_pc']['valid'])
    entry = next(e for e in packet['batch']['events'] if e['kind'] == 'enemy_enter')
    proof = entry['proof']['native_enemy_birth']
    if fault == 'missing':
        del proof['fixed']
    elif fault == 'pointer':
        proof['target_instance'] = '0xdead0'
        assert validated_enemy_birth_proof(entry) is not None
    elif fault == 'uid':
        proof['target_uid'] = '13'
    elif fault == 'clock':
        proof['fixed'] = '1'
    elif fault == 'sequence':
        proof['sequence_before_born'] = entry['sequence']
    else:
        proof['native_autochess_scope'] = dict(protocol_version=1, status='available', verified_same_frame=True,
            scope_key='old', phase=4, round=1, collection_mode='all', frame=30, game_time=1)
    stats, _ = replay(packet)
    assert stats.settlement_unassigned['hits'] == 1 and not stats.settled


def test_foreign_bar_negative_witness_survives_verified_first_admission(producer):
    stats, _ = replay(producer['native_pc']['valid'], foreign=True)
    assert stats.settlement_unassigned['hits'] == 0 and not stats.settled


def test_observation_gap_prevents_first_admission_certificate_from_preserving_bar(producer):
    stats, _ = replay(producer['native_pc']['valid'], gap=True)
    assert stats.settlement_unassigned['hits'] == 1 and not stats.settled


@pytest.mark.parametrize('fault', ['duplicate_entry', 'invalid_reborn'])
def test_rejected_new_lifecycle_event_cannot_leave_an_old_bar_owner_usable(producer, fault):
    packet = deepcopy(producer['native_pc']['valid'])
    entry = deepcopy(next(e for e in packet['batch']['events'] if e['kind'] == 'enemy_enter'))
    entry.update(sequence=3, frame=105, game_time=3.5)
    if fault == 'invalid_reborn':
        entry.update(kind='enemy_reborn', amount=100, hp_before=0, hp_after=99,
                     phase_total_hp=200, phase_count=2, phase_from_birth=True)
    records = packet['batch']['events']
    for raw in records:
        if raw['sequence'] >= 3:
            raw['sequence'] += 1
    records.insert(2, entry)
    stats, _ = replay(packet)
    assert stats.settlement_unassigned['hits'] == 1 and not stats.settled


@pytest.mark.parametrize('fault', ['version_bool', 'huge_fixed', 'bad_pointer', 'missing_clock'])
def test_birth_certificate_decoder_rejects_untrusted_shapes_without_throwing(producer, fault):
    entry = deepcopy(next(e for e in producer['native_pc']['valid']['batch']['events'] if e['kind'] == 'enemy_enter'))
    proof = entry['proof']['native_enemy_birth']
    if fault == 'version_bool':
        proof['version'] = True
    elif fault == 'huge_fixed':
        proof['fixed'] = '9' * 10000
    elif fault == 'bad_pointer':
        proof['target_instance'] = '0x1'
    else:
        entry.pop('game_time')
    assert validated_enemy_birth_proof(entry) is None

@pytest.mark.parametrize('platform', ['native_pc', 'android'])
def test_lifecycle_raw_proofs_replay_and_default_history_roundtrip(producer, platform, tmp_path):
    from dataclasses import asdict
    from battle_history import BattleRecord, BattleHistory
    from capture_battles import replay_product
    from roster import CharacterEntry, RosterSnapshot
    from test_lifecycle_events import native_connection
    packet = producer[platform]['valid']
    runtime, collector, observer, snapshot, state, events, _, _ = native_connection()
    original_read = snapshot.read
    def read():
        sample = original_read()
        sample['rows'][0]['id'] = 'char_elemental_fixture'
        return sample
    snapshot.read = read
    snapshot.read_roster = lambda: RosterSnapshot(snapshot.frame,
        (CharacterEntry('char_elemental_fixture', 1010, False, False, None, None, 3),),
        present_unique_ids=frozenset({1010}), present_known=True, local_player_side=3)
    quality_fields = ('quality_contract_version', 'partial_damage_version', 'source_attribution_version',
        'source_character_uid_ready', 'damage_unknown', 'engagement_unknown', 'shield_unknown', 'skill_unknown',
        'mitigation_ready', 'mitigation_unknown', 'enemy_damage_ready', 'enemy_damage_unknown',
        'damage_settlement_ready', 'damage_settlement_unknown')
    events.claims['batch'] = {key: packet['batch'][key] for key in quality_fields if key in packet['batch']}
    journal = [dict(kind='lifecycle_header', data={'feed': runtime.feed.id})]
    runtime.record = lambda value: journal.append(dict(kind='lifecycle_observation', data=asdict(value)))
    runtime.poll(collector, observer=observer)
    snapshot.frame, snapshot.game_time = 120, 4
    events.queue = deepcopy(packet['batch']['events'])
    runtime.poll(collector, observer=observer)
    snapshot.frame, snapshot.game_time, state.battle_state = 150, 5, 3
    runtime.poll(collector, observer=observer)
    state.active = False
    runtime.poll(collector, observer=observer)
    runtime.poll(collector, observer=observer)
    archive, = runtime.sessions.pop_archives()
    journal.extend((dict(kind='archive_ready', data=archive),
                    dict(kind='lifecycle_summary', data=runtime.sessions.live())))
    encoded = [json.dumps(item, default=sorted) for item in journal]
    assert 'native_enemy_birth' in ''.join(encoded) and 'native_enemy_bar_birth' in ''.join(encoded)
    assert replay_product(encoded)['archives'] == 1
    record = BattleRecord.from_archive(archive)
    history = BattleHistory(tmp_path)
    assert history.save(record).written
    restored, = BattleHistory(tmp_path).get(record.battle_id).records
    lane = restored.statistics['rows']['char_elemental_fixture']['settled_damage']
    assert lane['observed_damage'] == 50 and lane['unverified_hits'] == 0

@pytest.mark.parametrize('fault', ['missing', 'bad_epoch', 'extra', 'wrong_type'])
def test_bad_present_bar_return_marker_cannot_be_supplied_through_a_consumer_field(producer, fault):
    packet = deepcopy(producer['native_pc']['valid'])
    bar = next(e for e in packet['batch']['events'] if e['kind'] == 'elemental_bar')
    marker = bar['proof']['native_enemy_bar_birth']
    bar['elemental_bar_birth'] = dict(marker)  # Raw wire cannot set normalized consumer input.
    if fault == 'missing':
        del bar['proof']['native_enemy_bar_birth']
    elif fault == 'bad_epoch':
        marker['modifier_enter_epoch'] = marker['epoch_after_born']
    elif fault == 'extra':
        marker['inferred'] = True
    else:
        bar['proof']['native_enemy_bar_birth'] = []
    stats, _ = replay(packet)
    assert stats.settlement_unassigned['hits'] == 1 and not stats.settled
