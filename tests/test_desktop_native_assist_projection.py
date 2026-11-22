"""Assist display unions accepted actor rows without changing their accounts."""
from copy import deepcopy
from dataclasses import replace

import pytest

from battle_history import BattleHistory, BattleRecord
from battle_share import json_bytes
from desktop_bridge import DesktopState, _native_local_player
from panel_model import PanelConfig
from test_autochess_context import member
from test_multiplayer_integration import CID
from test_native_pc_autochess_acquisition import ScopedNativeBattle


class AssistBattle(ScopedNativeBattle):
    def __init__(self, phase=5):
        super().__init__()
        self.native_phase = phase

    def binding(self):
        value = super().binding()
        value['native_autochess_context']['memberships'].extend(
            member(cid=CID, data_uid=uid, player_index=3,
                   player_side=1 if self.native_phase == 5 else 3)
            for uid in range(302, 307))
        return value


def accepted_assist(*, phase=5, self_hit=False):
    battle = AssistBattle(phase)
    assert battle.poll().kind == 'sample'
    battle.advance(2, damage=90000)
    indexes = (1, 2, 3, 4) if self_hit else (1, 2, 4)
    events = [battle.scoped_hit(sequence, index, index * 100)
              for sequence, index in enumerate(indexes, 1)]
    battle.advance(2, events)
    assert battle.poll().kind == 'sample'
    return battle


def state_for(tmp_path, sample):
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(
        PanelConfig(), PanelConfig(panel_id='effective', effective_only=True)))
    state.accept('sample', sample)
    return state


def assert_scene(view, amounts=(100, 200, 400)):
    assert view['scope_label'] == '当前协防战场'
    assert view['title'].startswith('当前协防战场 · ')
    assert view['metric_label'].startswith('已观察')
    assert '当前协防战场' in view['share_reason'] and '原生累计未知' in view['share_reason']
    assert sorted(entry['value'] for entry in view['entries']) == list(amounts)
    assert view['footer_total'] == sum(amounts) and view['footer_total_complete'] is False
    assert view['share_known'] is False
    for entry in view['entries']:
        uid, actor_uid = entry['player_uid'], entry['actor_uid']
        assert entry['owner_id'] == f'{uid}:{CID}#{actor_uid}'
        assert entry['portrait_id'] == CID and entry['player_side'] == 1
        assert view['source'][entry['owner_id']]['player_uid'] == uid
        assert view['source'][entry['owner_id']]['actor_uid'] == actor_uid
        assert view['source'][entry['owner_id']]['damage'] is None
        assert view['details_by_owner'][entry['owner_id']]['effective_damage'] == entry['value']


def test_idle_six_actor_self_does_not_hide_other_accounts_assist_output(tmp_path):
    battle = accepted_assist()
    sample = battle.runtime.display_sample()
    original = deepcopy(sample)
    assert sample['statistics']['rows'] == {}
    assert len(_native_local_player(sample)['statistics']['rows']) == 6
    assert _native_local_player(sample)['observed_damage'] is None
    assert [p['observed_damage'] for p in sample['multiplayer']['players']] == [100, 200, None, 400]
    state = state_for(tmp_path, sample)
    snapshot = state.snapshot()
    for view in snapshot['panels']:
        assert_scene(view)
        assert {entry['player_uid'] for entry in view['entries']} == {
            '910000001', '910000002', '910000004'}
        assert len(view['source']) == 9  # Six idle local rows remain available to other categories.
    trend = snapshot['live_analysis']
    assert trend['scope_label'] == '当前协防战场'
    assert trend['team_total_damage'] is trend['team_average_dps'] is None
    assert trend['team_recent_dps'] is None
    assert trend['observed_team_recent_dps'] == 350
    assert len(trend['operators']) == 9
    assert {op['player_uid'] for op in trend['operators'] if op['recent_dps'] is not None} == {
        '910000001', '910000002', '910000004'}
    for op in trend['operators']:
        assert op['owner_id'] == f"{op['player_uid']}:{CID}#{op['actor_uid']}"
    assert snapshot['totals']['damage'] is snapshot['totals']['healing'] is None
    assert sample == original and state.last is sample
    assert battle.commits == [0, 3]


@pytest.mark.parametrize('phase', [4, 6], ids=['defense', 'boss'])
def test_other_combat_phases_still_display_only_evidenced_self(tmp_path, phase):
    sample = accepted_assist(phase=phase, self_hit=True).runtime.display_sample()
    original = deepcopy(sample)
    assert _native_local_player(sample)['player_uid'] == '910000003'
    snapshot = state_for(tmp_path, sample).snapshot()
    for view in snapshot['panels']:
        entry, = view['entries']
        assert entry['value'] == 300 and entry['player_uid'] == '910000003'
        assert 'scope_label' not in view
    assert 'scope_label' not in snapshot['live_analysis']
    assert sample == original


