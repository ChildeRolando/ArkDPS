"""Accepted native observations reach the desktop without inventing counters."""
from copy import deepcopy
from dataclasses import replace

import pytest

from battle_history import BattleHistory, BattleRecord
from desktop_bridge import DesktopState, _event_only_statistics, _native_local_player
from panel_model import Category, EventAvailability, PanelConfig, Ranking, RateBasis
from test_multiplayer_integration import CID
from test_native_pc_autochess_acquisition import ScopedNativeBattle


def populated(*, phase=4):
    battle = ScopedNativeBattle()
    battle.native_phase = phase
    battle.poll()
    battle.advance(2, damage=90000)
    battle.advance(2, [battle.scoped_hit(index, index, index * 100) for index in range(1, 5)])
    battle.poll()
    return battle


def saved_native_record(tmp_path, *, end_phase=False):
    battle = populated()
    if end_phase:
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
    path = history.path_for(loaded.battle_id)
    return loaded, path


def test_accepted_four_account_views_and_local_panels_share_the_existing_renderer(tmp_path):
    battle = populated()
    sample = battle.runtime.display_sample()
    assert sample['multiplayer']['self_player_uid'] == '910000003'
    original = deepcopy(sample)
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(PanelConfig(), PanelConfig(panel_id='effective', effective_only=True)))
    state.accept('sample', sample)
    snapshot = state.snapshot()
    projected = snapshot['multiplayer']
    assert projected['schema_version'] == 2 and projected['scope_kind'] == 'defense'
    assert projected['native_round'] == 4 and projected['scope'] == sample['multiplayer']['scope']
    for slot, player in enumerate(projected['players'], 1):
        assert player['slot'] == slot and player['player_uid'] == f'91000000{slot}'
        assert 'statistics' not in player and 'actor_ledgers' not in player
        entry, = player['view']['entries']
        assert entry['value'] == slot * 100 and entry['actor_uid'] == slot * 100 + 1
        assert entry['portrait_id'] == CID and entry['player_uid'] == player['player_uid']
        assert entry['owner_id'].startswith(player['player_uid'] + ':')
        assert player['display']['damage'].startswith('已观察 ')
        assert '观察 0.0—2.0' in player['window_text']
    for panel in snapshot['panels']:
        entry, = panel['entries']
        assert entry['owner_id'] == f'910000003:{CID}#301'
        assert entry['value'] == 300 and entry['portrait_id'] == CID
        assert panel['footer_total'] == 300 and panel['footer_total_complete'] is False
        assert panel['share_known'] is False and '原生累计未知' in panel['share_reason']
        assert panel['metric_label'].startswith('已观察')
        assert panel['source'][entry['owner_id']]['damage'] is None
    assert snapshot['totals']['damage'] is snapshot['totals']['healing'] is None
    trend = snapshot['live_analysis']
    assert trend['team_total_damage'] is trend['team_average_dps'] is trend['team_recent_dps'] is None
    assert trend['observed_team_recent_dps'] == 150
    assert trend['operators'] == [dict(owner_id=f'910000003:{CID}#301', name='灰喉 · 301', recent_dps=150)]
    state.snapshot()
    assert sample == original  # View identity, flags and counters never enter raw/history data.


def test_known_identity_without_events_stays_unknown_even_in_effective_footer(tmp_path):
    battle = ScopedNativeBattle()
    battle.poll()
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(PanelConfig(effective_only=True),))
    state.accept('sample', battle.runtime.display_sample())
    snapshot = state.snapshot()
    for player in snapshot['multiplayer']['players']:
        assert player['display']['damage'] == player['display']['dps'] == '未知'
        assert player['view']['entries'] == [] and player['view']['footer_total'] is None
    assert snapshot['panels'][0]['footer_total'] is None
    assert snapshot['live_analysis']['observed_peak_dps'] is None


