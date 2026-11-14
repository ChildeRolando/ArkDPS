"""WPF boundary regression tests; no device access or window required."""
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from battle_history import BattleHistory, BattleRecord
from desktop_bridge import DesktopState, CollectionWorker, PROTOCOL_VERSION, empty_text, serve
from layout_store import LayoutStore
from panel_model import PanelConfig
from stats_core import Statistics
from test_battle_history import record
from test_battle_share import exported_record
from sample_data import sample


def cmd(state, op, **fields):
    return state.command(dict(version=PROTOCOL_VERSION, op=op, **fields))


def test_worker_import_does_not_import_tk_or_frida():
    result = subprocess.run([sys.executable, '-c',
        "import desktop_bridge, sys; assert 'tkinter' not in sys.modules; assert 'frida' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_effective_panel_with_known_native_damage_does_not_call_unknown_effective_damage_zero():
    config = PanelConfig(effective_only=True)
    source = {'char_a': {'damage': 50, 'effective_damage': None, 'counter_snapshot_complete': True}}
    assert empty_text(config, source, True, {}) == '当前有效伤害尚无可靠数据'


def test_first_launch_and_default_model_ignores_legacy_projection(tmp_path):
    state = DesktopState(tmp_path)
    assert len(state.layout.panels) == 1 and not state.layout.huds
    assert state.layout.theme == 'lone-trail'
    data = sample()
    before = copy.deepcopy(data)
    state.accept('sample', data)
    view = state.snapshot()['panels'][0]
    assert [e['value'] for e in view['entries']] == [1230., 500.]
    assert data == before
    assert view['source']['a']['shield_absorbed'] is None


def test_side_mode_persists_full_window_geometry_and_preserves_rankings(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', boss_analysis_sample(22.3))
    cfg = state.layout.panels[0].to_dict()
    cmd(state, 'panel_update', config=dict(cfg, boss_only=True, effective_only=True))
    normal = state.snapshot()
    first = normal['panels'][0]
    assert first['boss_damage_available'] and first['config']['effective_only']
    assert first['entries'][0]['value'] == 160.
    gui = dict(x=50, y=60, width=1200, height=800)
    cmd(state, 'placement', gui=gui, huds=[], side_mode=True)
    docked = state.snapshot()
    assert docked['panels'] == normal['panels']
    restored = DesktopState(tmp_path)
    assert restored.layout.side_mode and restored.layout.gui.to_dict() == gui
    assert restored.layout.panels[0].boss_only and restored.layout.panels[0].effective_only
    cmd(restored, 'placement', gui=gui, huds=[])
    assert restored.layout.side_mode
    cmd(restored, 'placement', gui=gui, huds=[], side_mode=False)
    assert not DesktopState(tmp_path).layout.side_mode


def test_legacy_recent_ranking_reports_cumulative_amount_for_side_column(tmp_path):
    state = DesktopState(tmp_path)
    cmd(state, 'panel_update', config=dict(panel_id='default', category='damage', ranking='recent'))
    state.accept('sample', analysis_sample(rate=20.))
    entry = state.snapshot()['panels'][0]['entries'][0]
    assert entry['display_value'] == '20'
    assert entry['cumulative_display_value'] == '1.2K'


def test_snapshot_team_totals_keep_unknown_unknown():
    from desktop_bridge import team_totals
    known = {'rows': {'a': {'damage': 10, 'healing': 2, 'shield_absorbed': 1},
                      'b': {'damage': 5, 'healing': None, 'shield_absorbed': 3}}}
    totals = team_totals(known)
    assert totals['damage'] == 15 and totals['shield_absorbed'] == 4
    assert totals['healing'] is None
    assert team_totals({})['damage'] is None


def analysis_sample(game_time=22.3, rate=100., *, battle_id='battle-a'):
    data = sample()
    data.update(battle_id=battle_id, session=battle_id, game_time=game_time,
                frame=int(game_time * 30))
    data['statistics']['game_time'] = game_time
    rows = data['statistics']['rows']
    rows['a']['recent_damage'] = rate
    for row in rows.values():
        row['recent_damage_window'] = (max(0., game_time - 5), game_time)
    return data


def test_live_analysis_preserves_known_zero_and_excludes_non_team_output(tmp_path):
    state = DesktopState(tmp_path)
    assert state.snapshot()['live_analysis'] is None
    data = analysis_sample(rate=0.)
    data['statistics']['non_team_sources'] = ['trap_321_shnbox']
    data['statistics']['non_team_output'] = {'trap_321_shnbox': {'damage': 999}}
    state.accept('sample', data)
    snapshot = state.snapshot()
    assert snapshot['status'] == 'degraded'
    assert snapshot['live_analysis'] == {
        'battle_id': 'battle-a', 'game_time': 22.3, 'segment': 0,
        'team_recent_dps': 0., 'team_average_dps': pytest.approx(1730. / 22.3),
        'observed_peak_dps': 0., 'team_total_damage': 1730.,
        'operators': [{'owner_id': 'a', 'name': '赫默', 'recent_dps': 0.},
                      {'owner_id': 'b', 'name': '夜刀', 'recent_dps': 0.}],
    }


@pytest.mark.parametrize('coverage_issue', ['different_window', 'stale_window', 'uncertified',
                                             'unattributed'])
def test_live_analysis_requires_joint_coverage_for_team_dps(tmp_path, coverage_issue):
    state = DesktopState(tmp_path)
    data = analysis_sample()
    stats = data['statistics']
    other = stats['rows']['b']
    if coverage_issue == 'different_window':
        other['recent_damage_window'] = (20., 22.3)
    elif coverage_issue == 'stale_window':
        other['recent_damage_window'] = (19., 22.)
    elif coverage_issue == 'uncertified':
        other['counter_snapshot_complete'] = False
    else:
        stats['unknown_sources'] = ['unknown']
    state.accept('sample', data)
    dto = state.snapshot()['live_analysis']
    assert dto['team_recent_dps'] is None
    assert dto['observed_peak_dps'] is None
    assert dto['operators'][0]['recent_dps'] == 100.
    assert dto['operators'][1]['recent_dps'] == (
        None if coverage_issue in ('stale_window', 'uncertified') else 0.)
    assert dto['team_total_damage'] == (
        None if coverage_issue in ('unattributed', 'uncertified') else 1730.)


def test_live_overview_retains_the_verified_team_subset_for_new_captures(tmp_path):
    state = DesktopState(tmp_path)
    data = analysis_sample()
    stats = data['statistics']
    stats['unknown_sources'] = ['unmapped']
    stats['rows']['a']['partial_damage'] = {'effective_damage': 100}
    state.accept('sample', data)
    dto = state.snapshot()['live_analysis']
    assert dto['team_total_damage'] is None
    assert dto['observed_team_total_damage'] == 1730
    assert dto['observed_team_recent_dps'] == 100


def test_live_analysis_preserves_peak_across_gaps_and_resets_at_new_battle(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', analysis_sample(20., 50.))
    first = state.snapshot()['live_analysis']

    state.last_progress -= 1
    assert state.snapshot()['status'] == 'paused'
    assert state.snapshot()['live_analysis'] == first

    event_gap = dict(analysis_sample(21., 90.), event_status='gap')
    state.accept('sample', event_gap)
    dto = state.snapshot()['live_analysis']
    assert dto['segment'] == 0 and dto['observed_peak_dps'] == 90.

    counter_gap = analysis_sample(22., 999.)
    counter_gap['statistics']['rows']['b']['counter_snapshot_complete'] = False
    state.accept('sample', counter_gap)
    state.accept('sample', analysis_sample(23., 70.))
    dto = state.snapshot()['live_analysis']
    assert dto['segment'] == 1 and dto['observed_peak_dps'] == 90.

    state.accept('error', 'lost')
    assert state.snapshot()['status'] == 'disconnected'
    assert state.snapshot()['live_analysis'] == dto
    state.accept('sample', analysis_sample(24., 80.))
    dto = state.snapshot()['live_analysis']
    assert dto['segment'] == 2 and dto['observed_peak_dps'] == 90.

    terminal = analysis_sample(24., 1000.)
    terminal['statistics']['ended'] = True
    state.accept('lifecycle', 'ended')
    state.accept('sample', terminal)
    dto = state.snapshot()['live_analysis']
    assert dto['team_total_damage'] == 1730.
    assert dto['team_recent_dps'] is None
    assert dto['segment'] == 2 and dto['observed_peak_dps'] == 90.

    state.accept('lifecycle', 'live')
    state.accept('sample', analysis_sample(2., 15., battle_id='battle-b'))
    dto = state.snapshot()['live_analysis']
    assert dto['battle_id'] == 'battle-b'
    assert dto['segment'] == 0 and dto['observed_peak_dps'] == 15.


def test_live_analysis_cuts_series_when_stale_and_recovers(tmp_path):
    state = DesktopState(tmp_path)
    data = analysis_sample(20.)
    state.accept('sample', data)
    first = state.snapshot()['live_analysis']
    data['captured_monotonic'] -= 3
    assert state.snapshot()['status'] == 'stale'
    assert state.snapshot()['live_analysis'] == first
    state.accept('sample', analysis_sample(23., 30.))
    resumed = state.snapshot()['live_analysis']
    assert resumed['segment'] == 1
    assert resumed['observed_peak_dps'] == 100.


def test_panel_details_keep_owners_outside_rank_and_in_old_history(tmp_path):
    legacy = record().to_dict()
    for key in tuple(legacy['statistics']['rows']['a']):
        if 'skill' in key:
            del legacy['statistics']['rows']['a'][key]
    BattleHistory(tmp_path / 'history').save(BattleRecord.from_dict(legacy))
    state = DesktopState(tmp_path)
    data = analysis_sample()
    data['statistics']['rows']['b'].update(
        overkill_damage=100., overkill_damage_by_type={'physical': 100.},
        overkill_coverage_complete=True, overkill_type_complete=True,
        skill_damage_effective=75., non_skill_damage_effective=325.,
        skill_split_effective_complete=True)
    state.accept('sample', data)
    config = state.layout.panels[0].to_dict()
    cmd(state, 'panel_update', config=dict(config, ranking='dps', effective_only=True))
    snapshot = state.snapshot()
    view = snapshot['panels'][0]
    assert not any(entry['owner_id'] == 'b' for entry in view['entries'])
    assert view['details_by_owner']['b']['effective_damage_by_type'] == {'physical': 400.}
    assert view['details_by_owner']['b']['skill_damage_effective'] == 75.
    assert snapshot['history_selected']['view']['details_by_owner']['a']['skill_damage'] is None


def boss_fixture():
    return dict(damage=180., effective_damage=160., overkill_damage=20.,
                counter_snapshot_complete=True, event_coverage_complete=True,
                overkill_coverage_complete=True, overkill_type_complete=True,
                damage_by_type={'arts': 180.}, overkill_damage_by_type={'arts': 20.},
                observed_damage=180., active_damage=4., engagement_damage=45.,
                recent_damage=36., recent_damage_window=(17.3, 22.3),
                recent_overkill_damage=20., recent_damage_by_type={'arts': 36.},
                skill_damage=120., non_skill_damage=60., skill_damage_effective=100.,
                non_skill_damage_effective=60., skill_split_complete=True,
                skill_split_effective_complete=True, mitigation_rate=10.)


def boss_analysis_sample(game_time, *, battle_id='battle-a'):
    data = analysis_sample(game_time, battle_id=battle_id)
    data.update(event_status='recording', event_error=None)
    stats = data['statistics']
    stats['boss_damage_available'] = True
    for row in stats['rows'].values():
        row['boss_damage'] = dict(
            boss_fixture(), recent_damage_window=(max(0., game_time - 5), game_time))
    stats['rows']['b']['boss_damage'].update(recent_damage=0., recent_overkill_damage=0.)
    return data


def test_boss_trend_samples_both_scopes_independent_of_panel_filter(tmp_path):
    state = DesktopState(tmp_path)
    assert state.snapshot()['boss_live_analysis'] is None
    state.accept('sample', boss_analysis_sample(21.))
    before = state.snapshot()
    raw = before['boss_live_analysis']
    effective = before['boss_effective_live_analysis']
    assert raw == {
        'battle_id': 'battle-a', 'game_time': 21., 'segment': 0, 'team_recent_dps': 36.,
        'operators': [{'owner_id': 'a', 'name': '赫默', 'recent_dps': 36.},
                      {'owner_id': 'b', 'name': '夜刀', 'recent_dps': 0.}],
    }
    assert effective['team_recent_dps'] == 32.
    assert [op['recent_dps'] for op in effective['operators']] == [32., 0.]
    assert before['live_analysis']['team_recent_dps'] == 100.
    cmd(state, 'panel_update', config=dict(state.layout.panels[0].to_dict(),
                                           boss_only=True, effective_only=True))
    assert state.snapshot()['boss_live_analysis'] == raw
    assert state.snapshot()['boss_effective_live_analysis'] == effective


def test_boss_trend_preserves_unknown_scopes_and_breaks_on_event_gaps(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', boss_analysis_sample(20.))
    partial = boss_analysis_sample(21.)
    partial['statistics']['rows']['a']['boss_damage']['overkill_coverage_complete'] = False
    state.accept('sample', partial)
    snapshot = state.snapshot()
    raw = snapshot['boss_live_analysis']
    effective = snapshot['boss_effective_live_analysis']
    assert raw['team_recent_dps'] == 36. and raw['segment'] == 0
    assert effective['team_recent_dps'] is None and effective['segment'] == 1
    assert [op['recent_dps'] for op in effective['operators']] == [None, 0.]

    # Even a stale previous calculation must not carry through an event gap.
    gap = dict(boss_analysis_sample(22.), event_status='gap')
    state.accept('sample', gap)
    snapshot = state.snapshot()
    assert snapshot['live_analysis']['team_recent_dps'] == 100.
    assert snapshot['live_analysis']['segment'] == 0
    for key in ('boss_live_analysis', 'boss_effective_live_analysis'):
        assert snapshot[key]['team_recent_dps'] is None
        assert all(op['recent_dps'] is None for op in snapshot[key]['operators'])
    assert snapshot['boss_live_analysis']['segment'] == 1
    assert snapshot['boss_effective_live_analysis']['segment'] == 2

    state.accept('sample', boss_analysis_sample(23.))
    assert state.snapshot()['boss_live_analysis']['segment'] == 1
    state.accept('error', 'lost')
    state.accept('sample', boss_analysis_sample(24.))
    assert state.snapshot()['boss_live_analysis']['segment'] == 2
    assert state.snapshot()['boss_effective_live_analysis']['segment'] == 3

    terminal = boss_analysis_sample(25.)
    terminal['statistics']['ended'] = True
    state.accept('sample', terminal)
    assert state.snapshot()['boss_live_analysis']['team_recent_dps'] is None
    assert state.snapshot()['boss_live_analysis']['segment'] == 2
    state.accept('sample', analysis_sample(3., battle_id='battle-b'))
    assert state.snapshot()['boss_live_analysis'] is None
    assert state.snapshot()['boss_effective_live_analysis'] is None


@pytest.mark.parametrize('basis,expected', [
    ('recent_5s', 32.), ('engagement', 40.), ('full_cycle', 20.),
])
@pytest.mark.parametrize('ranking', ['total', 'dps'])
def test_boss_only_uses_scoped_rates_and_details_without_changing_global_stats(tmp_path, basis, expected, ranking):
    state = DesktopState(tmp_path)
    data = analysis_sample()
    data.update(event_status='recording', event_error=None)
    stats = data['statistics']
    stats['boss_damage_available'] = True
    stats['rows']['a'].update(boss_damage=boss_fixture(), presence_complete=True, presence_seconds=8.)
    # A known zero participates in the denominator without appearing as a rank.
    stats['rows']['b']['boss_damage'] = dict(
        boss_fixture(), damage=0., effective_damage=0., observed_damage=0., active_damage=0.,
        recent_damage=0., engagement_damage=None, overkill_damage=0., recent_overkill_damage=0.)
    stats['rows']['b'].update(presence_complete=True, presence_seconds=8.)
    state.accept('sample', data)
    cfg = state.layout.panels[0].to_dict()
    cmd(state, 'panel_update', config=dict(cfg, boss_only=True, effective_only=True,
                                           rate_basis=basis, ranking=ranking))
    snapshot = state.snapshot()
    view = snapshot['panels'][0]
    a, = view['entries']
    assert view['boss_damage_available']
    assert (a['owner_id'], a['value'], a['rate'], a['share']) == (
        'a', expected if ranking == 'dps' else 160., expected, 1.)
    assert view['footer_total'] == 160. and view['share_scope'] == 'team'
    assert view['source']['a']['damage'] == 180.
    assert view['source']['a']['mitigation_rate'] == 10.
    assert view['details_by_owner']['a']['effective_damage_by_type'] == {'arts': 160.}
    assert view['details_by_owner']['a']['skill_damage_effective'] == 100.
    assert view['rates']['a']['damage']['text'] == '40/s'
    assert snapshot['totals']['damage'] == 1730.
    assert stats['rows']['a']['damage'] == 1230.

    cmd(state, 'panel_update', config=dict(cfg, boss_only=True, effective_only=False))
    total = state.snapshot()['panels'][0]
    assert total['entries'][0]['value'] == 180.
    assert total['details_by_owner']['a']['skill_damage'] == 120.

    cmd(state, 'panel_update', config=dict(cfg, boss_only=True, category='healing'))
    healing = state.snapshot()['panels'][0]
    assert healing['config']['boss_only'] and healing['entries'][0]['value'] == 615.
    assert 'boss_damage_available' not in healing
    assert LayoutStore(tmp_path / 'layout.json').load().layout.panels[0].boss_only


def test_boss_only_history_uses_archived_scope_and_reports_legacy_unknown(tmp_path):
    old = record(0).to_dict()
    old['statistics'].pop('boss_damage_available', None)
    old['statistics'].pop('boss_damage_reason', None)
    old['statistics']['rows']['a'].pop('boss_damage', None)
    new = record(1).to_dict()
    new['statistics']['boss_damage_available'] = True
    new['statistics']['rows']['a']['boss_damage'] = boss_fixture()
    history = BattleHistory(tmp_path / 'history')
    history.save(BattleRecord.from_dict(old))
    history.save(BattleRecord.from_dict(new))
    state = DesktopState(tmp_path)
    cmd(state, 'history_config', config={'boss_only': True, 'effective_only': True})
    cmd(state, 'history_select', battle_id='battle:1')
    selected = state.snapshot()['history_selected']
    assert selected['view']['entries'][0]['value'] == 160.
    target = tmp_path / 'boss-filter.json'
    cmd(state, 'export', format='json', path=str(target), battle_id=selected['battle_id'])
    assert exported_record(target.read_text('utf-8'))[1]['statistics']['rows']['a']['damage'] == 230.

    cmd(state, 'history_select', battle_id='battle:0')
    view = state.snapshot()['history_selected']['view']
    assert not view['boss_damage_available']
    assert '未记录首领伤害' in view['empty_text']
    assert not view['entries'] and view['footer_total'] is None
    assert view['details_by_owner']['a']['damage'] is None


def test_boss_only_does_not_report_partial_effective_data_as_zero(tmp_path):
    state = DesktopState(tmp_path)
    data = analysis_sample()
    stats = data['statistics']
    stats.update(boss_damage_available=True, boss_damage_reason='部分目标身份未确认')
    stats['rows']['a']['boss_damage'] = dict(
        boss_fixture(), effective_damage=None, overkill_coverage_complete=False)
    state.accept('sample', data)
    cmd(state, 'panel_update', config=dict(state.layout.panels[0].to_dict(),
                                           boss_only=True, effective_only=True))
    view = state.snapshot()['panels'][0]
    assert view['boss_damage_available']
    assert not view['entries'] and view['footer_total'] is None
    assert view['empty_text'] == '部分目标身份未确认'


def test_rate_does_not_change_ranks_bars_shares_and_shield_rejects_engagement(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', sample())
    old = state.snapshot()['panels'][0]
    config = dict(state.layout.panels[0].to_dict(), rate_basis='engagement')
    cmd(state, 'panel_update', config=config)
    new = state.snapshot()['panels'][0]
    for key in ('owner_id', 'value', 'bar_ratio', 'share'):
        assert [e[key] for e in old['entries']] == [e[key] for e in new['entries']]
    assert all(e['rate'] is None for e in new['entries'])
    with pytest.raises(ValueError):
        cmd(state, 'panel_update', config=dict(config, category='shield'))


@pytest.mark.parametrize('theme', ['people', 'babel'])
def test_theme_and_panel_management_persist_without_touching_live_or_history(tmp_path, theme):
    state = DesktopState(tmp_path)
    state.accept('sample', sample())
    state.history.save(record())
    state.refresh_history()
    selected, last = state.selected, state.last
    cmd(state, 'panel_duplicate', panel_id='default')
    second = state.layout.panels[-1].panel_id
    assert second != 'default'
    hud = dict(panel_id=second, x=10, y=20, width=410, height=430, opacity=.83, locked=True)
    cmd(state, 'placement', gui=dict(x=50, y=60, width=1200, height=800), huds=[hud])
    cmd(state, 'theme', theme=theme)
    assert state.snapshot()['layout']['theme'] == theme
    assert state.last is last and state.selected is selected
    assert state.layout.huds[0].to_dict() == hud
    restarted = DesktopState(tmp_path)
    assert restarted.layout == state.layout
    cmd(state, 'theme', theme='lone-trail')
    assert DesktopState(tmp_path).layout.theme == 'lone-trail'
    cmd(state, 'panel_delete', panel_id=second)
    assert not state.layout.huds
    assert state.last is last
    with pytest.raises(ValueError, match='至少'):
        cmd(state, 'panel_delete', panel_id='default')


def test_hud_styles_are_per_panel_and_survive_closing_and_restarting(tmp_path):
    state = DesktopState(tmp_path)
    cmd(state, 'panel_duplicate', panel_id='default')
    second = state.layout.panels[-1].panel_id
    assert state.snapshot()['layout']['hud_styles'] == {}

    cmd(state, 'hud_style', panel_id='default',
        style={'name_font': 21.5, 'name_width': 168, 'column_gap': 9, 'bar_width': 0})
    cmd(state, 'hud_style', panel_id=second, style={'width': 700, 'row_gap': 4, 'scale': 0.8})
    profiles = state.snapshot()['layout']['hud_styles']
    default_style = dict(title_font=20, name_font=15, number_font=15, width=484,
                         row_height=46, title_height=44, portrait_size=38,
                         rank_width=28, portrait_width=52, name_width=92, bar_width=158,
                         total_width=78, rate_width=76, share_width=68,
                         row_gap=1, column_gap=2, padding=4, scale=1.0)
    assert profiles['default'] == dict(default_style, name_font=21.5, name_width=168,
                                       column_gap=9, bar_width=0)
    assert profiles[second] == dict(default_style, width=700, row_gap=4, scale=0.8)

    gui = dict(x=50, y=60, width=1200, height=800)
    hud = dict(panel_id=second, x=-700, y=30, width=830, height=480, opacity=.83, locked=True)
    cmd(state, 'placement', gui=gui, huds=[hud])
    assert state.snapshot()['layout']['huds'] == [hud]
    cmd(state, 'placement', gui=gui, huds=[])
    state = DesktopState(tmp_path)
    assert state.snapshot()['layout']['hud_styles'] == profiles
    assert state.snapshot()['layout']['huds'] == []

    cmd(state, 'hud_style', panel_id='default', style=None)
    assert state.snapshot()['layout']['hud_styles'] == {second: profiles[second]}
    cmd(state, 'panel_delete', panel_id=second)
    assert DesktopState(tmp_path).snapshot()['layout']['hud_styles'] == {}
    with pytest.raises(ValueError, match='源面板'):
        cmd(state, 'hud_style', panel_id=second, style={'width': 700})


@pytest.mark.parametrize('style', [
    {'number_font': True}, {'title_font': float('nan')}, {'width': float('inf')},
    {'row_height': 0}, {'column_gap': -1}, {'share_width': 601}, {'unknown': 18}, [],
])
def test_bad_hud_style_command_preserves_saved_profile(tmp_path, style):
    state = DesktopState(tmp_path)
    cmd(state, 'hud_style', panel_id='default', style={'number_font': 19})
    before = state.layout
    original = state.store.path.read_bytes()

    with pytest.raises(ValueError, match='HUD 样式'):
        cmd(state, 'hud_style', panel_id='default', style=style)

    assert state.layout == before
    assert state.store.path.read_bytes() == original


def test_number_units_reformats_live_and_history_without_changing_raw_data(tmp_path):
    state = DesktopState(tmp_path)
    data = sample()
    archived = record().to_dict()
    values = dict(damage=1_234_567.89, recent_damage=23_456.78,
                  engagement_damage=20_000., damage_by_type={'physical': 1_234_567.89})
    data['statistics']['rows']['a'].update(values)
    archived['statistics']['rows']['a'].update(values, recent_damage_window=[7., 12.])
    state.history.save(BattleRecord.from_dict(archived))
    state.refresh_history()
    original_sample = copy.deepcopy(data)
    state.accept('sample', data)
    before = copy.deepcopy(state.snapshot())
    original_export = tmp_path / 'western.json'
    cmd(state, 'export', format='json', path=str(original_export),
        battle_id=before['history_selected']['battle_id'])

    cmd(state, 'number_units', number_units='chinese')

    after = state.snapshot()
    assert before['layout']['number_units'] == 'western'
    assert after['layout']['number_units'] == 'chinese'
    assert data == original_sample
    assert after['live_analysis'] == before['live_analysis']
    assert after['totals'] == before['totals']
    assert after['history_selected']['battle_id'] == before['history_selected']['battle_id']
    for old, new in [(before['panels'][0], after['panels'][0]),
                     (before['history_selected']['view'], after['history_selected']['view'])]:
        assert [entry['owner_id'] for entry in new['entries']] == [entry['owner_id'] for entry in old['entries']]
        assert new['source'] == old['source']
        assert new['footer_total'] == old['footer_total']
        assert new['footer_rate'] == old['footer_rate']
        assert old['footer_total_display'] == '1.2M'
        assert new['footer_total_display'] == '123.5万'
        assert old['footer_rate_display'] == '23.5K/s'
        assert new['footer_rate_display'] == '2.3万/s'
        original, reformatted = old['entries'][0], new['entries'][0]
        for key in ('value', 'exact_value', 'rate', 'share', 'bar_ratio', 'segments'):
            assert reformatted[key] == original[key]
        assert original['display_value'] == '1.2M'
        assert reformatted['display_value'] == '123.5万'
        assert original['rate_text'] == '23.5K/s'
        assert reformatted['rate_text'] == '2.3万/s'
        assert old['rates']['a']['damage']['text'] == '20K/s'
        assert new['rates']['a']['damage']['text'] == '2万/s'

    new_export = tmp_path / 'chinese.json'
    cmd(state, 'export', format='json', path=str(new_export),
        battle_id=after['history_selected']['battle_id'])
    assert new_export.read_bytes() == original_export.read_bytes()
    restarted = DesktopState(tmp_path)
    assert restarted.layout.number_units == 'chinese'
    assert restarted.snapshot()['history_selected']['view']['entries'][0]['display_value'] == '123.5万'


def test_number_units_defaults_for_older_layout_and_rejects_invalid_setting(tmp_path):
    original = DesktopState(tmp_path).layout.to_dict()
    del original['number_units']
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps(original), encoding='utf-8')

    state = DesktopState(tmp_path)
    assert state.snapshot()['layout']['number_units'] == 'western'
    with pytest.raises(ValueError, match='未知数值单位'):
        cmd(state, 'number_units', number_units=['invalid'])
    assert state.layout.number_units == 'western'
    assert json.loads(path.read_text(encoding='utf-8')) == original


def test_history_selection_is_immutable_across_refresh_eviction_and_new_battle(tmp_path):
    state = DesktopState(tmp_path)
    first = record(1)
    state.history.save(first)
    state.refresh_history()
    selected = state.selected
    for i in range(2, 103):
        state.history.save(record(i))
    cmd(state, 'history_refresh')
    assert selected.battle_id not in state.records
    assert state.selected is selected
    state.accept('sample', sample())
    new = dict(sample(), battle_id='new', session='new')
    state.accept('sample', new)
    assert state.snapshot()['battle_id'] == 'new'
    assert state.snapshot()['history_selected']['battle_id'] == first.battle_id
    assert state.snapshot()['panels'][0]['entries'][0]['value'] == 1230
    assert not state.history.path_for(first.battle_id).exists()
    cmd(state, 'history_select', battle_id='battle:102')
    target = tmp_path / 'evicted-selection.json'
    cmd(state, 'export', format='json', path=str(target), battle_id=first.battle_id)
    assert exported_record(target.read_text('utf-8'))[1] == first.to_dict()


@pytest.mark.parametrize('version', [None, 0, 2, '1', True])
def test_incompatible_protocol_refuses_before_state_mutation(tmp_path, version):
    state = DesktopState(tmp_path)
    # JSON bool must not be accepted as an integer protocol version.
    with pytest.raises(ValueError, match='版本'):
        state.command(dict(version=version, op='theme', theme='people'))
    assert state.layout.theme == 'lone-trail'


def test_theme_command_rejects_retired_skin_without_overwriting_saved_choice(tmp_path):
    state = DesktopState(tmp_path)
    cmd(state, 'theme', theme='people')
    with pytest.raises(ValueError, match='未知皮肤'):
        cmd(state, 'theme', theme='original')
    assert state.layout.theme == 'people'
    assert DesktopState(tmp_path).layout.theme == 'people'


def test_json_lines_handshake_state_error_and_clean_exit(tmp_path):
    state = DesktopState(tmp_path)
    messages = [dict(version=1, id=1, op='snapshot'),
                dict(version=999, id=2, op='hello'),
                dict(version=1, id=3, op='hello'),
                dict(version=1, id=4, op='snapshot'),
                dict(version=1, id=5, op='quit')]
    output = io.StringIO()
    serve(io.StringIO(''.join(json.dumps(m)+'\n' for m in messages)), output, state)
    values = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [v['kind'] for v in values] == ['error', 'error', 'hello', 'snapshot', 'bye']
    assert [v['id'] for v in values] == [1, 2, 3, 4, 5]
    assert values[2]['result']['transport'] == 'stdio-jsonl'
    assert values[2]['result']['request_replay'] is True
    assert values[3]['state']['status'] == 'waiting'


def test_bad_json_is_diagnostic_not_protocol_corruption(tmp_path):
    output = io.StringIO()
    serve(io.StringIO('not JSON\n'), output, DesktopState(tmp_path))
    value = json.loads(output.getvalue())
    assert value['kind'] == 'error' and not value['ok']


def test_same_request_replays_reply_without_applying_command_or_events_twice(tmp_path):
    state = DesktopState(tmp_path)

    class Worker:
        events = queue.Queue()

    worker = Worker()
    first = dict(version=1, id=1, op='hello')
    mutation = dict(version=1, id=2, op='panel_add')
    conflict = dict(version=1, id=2, op='theme', theme='people')
    followup = dict(version=1, id=3, op='snapshot')
    output = io.StringIO()

    def command_lines():
        for request in (first, mutation):
            yield json.dumps(request) + '\n'
        worker.events.put(('connection', '新连接状态'))
        for request in (conflict, mutation, first, followup):
            yield json.dumps(request) + '\n'

    class Input:
        lines = iter(command_lines())

        def readline(self, _limit):
            return next(self.lines, '')

    serve(Input(), output, state, worker)

    lines = output.getvalue().splitlines()
    replies = [json.loads(line) for line in lines]
    assert [reply['kind'] for reply in replies] == ['hello', 'state', 'error', 'state', 'error', 'snapshot']
    assert [reply['id'] for reply in replies] == [1, 2, 2, 2, 1, 3]
    assert lines[1] == lines[3]
    assert replies[1]['state']['connection_text'] != '新连接状态'
    assert replies[-1]['state']['connection_text'] == '新连接状态'
    assert len(state.layout.panels) == 2
    assert state.layout.theme == 'lone-trail'


def test_same_request_replays_original_command_error(tmp_path):
    state = DesktopState(tmp_path)
    failed = dict(version=1, id=2, op='theme', theme='original')
    requests = (dict(version=1, id=1, op='hello'), failed, failed,
                dict(version=1, id=3, op='snapshot'))
    output = io.StringIO()
    with patch.object(state, 'command', wraps=state.command) as command:
        serve(io.StringIO(''.join(json.dumps(request) + '\n' for request in requests)), output, state)
    lines = output.getvalue().splitlines()
    assert lines[1] == lines[2]
    assert json.loads(lines[1])['kind'] == 'error'
    assert json.loads(lines[-1])['kind'] == 'snapshot'
    assert command.call_count == 2


def test_request_without_delimiter_at_eof_does_not_apply_command(tmp_path):
    state = DesktopState(tmp_path)
    first = dict(version=1, id=1, op='hello')
    interrupted = dict(version=1, id=2, op='panel_add')
    output = io.StringIO()
    serve(io.StringIO(json.dumps(first) + '\n' + json.dumps(interrupted)), output, state)
    assert [json.loads(line)['kind'] for line in output.getvalue().splitlines()] == ['hello']
    assert len(state.layout.panels) == 1


@pytest.mark.parametrize(('bad_state', 'message'), [
    ({'field': object()}, 'worker 无法编码响应'),
    ({'field': float('nan')}, 'worker 无法编码响应'),
    ({'field': '战' * 600}, 'worker 响应长度越界'),
])
def test_failed_reply_keeps_command_stream_usable(tmp_path, monkeypatch, capsys, bad_state, message):
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'MAX_MESSAGE', 1024)
    monkeypatch.delenv('ARKDPS_BRIDGE_TRACE', raising=False)
    state = DesktopState(tmp_path)
    states = iter(({}, bad_state, {'status': 'waiting'}, {}))
    monkeypatch.setattr(state, 'snapshot', lambda: next(states))
    operations = ('hello', 'history_refresh', 'snapshot', 'quit')
    requests = [dict(version=1, id=index, op=op) for index, op in enumerate(operations, 1)]
    output = io.StringIO()
    serve(io.StringIO(''.join(json.dumps(request) + '\n' for request in requests)), output, state)

    values = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply['id'] for reply in values] == [1, 2, 3, 4]
    assert [reply['kind'] for reply in values] == ['hello', 'error', 'snapshot', 'bye']
    assert values[1]['ok'] is False and values[1]['error'] == message
    assert values[2]['state'] == {'status': 'waiting'}
    error_log = capsys.readouterr().err
    assert 'op=history_refresh id=2 phase=request event=received' in error_log
    assert 'op=history_refresh id=2 phase=encode event=failed' in error_log
    assert 'op=history_refresh id=2 phase=complete event=sent' in error_log
    assert 'op=snapshot' not in error_log
    assert '战战' not in error_log


def test_statuses_keep_last_valid_values_and_do_not_convert_unknown_to_zero(tmp_path):
    state = DesktopState(tmp_path)
    assert state.status()[0] == 'waiting'
    data = sample()
    data['event_status'] = 'recording'
    state.accept('sample', data)
    assert state.status()[0] == 'live'
    state.last_progress -= 1
    assert state.status()[0] == 'paused'
    data['captured_monotonic'] -= 3
    status, label = state.status()
    assert status == 'stale'
    assert '等待下一次可靠采样' in label and '保留最后有效记录' in label
    state.accept('error', 'lost')
    assert state.status()[0] == 'disconnected'
    assert state.snapshot()['panels'][0]['entries'][0]['value'] == 1230
    resumed = dict(sample(), frame=670)
    state.accept('sample', resumed)
    assert state.status()[0] == 'degraded'


def test_preparing_status_keeps_last_verified_view_and_observer_status(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('observer', ('ready', None))
    state.accept('lifecycle', 'preparing')
    assert state.status() == ('preparing', '战斗准备中')
    state.accept('sample', sample())
    state.accept('lifecycle', 'preparing')
    result = state.snapshot()
    assert result['status'] == 'preparing'
    assert '保留上一局统计' in result['status_text']
    assert result['observer_status'] == 'ready'
    assert result['panels'][0]['entries'][0]['value'] == 1230


def test_lobby_signal_preserves_the_last_battle_and_terminal_status(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', dict(sample(), battle_metadata={'stage_id': 'Obt/Main/level_main_01-07'}))
    state.accept('lifecycle', 'ended')
    state.accept('battle_waiting', True)
    snapshot = state.snapshot()
    assert snapshot['battle_waiting'] is True
    assert snapshot['status'] == 'ended'
    assert snapshot['stage_code'] == '1-7'
    assert snapshot['panels'][0]['entries'][0]['value'] == 1230


def test_export_exact_json_and_archive_protection(tmp_path):
    state = DesktopState(tmp_path)
    data = record().to_dict()
    data['statistics']['enemy_state'] = {'events': [{'raw_value': 1.23456789}]}
    original = BattleRecord.from_dict(data)
    state.history.save(original)
    state.refresh_history()
    selected = state.snapshot()['history_selected']
    assert 'record' not in selected
    assert 'enemy_state' not in selected['view']
    target = tmp_path / 'share.json'
    cmd(state, 'export', format='json', path=str(target), battle_id=selected['battle_id'])
    assert exported_record(target.read_text('utf-8'))[1] == original.to_dict()
    with pytest.raises(FileExistsError):
        cmd(state, 'export', format='json', path=str(target), battle_id=selected['battle_id'])
    with pytest.raises(ValueError, match='历史目录'):
        cmd(state, 'export', format='json', path=str(state.history.directory / 'bad.json'),
            battle_id=selected['battle_id'])
    with pytest.raises(ValueError, match='无法找到要导出的战绩'):
        cmd(state, 'export', format='json', path=str(target), battle_id='unknown')


def test_failed_layout_write_does_not_publish_candidate(tmp_path):
    state = DesktopState(tmp_path)
    with patch.object(state.store, 'save', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            cmd(state, 'theme', theme='people')
    assert state.layout.theme == 'lone-trail'


def test_connection_apply_layout_failure_keeps_connection_unchanged(tmp_path):
    state = DesktopState(tmp_path)
    before_connection, before_layout = state.connection, state.layout
    with patch.object(state.store, 'save', side_effect=OSError('layout disk full')):
        with pytest.raises(OSError, match='layout disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert state.connection == before_connection
    assert state.layout == before_layout
    assert not state.connection_store.path.exists()


def test_connection_apply_rolls_layout_back_when_connection_write_fails(tmp_path):
    state = DesktopState(tmp_path)
    before_connection, before_layout = state.connection, state.layout
    with patch.object(state.connection_store, 'save', side_effect=OSError('connection disk full')):
        with pytest.raises(OSError, match='connection disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert state.connection == before_connection
    assert state.layout == before_layout
    assert json.loads(state.store.path.read_text('utf-8')) == before_layout.to_dict()
    assert not state.connection_store.path.exists()


def test_connection_apply_reports_layout_rollback_failure(tmp_path):
    state = DesktopState(tmp_path)
    before_layout = state.layout
    real_save = state.store.save
    calls = 0

    def fail_rollback(layout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('rollback disk full')
        real_save(layout)

    with patch.object(state.store, 'save', side_effect=fail_rollback), \
         patch.object(state.connection_store, 'save', side_effect=OSError('connection disk full')):
        with pytest.raises(RuntimeError, match='connection disk full.*rollback disk full'):
            cmd(state, 'connection_apply',
                config={'auto_detect': False, 'serial': '127.0.0.1:16384', 'adb_path': 'new-adb'},
                events=False)
    assert calls == 2
    assert state.layout == before_layout  # in-memory values publish only after both writes
    assert json.loads(state.store.path.read_text('utf-8')) != before_layout.to_dict()


def test_layout_v1_migrates_into_same_v3_owner(tmp_path):
    raw = dict(version=1, panels=[dict(panel_id='old', category='damage', ranking='total', rate_basis='recent_5s')])
    (tmp_path / 'layout.json').write_text(json.dumps(raw), encoding='utf-8')
    state = DesktopState(tmp_path)
    assert state.layout.panels[0].panel_id == 'old'
    cmd(state, 'theme', theme='people')
    assert json.loads((tmp_path/'layout.json').read_text())['version'] == 3


def test_cleanup_failure_keeps_session_owned_and_prevents_successful_release(tmp_path):
    state = DesktopState(tmp_path)
    worker = CollectionWorker(state)
    class Session:
        cleanup_errors = {'observer': ['busy']}
        fail = True
        def close(self):
            if self.fail:
                raise OSError('busy')
    session = worker.session = Session()
    assert not worker.release()
    assert worker.session is session
    session.fail = False
    assert worker.release() and worker.session is None
    assert worker.retired_errors  # successful retry cannot erase prior failure


def test_worker_preserves_final_audit_failure_after_collection_close_retry(tmp_path):
    from collection_session import CollectionSession
    from types import SimpleNamespace

    calls, stopped = [], []
    fail_once = True

    def audit(kind, data):
        nonlocal fail_once
        if kind == 'stopped':
            stopped.append(data)
            if fail_once:
                fail_once = False
                raise OSError('final audit write failed')

    worker = CollectionWorker(DesktopState(tmp_path))
    session = CollectionSession('adb', 'serial', collector=SimpleNamespace(
        close=lambda: calls.append('collector_close')), audit=audit)
    worker.session = session
    assert not worker.release()
    assert worker.session is session
    assert worker.release()
    assert worker.session is None
    assert calls == ['collector_close']
    assert len(stopped) == 2
    assert any('audit' in error and 'final audit write failed' in error
               for error in worker.retired_errors)


def test_worker_startup_setup_error_is_reported(tmp_path, monkeypatch):
    import desktop_bridge

    blocked_root = tmp_path / 'not-a-directory'
    blocked_root.write_text('file blocks runtime directory', encoding='utf-8')
    monkeypatch.setattr(desktop_bridge, 'ROOT', blocked_root)
    worker = CollectionWorker(DesktopState(tmp_path/'state'))
    worker.run()
    kind, error = worker.events.get_nowait()
    assert kind == 'error' and error
    assert worker.session is None


def test_worker_paces_advancing_samples_and_preserves_retry_backoff(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge
    from event_capture import CaptureRetry

    clock = 0.0
    delays, starts = [], []
    reads = iter([
        (.08, {'state': 2, 'battle_id': 'live', 'frame': 10}),
        (.27, {'state': 2, 'battle_id': 'live', 'frame': 20}),
        (.08, {'state': 2, 'battle_id': 'live', 'frame': 20}),
        (.08, CaptureRetry('baseline crossed an event or game frame')),
        (.08, {'state': 3, 'battle_id': 'live', 'frame': 30}),
        (.08, None),
    ])
    diagnostics = SimpleNamespace(as_dict=dict, selected_serial='serial', version_actual='test')
    target = SimpleNamespace(adb_path='adb', serial='serial', diagnostics=diagnostics)
    worker = CollectionWorker(DesktopState(tmp_path/'state'))

    def wait(delay):
        nonlocal clock
        delays.append(delay)
        clock += delay
        if len(delays) == 6:
            worker.stop.set()

    class Session:
        cleanup_errors = {}
        collector = SimpleNamespace(diagnostics=diagnostics)

        def __init__(self, *_args, **_kwargs):
            pass

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            runtime.sessions.state = 'playing'

        def connect(self):
            pass

        def read(self):
            nonlocal clock
            starts.append(clock)
            duration, sample = next(reads)
            clock += duration
            if isinstance(sample, CaptureRetry):
                raise sample
            return sample

        def close(self):
            pass

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    monkeypatch.setattr(worker.stop, 'wait', wait)
    worker.request(worker.effective[0], False)
    worker.run()

    assert delays == pytest.approx([.12, .05, .2, .2, .2, .2])
    assert starts == pytest.approx([0, .20, .52, .80, 1.08, 1.36])
    events = list(worker.events.queue)
    assert not any(kind == 'error' for kind, _ in events)
    assert [sample['frame'] for kind, sample in events if kind == 'sample'] == [10, 20, 20, 30]


def test_worker_only_uses_fast_keepalive_for_successfully_bound_selection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge
    from collector import ProcessChanged
    from event_capture import CaptureRetry

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)
    clock = [0.0]
    checks = []
    monkeypatch.setattr(desktop_bridge, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    first = replace(state.connection, serial='serial-a', auto_detect=False)
    second = replace(state.connection, serial='serial-b', auto_detect=False)

    def resolve(config):
        diagnostics = SimpleNamespace(as_dict=lambda: {}, selected_serial=config.serial,
                                      version_actual='test')
        return SimpleNamespace(adb_path='adb', serial=config.serial, diagnostics=diagnostics)

    def bind(_adb, serial, *, remote):
        checks.append(('bind', serial))
        return checks.count(('bind', serial)) > 1 or serial == first.serial, 'binding result'

    def keep(_adb, serial, *, remote):
        checks.append(('keep', serial))
        return True, 'healthy endpoint'

    class Session:
        cleanup_errors = {}

        def __init__(self, _adb, serial, **_kwargs):
            self.serial = serial
            self.reads = 0
            self.collector = SimpleNamespace(diagnostics=resolve(SimpleNamespace(serial=serial)).diagnostics)
            self.observer = SimpleNamespace(status='ready', error=None)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime
            runtime.sessions.state = 'playing'

        def connect(self):
            pass

        def read(self):
            self.reads += 1
            if self.serial == first.serial and self.reads == 3:
                worker.request(second, True)
                return None
            clock[0] += 6
            if self.serial == first.serial:
                if self.reads == 2:
                    self.observer.status = 'failed'
                    self.observer.error = 'observer detached'
                    raise CaptureRetry('observer mark request failed: raw diagnostic detail')
                raise CaptureRetry('baseline crossed an event or game frame')
            if checks.count(('bind', second.serial)) > 2:
                worker.stop.set()
                return None
            if self.reads == 2:
                raise ProcessChanged('游戏进程出生标识已变化')

        def close(self):
            pass

    monkeypatch.setattr(desktop_bridge, 'resolve_configured', resolve)
    monkeypatch.setattr(desktop_bridge, 'ensure_frida_endpoint', bind)
    monkeypatch.setattr(desktop_bridge, 'keep_frida_endpoint', keep)
    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(first, True)
    worker.run()

    assert checks == [('bind', 'serial-a'), ('keep', 'serial-a'), ('bind', 'serial-a'),
                      ('bind', 'serial-b'), ('bind', 'serial-b'), ('bind', 'serial-b'),
                      ('keep', 'serial-b')]
    assert worker.requests.empty()
    observer_states = []
    while not worker.events.empty():
        kind, value = worker.events.get_nowait()
        if kind == 'observer':
            state.accept(kind, value)
            snapshot = state.snapshot()
            if snapshot['observer_status'] == 'failed':
                assert 'observer detached' in snapshot['event_text']
            observer_states.append((snapshot['status'], snapshot['observer_status']))
    assert observer_states[:3] == [('waiting', 'ready'), ('waiting', 'failed'), ('waiting', 'ready')]
    path, = (tmp_path/'runtime'/'captures').glob('wpf-*.jsonl')
    journal = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert [item['data']['duration_ms'] for item in journal if item['kind'] == 'frida_keepalive'] == [0, 0, 0, 0]
    retries = [item['data'] for item in journal if item['kind'] == 'capture_retry']
    assert [retry['reason'] for retry in retries] == ['baseline crossed an event or game frame',
                                                     'observer mark request failed']
    assert all(retry['duration_ms'] == 6000 for retry in retries)


def test_worker_retries_final_cleanup_before_closing_audit_stream(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)

    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None
            self.attempts = 0

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            worker.stop.set()

        def read(self):
            return None

        def close(self):
            self.attempts += 1
            self.audit('cleanup_attempt', {'attempt': self.attempts})
            if self.attempts == 1:
                raise OSError('transient cleanup failure')

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, False)
    worker.run()

    assert worker.session is None and worker.cleanup_error == ''
    audit_path = next((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    events = [json.loads(line) for line in audit_path.read_text('utf-8').splitlines()]
    assert events == [
        {'kind': 'cleanup_attempt', 'data': {'attempt': 1}},
        {'kind': 'cleanup_attempt', 'data': {'attempt': 2}},
    ]


def test_worker_retains_and_reports_session_after_final_cleanup_retries_fail(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)
    sessions = []

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None
            self.attempts = 0
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            worker.stop.set()

        def read(self):
            return None

        def close(self):
            self.attempts += 1
            self.audit('cleanup_attempt', {'attempt': self.attempts})
            raise OSError('persistent cleanup failure')

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, False)
    worker.thread.start()
    worker.thread.join()

    assert len(sessions) == 1 and sessions[0].attempts == 2
    assert worker.session is sessions[0]
    assert 'persistent cleanup failure' in worker.cleanup_error
    with pytest.raises(RuntimeError, match='persistent cleanup failure'):
        worker.close()
    audit_path = next((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    assert len(audit_path.read_text('utf-8').splitlines()) == 2


def test_worker_releases_session_while_audit_stream_is_open(tmp_path, monkeypatch):
    from datetime import datetime as RealDateTime
    from types import SimpleNamespace
    import desktop_bridge

    class FixedDateTime:
        @staticmethod
        def now(tz):
            return RealDateTime(2026, 11, 10, 9, 30, 0, tzinfo=tz)

    monkeypatch.setattr(desktop_bridge, 'datetime', FixedDateTime)
    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(
        as_dict=lambda: {'status': 'ready'},
        selected_serial='127.0.0.1:16384',
        version_actual='test',
    )
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)

    state = DesktopState(tmp_path/'state')
    workers = []

    class Session:
        cleanup_errors = {}

        def __init__(self, *_args, audit, **_kwargs):
            self.audit = audit
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            workers[-1].stop.set()

        def read(self):
            return None

        def close(self):
            self.audit('session_closed', {})

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    for _ in range(2):
        worker = CollectionWorker(state)
        workers.append(worker)
        worker.request(state.connection, False)
        worker.run()
        assert worker.cleanup_error == ''
        assert worker.session is None

    paths = list((tmp_path/'runtime'/'captures').glob('wpf-*.jsonl'))
    assert len(paths) == 2
    assert len({path.name for path in paths}) == 2
    for audit_path in paths:
        assert json.loads(audit_path.read_text('utf-8').splitlines()[-1]) == {
            'kind': 'session_closed', 'data': {},
        }


def test_quit_ack_is_after_collector_cleanup(tmp_path):
    order = []
    class Worker:
        import queue
        events = queue.Queue()
        def close(self):
            order.append('close')
    class Output(io.StringIO):
        def write(self, data):
            if '"bye"' in data:
                assert order == ['close']
            return super().write(data)
    serve(io.StringIO('{"version":1,"op":"hello"}\n{"version":1,"op":"quit"}\n'), Output(), DesktopState(tmp_path), Worker())
    assert order == ['close']


def test_default_app_entry_routes_to_wpf_not_tk(monkeypatch):
    import app
    import desktop_launcher
    calls = []
    monkeypatch.setattr(sys, 'argv', ['app.py', '--no-events'])
    monkeypatch.setattr(desktop_launcher, 'launch', lambda args: calls.append(args))
    app.main()
    assert len(calls) == 1 and calls[0].frida_remote == ''


def test_app_entry_never_imports_tk():
    # The sealed Tk panel must not come back through the product entry point.
    result = subprocess.run([sys.executable, '-c',
        "import app, sys; assert 'tkinter' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_real_metadata_key_and_invalid_manual_connection(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', dict(sample(), battle_metadata={'stage_id': 'OF-8'}))
    assert state.snapshot()['stage'] == 'OF-8'
    before = state.connection
    with pytest.raises(ValueError, match='实例地址'):
        cmd(state, 'connection_apply', config={'auto_detect': False, 'serial': ''}, events=True)
    assert state.connection == before


def test_rescan_retains_effective_cli_target_until_explicit_settings_apply(tmp_path):
    from connection import ConnectionConfig
    import queue
    state = DesktopState(tmp_path)
    override = ConnectionConfig('override-adb', 'selected-serial', False)
    class Worker:
        events = queue.Queue()
        effective = (override, False)
        requests = []
        def request(self, config, enabled):
            self.requests.append((config, enabled))
        def close(self):
            pass
    worker = Worker()
    commands = [dict(version=1, op='hello'), dict(version=1, op='rescan'), dict(version=1, op='quit')]
    serve(io.StringIO(''.join(json.dumps(c)+chr(10) for c in commands)), io.StringIO(), state, worker)
    assert worker.requests == [(override, False)]
    assert state.connection != override


@pytest.mark.parametrize('flag,args,needle', [
    ('--legacy-tk', [], '--legacy-tk'),
    ('--self-test', ['out'], 'verify_wpf'),
    ('--acceptance-output', ['out', '--acceptance-seconds', '1'], 'verify_wpf'),
    ('--acceptance-seconds', ['1'], 'verify_wpf'),
])
def test_removed_tk_flags_are_rejected_before_any_startup(tmp_path, monkeypatch, capsys,
                                                          flag, args, needle):
    import app
    import desktop_launcher
    monkeypatch.setattr(sys, 'argv', ['app.py', flag, *args])
    monkeypatch.setattr(desktop_launcher, 'launch', lambda _: pytest.fail('removed flag launched WPF'))
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert '已封存' in error and needle in error
    assert not (tmp_path / 'out').exists()


def test_history_event_refreshes_records_before_the_next_snapshot(tmp_path):
    class Worker:
        def __init__(self):
            self.events = queue.Queue()
        def close(self):
            pass
    state = DesktopState(tmp_path)
    first = record(1)
    state.history.save(first)
    cmd(state, 'history_refresh')
    selected = state.selected
    new = record(2)
    state.history.save(new)
    assert all(x['battle_id'] != new.battle_id for x in state.snapshot()['history'])
    worker = Worker()
    # This is exactly what CollectionSession's on_history callback enqueues.
    worker.events.put(('history', None))
    output = io.StringIO()
    serve(io.StringIO('{"version":1,"op":"hello"}\n{"version":1,"op":"quit"}\n'),
          output, state, worker)
    reply = json.loads(output.getvalue().splitlines()[0])
    assert [x['battle_id'] for x in reply['state']['history']] == [new.battle_id, first.battle_id]
    assert reply['state']['history_selected']['battle_id'] == first.battle_id
    assert state.selected is selected


def test_worker_wires_archive_notification_into_the_event_queue(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import desktop_bridge

    monkeypatch.setattr(desktop_bridge, 'ROOT', tmp_path)
    diagnostics = SimpleNamespace(as_dict=lambda: {'status': 'ready'},
                                  selected_serial='127.0.0.1:16384', version_actual='test')
    target = SimpleNamespace(adb_path='adb', serial='127.0.0.1:16384', diagnostics=diagnostics)
    monkeypatch.setattr(desktop_bridge, 'resolve_configured', lambda _config: target)
    state = DesktopState(tmp_path/'state')
    worker = CollectionWorker(state)
    sessions = []

    class Session:
        cleanup_errors = {}
        def __init__(self, *_args, audit=None, on_history=None, **_kwargs):
            self.audit = audit
            self.on_history = on_history
            self.collector = SimpleNamespace(diagnostics=diagnostics)
            self.runtime = None
            sessions.append(self)
        def enable_lifecycle(self, runtime):
            self.runtime = runtime
        def connect(self):
            worker.stop.set()
        def read(self):
            return None
        def close(self):
            pass

    monkeypatch.setattr(desktop_bridge, 'CollectionSession', Session)
    worker.request(state.connection, False)
    worker.thread.start()
    worker.thread.join()
    assert len(sessions) == 1
    sessions[0].on_history()
    events = []
    while True:
        try:
            events.append(worker.events.get_nowait())
        except queue.Empty:
            break
    assert ('history', None) in events


def event_sample(status, error, statistics):
    data = sample()
    data.update(battle_id='battle-a', session='a', frame=670,
                event_status=status, event_error=error,
                statistics=statistics)
    return data


def test_engagement_without_events_explains_itself_and_recovers_in_same_panel(tmp_path):
    state = DesktopState(tmp_path)
    state.accept('sample', sample())
    config = dict(state.layout.panels[0].to_dict(), rate_basis='engagement')
    cmd(state, 'panel_update', config=config)
    panel_id = state.layout.panels[0].panel_id
    view = state.snapshot()['panels'][0]
    assert view['config']['panel_id'] == panel_id
    assert view['rate_available'] is False
    assert '事件观察未连接' in view['rate_reason']
    assert view['rate_action'] == 'retry' and view['rate_action_label']
    assert all(entry['rate'] is None for entry in view['entries'])
    assert all(entry['rate_text'] == '—' for entry in view['entries'])

    # The same running panel must recover automatically once events arrive.
    stats = Statistics('battle-a', 0, event_coverage=True)
    stats.register_source('a', 'a')
    stats.register_source('b', 'b')
    stats.snapshot(0, {key: {'damage': 100, 'healing': 0} for key in ('a', 'b')})
    stats.advance(1)
    stats.event('damage-1', 1, 'a', 'damage', 100)
    stats.advance(2)
    state.accept('sample', event_sample('recording', None, stats.result()))
    view = state.snapshot()['panels'][0]
    assert view['config']['panel_id'] == panel_id
    assert view['rate_available'] is True and view['rate_reason'] == ''
    by_owner = {entry['owner_id']: entry for entry in view['entries']}
    assert by_owner['a']['rate'] == pytest.approx(100)
    assert by_owner['a']['rate_text'] == '100/s'
    assert by_owner['b']['rate'] is None
    assert by_owner['b']['rate_state'] == 'no_engagement'
    assert by_owner['b']['rate_text'] == '无接敌'


def test_history_view_explains_missing_event_coverage_without_backfill(tmp_path):
    state = DesktopState(tmp_path)
    archived = record().to_dict()
    archived['event_status'] = 'failed'
    archived['event_error'] = 'unable to connect to remote frida-server'
    BattleHistory(tmp_path / 'history').save(BattleRecord.from_dict(archived))
    state.refresh_history()
    state.selected = state.records[archived['battle_id']]
    state.history_config = PanelConfig.from_dict(
        dict(panel_id='history', category='damage', ranking='total',
             rate_basis='engagement'))
    selected = state.snapshot()['history_selected']
    view = selected['view']
    assert selected['event_status'] == 'failed'
    assert selected['event_error'] == archived['event_error']
    assert view['rate_available'] is False
    assert '该局事件观察未连接' in view['rate_reason']
    assert '无法补采' in view['rate_hint']
    assert view['rate_action'] == 'settings'
    assert all(entry['rate'] is None for entry in view['entries'])
    assert all(entry['rate_text'] == '—' for entry in view['entries'])
    assert all(entry['rate_state'] != 'no_engagement' for entry in view['entries'])


def test_finished_history_record_publishes_team_shares(tmp_path):
    from battle_history import BattleHistory
    BattleHistory(tmp_path / 'history').save(record())
    state = DesktopState(tmp_path)
    selected = state.snapshot()['history_selected']
    assert selected['view']['share_known'] is True
    assert selected['view']['entries'][0]['share'] == 1.
    assert 'share_note' in selected['view']


def test_live_panel_surfaces_a_classified_non_team_source(tmp_path):
    state = DesktopState(tmp_path)
    data = sample()
    data['statistics'].update(
        totals_complete=True,
        non_team_sources=['trap_321_shnbox'],
        non_team_output={'trap_321_shnbox': {'damage': 500., 'healing': 0.}})
    state.accept('sample', data)
    view = state.snapshot()['panels'][0]
    assert view['share_known'] is True
    assert 'trap_321_shnbox' in view['share_note']
    assert '未归属输出' in view['share_note']
