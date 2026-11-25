"""Owned producer certificates must survive both shared consumers unchanged."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from multiplayer_battle import MultiplayerBattleStatistics
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver
from test_multiplayer_native_context import consume, context, member, player, stamp


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def producer():
    result = subprocess.run(['node', 'tests/event_probe_nested_elemental_chain_fixture.js'],
                            cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def accounts(records):
    identities = {}
    nominal, hp, hits, overkill, bars = Counter(), Counter(), Counter(), Counter(), {}
    for event in records:
        source, uid = event.get('source'), event.get('source_character_uid')
        if source is not None:
            assert type(uid) is int and uid > 0
            assert identities.setdefault(source, uid) == uid
        kind = event['kind']
        if kind in ('damage', 'elemental_burst') and source is not None:
            nominal[source] += event['amount']
        target = event.get('target_uid')
        if kind == 'elemental_bar':
            bars[target] = source
        elif kind == 'damage_settlement':
            assert event['settlement_complete'] is True
            owner = bars.pop(target, None) if source is None else source
            assert owner is not None
            hp[owner] += event['amount'] + event['shield_absorbed']
            hits[owner] += 1
            if event.get('killed') is True or event.get('settled_after_nested_kill') is True:
                overkill[owner] += max(0, event['nominal_amount'] - event['amount'] - event['shield_absorbed'])
            if event['damage_type'] == 5 and source is not None:
                bars.pop(target, None)
    assert hp
    return identities, nominal, hp, hits, overkill


def quality_of(report, platform):
    raw = report['batch']
    assert raw['partial_damage_version'] == 7
    assert raw['enemy_damage_ready'] is True and raw['enemy_damage_unknown'] is False
    contract = NATIVE_PC_CONTRACT if platform == 'native_pc' else MUMU_CONTRACT
    return {key: value for key, value in contract.normalize_quality(raw).items() if key != 'events'}


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
def test_multi_stage_certificates_keep_amounts_owners_and_replay_once(producer, platform):
    for report in producer['android' if platform == 'mumu' else platform]['valid']:
        records = report['batch']['events']
        original = deepcopy(records)
        identities, nominal, expected, hits, overkill = accounts(records)

        class Collector:
            session = 'owned-elemental-chain'
            frame = 0
            game_time = 0

            def read(self):
                return dict(session=self.session, native_session=self.session, state=2,
                    frame=self.frame, game_time=self.game_time,
                    battle_metadata=dict(platform=platform, source_build='synthetic-build'),
                    rows=[dict(id=cid, damage=nominal[cid] if self.frame else 0,
                               healing=0, physical=0, arts=0, true=0, elemental=0)
                          for cid in identities])

            def read_roster(self):
                entries = tuple(CharacterEntry(cid, uid, False, False, None, None, 0)
                                for cid, uid in identities.items())
                return RosterSnapshot(self.frame, entries,
                    frozenset(identities.values()) if self.frame else frozenset(), True, 0)

        collector = Collector()
        observer = BoundedObserver(collector,
            MUMU_CONTRACT if platform == 'mumu' else NATIVE_PC_CONTRACT, report['batch'])
        capture = LiveEventCapture(collector, observer)
        capture.sample()
        collector.frame, collector.game_time = report['mark']['frame'], report['mark']['game_time']
        observer.records = deepcopy(records)
        observer.sequence = report['mark']['sequence']
        result = capture.sample()
        for cid, amount in expected.items():
            row = result['rows'][cid]
            lane = row['settled_damage']
            # The published total separately adds certified sourceless bursts.
            # It must leave the independently supplied native counter intact.
            assert row['damage'] - (row.get('burst_added_settlement') or 0) == pytest.approx(nominal[cid])
            assert lane['complete'] is True and lane['unverified_hits'] == 0
            assert lane['observed_hits'] == hits[cid], (report['name'], cid, lane, hits[cid])
            assert lane['known_hp_damage'] + lane['known_shield_damage'] == pytest.approx(amount)
            assert lane['overkill_damage'] == pytest.approx(overkill[cid])
        world = result['enemy_damage']['by_uid']
        assert sum(row['amount'] for row in world.values()) == pytest.approx(sum(expected.values()))
        observer.records = deepcopy(records)
        assert capture.sample()['rows'] == result['rows']
        assert records == original


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('mode', ['all', 'local'])
def test_solo_and_multiplayer_use_same_certificates_without_same_name_leak(producer, platform, mode):
    for report in producer[platform]['valid']:
        records = deepcopy(report['batch']['events'])
        identities, _nominal, expected, hits, overkill = accounts(records)
        members = [member(3, uid=uid, cid=cid) for cid, uid in identities.items()]
        # An explicitly foreign actor of the same CID is present concurrently.
        peer_cid = next(iter(identities))
        peer_uid = max(identities.values()) + 100000
        members.append(member(1, uid=peer_uid, cid=peer_cid))
        meter = MultiplayerBattleStatistics()
        metadata = dict(platform='native_pc' if platform == 'native_pc' else 'android',
                        stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m04')
        native = dict(context(0, members=members), collection_mode=mode)
        consume(meter, 0, native=native, battle_metadata=metadata)
        when = report['mark']['game_time']
        quality = quality_of(report, platform)
        for event in records:
            event['native_autochess_scope'] = dict(
                stamp(when=event['game_time'], collection_mode=mode), frame=event['frame'])
        native = dict(context(when, members=members), collection_mode=mode,
                      frame=report['mark']['frame'])
        result = consume(meter, when, records, native=native, quality=quality,
                         frame=report['mark']['frame'], battle_metadata=metadata)
        own = player(result, 3)
        ledgers = {row['cid']: row['ledger'] for row in own['actor_ledgers']}
        for cid, amount in expected.items():
            lane = ledgers[cid]
            assert lane['complete'] is True and lane['unverified_hits'] == 0
            assert lane['observed_hits'] == hits[cid], (report['name'], cid, lane, hits[cid])
            assert lane['known_hp_damage'] + lane['known_shield_damage'] == pytest.approx(amount)
            assert lane['overkill_damage'] == pytest.approx(overkill[cid])
        assert result['unknown_attribution'] == []
        if mode == 'local':
            assert not player(result, 1)['actor_ledgers']
        else:
            assert all(row['ledger']['observed_hits'] == 0
                       for row in player(result, 1)['actor_ledgers'])
        assert consume(meter, when, records, native=native, quality=quality,
                       frame=report['mark']['frame'], battle_metadata=metadata) == result