@pytest.mark.parametrize('schema_version', [1, 2], ids=['legacy', 'native'])
@pytest.mark.parametrize('historical', [False, True], ids=['live', 'history'])
def test_wire_omits_only_raw_player_ledgers_and_keeps_full_rendered_views(tmp_path, schema_version, historical):
    state = DesktopState(tmp_path)
    if historical:
        record, path = saved_native_record(tmp_path, end_phase=True)
        original_bytes = path.read_bytes()
        raw_record = record.to_dict()
        state.selected = record
        payload = record.last_sample['multiplayer']
        event = EventAvailability(record.event_status, record.event_error, live=False)
    else:
        sample = populated().runtime.display_sample()
        raw_sample = deepcopy(sample)
        payload = sample['multiplayer']
        state.accept('sample', sample)
        event = EventAvailability(sample['event_status'], sample.get('event_error'))
    if schema_version == 1:
        payload = dict(payload, schema_version=1)
    original = deepcopy(payload)
    if schema_version == 2:
        projected = state.snapshot()['history_selected' if historical else 'multiplayer']
        if historical:
            projected = projected['multiplayer']
    else:
        projected = state.multiplayer_view(payload, event=event)
    omitted = {'statistics', 'actor_ledgers'}
    assert projected.keys() == payload.keys()
    assert {key: value for key, value in projected.items() if key != 'players'} == {
        key: value for key, value in payload.items() if key != 'players'}
    for raw, player in zip(payload['players'], projected['players'], strict=True):
        assert omitted.isdisjoint(player)
        assert player.keys() == (raw.keys() - omitted | {'view', 'display', 'window_text'})
        assert {key: player[key] for key in raw.keys() - omitted} == {
            key: value for key, value in raw.items() if key not in omitted}
        position = raw['slot'] if schema_version == 2 else raw['player_side']
        config = PanelConfig(panel_id=f'multiplayer-{position}', effective_only=True,
                             ranking=Ranking.TOTAL, rate_basis=RateBasis.ENGAGEMENT,
                             title='已观察有效伤害 · 接敌 DPS')
        statistics = (_event_only_statistics(raw['statistics']) if schema_version == 2
                      else raw['statistics'])
        expected = state._actor_view(config, statistics, raw['actor_ledgers'],
            side=raw['player_side'], player_uid=raw['player_uid'] if schema_version == 2 else None,
            event=event)
        assert player['view'] == expected
        assert player['view']['footer_total'] == raw['observed_damage']
        assert player['view']['details_by_owner']
    assert payload == original
    if historical:
        assert record.to_dict() == raw_record and path.read_bytes() == original_bytes
    else:
        assert sample == raw_sample and state.last is sample


@pytest.mark.parametrize('invalid', ['missing', 'multiple', 'duplicate_uid', 'unknown_uid', 'bad_uid'])
def test_events_only_never_falls_back_to_cid_counters_or_another_player(tmp_path, invalid):
    sample = deepcopy(populated().runtime.display_sample())
    players = sample['multiplayer']['players']
    if invalid == 'missing':
        for player in players:
            player['is_local'] = None
    elif invalid == 'multiple':
        players[0]['is_local'] = True
    elif invalid == 'duplicate_uid':
        players[0]['player_uid'] = players[2]['player_uid']
    elif invalid == 'unknown_uid':
        players[2]['player_uid'] = None
    else:
        players[2]['player_uid'] = 'side3'
    sample['statistics']['rows'] = {CID: dict(damage=90000, healing=900, counter_snapshot_complete=True)}
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert snapshot['panels'][0]['entries'] == [] and snapshot['panels'][0]['source'] == {}
    assert snapshot['totals']['damage'] is None
    assert snapshot['live_analysis']['operators'] == []
    assert snapshot['live_analysis']['team_total_damage'] is None


def test_slot_and_help_side_changes_never_choose_the_local_identity(tmp_path):
    sample = deepcopy(populated(phase=5).runtime.display_sample())
    players = sample['multiplayer']['players']
    players[0]['slot'], players[2]['slot'] = players[2]['slot'], players[0]['slot']
    for player in players:
        player['player_side'] = 1
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert [player['slot'] for player in snapshot['multiplayer']['players']] == [1, 2, 3, 4]
    assert snapshot['multiplayer']['players'][0]['player_uid'] == '910000003'
    assert _native_local_player(sample)['player_uid'] == '910000003'
    entries = snapshot['panels'][0]['entries']
    assert sorted(entry['value'] for entry in entries) == [100, 200, 300, 400]
    assert {entry['player_uid'] for entry in entries} == {f'91000000{i}' for i in range(1, 5)}
    assert all(entry['owner_id'].startswith(entry['player_uid'] + ':')
               and entry['player_side'] == 1 for entry in entries)


def test_a_known_account_with_no_single_temporary_side_is_still_rendered(tmp_path):
    sample = deepcopy(populated().runtime.display_sample())
    sample['multiplayer']['players'][2]['player_side'] = None
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    player = snapshot['multiplayer']['players'][2]
    assert player['player_uid'] == '910000003' and player['view']['entries'][0]['value'] == 300
    assert snapshot['panels'][0]['entries'][0]['player_side'] is None


