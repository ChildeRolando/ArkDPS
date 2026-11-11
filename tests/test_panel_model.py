import pytest

from panel_model import (Category, DAMAGE_COLORS, EventAvailability, PanelCollection,
                         PanelConfig, PanelSourceRow, Ranking, RateBasis, detail_rates,
                         format_compact, non_team_note, render_board, render_panel,
                         rows_from_statistics, team_status)
from stats_core import Statistics


def src(owner, *, damage=None, healing=None, shield=None, recent_damage=None,
        recent_healing=None, recent_shield=None, engagement_damage=None,
        engagement_healing=None, active_damage=None, active_healing=None,
        observed_damage=None, observed_healing=None,
        present=True, types=None, counter_complete=True, event_complete=True,
        sources=(), recent_window=(0, 5), recent_types=None):
    return PanelSourceRow(
        owner_id=owner, name=owner, present=present, damage=damage,
        healing=healing, shield=shield, recent_damage=recent_damage,
        recent_healing=recent_healing, recent_shield=recent_shield,
        engagement_damage=engagement_damage,
        engagement_healing=engagement_healing,
        active_damage=active_damage, active_healing=active_healing,
        observed_damage=observed_damage, observed_healing=observed_healing,
        damage_by_type=types or {}, counter_complete=counter_complete,
        event_complete=event_complete, sources=sources,
        recent_damage_window=recent_window, recent_healing_window=recent_window,
        recent_shield_window=recent_window,
        recent_damage_by_type=recent_types or {})


@pytest.mark.parametrize('category', [Category.DAMAGE, Category.HEALING])
@pytest.mark.parametrize('gap', [False, True])
def test_statistics_recent_share_requires_full_common_window(category, gap):
    stats = Statistics('window', 0)
    stats.register_source('a', 'A')
    stats.snapshot(0, {'a': {'damage': 0, 'healing': 0}})
    stats.register_source('b', 'B')
    stats.snapshot(4, {'a': {'damage': 500, 'healing': 500},
                       'b': {'damage': 0, 'healing': 0}})
    if gap:
        stats.invalidate_snapshots()
        stats.snapshot(4.5, {'a': {'damage': 500, 'healing': 500},
                             'b': {'damage': 0, 'healing': 0}})
    stats.snapshot(5, {'a': {'damage': 500, 'healing': 500},
                       'b': {'damage': 200, 'healing': 200}})
    view = render_panel(recent(category), rows_from_statistics(stats.result()))
    assert not view.share_known
    assert view.share_reason
    assert all(entry.share is None for entry in view.entries)


def test_recent_damage_segments_use_recent_types_only():
    row = src('A', damage=1000, recent_damage=20,
              types={'physical': 900, 'arts': 100})
    entry = render_panel(recent(), [row]).entries[0]
    assert entry.segments == ()
    assert not entry.segments_complete
    row.recent_damage_by_type = {'arts': 20}
    entry = render_panel(recent(), [row]).entries[0]
    assert entry.segments_complete
    assert [(s['kind'], s['ratio']) for s in entry.segments] == [('arts', 1.0)]
    assert render_panel(total(), [row]).entries[0].segments[0]['kind'] == 'physical'


def test_statistics_recent_share_recovers_when_full_window_observed():
    stats = Statistics('window', 0)
    stats.register_source('a', 'A')
    stats.register_source('b', 'B')
    stats.snapshot(0, {'a': {'damage': 0, 'healing': 0},
                       'b': {'damage': 0, 'healing': 0}})
    stats.invalidate_snapshots()
    stats.snapshot(1, {'a': {'damage': 0, 'healing': 0},
                       'b': {'damage': 0, 'healing': 0}})
    stats.snapshot(6, {'a': {'damage': 100, 'healing': 50},
                       'b': {'damage': 300, 'healing': 150}})
    for category in (Category.DAMAGE, Category.HEALING):
        view = render_panel(recent(category), rows_from_statistics(stats.result()))
        assert view.share_known
        assert view.entries[0].share == pytest.approx(0.75)