def test_slot_side_and_missing_self_do_not_reassign_scene_actor_identity(tmp_path):
    sample = accepted_assist().runtime.display_sample()
    payload = sample['multiplayer']
    players = payload['players']
    players[0]['slot'], players[2]['slot'] = players[2]['slot'], players[0]['slot']
    payload['self_player_uid'] = None
    for player in players:
        player['is_local'] = None
        player['player_side'] = 1
    original = deepcopy(sample)
    assert _native_local_player(sample) is None
    for view in state_for(tmp_path, sample).snapshot()['panels']:
        assert_scene(view)
        assert {entry['actor_uid'] for entry in view['entries']} == {101, 201, 401}
    assert sample == original


def test_same_cid_multiple_actor_uids_of_one_account_stay_distinct(tmp_path):
    battle = accepted_assist()
    battle.advance(3)
    first = battle.scoped_hit(4, 3, 30)
    second = dict(battle.scoped_hit(5, 3, 60), source_character_uid=302)
    battle.advance(3, [first, second])
    assert battle.poll().kind == 'sample'
    sample = battle.runtime.display_sample()
    original = deepcopy(sample)
    for view in state_for(tmp_path, sample).snapshot()['panels']:
        assert_scene(view, (30, 60, 100, 200, 400))
        local = [entry for entry in view['entries'] if entry['player_uid'] == '910000003']
        assert {entry['actor_uid'] for entry in local} == {301, 302}
        assert len({entry['owner_id'] for entry in view['entries']}) == 5
    assert _native_local_player(sample)['observed_damage'] == 90
    assert sample == original


@pytest.mark.parametrize('invalid', ['foreign_scope', 'missing_uid', 'wrong_actor_uid',
    'missing_ledger', 'duplicate_ledger', 'duplicate_player_uid', 'wrong_row_player'])
def test_bad_account_or_actor_proof_is_local_unknown_without_cid_fallback(tmp_path, invalid):
    sample = accepted_assist().runtime.display_sample()
    player = sample['multiplayer']['players'][0]
    actor = player['actor_ledgers'][0]
    row = next(iter(player['statistics']['rows'].values()))
    if invalid == 'foreign_scope':
        player['statistics']['session'] = 'foreign:' + player['player_uid']
    elif invalid == 'missing_uid':
        player['player_uid'] = None
    elif invalid == 'wrong_actor_uid':
        actor['actor_uid'] = 201
    elif invalid == 'missing_ledger':
        player.pop('actor_ledgers')
    elif invalid == 'duplicate_ledger':
        player['actor_ledgers'].append(deepcopy(actor))
    elif invalid == 'duplicate_player_uid':
        sample['multiplayer']['players'][2]['player_uid'] = player['player_uid']
    else:
        row['player_uid'] = '910000003'
    original = deepcopy(sample)
    snapshot = state_for(tmp_path, sample).snapshot()
    for view in snapshot['panels']:
        assert_scene(view, (200, 400))
        assert all(entry['player_uid'] != '910000001' for entry in view['entries'])
    assert sample == original


@pytest.mark.parametrize('field,value', [('available', False), ('native_phase', None),
    ('native_phase', 4), ('native_round', None), ('scope', None),
    ('scope_kind', 'prepare'), ('scope_kind', 'unknown')])
def test_missing_combat_scope_never_reuses_assist_values(tmp_path, field, value):
    sample = accepted_assist().runtime.display_sample()
    sample['multiplayer'][field] = value
    snapshot = state_for(tmp_path, sample).snapshot()
    for view in snapshot['panels']:
        assert view['entries'] == [] and view['footer_total'] is None
    assert snapshot['live_analysis']['operators'] == []


def test_baseline_without_hits_preserves_none_instead_of_certifying_zero(tmp_path):
    battle = AssistBattle()
    battle.poll()
    sample = battle.runtime.display_sample()
    original = deepcopy(sample)
    snapshot = state_for(tmp_path, sample).snapshot()
    for view in snapshot['panels']:
        assert view['scope_label'] == '当前协防战场'
        assert view['entries'] == [] and view['footer_total'] is None
    assert snapshot['totals']['damage'] is None
    assert sample == original


def test_preparing_retains_last_main_scene_with_existing_previous_battle_notice(tmp_path):
    sample = accepted_assist().runtime.display_sample()
    original = deepcopy(sample)
    state = state_for(tmp_path, sample)
    before = state.snapshot()
    state.accept('lifecycle', 'preparing')
    current = state.snapshot()
    assert current['status'] == 'preparing' and '保留上一局统计' in current['status_text']
    assert current['panels'] == before['panels']
    assert current['live_analysis'] == before['live_analysis']
    assert current['multiplayer']['scope_kind'] == 'prepare'
    assert all(player['view']['entries'] == [] for player in current['multiplayer']['players'])
    assert sample == original and state.last is sample


