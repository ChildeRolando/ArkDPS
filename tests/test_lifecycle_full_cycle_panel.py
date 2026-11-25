"""Full-cycle rates require a matched exact body lifetime and event numerator."""
from copy import deepcopy
from dataclasses import replace

import pytest

from desktop_bridge import DesktopState
from panel_model import Category, PanelConfig, Ranking, RateBasis, rows_from_statistics


CONFIG = PanelConfig(ranking=Ranking.DPS, rate_basis=RateBasis.FULL_CYCLE)


def sample(**changes):
    lane = dict(effective_damage=250, observed_damage=300, complete=True,
        observed_damage_complete=True, observed_damage_window=(0, 10),
        nominal_hits=2, observed_hits=2, unverified_hits=0)
    actor = dict(name='operator', damage=None, healing=None, presence_seconds=None,
        presence_complete=False, counter_snapshot_complete=False,
        coverage_start=0, event_coverage_complete=True, settled_damage=lane,
        active_damage=5, observed_presence_seconds=1,
        lifecycle_presence_seconds=2, lifecycle_presence_complete=True,
        lifecycle_presence_window=(0, 10), lifecycle_terminal_complete=True,
        observed_healing_total=60, observed_healing_complete=True,
        observed_healing_window=(0, 10))
    actor.update(changes)
    return dict(game_time=10, coverage_start=0, ended=False,
        native_counter_scope='unavailable', event_only_projection=True,
        totals_complete=False, rows={'a': actor})


@pytest.mark.parametrize('effective,expected', [(False, 150), (True, 125)])
def test_exact_body_time_uses_authoritative_event_amount_without_native_counters(tmp_path, effective, expected):
    source = sample()
    original = deepcopy(source)
    view = DesktopState(tmp_path).view(replace(CONFIG, effective_only=effective), source)
    assert [entry['value'] for entry in view['entries']] == [expected]
    assert view['details_by_owner']['a']['presence_seconds'] == 2
    assert view['details_by_owner']['a']['presence_complete']
    # The 5-second engagement range can include verified post-retreat damage.
    # It must not extend the body's actual 2-second deployment.
    assert view['source']['a']['presence_seconds'] is None
    assert view['source']['a']['damage'] is None
    assert source == original


def test_exact_lifetime_supports_event_healing_hps_with_its_own_certificate(tmp_path):
    source = sample()
    config = replace(CONFIG, category=Category.HEALING)
    view = DesktopState(tmp_path).view(config, source)
    assert [entry['value'] for entry in view['entries']] == [30]
    source['rows']['a']['observed_healing_window'] = (0, 9)
    assert not DesktopState(tmp_path).view(config, source)['entries']


@pytest.mark.parametrize('changes', [
    dict(lifecycle_presence_complete=1), dict(lifecycle_presence_complete=False),
    dict(lifecycle_presence_seconds=True), dict(lifecycle_presence_seconds=float('nan')),
    dict(lifecycle_presence_seconds=11), dict(lifecycle_presence_seconds=-1),
    dict(lifecycle_presence_window=(0, 9)), dict(lifecycle_presence_window=(1, 10)),
    dict(lifecycle_presence_window=(0, True)), dict(lifecycle_presence_window=None),
    dict(lifecycle_presence_window=(-1, 10)), dict(lifecycle_presence_seconds=0),
])
def test_unreliable_or_mismatched_lifetimes_cannot_promote_sampled_lower_bound(tmp_path, changes):
    source = sample(**changes)
    assert not DesktopState(tmp_path).view(CONFIG, source)['entries']


def test_real_numeric_gaps_remain_unknown_independently_for_nominal_and_effective(tmp_path):
    source = sample()
    lane = source['rows']['a']['settled_damage']
    lane['complete'] = False
    assert not DesktopState(tmp_path).view(replace(CONFIG, effective_only=True), source)['entries']
    assert DesktopState(tmp_path).view(replace(CONFIG, effective_only=False), source)['entries'][0]['value'] == 150
    lane['observed_damage_complete'] = False
    assert not DesktopState(tmp_path).view(replace(CONFIG, effective_only=False), source)['entries']


def test_numerator_from_another_watermark_cannot_use_this_lifetime(tmp_path):
    source = sample()
    source['rows']['a']['settled_damage']['observed_damage_window'] = (0, 9)
    assert not DesktopState(tmp_path).view(replace(CONFIG, effective_only=False), source)['entries']


def test_open_lifetime_is_valid_live_but_needs_a_real_endpoint_in_archive(tmp_path):
    source = sample(lifecycle_terminal_complete=False)
    assert DesktopState(tmp_path).view(CONFIG, source)['entries']
    source['ended'] = True
    assert not DesktopState(tmp_path).view(CONFIG, source)['entries']
    source['rows']['a']['lifecycle_terminal_complete'] = True
    assert DesktopState(tmp_path).view(CONFIG, source)['entries']


def test_legacy_counter_denominator_still_keeps_its_original_sampling_guard():
    source = sample(damage=300, counter_snapshot_complete=True, presence_seconds=2,
                    presence_complete=True)
    source['native_counter_scope'] = 'all'
    source['event_only_projection'] = False
    actor, = rows_from_statistics(source)
    assert actor.lifecycle_presence_window is None
    assert actor.full_cycle(Category.DAMAGE) == 60
