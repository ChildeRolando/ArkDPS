"""Owned producer boundaries model the saved defense-to-assistance transition.

The saved debug-off archives establish the phase/clock order, not their raw
callback or ACK prefix. These tests exercise that order with explicit fences.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from battle_lifecycle import BattleSessions
from capture_battles import observation_from_json
from test_autochess_scope_end import OwnedScopeEnd, old_local


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('mode', ['all', 'local'])
def test_same_round_defense_to_assist_seals_only_the_accepted_defense_watermark(platform, mode):
    case = OwnedScopeEnd(platform, phase=4, mode=mode)
    # R8's persisted order is defense ~117s -> assistance ~118s -> assistance
    # zero. The later assistance clock is never a defense endpoint witness.
    case.advance(117)
    case.fixture.poll()
    old_sample = deepcopy(case.fixture.runtime.sessions.latest_sample)
    old_commits = list(case.fixture.commits)
    case.advance(118, phase=5)
    successor = case.fixture.poll()
    archive, = case.fixture.runtime.sessions.pop_archives()
    assert archive['event_status'] == 'finished' and archive['event_error'] is None
    assert archive['last_sample']['game_time'] == archive['statistics']['game_time'] == 117
    assert archive['last_sample']['frame'] == old_sample['frame']
    assert archive['last_sample']['autochess_context']['phase'] == 4
    assert successor.sample['autochess_context']['phase'] == 5
    assert successor.sample['game_time'] == 118
    assert old_local(archive)['observed_damage'] == 300
    assert case.fixture.commits == old_commits + [old_commits[-1]]
    case.advance(0, phase=5)
    case.fixture.poll()
    fragment, = case.fixture.runtime.sessions.pop_archives()
    assert fragment['last_sample']['autochess_context']['phase'] == 5
    assert archive['statistics']['game_time'] == 117
    replay = BattleSessions(successor.feed)
    for record in case.records:
        assert replay.consume(observation_from_json(asdict(record)))
    recreated = replay.pop_archives()
    assert list(recreated) == [archive, fragment]


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('fault', ['unread_tail', 'no_output_callback', 'observer', 'late_start', 'mode'])
def test_defense_assist_candidate_never_hides_a_real_missing_prefix(platform, fault):
    case = OwnedScopeEnd(platform, phase=4, mode='all', start=.5 if fault == 'late_start' else 0)
    if fault == 'unread_tail':
        case.advance(1.25, amount=75)
    elif fault == 'no_output_callback':
        case.extra_epoch += 1
    elif fault == 'observer':
        case.fixture.observer = SimpleNamespace(**vars(case.fixture.observer))
    elif fault == 'mode':
        case.mode = 'local'
        if platform == 'android':
            case.fixture.mode = 'local'
    successor, archive = case.finish(phase=5)
    assert archive['event_status'] == 'gap' and archive['event_error'] == '未取得最终事件水位'
    assert old_local(archive)['observed_damage'] == 300
    assert archive['statistics']['game_time'] == 1


@pytest.mark.parametrize('platform', ['native_pc', 'android'])
@pytest.mark.parametrize('fault', ['account', 'epoch', 'sequence', 'clock', 'build'])
def test_defense_assist_raw_replay_keeps_account_source_and_clock_guards(platform, fault):
    case = OwnedScopeEnd(platform, phase=4, mode='all')
    successor, archive = case.finish(phase=5)
    assert archive['event_status'] == 'finished'
    sample, evidence = deepcopy(successor.sample), deepcopy(successor.autochess_successor)
    if fault == 'account':
        sample['autochess_context']['players'][0]['uid'] += '-different'
    elif fault in ('epoch', 'sequence'):
        evidence['before'][fault] += 1
    elif fault == 'clock':
        evidence['before']['game_time'] = 0
    else:
        sample['battle_metadata']['source_build'] += '-different'
    replay = BattleSessions(successor.feed)
    for record in case.records[:-1]:
        assert replay.consume(record)
    assert replay.consume(replace(successor, sample=sample, autochess_successor=evidence))
    rejected, = replay.pop_archives()
    assert rejected['event_status'] == 'gap'
    assert rejected['statistics']['game_time'] == 1