def test_end8_saved_history_uses_same_scene_and_survives_prepare_or_new_live_scope(tmp_path):
    battle = accepted_assist()
    live = state_for(tmp_path, battle.runtime.display_sample()).snapshot()['panels']
    battle.native_phase, battle.autochess_epoch = 8, 2
    battle.advance(3, state=3)
    assert battle.poll().kind == 'terminal'
    battle.active = False
    assert battle.poll().kind == 'end'
    archive, = battle.runtime.sessions.pop_archives()
    history = BattleHistory(tmp_path / 'history')
    record = BattleRecord.from_archive(archive)
    assert history.save(record).written
    loaded, = history.get(record.battle_id).records
    path = history.path_for(record.battle_id)
    before, original_bytes, export = loaded.to_dict(), path.read_bytes(), json_bytes(loaded)
    assert loaded.statistics['rows'] == {}
    assert loaded.last_sample['autochess_context']['phase'] == 8
    assert loaded.last_sample['multiplayer']['native_phase'] == 5
    state = DesktopState(tmp_path)
    state.command(dict(version=1, op='history_select', battle_id=record.battle_id))
    for effective in (False, True):
        state.history_config = PanelConfig(panel_id='history', effective_only=effective)
        selected = state.snapshot()['history_selected']
        assert_scene(selected['view'])
        assert {entry['owner_id']: entry['value'] for entry in selected['view']['entries']} == {
            entry['owner_id']: entry['value'] for entry in live[int(effective)]['entries']}
    selected = deepcopy(state.snapshot()['history_selected'])
    state.accept('lifecycle', 'preparing')
    assert state.snapshot()['history_selected'] == selected
    other = accepted_assist(phase=4, self_hit=True).runtime.display_sample()
    state.accept('sample', other)
    assert state.snapshot()['history_selected'] == selected
    assert loaded.to_dict() == before and path.read_bytes() == original_bytes
    assert state.selected.to_dict() == before and json_bytes(loaded) == export


def test_claimed_new_round_cannot_relabel_old_scope_rows_or_operators(tmp_path):
    sample = accepted_assist().runtime.display_sample()
    state = state_for(tmp_path, sample)
    first = deepcopy(state.live_analysis)
    sample = deepcopy(sample)
    sample['multiplayer']['native_round'] += 1
    state.accept('sample', sample)
    current = state.snapshot()
    assert all(view['entries'] == [] and view['source'] == {} and view['footer_total'] is None
               for view in current['panels'])
    assert current['live_analysis']['operators'] == []
    assert 'observed_team_recent_dps' not in current['live_analysis']
    second = deepcopy(state.live_analysis)
    assert second['battle_id'] != first['battle_id'] and second['segment'] == 0


@pytest.mark.parametrize('identity', [None, [], {},
    dict(scope_key=None, phase=5, round=4), dict(scope_key=42, phase=5, round=4),
    dict(scope_key='', phase=5, round=4), dict(scope_key='root key', phase=5, round=4),
    dict(scope_key='root\x00key', phase=5, round=4),
    dict(scope_key='root', phase=True, round=4), dict(scope_key='root', phase=4, round=4),
    dict(scope_key='root', phase=5, round=True), dict(scope_key='root', phase=5, round=5),
    dict(scope_key='root', phase=5, round=4, extra='unverified')])
def test_missing_or_conflicting_context_identity_cannot_expose_old_scene(tmp_path, identity):
    sample = accepted_assist().runtime.display_sample()
    state = state_for(tmp_path, sample)
    assert state.snapshot()['panels'][0]['footer_total'] == 700
    invalid = deepcopy(sample)
    invalid['multiplayer']['context_identity'] = identity
    original = deepcopy(invalid)
    state.accept('sample', invalid)
    snapshot = state.snapshot()
    assert all(view['source'] == {} and view['entries'] == [] and view['footer_total'] is None
               for view in snapshot['panels'])
    assert snapshot['live_analysis']['operators'] == []
    assert snapshot['totals']['damage'] is None
    assert 'observed_team_recent_dps' not in snapshot['live_analysis']
    assert invalid == original


def test_real_assist_round_scope_switch_starts_empty_then_shows_only_new_hits(tmp_path):
    battle = accepted_assist()
    state = state_for(tmp_path, battle.runtime.display_sample())
    previous = state.live_analysis['battle_id']
    battle.native_round += 1
    battle.autochess_epoch += 1
    battle.advance(3)
    assert battle.poll().kind == 'sample'
    state.accept('sample', battle.runtime.display_sample())
    empty = state.snapshot()
    assert empty['panels'][0]['entries'] == [] and empty['panels'][0]['footer_total'] is None
    assert empty['live_analysis']['battle_id'] != previous
    battle.advance(4)
    battle.advance(4, [battle.scoped_hit(4, 4, 80)])
    assert battle.poll().kind == 'sample'
    state.accept('sample', battle.runtime.display_sample())
    view = state.snapshot()['panels'][0]
    assert_scene(view, (80,))
    entry, = view['entries']
    assert entry['player_uid'] == '910000004'