def test_recent_share_requires_matching_certified_windows_including_zero_rows():
    rows = [src('A', recent_damage=10), src('B', recent_damage=0, recent_window=(1, 5))]
    assert not render_panel(recent(), rows).share_known
    rows[1].recent_damage_window = None
    assert not render_panel(recent(), rows).share_known
    rows[1].recent_damage_window = (0, 5)
    assert render_panel(recent(), rows).share_known


def test_adapter_recent_types_and_absent_window_are_explicit():
    rows = rows_from_statistics({'rows': {'A': {
        'damage': 1000, 'recent_damage': 20,
        'damage_by_type': {'physical': 1000},
        'recent_damage_by_type': {'arts': 20},
    }}})
    view = render_panel(recent(), rows)
    assert not view.share_known
    assert view.entries[0].segments_complete
    assert view.entries[0].segments[0]['kind'] == 'arts'


def total(category=Category.DAMAGE):
    return PanelConfig(category=category, ranking=Ranking.TOTAL)


def recent(category=Category.DAMAGE):
    return PanelConfig(category=category, ranking=Ranking.RECENT)


def test_totals_and_recent_panels_coexist_with_separate_rankings():
    rows = [src('A', damage=1000, recent_damage=100),
            src('B', damage=500, recent_damage=0),
            src('C', damage=500, recent_damage=50)]
    totals, recents = render_board([total(), recent()], rows)
    assert [e.owner_id for e in totals.entries] == ['A', 'B', 'C']
    assert [e.owner_id for e in recents.entries] == ['A', 'C']
    assert totals.entries[1].share == pytest.approx(0.25)
    assert recents.entries[0].share == pytest.approx(100 / 150)


def test_rate_basis_does_not_change_ranking_bars_or_shares():
    rows = [src('A', damage=1000, recent_damage=200, engagement_damage=10),
            src('B', damage=500, recent_damage=300, engagement_damage=50)]
    by_recent = render_panel(
        PanelConfig(ranking=Ranking.RECENT, rate_basis=RateBasis.RECENT_5S), rows)
    by_engagement = render_panel(
        PanelConfig(ranking=Ranking.RECENT, rate_basis=RateBasis.ENGAGEMENT), rows)
    assert [e.owner_id for e in by_recent.entries] == ['B', 'A']
    assert [e.owner_id for e in by_engagement.entries] == ['B', 'A']
    for a, b in zip(by_recent.entries, by_engagement.entries):
        assert a.value == b.value
        assert a.bar_ratio == b.bar_ratio
        assert a.share == b.share
    assert by_recent.entries[0].rate == 300
    assert by_engagement.entries[0].rate == 50


def test_bar_length_is_linear_with_max_ratio_one():
    rows = [src('A', damage=1000), src('B', damage=250), src('C', damage=0)]
    view = render_panel(total(), rows)
    assert view.entries[0].bar_ratio == 1.0
    assert view.entries[1].bar_ratio == pytest.approx(0.25)
    assert [e.owner_id for e in view.entries] == ['A', 'B']


def test_sorting_uses_unrounded_values():
    rows = [src('B', damage=1499.4), src('A', damage=1499.6)]
    view = render_panel(total(), rows)
    assert [e.owner_id for e in view.entries] == ['A', 'B']
    assert view.entries[0].display_value == view.entries[1].display_value == '1.5K'


def test_damage_colors_and_segments():
    rows = [src('A', damage=100,
                types={'physical': 25, 'arts': 25, 'elemental': 25,
                       'true': 25})]
    entry = render_panel(total(), rows).entries[0]
    assert [s['kind'] for s in entry.segments] == ['physical', 'arts',
                                                   'elemental', 'true']
    assert [s['color'] for s in entry.segments] == [
        DAMAGE_COLORS['physical'], DAMAGE_COLORS['arts'],
        DAMAGE_COLORS['elemental'], DAMAGE_COLORS['true']]
    assert all(s['ratio'] == pytest.approx(0.25) for s in entry.segments)