@pytest.mark.parametrize('kind', ['prepare', 'unknown'])
def test_noncombat_scope_does_not_project_previous_local_amounts(tmp_path, kind):
    sample = deepcopy(populated().runtime.display_sample())
    sample['multiplayer']['scope_kind'] = kind
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert snapshot['multiplayer']['scope_kind'] == kind
    assert snapshot['panels'][0]['entries'] == []
    assert snapshot['live_analysis']['operators'] == []


def test_genuine_preparing_clears_only_live_four_player_view_and_keeps_history(tmp_path):
    battle = populated()
    sample = battle.runtime.display_sample()
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    battle.native_phase, battle.autochess_epoch = 5, 2
    battle.advance(3)
    battle.poll()
    archive, = battle.runtime.sessions.pop_archives()
    state.selected = BattleRecord.from_archive(archive)
    history = deepcopy(state.snapshot()['history_selected']['multiplayer'])
    original = deepcopy(sample)
    state.accept('lifecycle', 'preparing')
    snapshot = state.snapshot()
    current = snapshot['multiplayer']
    assert current['scope_kind'] == 'prepare' and current['available'] is False
    assert current['scope'] is current['native_round'] is None
    assert all(p['view']['entries'] == [] and p['observed_damage'] is None
               and p['display']['damage'] == '未知' for p in current['players'])
    assert snapshot['history_selected']['multiplayer'] == history
    assert sample == original and state.last is sample


def test_actual_phase_switch_resets_local_trend_and_uses_new_scoped_events(tmp_path):
    battle = populated()
    state = DesktopState(tmp_path)
    state.accept('sample', battle.runtime.display_sample())
    previous_id = state.snapshot()['live_analysis']['battle_id']
    battle.native_phase, battle.autochess_epoch = 5, 2
    battle.advance(3)
    battle.poll()
    battle.advance(4)
    battle.advance(4, [battle.scoped_hit(5, 3, 60)])
    battle.poll()
    sample = battle.runtime.display_sample()
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert snapshot['multiplayer']['scope_kind'] == 'assist'
    entry, = snapshot['panels'][0]['entries']
    assert entry['value'] == 60 and entry['owner_id'].startswith('910000003:')
    assert snapshot['live_analysis']['battle_id'] != previous_id
    assert snapshot['live_analysis']['observed_peak_dps'] is None


def test_unknown_account_slot_cannot_keep_an_old_players_values(tmp_path):
    payload = deepcopy(populated().runtime.display_sample()['multiplayer'])
    payload['players'][0]['player_uid'] = None
    payload['players'][0]['player_side'] = 3
    result = DesktopState(tmp_path).multiplayer_view(payload, event=EventAvailability('recording'))
    player = result['players'][0]
    assert player['slot'] == 1 and player['player_uid'] is None
    assert 'statistics' not in player and 'actor_ledgers' not in player
    assert player['view']['entries'] == [] and player['view']['source'] == {}
    assert player['display']['damage'] == '未知' and player['observed_damage'] is None


def test_unavailable_v2_payload_cannot_retain_old_values_in_its_views(tmp_path):
    payload = deepcopy(populated().runtime.display_sample()['multiplayer'])
    payload['available'] = False
    result = DesktopState(tmp_path).multiplayer_view(payload)
    for player in result['players']:
        assert player['display']['damage'] == '未知' and player['observed_damage'] is None
        assert 'statistics' not in player and 'actor_ledgers' not in player
        assert player['view']['source'] == {}
        assert player['view']['footer_total'] is None


def test_self_account_switch_with_same_battle_clock_resets_display_trend_identity(tmp_path):
    sample = deepcopy(populated().runtime.display_sample())
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    previous = deepcopy(state.snapshot()['live_analysis'])
    for player in sample['multiplayer']['players']:
        player['is_local'] = player['player_uid'] == '910000001'
    sample['multiplayer']['self_player_uid'] = '910000001'
    state.accept('sample', sample)
    current = state.snapshot()['live_analysis']
    assert current['battle_id'] != previous['battle_id']
    assert current['observed_team_recent_dps'] == 50
    assert current['operators'][0]['owner_id'].startswith('910000001:')


