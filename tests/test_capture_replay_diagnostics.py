from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from capture_battles import _same_lifecycle_state, _same_statistics, replay_product
from test_desktop_native_assist_projection import AssistBattle


OLD_MITIGATION_REASON = '部分命中缺少同一次结算的公式前后值或归属'
MATCHED_MITIGATION_REASON = '已记录命中的公式前后值均已配对，整局事件覆盖未确认'
INITIAL_SKILL_REASON = '部分命中的采集起点技能状态未确认'


def _recorded_before_diagnostics(value):
    if isinstance(value, dict):
        fields = {key: _recorded_before_diagnostics(item) for key, item in value.items()
                  if key not in ('skill_initialization', 'skill_initial_unknown_reason_codes')}
        if fields.get('mitigation_reason') == MATCHED_MITIGATION_REASON:
            fields['mitigation_reason'] = OLD_MITIGATION_REASON
        if fields.get('skill_split_reason') == INITIAL_SKILL_REASON:
            fields.pop('skill_split_reason')
        return fields
    if isinstance(value, list):
        return [_recorded_before_diagnostics(item) for item in value]
    return value


@pytest.fixture
def journal():
    battle = AssistBattle()
    runtime = battle.runtime
    records = [dict(kind='lifecycle_header', data={'feed': runtime.feed.id})]
    runtime.record = lambda value: records.append(dict(kind='lifecycle_observation', data=asdict(value)))
    battle.advance(2)
    assert battle.poll().kind == 'sample'
    battle.advance(3)
    hit = battle.scoped_hit(1, 3, 300)
    hit.update(mitigation_before=600., mitigation_after=300.)
    battle.advance(3, [hit])
    assert battle.poll().kind == 'sample'
    assert runtime.disconnected('capture read interrupted').kind == 'gap'
    battle.active = False
    assert battle.poll().kind == 'end'
    archive, = runtime.sessions.pop_archives()
    records.extend((dict(kind='archive_ready', data=archive),
                    dict(kind='lifecycle_summary', data=runtime.sessions.live())))
    lane = _archived_player(records)['actor_ledgers'][0]['ledger']
    assert lane['mitigation_reason'] == MATCHED_MITIGATION_REASON
    assert lane['skill_split_reason'] == INITIAL_SKILL_REASON
    return records


def _archived_player(records):
    archive = next(record['data'] for record in records if record['kind'] == 'archive_ready')
    return archive['last_sample']['multiplayer']['players'][2]


def _replay(records):
    return replay_product(json.dumps(record, default=sorted) for record in records)


def test_replay_accepts_legacy_archive_and_summary_with_same_hit_evidence(journal):
    legacy = _recorded_before_diagnostics(journal)
    original = deepcopy(legacy)
    result = _replay(legacy)
    assert result['samples'] == 2 and result['archives'] == 1
    assert legacy == original
    assert _replay(journal) == result


@pytest.mark.parametrize('field, value', [('effective_damage', 301.), ('observed_hits', 2),
    ('unverified_hits', 1), ('skill_unclassified_hits', 0), ('skill_initial_unknown_hits', 0),
    ('mitigation_matched_hits', 0), ('mitigation_complete', True),
    ('mitigation_unverified_share', 0.), ('mitigation_reason', '其它原因')])
def test_replay_checks_observed_evidence_and_unrelated_reason_text(journal, field, value):
    legacy = _recorded_before_diagnostics(journal)
    _archived_player(legacy)['actor_ledgers'][0]['ledger'][field] = value
    with pytest.raises(ValueError, match='archive differs'):
        _replay(legacy)


@pytest.mark.parametrize('mutate_initialization', [True, False])
def test_replay_checks_diagnostics_when_recorded(journal, mutate_initialization):
    player = _archived_player(journal)
    if mutate_initialization:
        player['skill_initialization']['sequence'] += 1
    else:
        player['actor_ledgers'][0]['ledger']['skill_initial_unknown_reason_codes'] = []
    with pytest.raises(ValueError, match='archive differs'):
        _replay(journal)


def test_replay_matches_only_optional_diagnostics_in_retired_player_summary():
    player = dict(player_uid='01', observed_damage=25., skill_initialization={'sequence': 4})
    current = {'multiplayer': {'previous_rounds': [{'players': [player]}]}}
    legacy = _recorded_before_diagnostics(current)
    assert _same_lifecycle_state(current, legacy)
    legacy['multiplayer']['previous_rounds'][0]['players'][0]['observed_damage'] += 1
    assert not _same_lifecycle_state(current, legacy)


@pytest.mark.parametrize('complete', [True, False])
def test_replay_requires_partial_coverage_proof_for_legacy_mitigation_reason(complete):
    lane = dict(mitigation_reason=MATCHED_MITIGATION_REASON, complete=complete,
                mitigation_complete=False, mitigation_unverified_share=None,
                observed_hits=2, mitigation_matched_hits=2)
    current = {'rows': {'owner': {'boss_damage': {'settled_damage': lane}}}}
    legacy = _recorded_before_diagnostics(current)
    assert _same_statistics(current, legacy) is not complete


def _healing_replay_envelopes(row):
    statistics = {'rows': {'owner': row}}
    player = {'player_uid': '01', 'statistics': statistics}
    return (
        {'statistics': statistics},
        {'multiplayer': {'players': [player]}},
        {'last_sample': {'multiplayer': {'players': [player]}}},
        {'multiplayer': {'previous_rounds': [{'players': [player]}]}},
    )


@pytest.mark.parametrize('field, current, different', [
    ('observed_healing_total', 40, 41),
    ('observed_healing_complete', True, False),
    ('observed_recent_healing', 8, 9),
    ('observed_recent_healing_window', [7, 12], [8, 12]),
    ('observed_recent_healing_complete', True, False),
])
def test_replay_ignores_only_absent_healing_derivations_in_ordinary_and_scoped_statistics(
        field, current, different):
    old_row = {'healing': None, 'observed_healing': 40}
    new_row = dict(old_row, **{field: current})
    altered_field = dict(old_row, **{field: different})
    altered_amount = dict(old_row, observed_healing=41)
    for actual, legacy, changed, wrong_amount in zip(
            *map(_healing_replay_envelopes, (new_row, old_row, altered_field, altered_amount))):
        assert _same_lifecycle_state(actual, legacy)
        assert _same_lifecycle_state(actual, deepcopy(actual))
        assert not _same_lifecycle_state(actual, changed)
        assert not _same_lifecycle_state(actual, wrong_amount)


def test_replay_does_not_normalize_healing_events_identity_clock_or_watermark():
    record = dict(
        statistics={'rows': {'owner': {'observed_healing': 40, 'observed_healing_total': 40}}},
        event_watermark={'sequence': 3, 'frame': 360, 'game_time': 12},
        events=[{'kind': 'healing', 'sequence': 3, 'frame': 360, 'game_time': 12,
                 'source': 'char_4041_chnut', 'source_character_uid': 301, 'amount': 40}],
    )
    for key, value in (('source_character_uid', 302), ('sequence', 4), ('frame', 361),
                       ('game_time', 13), ('amount', 41), ('observed_healing_total', 40)):
        different = deepcopy(record)
        del different['statistics']['rows']['owner']['observed_healing_total']
        different['events'][0][key] = value
        assert not _same_lifecycle_state(record, different)
    different = deepcopy(record)
    different['event_watermark']['sequence'] = 4
    assert not _same_lifecycle_state(record, different)