def test_retired_rows_are_kept_in_totals_and_portraits_are_muted():
    rows = [src('A', damage=1000, present=False),
            src('B', damage=500, present=True),
            src('C', damage=100, present=None)]
    view = render_panel(total(), rows)
    entries = {e.owner_id: e for e in view.entries}
    assert entries['A'].portrait_muted is True
    assert entries['B'].portrait_muted is False
    assert entries['C'].portrait_muted is False


def test_recent_zero_is_removed_and_reappears_with_output():
    rows = [src('A', damage=1000, recent_damage=0)]
    assert render_panel(recent(), rows).entries == []
    rows = [src('A', damage=1010, recent_damage=10)]
    assert [e.owner_id for e in render_panel(recent(), rows).entries] == ['A']


def test_unknown_metric_is_hidden_and_share_is_not_local():
    rows = [src('A', damage=100), src('B', damage=None)]
    view = render_panel(total(), rows)
    assert [e.owner_id for e in view.entries] == ['A']
    assert view.share_known is False
    assert view.entries[0].share is None
    assert view.share_reason
    # The input row is not deleted or zero-filled.
    assert rows[1].damage is None


def test_caller_reported_incomplete_team_disables_shares():
    rows = [src('A', damage=100), src('B', damage=100)]
    view = render_panel(total(), rows, team_complete=False)
    assert view.share_known is False
    assert all(e.share is None for e in view.entries)


def test_incomplete_counter_is_flagged_and_blocks_shares():
    rows = [src('A', damage=100), src('B', damage=100, counter_complete=False)]
    view = render_panel(total(), rows)
    assert view.entries[1].incomplete is True
    assert view.share_known is False


def test_healing_and_shield_categories_switch_metric():
    rows = [src('A', damage=1000, healing=20, shield=3, recent_healing=2,
                recent_shield=1)]
    heal = render_panel(total(Category.HEALING), rows).entries[0]
    shield = render_panel(total(Category.SHIELD), rows).entries[0]
    assert heal.value == 20 and heal.display_value == '20'
    assert shield.value == 3 and shield.display_value == '3'
    assert render_panel(total(Category.SHIELD), rows).rate_supported is False
    assert shield.rate is None


def test_shield_recent_can_be_rendered_when_supplied():
    rows = [src('A', shield=5, recent_shield=2)]
    view = render_panel(recent(Category.SHIELD), rows)
    assert view.entries[0].value == 2
    assert view.rate_supported is False


def test_full_cycle_dps_uses_presence_time_and_stays_unknown_without_coverage():
    row = PanelSourceRow(owner_id='A', name='A', damage=1000,
                         presence_seconds=10, presence_complete=True)
    view = render_panel(PanelConfig(rate_basis=RateBasis.FULL_CYCLE), [row])
    entry = view.entries[0]
    assert entry.rate == pytest.approx(100)
    assert entry.rate_text == '100/s'
    unknown = PanelSourceRow(owner_id='B', name='B', damage=1000,
                             presence_seconds=None, presence_complete=False)
    view = render_panel(PanelConfig(rate_basis=RateBasis.FULL_CYCLE), [unknown])
    assert view.entries[0].rate is None
    assert view.entries[0].rate_text == '—'


def test_dps_ranking_follows_selected_algorithm():
    rows = [
        PanelSourceRow(owner_id='A', name='A', damage=1000, recent_damage=200,
                       engagement_damage=10, presence_seconds=10, presence_complete=True),
        PanelSourceRow(owner_id='B', name='B', damage=500, recent_damage=300,
                       engagement_damage=50, presence_seconds=10, presence_complete=True),
    ]
    recent = render_panel(PanelConfig(ranking=Ranking.DPS,
                                      rate_basis=RateBasis.RECENT_5S), rows)
    assert [e.owner_id for e in recent.entries] == ['B', 'A']
    assert [e.value for e in recent.entries] == [300, 200]
    engagement = render_panel(PanelConfig(ranking=Ranking.DPS,
                                           rate_basis=RateBasis.ENGAGEMENT), rows)
    assert [e.owner_id for e in engagement.entries] == ['B', 'A']
    assert [e.value for e in engagement.entries] == [50, 10]
    full = render_panel(PanelConfig(ranking=Ranking.DPS,
                                    rate_basis=RateBasis.FULL_CYCLE), rows)
    assert [e.owner_id for e in full.entries] == ['A', 'B']
    assert [e.value for e in full.entries] == [100, 50]