@pytest.mark.parametrize('invalid', [
    'wrong_local_flag', 'missing_self_uid', 'null_self_uid', 'invalid_self_uid',
    'numeric_self_uid', 'leading_zero_self_uid', 'oversized_self_uid',
    'unresolved_self_uid', 'repeated_self_uid',
])
def test_native_self_identity_contract_cannot_be_replaced_by_local_flags(tmp_path, invalid):
    sample = deepcopy(populated().runtime.display_sample())
    payload = sample['multiplayer']
    assert payload['self_player_uid'] == '910000003'
    if invalid == 'wrong_local_flag':
        for player in payload['players']:
            player['is_local'] = player['player_uid'] == '910000001'
    elif invalid == 'missing_self_uid':
        payload.pop('self_player_uid')
    elif invalid == 'null_self_uid':
        payload['self_player_uid'] = None
    elif invalid == 'invalid_self_uid':
        payload['self_player_uid'] = 'side3'
    elif invalid == 'numeric_self_uid':
        payload['self_player_uid'] = 910000003
    elif invalid == 'leading_zero_self_uid':
        payload['self_player_uid'] = '0910000003'
    elif invalid == 'oversized_self_uid':
        payload['self_player_uid'] = str(1 << 64)
    elif invalid == 'unresolved_self_uid':
        payload['self_player_uid'] = '910000009'
    else:
        payload['players'][0]['player_uid'] = payload['self_player_uid']
    sample['statistics']['rows'] = {CID: dict(damage=90000, healing=900, counter_snapshot_complete=True)}
    original = deepcopy(sample)
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert snapshot['panels'][0]['entries'] == [] and snapshot['panels'][0]['source'] == {}
    assert snapshot['totals']['damage'] is snapshot['totals']['healing'] is None
    assert snapshot['live_analysis']['operators'] == []
    assert snapshot['live_analysis']['team_total_damage'] is None
    assert 'observed_team_recent_dps' not in snapshot['live_analysis']
    assert sample == original


def test_damage_empty_gate_keeps_other_family_evidence_in_the_source(tmp_path):
    battle = ScopedNativeBattle()
    battle.poll()
    sample = deepcopy(battle.runtime.display_sample())
    local = next(player for player in sample['multiplayer']['players'] if player['is_local'])
    row = next(iter(local['statistics']['rows'].values()))
    row['shield_absorbed'] = 25
    row['observed_healing'] = 40
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(PanelConfig(category=Category.SHIELD),))
    state.accept('sample', sample)
    view, = state.snapshot()['panels']
    entry, = view['entries']
    assert entry['value'] == 25
    assert view['source'][entry['owner_id']]['observed_healing'] == 40


def test_a_real_zero_settlement_is_distinct_from_an_empty_baseline(tmp_path):
    battle = ScopedNativeBattle()
    battle.poll()
    battle.advance(2)
    battle.advance(2, [battle.scoped_hit(1, 3, 0)])
    battle.poll()
    sample = battle.runtime.display_sample()
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(PanelConfig(), PanelConfig(panel_id='effective', effective_only=True)))
    state.accept('sample', sample)
    snapshot = state.snapshot()
    local = next(player for player in snapshot['multiplayer']['players'] if player['is_local'])
    assert local['observed_damage'] == 0 and local['display']['damage'] == '已观察 0'
    assert all(panel['footer_total'] == 0 for panel in snapshot['panels'])
    assert sample['statistics']['totals_complete'] is False


def test_incomplete_event_lane_preserves_observed_subset_without_certifying_totals(tmp_path):
    sample = deepcopy(populated().runtime.display_sample())
    local = next(player for player in sample['multiplayer']['players'] if player['is_local'])
    row = next(iter(local['statistics']['rows'].values()))
    row['settled_damage'].update(observed_damage_complete=False,
                                observed_recent_damage_complete=False, reason='区间存在缺口')
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    snapshot = state.snapshot()
    panel, = snapshot['panels']
    entry, = panel['entries']
    assert entry['value'] == 300 and entry['incomplete'] is True
    assert 'observed_damage' in entry['coverage']
    assert panel['footer_total'] == 300 and panel['footer_total_complete'] is False
    assert snapshot['live_analysis']['operators'][0]['recent_dps'] is None
    assert 'observed_team_recent_dps' not in snapshot['live_analysis']


def test_event_only_recent_value_needs_its_real_bounded_window(tmp_path):
    sample = deepcopy(populated().runtime.display_sample())
    local = next(player for player in sample['multiplayer']['players'] if player['is_local'])
    row = next(iter(local['statistics']['rows'].values()))
    row['settled_damage']['observed_recent_damage_window'] = [2, 2]
    state = DesktopState(tmp_path)
    state.layout = replace(state.layout, panels=(PanelConfig(ranking=Ranking.DPS),))
    state.accept('sample', sample)
    snapshot = state.snapshot()
    assert snapshot['panels'][0]['entries'] == []
    assert snapshot['panels'][0]['footer_rate'] is None
    assert snapshot['live_analysis']['operators'][0]['recent_dps'] is None


