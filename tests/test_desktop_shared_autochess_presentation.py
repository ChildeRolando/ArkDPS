"""Desktop contract tests inject accepted scenes, never a game or observer.

Both platform inputs deliberately share the same accepted accounting fixture.
Adapter authentication is tested by the source owner, not simulated here.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

import desktop_bridge as bridge
from battle_lifecycle import NativeAutoChessScene
from collector import ProcessChanged
from collection_session import CollectionSession
from connection import ConnectionConfig, ConnectionDiagnostics, DetectionStatus
from event_capture import CaptureRetry, LiveEventCapture
from event_observer import ObserverStatus
from test_multiplayer_native_context import consume, context, hit, stamp
from multiplayer_battle import MultiplayerBattleStatistics
from test_android_autochess_acquisition import AndroidAutoChessBattle


def accepted_inputs(platform):
    meter, samples, scenes = MultiplayerBattleStatistics(), {}, {}
    actions = [('defense', 0, 4, 4, []), ('defense_hits', 1, 4, 4,
        [hit(i, i, 1, i * 100) for i in range(1, 5)]),
        ('assist', 2, 5, 4, []), ('assist_hits', 3, 5, 4,
        [hit(i + 4, i, 3, i * 1000, event_scope=stamp(phase=5)) for i in range(1, 5)]),
        ('next_defense', 4, 4, 5, []), ('next_hits', 5, 4, 5,
        [hit(i + 8, i, 5, i * 7, event_scope=stamp(native_round=5)) for i in range(1, 5)])]
    for name, when, phase, number, events in actions:
        value = consume(meter, when, events, sequence=max((e['sequence'] for e in events),
            default=meter._sequence or 0), native=context(when, phase=phase, native_round=number))
        samples[name] = dict(native_counter_scope='events_only', multiplayer=value,
            battle_id='accepted-presentation-run', session='accepted-presentation-run',
            frame=when * 30, game_time=when, state=2, event_status='recording', event_error=None,
            battle_metadata=dict(platform=platform, source_build='accepted-fixture', stage_id='ACTAUTOCHESS/test'),
            captured_monotonic=time.monotonic(), statistics={}, _scene_tag=name)
        scenes[name] = NativeAutoChessScene((platform, 'accepted-device', 'run-1'),
            value['scope'], phase, number)
    scenes['prepare'] = NativeAutoChessScene(scenes['defense'].match, 'prepare-scope', 3, 4)
    return samples, scenes


def state_for(tmp_path, monkeypatch, platform):
    state = bridge.DesktopState(tmp_path)
    state.effective_connection = ConnectionConfig(platform='native_pc' if platform == 'native_pc' else 'mumu')
    state.layout = replace(state.layout, multiplayer_enabled=True)
    samples, scenes = accepted_inputs(platform)
    monkeypatch.setattr(bridge, 'autochess_scene', lambda sample: scenes.get(sample.get('_scene_tag')))
    return state, samples, scenes


def values(state):
    return [entry['value'] for entry in state.snapshot()['panels'][0]['entries']]


def comparison(state):
    snapshot = state.snapshot()
    if state.effective_connection.platform == 'native_pc':
        assert snapshot['multiplayer_supported'] is True
        return snapshot['multiplayer']
    assert snapshot['multiplayer_supported'] is snapshot['multiplayer_active'] is False
    assert snapshot['multiplayer'] is None
    # The shared retained model still drives the own-account main panel. Only
    # the PC-only comparison wire lane is suppressed for Android.
    return state._presented_battle()[1]


def receive(state, sample, scene):
    state.accept('autochess_scene', scene)
    state.accept('sample', sample)


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('assist', [False, True])
def test_shared_defense_cache_is_used_only_outside_combat(
        tmp_path, monkeypatch, platform, assist):
    state, samples, scenes = state_for(tmp_path, monkeypatch, platform)
    originals = deepcopy(samples)
    receive(state, samples['defense_hits'], scenes['defense_hits'])
    assert values(state) == [300]
    if assist:
        state.accept('autochess_scene', scenes['assist'])
        assert values(state) == [] and '当前协防战场' in state.snapshot()['status_text']
        receive(state, samples['assist_hits'], scenes['assist_hits'])
        assert values(state) == [4000, 3000, 2000, 1000]
    state.accept('lifecycle', 'preparing')
    state.accept('autochess_scene', scenes['prepare'])
    retained = state.snapshot()
    assert values(state) == [300] and '上一场防御' in retained['status_text']
    assert comparison(state)['previous_defense'] is True
    assert comparison(state)['scope'] == samples['defense_hits']['multiplayer']['scope']
    state.accept('autochess_scene', scenes['next_defense'])
    waiting = state.snapshot()
    assert values(state) == [] and comparison(state) is None
    assert waiting['battle_id'] is None and waiting['game_time'] is None
    assert '等待本轮防御数据' in waiting['status_text']
    state.accept('lifecycle', 'playing')
    state.accept('sample', samples['next_defense'])
    assert values(state) == [] and not comparison(state).get('previous_defense')
    assert comparison(state)['scope'] == samples['next_defense']['multiplayer']['scope']
    assert state.snapshot()['game_time'] == samples['next_defense']['game_time']
    state.accept('sample', samples['next_hits'])
    assert values(state) == [21]
    assert not comparison(state).get('previous_defense')
    assert samples == originals


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_same_scope_empty_rebaseline_keeps_current_and_review_data_independent(
        tmp_path, monkeypatch, platform):
    state, samples, scenes = state_for(tmp_path, monkeypatch, platform)
    receive(state, samples['defense_hits'], scenes['defense_hits'])
    retained = state._defense_sample
    assert retained is not state.last
    assert retained['multiplayer'] == state.last['multiplayer']
    assert retained['multiplayer'] is not state.last['multiplayer']
    assert retained['multiplayer']['players'] is not state.last['multiplayer']['players']
    assert state._defense_analysis[0] is not state.live_analysis
    before = deepcopy(retained)
    empty = dict(samples['defense_hits'], multiplayer=consume(MultiplayerBattleStatistics(), 1, [],
        sequence=0, native=context(1)))
    original = deepcopy(empty)
    assert empty['frame'] == samples['defense_hits']['frame']
    assert empty['multiplayer']['scope'] == samples['defense_hits']['multiplayer']['scope']
    state.accept('sample', empty)
    assert values(state) == []
    assert not comparison(state).get('previous_defense')
    assert state._defense_sample is retained and retained == before
    assert state.last is empty and empty == original
    state.accept('autochess_scene', scenes['prepare'])
    assert values(state) == [300] and comparison(state)['previous_defense'] is True


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_first_empty_defense_still_shows_identities_without_inventing_output(tmp_path, monkeypatch, platform):
    state, samples, scenes = state_for(tmp_path, monkeypatch, platform)
    receive(state, samples['defense'], scenes['defense'])
    snapshot = state.snapshot()
    assert values(state) == []
    assert comparison(state)['scope'] == samples['defense']['multiplayer']['scope']
    assert not comparison(state).get('previous_defense')
    assert [p['player_uid'] for p in comparison(state)['players']] == [
        p['player_uid'] for p in samples['defense']['multiplayer']['players']]
    assert all(p['observed_damage'] is None for p in comparison(state)['players'])


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
@pytest.mark.parametrize('nominal', [True, False])
def test_any_evidenced_player_settlement_can_replace_prior_defense(tmp_path, monkeypatch, platform, nominal):
    state, samples, scenes = state_for(tmp_path, monkeypatch, platform)
    receive(state, samples['defense_hits'], scenes['defense_hits'])
    receive(state, samples['next_defense'], scenes['next_defense'])
    meter = MultiplayerBattleStatistics()
    consume(meter, 4, native=context(4, native_round=5))
    event = hit(1, 1, 5, 7, event_scope=stamp(native_round=5))
    if not nominal:
        event['nominal_amount'] = None
    current = dict(samples['next_hits'], multiplayer=consume(meter, 5, [event],
        native=context(5, native_round=5)))
    receive(state, current, scenes['next_hits'])
    snapshot = state.snapshot()
    assert comparison(state)['scope'] == current['multiplayer']['scope']
    assert not comparison(state).get('previous_defense')
    assert values(state) == []  # Foreign output never becomes the local account.
    assert comparison(state)['players'][2]['observed_damage'] is None
    projected = state.multiplayer_view(current['multiplayer'])
    assert projected['players'][0]['view']['entries'][0]['value'] == 7


@pytest.mark.parametrize('platform', ['android', 'native_pc'])
def test_shared_reconnect_barrier_keeps_same_target_defense_and_rejects_old_queue(
        tmp_path, monkeypatch, platform):
    state, samples, scenes = state_for(tmp_path, monkeypatch, platform)
    config = (ConnectionConfig(platform='native_pc', native_game_root='owned-PC') if platform == 'native_pc'
              else ConnectionConfig(adb_path='owned-adb', serial='127.0.0.1:16384', auto_detect=False))
    state.expect_connection_request(config, 1)
    state.accept('connection_source', dict(revision=1, platform=config.platform))
    receive(state, samples['defense_hits'], scenes['defense_hits'])
    state.expect_connection_request(config, 2)
    receive(state, samples['assist_hits'], scenes['assist_hits'])  # Retired queued source.
    state.accept('connection_source', dict(revision=1, platform=config.platform))
    assert state._awaiting_source and state._defense_sample['multiplayer']['players'][2]['observed_damage'] == 300
    state.accept('connection_source', dict(revision=2, platform=config.platform))
    state.accept('autochess_scene', scenes['prepare'])
    assert values(state) == [300]
    assert comparison(state)['previous_defense'] is True
    different = (replace(config, native_game_root='different-PC') if platform == 'native_pc'
                 else replace(config, serial='127.0.0.1:16416'))
    state.expect_connection_request(different, 3)
    assert state._defense_sample is None
    receive(state, samples['defense_hits'], scenes['defense_hits'])
    assert state._defense_sample is None and state._awaiting_source


def test_legacy_mumu_without_a_proven_scene_keeps_its_existing_last_sample_behavior(tmp_path, monkeypatch):
    state, samples, _ = state_for(tmp_path, monkeypatch, 'android')
    state.accept('sample', samples['defense_hits'])
    state.accept('sample', samples['assist_hits'])
    state.accept('lifecycle', 'preparing')
    assert state._autochess_scene is state._defense_sample is None
    assert state.last is samples['assist_hits']
    assert '保留上一局统计' in state.snapshot()['status_text']
    assert state.snapshot()['multiplayer'] is None


@pytest.mark.parametrize('platform', ['mumu', 'native_pc'])
def test_worker_publishes_shared_scene_and_passes_one_live_setting_getter_before_connect(
        tmp_path, monkeypatch, platform):
    state = bridge.DesktopState(tmp_path / 'state')
    worker = bridge.CollectionWorker(state)
    monkeypatch.setattr(worker, '_pc_provider', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker.stop, 'wait', lambda *_args: False)
    diagnostics = ConnectionDiagnostics(status=DetectionStatus.READY_UNIQUE, platform=platform,
        selected_serial='127.0.0.1:16384', version_actual='fixture', message='accepted source fixture')
    monkeypatch.setattr(bridge, 'resolve_configured', lambda _config: SimpleNamespace(
        adb_path='owned-adb', serial='127.0.0.1:16384', diagnostics=diagnostics))
    monkeypatch.setattr(bridge, 'ensure_frida_endpoint', lambda *_args, **_kwargs: (True, 'fixture'))
    import native_pc_collector
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *_args, **_kwargs:
                        SimpleNamespace(diagnostics=diagnostics))
    scene = NativeAutoChessScene((platform, 'owned-run'), 'owned-scope', 4, 1)
    sessions, preferences, after_short = [], [], []

    class Session:
        cleanup_errors = {}
        event_status, event_error = 'ready', None

        def __init__(self, *_args, autochess_enabled=None, collector=None, **_kwargs):
            assert callable(autochess_enabled)
            self.setting = autochess_enabled
            self.collector = collector or SimpleNamespace(diagnostics=diagnostics)
            self.observer = SimpleNamespace(status=ObserverStatus.READY, error=None)
            self.reads = 0
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            self.runtime = runtime

        def connect(self):
            preferences.append(self.setting())

        def read(self):
            self.reads += 1
            if self.reads == 1:
                self.runtime.feed.autochess_scene = scene
                self.runtime.sessions.state = 'playing'
                worker.set_multiplayer_enabled(True)
                return None
            if self.reads == 2:
                preferences.append(self.setting())
                self.runtime.feed.autochess_scene = None
                self.runtime.feed.current_lifecycle = dict(battle=None, state=None, target='', metadata={})
                # This is the adapter's explicit exit capability, not a worker
                # inference from platform or from an absent small-round object.
                self.runtime.feed.autochess_exited = platform == 'native_pc'
                raise CaptureRetry('short small-round absence without an exit witness')
            if self.reads == 3:
                after_short.extend(value for kind, value in worker.events.queue if kind == 'autochess_scene')
                self.runtime.feed.current_lifecycle = dict(battle=None, state=None, target='',
                    metadata=dict(stage_id='main/1-7', evidence='accepted-stage-fixture'))
                self.runtime.feed.autochess_exited = True
                raise CaptureRetry('accepted ordinary stage')
            worker.stop.set()
            raise ProcessChanged('owned process exit fixture')

        def close(self):
            pass

    monkeypatch.setattr(bridge, 'CollectionSession', Session)
    config = (ConnectionConfig(platform='native_pc', native_game_root='owned-PC') if platform == 'native_pc'
              else ConnectionConfig(adb_path='owned-adb', serial='127.0.0.1:16384', auto_detect=False))
    worker.request(config, True)
    worker.run()
    assert len(sessions) == 1 and preferences == [False, True]
    events = list(worker.events.queue)
    assert not any(kind == 'worker_error' for kind, _value in events)
    published = [value for kind, value in events if kind == 'autochess_scene']
    assert published == [scene, None]
    assert after_short == ([scene] if platform == 'mumu' else [scene, None])


def run_android_worker(tmp_path, monkeypatch, actions):
    """Production Android admission/session/worker/state; replace transport only."""
    battle = AndroidAutoChessBattle()
    battle.observer.error = None
    battle.close = lambda: None
    state = bridge.DesktopState(tmp_path / 'desktop')
    state.layout = replace(state.layout, multiplayer_enabled=True)
    worker = bridge.CollectionWorker(state)
    monkeypatch.setattr(worker, '_pc_provider', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker.stop, 'wait', lambda *_args: False)
    target = SimpleNamespace(adb_path='owned-adb', serial='127.0.0.1:16384', diagnostics=battle.diagnostics)
    monkeypatch.setattr(bridge, 'resolve_configured', lambda _config: target)
    monkeypatch.setattr(bridge, 'ensure_frida_endpoint', lambda *_args, **_kwargs: (True, 'fixture'))
    pending, steps, accepted = iter(actions), [], []

    class Session(CollectionSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, collector=battle, **kwargs)

        def enable_lifecycle(self, runtime):
            super().enable_lifecycle(runtime)
            battle.runtime = runtime

        def connect(self):
            assert self.autochess_enabled() is True
            self.observer = battle.observer
            self.event_status, self.event_error = 'ready', None
            self.capture = LiveEventCapture(self.collector, self.observer, audit=self.audit)

        def read(self):
            if steps:
                steps[-1]['end'] = worker.events.qsize()
            action = next(pending, 'stop')
            step = dict(action=action, start=steps[-1]['end'] if steps else 0)
            steps.append(step)
            battle.active = True
            if action == 'stop':
                worker.stop.set()
                raise CaptureRetry('finite Android fixture complete')
            if action == 'defense':
                battle.advance(0, mode='all')
            elif action == 'defense_hits':
                battle.advance(1, amounts=tuple((i, i * 100) for i in range(1, 5)))
            elif action in ('assist_busy', 'assist'):
                battle.advance(0, phase=5, new_controller=True)
                battle.fail_drain = action == 'assist_busy'
            elif action == 'assist_hits':
                battle.advance(1, amounts=tuple((i, i * 1000) for i in range(1, 5)))
            elif action == 'prepare':
                battle.advance(0, phase=3, new_controller=True)
            elif action in ('next_defense', 'next_defense_busy'):
                battle.advance(0, phase=4, round_=2, new_controller=True)
                battle.fail_drain = action == 'next_defense_busy'
            elif action == 'next_hits':
                battle.advance(1, amounts=tuple((i, i * 7) for i in range(1, 5)))
            elif action == 'short_absent':
                battle.active = False
            elif action == 'ordinary_stage':
                battle.stage = 'Obt/Main/level_main_01-07'
            else:
                raise AssertionError(action)
            try:
                sample = super().read()
                accepted.append((sample, deepcopy(sample)))
                return sample
            finally:
                battle.fail_drain = False

    monkeypatch.setattr(bridge, 'CollectionSession', Session)
    config = ConnectionConfig(adb_path='owned-adb', serial=target.serial, auto_detect=False)
    revision = worker.request(config, True)
    state.expect_connection_request(config, revision)
    worker.run()
    events = list(worker.events.queue)
    steps[-1]['end'] = len(events)
    assert not any(kind == 'worker_error' for kind, _value in events)
    views = {}
    for step in steps:
        for kind, value in events[step['start']:step['end']]:
            state.accept(kind, value)
        views[step['action']] = state.snapshot()
    assert all(sample == original for sample, original in accepted)
    return state, views, events


@pytest.mark.parametrize('assist', [False, True])
def test_actual_android_accepted_context_restores_defense_after_small_controller_changes(
        tmp_path, monkeypatch, assist):
    actions = ['defense', 'defense_hits']
    if assist:
        actions += ['assist_busy', 'assist', 'assist_hits']
    actions += ['short_absent', 'prepare', 'next_defense', 'next_hits']
    state, views, events = run_android_worker(tmp_path, monkeypatch, actions)
    readings = lambda view: [entry['value'] for entry in view['panels'][0]['entries']]
    assert readings(views['defense_hits']) == [300]
    if assist:
        assert readings(views['assist_busy']) == []
        assert '当前协防战场' in views['assist_busy']['status_text']
        assert readings(views['assist_hits']) == [4000, 3000, 2000, 1000]
    assert readings(views['prepare']) == [300]
    assert '上一场防御' in views['prepare']['status_text']
    assert views['prepare']['multiplayer'] is None and views['prepare']['multiplayer_supported'] is False
    assert readings(views['next_defense']) == []
    assert views['next_defense']['battle_id'] != views['prepare']['battle_id']
    assert views['next_defense']['totals']['damage'] is None
    assert views['next_defense']['multiplayer'] is None
    assert readings(views['next_hits']) == [21]
    scenes = [scene for kind, scene in events if kind == 'autochess_scene']
    assert None not in scenes  # A small-round singleton absence is not a match exit.
    assert len({scene.match for scene in scenes}) == 1
    assert state.selected is not None  # Real archive notices remain independent.


def test_actual_android_ordinary_stage_proof_clears_the_retained_defense(tmp_path, monkeypatch):
    state, views, events = run_android_worker(tmp_path, monkeypatch,
        ['defense', 'defense_hits', 'prepare', 'ordinary_stage'])
    assert views['prepare']['multiplayer'] is None
    assert '上一场防御' in views['prepare']['status_text']
    assert any(kind == 'autochess_scene' and value is None for kind, value in events)
    assert state._defense_sample is None
    assert not (views['ordinary_stage']['multiplayer'] or {}).get('previous_defense')


def test_android_unaccepted_assist_and_new_defense_restore_the_existing_result(tmp_path, monkeypatch):
    state, views, events = run_android_worker(tmp_path, monkeypatch,
        ['defense', 'defense_hits', 'assist_busy', 'prepare', 'next_defense_busy', 'prepare'])
    assert '当前协防战场' in views['assist_busy']['status_text']
    assert (views['assist_busy']['multiplayer'] or {}).get('scope_kind') != 'defense'
    assert [entry['value'] for entry in views['prepare']['panels'][0]['entries']] == [300]
    assert views['prepare']['multiplayer'] is None and '上一场防御' in views['prepare']['status_text']
    assert views['next_defense_busy']['multiplayer'] is None
    assert '等待本轮防御数据' in views['next_defense_busy']['status_text']
    assert state._defense_sample['multiplayer']['players'][2]['observed_damage'] == 300
    assert len([value for kind, value in events if kind == 'sample']) == 2