def test_full_cycle_denominator_cannot_be_shorter_than_active_engagement():
    # Mirrors 冬时 in a real archive: roster presence started ~3s after the
    # first event, so the raw sampled presence was shorter than active time.
    row = PanelSourceRow(owner_id='A', name='A', damage=12630,
                         effective_damage=12282.5, overkill=347.5,
                         overkill_complete=True, presence_seconds=24.6333,
                         presence_complete=True, active_damage=27.1333)
    view = render_panel(PanelConfig(rate_basis=RateBasis.FULL_CYCLE,
                                    ranking=Ranking.TOTAL), [row])
    assert view.entries[0].rate == pytest.approx(12630 / 27.1333, rel=1e-6)
    effective = render_panel(PanelConfig(rate_basis=RateBasis.FULL_CYCLE,
                                         ranking=Ranking.TOTAL, effective_only=True), [row])
    assert effective.entries[0].rate == pytest.approx(12282.5 / 27.1333, rel=1e-6)


def test_effective_only_changes_dps_numerator():
    row = PanelSourceRow(owner_id='A', name='A', damage=1000,
                         effective_damage=800, overkill=200, overkill_complete=True,
                         presence_seconds=10, presence_complete=True,
                         recent_damage=200, recent_overkill=40,
                         recent_damage_window=(0, 5),
                         observed_damage=500, active_damage=10)
    total = render_panel(PanelConfig(ranking=Ranking.DPS, rate_basis=RateBasis.FULL_CYCLE,
                                     effective_only=False), [row])
    effective = render_panel(PanelConfig(ranking=Ranking.DPS, rate_basis=RateBasis.FULL_CYCLE,
                                         effective_only=True), [row])
    assert total.entries[0].rate == pytest.approx(100)
    assert effective.entries[0].rate == pytest.approx(80)
    recent = render_panel(PanelConfig(ranking=Ranking.DPS, rate_basis=RateBasis.RECENT_5S,
                                      effective_only=True), [row])
    assert recent.entries[0].rate == pytest.approx(192)
    engagement = render_panel(PanelConfig(ranking=Ranking.DPS,
                                          rate_basis=RateBasis.ENGAGEMENT,
                                          effective_only=True), [row])
    assert engagement.entries[0].rate == pytest.approx(30)


def test_shield_rejects_engagement_rate():
    with pytest.raises(ValueError):
        PanelConfig(category=Category.SHIELD, rate_basis=RateBasis.ENGAGEMENT)


def test_format_compact_units():
    assert format_compact(None) == '—'
    assert format_compact(999) == '999'
    assert format_compact(1000) == '1K'
    assert format_compact(1500) == '1.5K'
    assert format_compact(1_000_000) == '1M'
    assert format_compact(1_234_567) == '1.2M'


def test_config_roundtrip_and_collection_validation():
    config = PanelConfig(panel_id='p1', category=Category.HEALING,
                         ranking=Ranking.RECENT, rate_basis=RateBasis.ENGAGEMENT,
                         title='治疗')
    assert PanelConfig.from_dict(config.to_dict()) == config
    collection = PanelCollection((config,))
    assert PanelCollection.from_dict(collection.to_dict()) == collection
    with pytest.raises(ValueError):
        PanelCollection((config, config))
    with pytest.raises(ValueError):
        PanelCollection.from_dict({'version': 999, 'panels': []})
    with pytest.raises(ValueError):
        PanelConfig.from_dict({'category': 'nope'})


