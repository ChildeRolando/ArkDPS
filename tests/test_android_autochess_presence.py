"""Optional Android live-instance readings preserve the core mark and event prefix."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import CaptureRetry
from event_observer import ObserverReadRetry
from test_android_autochess_cross_frame_admission import CrossingAndroidBattle
from test_multiplayer_native_context import member, player, stamp
from test_multiplayer_native_skill_baseline import activation


@pytest.fixture(scope='module')
def source():
    fixture = Path(__file__).with_name('event_probe_android_presence_fixture.js')
    result = subprocess.run(['node', str(fixture)], check=True, capture_output=True,
                            encoding='utf-8', timeout=30)
    return json.loads(result.stdout)


def test_shared_producer_only_reads_android_presence_on_requested_mark(source):
    assert source['pc'] is None
    assert source['empty']['status'] == 'available' and source['empty']['units'] == []
    assert [row['data_uid'] for row in source['all']['units']] == [104, 101]
    assert [row['data_uid'] for row in source['local']['units']] == [104]
    assert source['all']['phase'] == 5 and source['all']['collection_mode'] == 'all'
    assert source['boss']['phase'] == 6 and source['boss']['units'] == source['all']['units']
    assert source['crowded']['units'] == source['all']['units']
    assert len(source['character_limit']['units']) == 128
    assert source['inactive']['units'] == source['token']['units'] == source['all']['units'][1:]
    assert source['callback_sequence']['units'] == source['all']['units']
    for report in [source['too_many_characters'], *source['invalid'].values()]:
        assert report['status'] == 'unavailable' and report['units'] is None
    for case, reads in source['budgets'].items():
        if case != 'over_character_limit':
            cold, warm = reads
            assert warm['read_calls'] <= cold['read_calls']
        assert all(0 < point['read_calls'] <= 12000 and 0 < point['read_bytes'] <= 256 * 1024
                   for point in reads)


class PresenceBattle(CrossingAndroidBattle):
    def __init__(self, phase, mode):
        super().__init__()
        self.phase, self.mode = phase, mode
        self.observer.mark_with_presence = self.mark_with_presence
        self.presence_requests = []
        self.fault = None

    def mark_with_presence(self):
        mark = self.window_mark()
        self.presence_requests.append(mark['frame'])
        if self.fault == 'transport':
            raise ObserverReadRetry('owned optional closing transport error')
        units = [dict(cid=row['cid'], data_uid=row['data_uid'], instance_uid=1000 + index, player_side=index)
                 for index in (3, 2) for row in [member(index)]]
        attrs = dict(version=1, status='available', frame=mark['frame'], game_time=mark['game_time'],
            native_fixed_time=mark['native_fixed_time'], scope_key=self.scope(), phase=self.phase,
            round=self.round, collection_mode=self.mode, units=units)
        mark['native_battle_state'] = 2
        if self.fault == 'wrong_frame':
            attrs['frame'] -= 1
        elif self.fault == 'state_type':
            mark['native_battle_state'] = 2.
        elif self.fault == 'duplicate_instance':
            attrs['units'].append(deepcopy(units[0]))
        elif self.fault == 'unavailable':
            attrs.update(status='unavailable', units=None)
        if self.fault != 'missing':
            mark['native_autochess_presence'] = attrs
        return mark


@pytest.mark.parametrize(('phase', 'mode', 'members'), [(4, 'local', [301]), (5, 'all', [301, 201]),
                                                     (6, 'all', [301, 201])])
def test_android_presence_uses_closing_event_clock_and_no_additional_roster_reads(phase, mode, members):
    battle = PresenceBattle(phase, mode)

    def unexpected_roster():
        pytest.fail('Android event-only samples must not read the broad roster')

    battle.read_roster = unexpected_roster
    battle.cross()
    observation = battle.poll()
    point = observation.sample['scoped_presence']
    assert observation.sample['autochess_snapshot_anchor'] == dict(frame=0, game_time=0)
    assert point == dict(version=1, binding='event_mark', sample_seq=1, frame=15, game_time=.5,
        units=[dict(cid=member(uid//100)['cid'], data_uid=uid, instance_uid=1000+uid//100,
                    player_side=uid//100) for uid in members])
    assert battle.presence_requests == [15] and battle.commits == [1]
    battle.window = None
    battle.advance(1)
    skipped = battle.poll()
    assert 'scoped_presence' not in skipped.sample and battle.presence_requests == [15]
    battle.virtual_host_time = 1
    battle.advance(2, amounts=((3, 100),))
    assert battle.poll().sample['scoped_presence']['frame'] == 60
    assert battle.presence_requests == [15, 60] and battle.commits == [1, 1, 2]


@pytest.mark.parametrize('fault', ['wrong_frame', 'state_type', 'duplicate_instance', 'unavailable', 'missing'])
def test_android_presence_failure_keeps_sample_and_ack(fault):
    battle = PresenceBattle(5, 'all')
    battle.poll()
    battle.virtual_host_time = 2
    battle.advance(2, amounts=((3, 30),))
    battle.fault = fault
    report = battle.poll()
    assert report.kind == 'sample'
    assert report.sample['scoped_presence']['units'] is None
    assert battle.commits == [0, 1]


def test_android_unpublished_presence_attempt_leaves_a_gap_in_the_endpoint_sequence():
    battle = PresenceBattle(4, 'local')
    assert battle.poll().sample['scoped_presence']['sample_seq'] == 1
    battle.virtual_host_time = 1
    battle.advance(1, amounts=((3, 20),))
    battle.fault = 'transport'
    with pytest.raises(CaptureRetry, match='optional closing transport'):
        battle.poll()
    assert battle.commits == [0]
    battle.fault = None
    battle.advance(1.5)
    recovered = battle.poll()
    assert 'scoped_presence' not in recovered.sample
    assert battle.presence_requests == [0, 30] and battle.commits == [0, 1]
    battle.virtual_host_time = 2
    battle.advance(2)
    assert battle.poll().sample['scoped_presence']['sample_seq'] == 3
    assert battle.presence_requests == [0, 30, 60]


def test_android_verified_closing_points_reach_actor_presence_and_skill_coverage():
    battle = PresenceBattle(4, 'local')
    battle.poll()
    start = stamp(phase=4, native_round=1, root=battle.scope(), when=.5, collection_mode='local')
    battle.events.append(activation(1, .5, event_scope=start))
    battle.virtual_host_time = 1
    battle.advance(1, amounts=((3, 30),))
    battle.cross(closing_shift=.25, amount=10)
    a = battle.poll()
    assert a.sample['autochess_snapshot_anchor']['frame'] != a.sample['scoped_presence']['frame']
    battle.window = None
    end = stamp(phase=4, native_round=1, root=battle.scope(), when=2, collection_mode='local')
    battle.events.append(dict(activation(4, 2, event_scope=end), kind='skill_end',
        proof={'method': 'Character.OnSkillFinish'}))
    battle.virtual_host_time = 2
    battle.advance(2.25, amounts=((3, 10),))
    assert battle.poll().kind == 'sample'
    rows = player(battle.runtime.display_sample()['multiplayer'], 3)['statistics']['rows']
    actor, = rows.values()
    assert actor['observed_presence_seconds'] == pytest.approx(2.25)
    assert actor['skill_timed_presence_seconds'] == pytest.approx(1)
    assert actor['observed_skill_active_seconds'] == pytest.approx(.75)
    assert actor['observed_skill_coverage'] == pytest.approx(.75)
    assert not actor.get('presence_complete', False)
