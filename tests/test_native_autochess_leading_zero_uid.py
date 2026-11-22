"""Opaque account text survives real PC admission, event routing and history.

``leading_zero_battle`` is a test-host-only factory. Portable-package checks can
export its production observations/samples/archives as JSON, then consume that
JSON without importing this module, the tests package or pytest.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from autochess_context import native_autochess_scope_id, normalize_native_autochess, valid_player_uid
from battle_history import BattleHistory, BattleRecord, HistoryError
from battle_lifecycle import BattleRuntime
from desktop_bridge import DesktopState, _native_local_player
from event_capture import CaptureRetry
from panel_model import PanelConfig
from test_multiplayer_native_context import CID, hit, stamp
from test_native_pc_autochess_collector_gate import ROOTS, ROOT_VALUES, fixture


LEADING_UIDS = ('091000001', '002000002', '083000003', '004000004')


class LeadingZeroBattle:
    """Replace owned raw acquisition only; retain the production consumer chain."""

    def __init__(self, tmp_path, *, uids=LEADING_UIDS):
        (self.collector, self.probe, self.observer,
         self.commits, self.records) = fixture(tmp_path)
        self.runtime = BattleRuntime()
        self.players = [dict(index=i, uid=uid, nick_name=f'玩家{i}', nick_number=f'{i:04}')
                        for i, uid in enumerate(uids, 1)]
        self.epoch, self.native_round, self.self_index = 1, 4, 3
        self.active = True
        original_sample = self.probe.sample

        def guarded_sample(**kwargs):
            if not self.active:
                return dict(status='waiting_for_battle', reason='singleton_is_null')
            serial = self.probe.battle_serial_for(ROOT_VALUES, self.probe.frame,
                int(self.probe.when * (1 << 32)), self.probe.stage)
            report = original_sample(**kwargs)
            self.probe._serial = serial
            report['sample']['battle_serial'] = serial
            return report

        self.probe.sample = guarded_sample
        original_mark, original_batch = self.observer.mark, self.observer.read_through

        def project(packet):
            raw = packet['native_autochess_context']
            raw.update(players=deepcopy(self.players), self_player_index=self.self_index,
                       scope_key=self.scope(), round=self.native_round)
            for row in raw['memberships']:
                # The helper's default context is already normalized. Export
                # only producer fields, never its old derived account aliases.
                row.pop('player_uid', None)
                row.pop('actor_key', None)
                row['player_side'] = 1 if self.probe.phase == 5 else row['player_index']
            packet.update(native_frame=self.probe.frame,
                          native_fixed_time=str(int(self.probe.when * (1 << 32))))
            return packet

        self.observer.mark = lambda: project(original_mark())
        self.observer.read_through = lambda through: project(original_batch(through))
        self.observer.read_baseline = lambda through: dict(
            self.observer.read_through(through), events=[])
        self.observer.read_terminal_mark = lambda: dict(terminal_boundary=self.observer.mark())

    def scope(self):
        return ':'.join(ROOTS.values()) + f':autochess:{self.epoch}'

    def advance(self, when, events=(), *, phase=None, state=None, native_round=None, epoch=None):
        self.probe.frame, self.probe.when = round(when * 30), when
        for name, value in (('phase', phase), ('state', state)):
            if value is not None:
                setattr(self.probe, name, value)
        if native_round is not None:
            self.native_round = native_round
        if epoch is not None:
            self.epoch = epoch
        self.records.extend(deepcopy(events))

    def hit(self, sequence, index, amount, *, scope=None, phase=None, native_round=None):
        evidence = stamp(root=self.scope() if scope is None else scope,
            phase=self.probe.phase if phase is None else phase,
            native_round=self.native_round if native_round is None else native_round,
            when=self.probe.when)
        return hit(sequence, index, self.probe.when, amount, event_scope=evidence)

    def poll(self):
        return self.runtime.poll(self.collector, self.observer)


def leading_zero_battle(tmp_path, *, uids=LEADING_UIDS):
    """Return a four-account true NativePcCollector → BattleRuntime fixture."""
    return LeadingZeroBattle(tmp_path, uids=uids)


def populated(tmp_path, *, uids=LEADING_UIDS):
    battle = leading_zero_battle(tmp_path, uids=uids)
    assert battle.poll().kind == 'sample'
    battle.advance(1)
    battle.advance(1, [battle.hit(i, i, i * 100) for i in range(1, 5)])
    assert battle.poll().kind == 'sample'
    return battle


@pytest.mark.parametrize('uid', ['1', '01', *LEADING_UIDS, '91000001',
    '00000000000000000001', '18446744073709551615'])
def test_valid_opaque_player_uid_accepts_leading_zeros_without_rewriting(uid):
    assert valid_player_uid(uid) is True


@pytest.mark.parametrize('uid', [None, 91000001, True, '', '0', '000000000', '0' * 20,
    ' 091000001', '091000001 ', '091000001\n', '０９１０００００１', '٠٩١٠٠٠٠٠١',
    '+91000001', '-91000001', 'side3', '0' * 20 + '1',
    '18446744073709551616', '99999999999999999999'])
def test_player_uid_preserves_ascii_uint64_nonzero_bounds(uid):
    assert valid_player_uid(uid) is False


def test_leading_zero_accounts_reach_real_collector_ui_and_end8_history(tmp_path):
    battle = populated(tmp_path)
    sample = battle.runtime.display_sample()
    players = sample['multiplayer']['players']
    assert [p['player_uid'] for p in players] == list(LEADING_UIDS)
    assert [p['observed_damage'] for p in players] == [100, 200, 300, 400]
    assert sample['multiplayer']['self_player_uid'] == LEADING_UIDS[2]
    assert sample['statistics']['rows'] == {}  # The 90000 CID counter is never assigned.
    assert all(p['damage'] is p['dps'] is p['recent_dps'] is None for p in players)
    assert battle.commits == [0, 4]
    for index, player in enumerate(players, 1):
        owner = f'{LEADING_UIDS[index - 1]}:{CID}#{100 * index + 1}'
        assert list(player['statistics']['rows']) == [owner]
        assert player['actor_ledgers'][0]['owner_id'] == owner
        assert player['statistics']['session'].endswith(':' + LEADING_UIDS[index - 1])
    state = DesktopState(tmp_path / 'desktop')
    state.effective_connection = replace(state.effective_connection, platform='native_pc')
    state.layout = replace(state.layout, multiplayer_enabled=True,
                           panels=(PanelConfig(), PanelConfig(panel_id='effective', effective_only=True)))
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert [p['player_uid'] for p in snapshot['multiplayer']['players']] == list(LEADING_UIDS)
    for panel in snapshot['panels']:
        entry, = panel['entries']
        assert entry['owner_id'] == f'{LEADING_UIDS[2]}:{CID}#301'
        assert entry['value'] == panel['footer_total'] == 300
    assert snapshot['totals']['damage'] is None
    battle.advance(2, phase=8, state=3)
    assert battle.poll().kind == 'terminal'
    battle.active = False
    assert battle.poll().kind == 'end'
    archive, = battle.runtime.sessions.pop_archives()
    record = BattleRecord.from_archive(archive)
    history = BattleHistory(tmp_path / 'history')
    assert history.save(record).written
    loaded, = history.get(record.battle_id).records
    assert loaded.to_dict() == record.to_dict()
    assert [p['player_uid'] for p in loaded.last_sample['multiplayer']['players']] == list(LEADING_UIDS)
    assert loaded.last_sample['multiplayer']['players'][2]['observed_damage'] == 300


def test_opaque_alias_spelling_replay_and_reordered_rows_do_not_merge_accounts(tmp_path):
    uids = ('091000001', '91000001', *LEADING_UIDS[2:])
    battle = populated(tmp_path, uids=uids)
    first = deepcopy(battle.runtime.display_sample()['multiplayer'])
    assert first['players'][0]['observed_damage'] == 100
    assert first['players'][1]['observed_damage'] == 200
    battle.players.reverse()
    battle.advance(2)
    battle.poll()  # Same raw records replay, with an independently reordered player table.
    second = battle.runtime.display_sample()['multiplayer']
    assert second['scope'] == first['scope']
    assert [p['player_uid'] for p in second['players']] == list(uids)
    assert [p['observed_damage'] for p in second['players']] == [100, 200, 300, 400]
    assert first['players'][0]['actor_ledgers'][0]['owner_id'] != first['players'][1]['actor_ledgers'][0]['owner_id']


@pytest.mark.parametrize('invalid', ['duplicate', 'unknown', 'wrong_roots', 'wrong_scope', 'wrong_self'])
def test_leading_zeros_do_not_relax_native_identity_proof_or_ack(tmp_path, invalid):
    battle = leading_zero_battle(tmp_path)
    if invalid == 'duplicate':
        battle.players[1]['uid'] = battle.players[0]['uid']
    elif invalid == 'unknown':
        battle.probe.observer_mode = 'unknown'
    elif invalid == 'wrong_roots':
        battle.probe.observer_mode = 'foreign'
    elif invalid == 'wrong_scope':
        battle.scope = lambda: '0x90000:' + ':'.join(list(ROOTS.values())[1:]) + ':autochess:1'
    else:
        battle.self_index = 9
    with pytest.raises(CaptureRetry):
        battle.poll()
    assert battle.commits == [] and battle.runtime.display_sample() is None


def test_new_assist_scope_reuses_data_uid_without_old_money_or_alias_binding(tmp_path):
    battle = populated(tmp_path)
    old = deepcopy(battle.runtime.display_sample()['multiplayer'])
    old_scope = battle.scope()
    battle.advance(2, phase=8, state=3)
    battle.poll()
    battle.active = False
    battle.poll()
    battle.runtime.sessions.pop_archives()
    battle.players[0]['uid'], battle.players[2]['uid'] = battle.players[2]['uid'], battle.players[0]['uid']
    battle.active = True
    battle.advance(0, phase=5, state=2, native_round=5, epoch=2)
    assert battle.poll().kind == 'sample'
    assert all(p['observed_damage'] is None for p in battle.runtime.display_sample()['multiplayer']['players'])
    battle.advance(1)
    old_record = battle.hit(5, 3, 999, scope=old_scope, phase=old['native_phase'],
                            native_round=old['native_round'])
    assert tuple(old_record['native_autochess_scope'][key] for key in ('scope_key', 'phase', 'round')) == (
        old['context_identity']['scope_key'], old['native_phase'], old['native_round'])
    battle.advance(1, [old_record, battle.hit(6, 3, 50)])
    battle.poll()
    value = battle.runtime.display_sample()['multiplayer']
    assert value['scope_kind'] == 'assist' and value['scope'] != old['scope']
    assert value['players'][2]['player_uid'] == LEADING_UIDS[0]
    assert value['players'][2]['observed_damage'] == 50
    assert value['players'][0]['observed_damage'] is None
    assert value['players'][2]['actor_ledgers'][0]['owner_id'] == f'{LEADING_UIDS[0]}:{CID}#301'
    # This is a new lifecycle generation, whose new meter did not observe the
    # retired scope; it rejects the exact old stamp as an explicit mismatch.
    assert value['unknown_attribution'][0]['reason'] == 'event_scope_mismatch'
    assert value['unknown_attribution'][0]['events'] == 1
    assert old['players'][2]['observed_damage'] == 300


def test_wrong_local_uid_and_cross_account_owner_remain_rejected_in_ui_and_history(tmp_path):
    battle = populated(tmp_path)
    sample = deepcopy(battle.runtime.display_sample())
    sample['multiplayer']['self_player_uid'] = LEADING_UIDS[0]
    assert _native_local_player(sample) is None
    battle.advance(2, phase=8, state=3)
    battle.poll()
    battle.active = False
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    data = BattleRecord.from_archive(archive).to_dict()
    data['last_sample']['multiplayer']['self_player_uid'] = LEADING_UIDS[0]
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)
    data = BattleRecord.from_archive(archive).to_dict()
    data['last_sample']['multiplayer']['players'][2]['actor_ledgers'][0]['owner_id'] = f'{LEADING_UIDS[0]}:{CID}#301'
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)
    data = BattleRecord.from_archive(archive).to_dict()
    data['last_sample']['multiplayer']['players'][2]['actor_ledgers'][0]['owner_id'] = f'{LEADING_UIDS[2].lstrip("0")}:{CID}#301'
    with pytest.raises(HistoryError):
        BattleRecord.from_dict(data)


def test_normalization_keeps_account_text_in_membership_keys_and_scope_identity(tmp_path):
    battle = leading_zero_battle(tmp_path)
    raw = battle.observer.mark()['native_autochess_context']
    first = normalize_native_autochess(raw, expected_frame=0, expected_game_time=0,
                                      expected_scope_key=battle.scope())
    assert [p['uid'] for p in first['players']] == list(LEADING_UIDS)
    assert first['memberships'][0]['actor_key'] == [LEADING_UIDS[0], 101]
    changed = deepcopy(raw)
    changed['players'][0]['uid'] = LEADING_UIDS[0].lstrip('0')
    second = normalize_native_autochess(changed, expected_frame=0, expected_game_time=0,
                                       expected_scope_key=battle.scope())
    assert first['identity'] == second['identity']  # Native roots alone do not identify an account.
    assert first['memberships'][0]['actor_key'] != second['memberships'][0]['actor_key']
    assert native_autochess_scope_id(first['identity'], [(p['index'], p['uid']) for p in first['players']]) != (
        native_autochess_scope_id(second['identity'], [(p['index'], p['uid']) for p in second['players']]))