def test_rows_from_statistics_maps_adapter_fields():
    result = {'rows': {'A': {
        'name': '灰烬', 'damage': 10, 'healing': 2, 'shield_absorbed': 3,
        'recent_damage': 4, 'recent_healing': 1,
        'engagement_damage': 5, 'engagement_healing': None,
        'active_damage': 2.5, 'active_healing': 1.5,
        'damage_by_type': {'physical': 10}, 'present': True,
        'counter_snapshot_complete': True, 'event_coverage_complete': False,
        'overkill_damage': 6, 'overkill_coverage_complete': True,
        'sources': ['A', 'token'],
    }}}
    row = rows_from_statistics(result)[0]
    assert (row.owner_id, row.name, row.damage, row.healing, row.shield) == (
        'A', '灰烬', 10, 2, 3)
    assert row.recent_shield is None
    assert row.event_complete is False
    assert row.overkill == 6 and row.overkill_complete is True
    assert row.sources == ('A', 'token')
    detail = render_panel(total(), [row]).entries[0].detail
    assert detail['overkill_damage'] == 6
    assert detail['overkill_coverage_complete'] is True
    # Overkill never changes the ranking basis or the damage denominator.
    assert detail['damage'] == 10


def test_share_denominator_includes_retired_and_zero_rows():
    rows = [src('A', damage=100), src('B', damage=300, present=False),
            src('C', damage=0)]
    view = render_panel(total(), rows)
    assert view.denominator == 400
    assert view.entries[0].share == pytest.approx(0.75)


def engagement_config():
    return PanelConfig(ranking=Ranking.TOTAL, rate_basis=RateBasis.ENGAGEMENT)


def test_event_unavailable_explains_engagement_and_never_zero_fills():
    rows = [src('A', damage=1000, engagement_damage=None)]
    view = render_panel(engagement_config(), rows,
                        event=EventAvailability('failed',
                                                'unable to connect to remote frida-server'))
    assert view.rate_available is False
    assert '事件观测未连接' in view.rate_reason
    assert 'unable to connect to remote frida-server' in view.rate_reason
    assert (view.rate_action, view.rate_action_label) == ('retry', '重试事件观测')
    assert '事件观测' in view.rate_hint
    entry = view.entries[0]
    assert entry.rate is None
    assert entry.rate_text == '—'
    assert entry.rate_state == 'unavailable'
    assert entry.rate_note and '不以 0 或墙钟替代' in entry.rate_note
    # 合理推断的 0 值冒充未知是被禁止的。
    assert '0' not in entry.rate_text


def test_event_recovered_engagement_uses_tail_window_and_active_time():
    stats = Statistics('battle', 0, event_coverage=True)
    stats.register_source('a', 'A')
    stats.snapshot(0, {'a': {'damage': 100, 'healing': 0}})
    for index, game_time in enumerate((0, 1, 4)):
        stats.advance(game_time)
        stats.event(str(index), game_time, 'a', 'damage', 100)
    stats.advance(4.5)
    result = stats.result()
    view = render_panel(engagement_config(), rows_from_statistics(result),
                        event=EventAvailability('recording'))
    entry = view.entries[0]
    assert view.rate_available is True and view.rate_reason == ''
    assert entry.rate == pytest.approx(result['rows']['A']['engagement_damage'])
    assert entry.rate == pytest.approx(100)   # 300 observed / 3 active game seconds
    assert entry.rate_text == '100/s'
    assert entry.detail['active_damage'] == pytest.approx(3)
    assert entry.detail['observed_damage'] == pytest.approx(300)


def test_zero_event_operator_is_reliable_zero_not_unknown():
    rows = [src('A', damage=10, engagement_damage=None, active_damage=0,
                observed_damage=0, observed_healing=0),
            src('B', damage=10, engagement_damage=None, active_damage=None,
                observed_damage=None)]
    view = render_panel(engagement_config(), rows, event=EventAvailability('recording'))
    by_owner = {entry.owner_id: entry for entry in view.entries}
    assert by_owner['A'].rate is None
    assert by_owner['A'].rate_state == 'no_engagement'
    assert by_owner['A'].rate_text == '无接敌'
    assert '可靠零' in by_owner['A'].rate_note
    assert by_owner['B'].rate_state == 'unknown'
    assert by_owner['B'].rate_text == '—'
    assert by_owner['A'].rate_text != by_owner['B'].rate_text