def test_event_lane_mapping_requires_explicit_display_container_and_preserves_native_unknown(tmp_path):
    sample = populated().runtime.display_sample()
    local = next(player for player in sample['multiplayer']['players'] if player['is_local'])
    raw = local['statistics']
    state = DesktopState(tmp_path)
    ordinary = state.view(PanelConfig(), raw, event=EventAvailability('recording'))
    assert ordinary['entries'] == [] and ordinary['footer_total'] is None
    state.accept('sample', sample)
    for ranking in (Ranking.TOTAL, Ranking.RECENT, Ranking.DPS):
        state.layout = replace(state.layout, panels=(PanelConfig(ranking=ranking),))
        view, = state.snapshot()['panels']
        entry, = view['entries']
        assert entry['value'] == (300 if ranking is Ranking.TOTAL else 150)
        assert entry['rate'] == 150 and view['footer_total_complete'] is False
    assert all(row['damage'] is None and row['recent_damage'] is None for row in raw['rows'].values())


@pytest.mark.parametrize('end_phase', [False, True], ids=['combat_terminal', 'native_END8'])
@pytest.mark.parametrize('effective', [False, True], ids=['nominal', 'effective'])
def test_saved_native_history_renders_only_verified_self_without_changing_record(tmp_path, end_phase, effective):
    record, path = saved_native_record(tmp_path, end_phase=end_phase)
    before, original_bytes = record.to_dict(), path.read_bytes()
    assert record.statistics['rows'] == {} and record.statistics['totals_complete'] is False
    assert record.last_sample['multiplayer']['status'] == 'ended'
    if end_phase:
        assert record.last_sample['autochess_context']['phase'] == 8
        assert record.last_sample['multiplayer']['native_phase'] == 4
    state = DesktopState(tmp_path)
    state.command(dict(version=1, op='history_select', battle_id=record.battle_id))
    state.history_config = PanelConfig(panel_id='history', effective_only=effective)
    selected = state.snapshot()['history_selected']
    view = selected['view']
    entry, = view['entries']
    assert entry['owner_id'] == f'910000003:{CID}#301' and entry['value'] == 300
    assert entry['actor_uid'] == 301 and entry['portrait_id'] == CID
    assert entry['player_uid'] == '910000003'
    assert view['metric_label'].startswith('已观察')
    assert view['footer_total'] == 300 and view['footer_total_complete'] is False
    assert view['source'][entry['owner_id']]['damage'] is None
    assert view['source'][entry['owner_id']]['settled_damage']['observed_damage'] == 300
    assert view['details_by_owner'][entry['owner_id']]['effective_damage'] == 300
    assert selected['multiplayer']['players'][2]['observed_damage'] == 300
    state.accept('lifecycle', 'preparing')
    assert state.snapshot()['history_selected'] == selected
    other_live = populated().runtime.display_sample()
    other_live['multiplayer']['scope_kind'] = 'assist'
    other_live['multiplayer']['scope'] += ':another-live-scope'
    state.accept('sample', other_live)
    assert state.snapshot()['history_selected'] == selected
    assert record.to_dict() == before and path.read_bytes() == original_bytes
    assert state.selected.to_dict() == before
    assert state.selected.statistics['rows'] == {}
    assert state.selected.last_sample['session'] == before['last_sample']['session']


@pytest.mark.parametrize('invalid', ['wrong_flag', 'missing_self', 'multiple_flags', 'repeated_uid'])
def test_native_history_consumer_does_not_infer_self_from_bad_late_metadata(tmp_path, monkeypatch, invalid):
    record, path = saved_native_record(tmp_path)
    original, original_bytes = record.to_dict(), path.read_bytes()
    late_sample = record.last_sample
    payload = late_sample['multiplayer']
    if invalid == 'wrong_flag':
        for player in payload['players']:
            player['is_local'] = player['player_uid'] == '910000001'
    elif invalid == 'missing_self':
        payload.pop('self_player_uid')
    elif invalid == 'multiple_flags':
        payload['players'][0]['is_local'] = True
    else:
        payload['players'][0]['player_uid'] = payload['self_player_uid']
    # Files and the validated base record stay intact. A bad later display
    # handoff still cannot make the consumer borrow another player's ledger.
    stored_sample = BattleRecord.last_sample
    monkeypatch.setattr(BattleRecord, 'last_sample', property(
        lambda value: deepcopy(late_sample) if value is record else stored_sample.fget(value)))
    state = DesktopState(tmp_path)
    state.selected = record
    selected = state.snapshot()['history_selected']
    assert selected['view']['entries'] == [] and selected['view']['source'] == {}
    assert selected['view']['footer_total'] is None
    assert record.to_dict() == original and path.read_bytes() == original_bytes

