"""Exercise the Android read/roster/lifecycle boundary without a game process."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from battle_lifecycle import BattleRuntime, autochess_scene
from collection_session import CollectionSession
from connection import ConnectionDiagnostics, DetectionStatus, VersionStatus
from event_capture import CaptureRetry
from event_observer import ObserverStatus
from event_probe import (MUMU_AUTOCHESS_CONTRACT, MUMU_CONTRACT, BarrierBusy,
                         ObserverReadRetry, bind_native_autochess_pair, contract_for)
from roster import RosterSnapshot
from test_multiplayer_native_context import QUALITY, PLAYERS, hit, member, stamp


class AndroidAutoChessBattle:
    """Host-owned inputs from the collector and probe remain independent."""

    stage = 'Activities/ACT3AUTOCHESS/level_act3autochess_m01'
    match_key = '0x400000:0x410000'

    def __init__(self):
        self.process = SimpleNamespace(process_instance=('fixture-boot', 100), pid=77)
        self.diagnostics = ConnectionDiagnostics(platform='mumu', status=DetectionStatus.READY_UNIQUE,
            connection_ok=True, version_actual='2.7.91', version_status=VersionStatus.COMPATIBLE)
        self.observer = SimpleNamespace(contract=MUMU_CONTRACT, mark=self.mark, read_through=self.read_through,
            read_baseline=self.read_baseline, commit_events=self.commit,
            status=ObserverStatus.READY, stop=lambda: None, provides_autochess_context=True)
        self.runtime = BattleRuntime()
        self.commits, self.events = [], []
        self.frame = self.when = 0
        self.phase, self.round, self.epoch, self.state = 4, 1, 1, 2
        self.mode = 'local'
        self.context_available = self.active = True
        self.fail_drain = False
        self.roots = dict(controller=0x100000, native=0x110000, logger=0x120000,
                          stats=0x130000, level=0x140000)

    def read_lifecycle(self):
        return dict(battle=self.identity() if self.active else None, state=self.state, ready=True,
            transiting=False, source='', target='', metadata=self.metadata())

    def metadata(self):
        return dict(platform='android', source_build='android:2.7.91',
                    stage_id=self.stage, evidence='android_package_version')

    def identity(self):
        return self.roots['controller'], self.roots['native']

    def bind_battle(self, address):
        assert address == self.roots['controller']

    def read(self):
        session = ':'.join(map(str, (*self.process.process_instance, self.process.pid,
            self.roots['controller'], self.roots['stats'], self.roots['level'])))
        return dict(session=session, battle_identity=self.identity(), observer_logger=self.roots['logger'],
                    frame=self.frame, game_time=self.when, state=self.state, rows=[], global_total=0,
                    guard=dict(start=self.frame, end=self.frame, complete=True))

    def read_roster(self):
        return RosterSnapshot(self.frame, present_known=True)

    def scope(self):
        return ':'.join(map(hex, self.roots.values())) + f':autochess:{self.epoch}'

    def mark(self):
        payload = dict(frame=self.frame, game_time=self.when, sequence=len(self.events),
                       epoch=len(self.events), active=0, damage_settlement_scope=1, **QUALITY)
        if not self.context_available:
            return payload
        raw = dict(protocol_version=1, status='available', verified_same_frame=True,
            scope_key=self.scope(), match_key=self.match_key, frame=self.frame, game_time=self.when,
            phase=self.phase, round=self.round, collection_mode=self.mode, self_player_index=3,
            players=deepcopy(PLAYERS), memberships=[member(i) for i in range(1, 5)])
        return dict(payload, native_autochess_context=raw,
                    native_identity={k: hex(v) for k, v in self.roots.items()},
                    native_process_pid=self.process.pid,
                    native_frame=self.frame, native_fixed_time=str(int(self.when * (1 << 32))),
                    native_stage_id=self.stage)

    def read_through(self, through):
        if self.fail_drain:
            raise ObserverReadRetry('fixture bounded read temporarily unavailable')
        acknowledged = self.commits[-1] if self.commits else 0
        return dict(self.mark(), events=[deepcopy(event) for event in self.events
                                        if acknowledged < event['sequence'] <= through])

    def read_baseline(self, through):
        if self.fail_drain:
            raise ObserverReadRetry('fixture bounded baseline temporarily unavailable')
        return dict(self.mark(), events=[])

    def commit(self, through):
        self.commits.append(through)

    def advance(self, when, *, phase=None, round_=None, epoch=None, mode=None, new_controller=False, amounts=()):
        previous = self.phase
        self.frame, self.when = round(when * 30), when
        for attr, value in (('phase', phase), ('round', round_), ('epoch', epoch), ('mode', mode)):
            if value is not None:
                setattr(self, attr, value)
        if new_controller:
            self.roots = {key: pointer + 0x20000 for key, pointer in self.roots.items()}
        if phase is not None and phase != previous and epoch is None:
            self.epoch += 1
        for index, amount in amounts:
            scope = stamp(phase=self.phase, native_round=self.round, root=self.scope(),
                          when=when, collection_mode=self.mode)
            self.events.append(hit(len(self.events) + 1, index, when, amount, event_scope=scope))

    def poll(self):
        return self.runtime.poll(self, self.observer)


def test_paired_android_proof_checks_snapshot_roots_clock_and_stage():
    battle = AndroidAutoChessBattle()
    sample = dict(battle.read(), battle_metadata=battle.metadata())
    opening, closing = battle.mark(), battle.mark()
    bind_native_autochess_pair(sample, opening, closing)
    assert sample['native_counter_scope'] == 'events_only'
    assert contract_for(battle.observer, sample) is MUMU_AUTOCHESS_CONTRACT
    assert autochess_scene(sample).collection_mode == 'local'
    for altered in (dict(native_stage_id='other'), dict(native_process_pid=78),
                    dict(native_frame=1), dict(native_fixed_time='1'),
                    dict(native_identity=dict(opening['native_identity'], logger='0x420000')),
                    dict(native_autochess_context=dict(opening['native_autochess_context'], match_key='0x420000:0x430000'))):
        target = dict(battle.read(), battle_metadata=battle.metadata())
        with pytest.raises(BarrierBusy):
            bind_native_autochess_pair(target, opening, dict(closing, **altered))
        assert 'autochess_context' not in target


def test_absent_android_proof_never_uses_the_scoped_mode_or_account_ledger():
    battle = AndroidAutoChessBattle()
    sample = dict(battle.read(), battle_metadata=battle.metadata())
    battle.context_available = False
    bind_native_autochess_pair(sample, battle.mark(), battle.mark())
    assert autochess_scene(sample) is None
    assert contract_for(battle.observer, sample) is MUMU_CONTRACT


def test_absent_android_proof_records_reason_without_manufacturing_scope():
    battle = AndroidAutoChessBattle()
    battle.context_available = False
    battle.observer.mark = lambda: dict(battle.mark(), autochess_binding_diagnostic={
        'status': 'unavailable', 'step': 'root_identity', 'reason': 'checked logger owner differs'})
    observation = battle.poll()
    assert observation.sample['autochess_scope_unverified'] is True
    assert observation.sample['autochess_scope_detail'] == 'checked logger owner differs'
    assert 'autochess_context' not in observation.sample
    assert battle.runtime.feed.autochess_scene is None


@pytest.mark.parametrize('retry', [False, True])
def test_android_root_guard_reaches_panel_or_retry_journal_without_claiming_scope(retry):
    battle = AndroidAutoChessBattle()
    battle.context_available = False
    battle.observer.mark = lambda: dict(battle.mark(), retry=retry, autochess_binding_diagnostic={
        'status': 'unavailable', 'step': 'root_identity', 'guard': 'stats_class_mismatch',
        'reason': 'expected BattleStats'})
    message = 'AutoChess stats_class_mismatch: expected BattleStats'
    if retry:
        with pytest.raises(CaptureRetry, match=message):
            battle.poll()
        assert battle.commits == []
    else:
        observation = battle.poll()
        assert observation.sample['autochess_scope_detail'] == message
        assert observation.sample['autochess_scope_unverified'] is True
        assert 'autochess_context' not in observation.sample
    assert battle.runtime.feed.autochess_scene is None


def test_android_local_and_all_share_scope_account_ledger_and_single_ack():
    battle = AndroidAutoChessBattle()
    assert battle.poll().kind == 'sample'
    own_scope = battle.runtime.feed.autochess_scene
    battle.advance(1, amounts=((1, 100), (3, 300), (4, 400)))
    assert battle.poll().kind == 'sample'
    live = battle.runtime.display_sample()['multiplayer']
    assert live['schema_version'] == 2 and live['collection_mode'] == 'local'
    assert [player['observed_damage'] for player in live['players']] == [None, None, 300, None]
    assert len(battle.runtime.sessions.multiplayer._players) == 1
    battle.advance(0, phase=5, mode='all', new_controller=True)
    assert battle.poll().kind == 'sample'
    new_scope = battle.runtime.feed.autochess_scene
    assert new_scope.match == own_scope.match and new_scope.scope != own_scope.scope
    battle.advance(1, amounts=((1, 200), (3, 600), (4, 800)))
    assert battle.poll().kind == 'sample'
    live = battle.runtime.display_sample()['multiplayer']
    assert [player['observed_damage'] for player in live['players']] == [200, None, 600, 800]
    assert live['scope_kind'] == 'assist' and live['collection_mode'] == 'all'
    assert battle.commits == [0, 3, 3, 6]


def test_android_defense_enemy_projection_keeps_shared_facts_and_reports_local_hp_damage():
    battle = AndroidAutoChessBattle()
    battle.poll()
    battle.advance(1, amounts=((1, 100), (3, 300), (4, 400)))
    battle.poll()
    sample = battle.runtime.display_sample()
    assert sample['statistics']['enemy_damage']['by_uid']['500']['amount'] == 800
    requests = []

    def read_enemies(*, game_time, our_damage, frame=None, enemy_entry=None, enemy_phases=None,
                     enemy_state=None, enemy_damage=None):
        requests.append((game_time, our_damage, frame, enemy_damage))
        return dict(status='ready', ready=True, rows=[], totals={}, damage_scope='local_account')

    battle.read_enemies = read_enemies
    CollectionSession(None, None, collector=battle)._attach_enemy(sample)
    [(when, aggregate, frame, damage)] = requests
    assert (when, aggregate, frame) == (1, None, None)
    assert damage['scope'] == 'local_account' and damage['by_uid']['500']['amount'] == 300
    assert sample['enemy']['status'] == 'ready'


def test_android_paired_steady_scope_accepts_same_frame_events_from_closing_mark(monkeypatch):
    battle = AndroidAutoChessBattle()
    assert battle.poll().kind == 'sample'
    battle.advance(1)
    snapshot = battle.read

    def read():
        value = snapshot()
        monkeypatch.setattr(battle, 'read', snapshot)
        battle.advance(1, amounts=((3, 120),))
        return value

    monkeypatch.setattr(battle, 'read', read)
    assert battle.poll().kind == 'sample'
    result = battle.runtime.display_sample()['multiplayer']
    assert result['players'][2]['observed_damage'] == 120
    assert battle.commits == [0, 1]


def test_android_failed_terminal_proof_keeps_the_last_bound_prefix_and_does_not_ack():
    battle = AndroidAutoChessBattle()
    assert battle.poll().kind == 'sample'
    battle.advance(1, amounts=((3, 120),))
    assert battle.poll().kind == 'sample'
    sample = battle.runtime.display_sample()
    battle.advance(2, phase=8, amounts=((3, 50),))
    battle.state, battle.context_available = 3, False
    observation = battle.poll()
    assert observation.kind == 'terminal' and observation.sample is None
    result = battle.runtime.display_sample()
    assert result['native_counter_scope'] == 'events_only'
    assert result['multiplayer'] == sample['multiplayer']
    assert result['enemy_world_scope'] == sample['enemy_world_scope']
    assert battle.commits == [0, 1]