def test_event_coverage_closed_mid_battle_keeps_prefix_and_marks_gap():
    rows = [src('A', damage=100, engagement_damage=50, active_damage=2,
                observed_damage=100, event_complete=False)]
    view = render_panel(engagement_config(), rows,
                        event=EventAvailability('gap', 'observer stopped'))
    entry = view.entries[0]
    assert entry.rate == pytest.approx(50) and entry.rate_text == '50/s'
    assert view.rate_available is False
    assert '中断' in view.rate_reason and 'observer stopped' in view.rate_reason
    assert (view.rate_action, view.rate_action_label) == ('retry', '重试事件观测')
    assert '不补零' in view.rate_hint


def test_history_event_incomplete_reason_is_explicit_and_not_backfilled():
    rows = [src('A', damage=100, engagement_damage=None, active_damage=None,
                observed_damage=None, event_complete=False)]
    view = render_panel(engagement_config(), rows,
                        event=EventAvailability('failed', 'observer unavailable', live=False))
    assert view.rate_available is False
    assert '该局事件观测未连接' in view.rate_reason
    assert '无法补采' in view.rate_hint
    assert (view.rate_action, view.rate_action_label) == ('settings', '打开连接设置')
    entry = view.entries[0]
    assert entry.rate is None and entry.rate_text == '—'
    assert entry.rate_state == 'unavailable'


def test_detail_rates_distinguish_zero_unknown_and_unavailable():
    rows = [src('A', damage=10, engagement_damage=None, engagement_healing=None,
                active_damage=0, active_healing=0, observed_damage=0,
                observed_healing=0)]
    available = detail_rates(rows, EventAvailability('recording'))['A']
    assert available['damage']['state'] == 'no_engagement'
    assert available['healing']['state'] == 'no_engagement'
    unknown = detail_rates(rows, EventAvailability('recording'))
    unavailable = detail_rates(rows, EventAvailability('failed', 'observer unavailable'))['A']
    assert unavailable['damage']['state'] == 'unavailable'
    assert unavailable['damage']['text'] == '—'
    assert unknown['A']['damage']['text'] == '无接敌'


def test_overkill_segments_are_typed_and_only_on_the_totals_basis():
    row = src('A', damage=1000, recent_damage=100)
    row.damage_by_type = {'physical': 600, 'arts': 400}
    row.overkill = 156
    row.overkill_by_type = {'physical': 100, 'arts': 56, 'elemental': 0, 'true': 0}
    row.overkill_type_complete = True
    view = render_panel(PanelConfig(ranking=Ranking.TOTAL), [row])
    segments = {segment['kind']: segment for segment in view.entries[0].segments}
    assert segments['physical']['overkill_ratio'] == pytest.approx(100 / 600)
    assert segments['arts']['overkill_ratio'] == pytest.approx(56 / 400)
    recent = render_panel(PanelConfig(ranking=Ranking.RECENT), [row])
    assert all('overkill_ratio' not in segment for segment in recent.entries[0].segments)
    row.overkill_type_complete = False
    incomplete = render_panel(PanelConfig(ranking=Ranking.TOTAL), [row])
    assert all('overkill_ratio' not in segment for segment in incomplete.entries[0].segments)
    assert incomplete.entries[0].detail['overkill_damage'] == 156


def test_team_status_names_blocking_and_classified_sources():
    stats = {'totals_complete': True,
             'rows': {'A': {'name': 'A', 'counter_snapshot_complete': True}},
             'unknown_sources': ['char_mystery'],
             'non_team_sources': ['trap_321_shnbox'],
             'non_team_output': {'trap_321_shnbox': {'damage': 500., 'healing': 0.}}}
    complete, reason, note = team_status(stats)
    assert complete is False
    assert '未归属统计来源：char_mystery' in reason
    assert 'trap_321_shnbox' in note and '未归属输出' in note and '500' in note
    assert '已排除非队伍来源' in note


def test_team_status_is_complete_without_unknown_sources():
    stats = {'totals_complete': True,
             'rows': {'A': {'name': 'A', 'counter_snapshot_complete': True}},
             'non_team_sources': ['trap_321_shnbox'],
             'non_team_output': {'trap_321_shnbox': {'damage': 0., 'healing': 0.}}}
    complete, reason, note = team_status(stats)
    assert complete is True and reason == ''
    assert '未观察到输出' in note


