"""The Android source's closing mark reaches live and saved actor details."""
from copy import deepcopy

from battle_history import BattleRecord
from desktop_bridge import DesktopState
from test_android_autochess_cross_frame_admission import CrossingAndroidBattle
from test_android_autochess_presence import source


def test_android_presence_wire_reaches_history_and_inspector(source, tmp_path):
    battle = CrossingAndroidBattle()
    state = DesktopState(tmp_path)
    for index, wire in enumerate(source['wire']):
        opening, closing, drain = (wire[key] for key in ('opening', 'closing', 'drain'))
        battle.roots = {key: int(value, 16) for key, value in opening['native_identity'].items()}
        battle.process.pid = opening['native_process_pid']
        battle.stage = opening['native_stage_id']
        battle.frame, battle.when = opening['frame'], opening['game_time']
        battle.virtual_host_time = index
        battle.observer.mark = lambda: deepcopy(opening)
        battle.observer.mark_with_presence = lambda: deepcopy(closing)
        battle.observer.read_baseline = battle.observer.read_through = lambda through: deepcopy(drain)
        observation = battle.poll()
        assert observation.kind == 'sample'
        assert observation.sample['autochess_snapshot_anchor']['frame'] == opening['frame']
        point = observation.sample['scoped_presence']
        assert point['frame'] == closing['frame'] and point['sample_seq'] == index + 1
        assert point['units'] == closing['native_autochess_presence']['units']

    assert battle.commits == [0, 1, 1]
    state.accept('autochess_scene', battle.runtime.feed.autochess_scene)
    state.accept('lifecycle', battle.runtime.sessions.state)
    state.accept('sample', battle.runtime.display_sample())
    view = state.snapshot()['panels'][0]
    owner, row = next((key, row) for key, row in view['source'].items() if row['actor_uid'] == 104)
    assert row['observed_presence_seconds'] == 2
    assert row['observed_skill_coverage'] == 0 and row['skill_timed_presence_seconds'] == 1
    assert row.get('presence_complete') is not True and row.get('presence_seconds') is None

    battle.active = False
    assert battle.poll().kind == 'end'
    [archive] = battle.runtime.sessions.pop_archives()
    record = BattleRecord.from_archive(archive)
    saved = state.history.save(record)
    assert saved.written
    state.selected, = state.history.get(record.battle_id).records
    archived = state.snapshot()['history_selected']['view']['source'][owner]
    assert archived['observed_presence_seconds'] == 2
    assert archived['observed_skill_coverage'] == 0
    assert archived['skill_timed_presence_seconds'] == 1