def test_team_status_does_not_claim_an_incomplete_snapshot_for_a_diagnostic_only_gap():
    stats = {'totals_complete': False,
             'rows': {'A': {'name': 'A', 'counter_snapshot_complete': True}},
             'unknown_sources': ['char_mystery']}
    complete, reason, _ = team_status(stats)
    assert complete is False
    assert '未归属统计来源：char_mystery' in reason
    assert '累计快照不完整' not in reason


def test_non_team_note_renders_the_classification_label(monkeypatch):
    import source_classification as rules
    monkeypatch.setattr(rules, 'NON_TEAM_CLASSES',
                        rules.NON_TEAM_CLASSES + (('npc_', 'npc', '剧情 NPC'),))
    stats = {'non_team_sources': ['trap_321_shnbox', 'npc_77'],
             'non_team_output': {'trap_321_shnbox': {'damage': 0., 'healing': 0.},
                                 'npc_77': {'damage': None, 'healing': None}}}
    note = non_team_note(stats)
    assert 'trap_321_shnbox（非队伍地图装置/陷阱，未观察到输出）' in note
    assert 'npc_77（剧情 NPC，输出未知）' in note


def test_render_panel_keeps_non_team_note_when_shares_are_known():
    rows = [src('A', damage=100), src('B', damage=300)]
    view = render_panel(total(), rows, team_note='已排除非队伍来源：trap_x')
    assert view.share_known is True
    assert view.share_note == '已排除非队伍来源：trap_x'


def test_event_unknown_reason_is_specific_for_failed_and_partial_coverage():
    row = src('A', damage=100)
    row.event_complete = False
    failed = EventAvailability(status='failed',
                               error='unable to connect to remote frida-server', live=True)
    view = render_panel(PanelConfig(), [row], event=failed)
    reason = view.entries[0].detail['event_reason']
    assert '事件观测未连接' in reason
    assert 'unable to connect to remote frida-server' in reason

    row.event_coverage_end = 12.34
    partial = render_panel(PanelConfig(), [row], event=EventAvailability(status='recording'))
    assert partial.entries[0].detail['event_reason'] == '事件仅覆盖到 12.3 游戏秒，之后未知'


def test_effective_only_panel_uses_effective_damage_and_type_segments():
    row = src('A', damage=1000, recent_damage=100)
    row.effective_damage = 800
    row.overkill = 200
    row.damage_by_type = {'physical': 600, 'arts': 400}
    row.overkill_by_type = {'physical': 100, 'arts': 100, 'elemental': 0, 'true': 0}
    row.overkill_type_complete = True
    view = render_panel(PanelConfig(effective_only=True), [row])
    entry = view.entries[0]
    assert entry.value == 800
    assert entry.detail['effective_damage'] == 800
    assert all('overkill_ratio' not in segment for segment in entry.segments)
    assert {segment['kind']: segment['value'] for segment in entry.segments} == {
        'physical': 500, 'arts': 300}


def test_skill_coverage_and_effective_skill_split_reach_entry_detail():
    row = PanelSourceRow(
        owner_id='A', name='A', damage=1000, effective_damage=600,
        overkill=400, overkill_complete=True,
        skill_damage=500, non_skill_damage=500,
        skill_damage_effective=300, non_skill_damage_effective=300,
        skill_split_complete=True, skill_split_effective_complete=True,
        skill_active_seconds=5, presence_seconds=10, presence_complete=True,
        skill_coverage_complete=True,
        damage_by_type={'physical': 1000},
        overkill_by_type={'physical': 400}, overkill_type_complete=True)
    assert row.skill_coverage() == .5
    view = render_panel(PanelConfig(panel_id='p', effective_only=True), [row],
                        event=EventAvailability('recording'))
    detail = view.entries[0].detail
    assert detail['skill_damage_effective'] == 300
    assert detail['non_skill_damage_effective'] == 300
    assert detail['skill_coverage'] == .5
    assert detail['skill_split_effective_complete'] is True
    assert detail['effective_damage_by_type'] == {'physical': 600}
