"""Real acquisition, session and worker notices retain an owned defense view.

Only transport setup and deterministic raw inputs are replaced. No game,
observer runtime, GUI or thread is started; CollectionWorker.run is synchronous.
"""
from dataclasses import replace
import hashlib
import json

import pytest
import desktop_bridge as bridge
import native_pc_collector
from collection_session import CollectionSession
from connection import ConnectionConfig
from event_capture import CaptureRetry, LiveEventCapture
from event_observer import ObserverReadRetry, ObserverStatus
from battle_lifecycle import native_autochess_scene
from panel_model import PanelConfig
from test_multiplayer_integration import CID
from test_native_autochess_leading_zero_uid import LEADING_UIDS, leading_zero_battle


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def run_chain(tmp_path, monkeypatch, actions, *, multiplayer_enabled=True,
              mode_by_epoch=None, settings_by_action=None):
    """Run real CollectionSession.read + worker FIFO from one owned PC source."""
    state = bridge.DesktopState(tmp_path / 'desktop')
    state.layout = replace(state.layout, multiplayer_enabled=multiplayer_enabled, panels=(PanelConfig(),
        PanelConfig(panel_id='effective', effective_only=True)))
    battle = leading_zero_battle(tmp_path / 'raw')
    worker = bridge.CollectionWorker(state)
    # Transport ownership is local; do not construct a Gadget/bootstrap owner.
    monkeypatch.setattr(worker, '_pc_provider', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(native_pc_collector, 'NativePcCollector', lambda *_args, **_kwargs: battle.collector)
    worker.stop.wait = lambda *_: False
    original_batch = battle.observer.read_through
    original_mark = battle.observer.mark
    fail_batch = [False]

    def mode_for(scope):
        return (mode_by_epoch or {}).get(int(scope['scope_key'].rsplit(':', 1)[-1]))

    def with_collection_mode(packet):
        raw = packet['native_autochess_context']
        mode = mode_for(raw)
        if mode is not None:
            raw['collection_mode'] = mode
        for event in packet.get('events', ()):
            scope = event.get('native_autochess_scope')
            if scope is not None and (mode := mode_for(scope)) is not None:
                scope['collection_mode'] = mode
        return packet

    def batch(through):
        if fail_batch[0]:
            fail_batch[0] = False
            raise ObserverReadRetry('owned guarded assist batch not yet available')
        packet = original_batch(through)
        if mode_by_epoch and battle.commits:
            packet['events'] = [event for event in packet['events'] if event['sequence'] > battle.commits[-1]]
        return with_collection_mode(packet)

    battle.observer.read_through = batch
    if mode_by_epoch:
        battle.observer.mark = lambda: with_collection_mode(original_mark())
    battle.observer.status = ObserverStatus.READY
    battle.observer.stop = lambda: None
    steps, accepted, saved, sessions = [], [], {}, []
    remaining = iter(actions)

    class OwnedSession(CollectionSession):
        def __init__(self, *_args, **kwargs):
            super().__init__(None, None, **kwargs)
            sessions.append(self)

        def enable_lifecycle(self, runtime):
            super().enable_lifecycle(runtime)
            battle.runtime = runtime

        def connect(self):
            self.observer = battle.observer
            self.event_status, self.event_error = 'ready', None
            self.capture = LiveEventCapture(self.collector, self.observer, audit=self.audit)

        def read(self):
            if steps:
                steps[-1]['event_end'] = worker.events.qsize()
            action = next(remaining, 'stop')
            step = dict(action=action, event_start=steps[-1]['event_end'] if steps else 0,
                        session_sequence_before=self.runtime.sessions.sequence)
            steps.append(step)
            if action == 'stop':
                worker.stop.set()
                raise CaptureRetry('owned finite input complete')
            if action == 'defense_baseline':
                battle.advance(0)
            elif action == 'defense_hits':
                battle.advance(1)
                battle.advance(1, [battle.hit(i, i, i * 100) for i in range(1, 5)])
            elif action == 'deferred_terminal':
                old_scope = battle.scope()
                battle.advance(2, state=3, phase=8, epoch=2)
                battle.advance(2, [battle.hit(4 + i, i, 10, scope=old_scope, phase=4)
                                   for i in range(1, 5)])
            elif action == 'assist_busy':
                battle.advance(0, state=2, phase=5, epoch=3)
                fail_batch[0] = True
            elif action == 'ordinary_retry':
                # No new native pair was proved. The previous phase is retained.
                raise CaptureRetry('ordinary unproven clock race')
            elif action == 'unknown_assist':
                battle.advance(2, state=2, phase=5, epoch=3)
                battle.probe.observer_mode = 'unknown'
            elif action == 'assist_baseline':
                battle.advance(1, state=2, phase=5, epoch=3)
            elif action == 'assist_hits':
                battle.advance(2)
                first = len(battle.records) + 1
                battle.advance(2, [battle.hit(first + i - 1, i, i * 1000) for i in range(1, 5)])
            elif action == 'prepare':
                battle.advance(0, state=2, phase=3, epoch=4)
            elif action == 'late_terminal':
                result = saved['terminal']
                step.update(returned=result, returned_hash=digest(result), delayed=True)
                return result
            elif action == 'next_defense_baseline':
                battle.advance(0, state=2, phase=4, epoch=5, native_round=5)
            elif action in ('next_defense_hits', 'next_defense_zero_hits'):
                battle.advance(1)
                first = len(battle.records) + 1
                amount = 0 if action == 'next_defense_zero_hits' else 7
                battle.advance(1, [battle.hit(first + i - 1, i, i * amount) for i in range(1, 5)])
            elif action == 'absent':
                battle.active = False
            elif action == 'new_match_prepare':
                for i, player in enumerate(battle.players, 1):
                    player['uid'] = f'07100000{i}'
                battle.advance(0, state=2, phase=3, epoch=6, native_round=1)
            else:
                raise AssertionError(action)
            try:
                result = super().read()
            except CaptureRetry:
                step.update(session_sequence_after=self.runtime.sessions.sequence)
                raise
            finally:
                if action == 'unknown_assist':
                    battle.probe.observer_mode = 'valid'
            accepted.append((action, result, digest(result)))
            step.update(returned=result, returned_hash=digest(result),
                        session_sequence_after=self.runtime.sessions.sequence)
            if action == 'deferred_terminal':
                saved['terminal'] = result
                raise CaptureRetry('owned accepted terminal publication delayed')
            return result

    monkeypatch.setattr(bridge, 'CollectionSession', OwnedSession)
    config = ConnectionConfig(platform='native_pc', native_game_root='owned-offline-PC')
    state.effective_connection = config
    revision = worker.request(config, True)
    state.expect_connection_request(config, revision)
    worker.run()
    events = list(worker.events.queue)
    if steps:
        steps[-1]['event_end'] = len(events)
    assert len(sessions) == 1 and worker.pc_runtime is None
    assert not [(kind, value) for kind, value in events if kind in ('error', 'worker_error')]
    snapshots = {}
    for step in steps:
        if settings_by_action and step['action'] in settings_by_action:
            state.command(dict(version=1, op='multiplayer_enabled', enabled=settings_by_action[step['action']]))
        for kind, value in events[step['event_start']:step['event_end']]:
            state.accept(kind, value)
        snapshots[step['action']] = state.snapshot()
    for _, raw, original in accepted:
        assert digest(raw) == original
    return state, battle, steps, events, snapshots, accepted


def view_values(snapshot):
    return [entry['value'] for entry in snapshot['panels'][0]['entries']]


def four_values(snapshot):
    return [[entry['value'] for entry in player['view']['entries']]
            for player in snapshot['multiplayer']['players']]


def assert_retained(snapshot, scope, values=(100, 200, 300, 400)):
    assert view_values(snapshot) == [values[2]]
    multiplayer = snapshot['multiplayer']
    assert multiplayer['scope_kind'] == 'defense' and multiplayer['scope'] == scope
    assert multiplayer['available'] is True and multiplayer['previous_defense'] is True
    assert multiplayer['note']
    assert four_values(snapshot) == [[amount] for amount in values]
    for panel in snapshot['panels']:
        assert len(panel['entries']) == 1
        entry, = panel['entries']
        assert entry['owner_id'] == f'{LEADING_UIDS[2]}:{CID}#301'
        assert entry['player_uid'] == LEADING_UIDS[2]


def assert_assist_waiting(snapshot):
    assert view_values(snapshot) == []
    assert '当前协防战场' in snapshot['status_text'] and '等待数据' in snapshot['status_text']
    payload = snapshot['multiplayer']
    assert payload is None or payload.get('scope_kind') == 'assist' or not payload.get('available')


@pytest.mark.parametrize('initial', (False, True))
def test_multiplayer_setting_latches_at_next_combat_and_preserves_retained_defense(tmp_path, monkeypatch, initial):
    old_mode, new_mode = ('all', 'local') if initial else ('local', 'all')
    state, battle, _, _, views, _ = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'ordinary_retry', 'assist_busy', 'assist_baseline',
         'assist_hits', 'prepare', 'next_defense_baseline', 'next_defense_hits'],
        multiplayer_enabled=initial, mode_by_epoch={1: old_mode, 3: new_mode, 5: new_mode},
        settings_by_action={'ordinary_retry': not initial})
    assert views['defense_hits']['multiplayer_active'] is initial
    assert view_values(views['defense_hits']) == [300]
    pending = views['ordinary_retry']
    assert pending['layout']['multiplayer_enabled'] is not initial
    assert pending['multiplayer_active'] is initial and pending['multiplayer_mode_pending'] is True
    assert pending['panels'] == views['defense_hits']['panels']
    assert_assist_waiting(views['assist_busy'])
    assert views['assist_busy']['multiplayer_active'] is not initial
    assert views['assist_busy']['multiplayer_mode_pending'] is False
    expected_assist = [3000] if initial else [4000, 3000, 2000, 1000]
    assert view_values(views['assist_hits']) == expected_assist
    assert view_values(views['prepare']) == [300]
    assert '上一场防御' in views['prepare']['status_text']
    assert views['prepare']['multiplayer'] is None
    assert view_values(views['next_defense_hits']) == [21]
    assert views['next_defense_hits']['multiplayer_active'] is not initial
    assert (views['next_defense_hits']['multiplayer'] is not None) is not initial
    assert state.last is not None and battle.commits[-1] == 12


def test_guarded_assist_without_an_accepted_sample_restores_defense_on_prepare(tmp_path, monkeypatch):
    state, battle, steps, events, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'assist_busy', 'ordinary_retry', 'prepare'])
    defense = next(raw for action, raw, _ in accepted if action == 'defense_hits')
    assert not [raw for action, raw, _ in accepted if action == 'assist_busy']
    busy = next(step for step in steps if step['action'] == 'assist_busy')
    assert busy['session_sequence_before'] == busy['session_sequence_after']
    busy_events = events[busy['event_start']:busy['event_end']]
    assert not [value for kind, value in busy_events if kind == 'sample']
    scenes = [value for kind, value in busy_events if kind == 'autochess_scene']
    assert scenes and scenes[-1].phase == 5
    for action in ('assist_busy', 'ordinary_retry'):
        assert_assist_waiting(views[action])
    assert not [raw for _, raw, _ in accepted if raw['multiplayer']['scope_kind'] == 'assist']
    assert_retained(views['prepare'], defense['multiplayer']['scope'])


def test_accepted_assist_stays_its_own_scene_then_preparing_restores_owned_defense(tmp_path, monkeypatch):
    state, _, _, _, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'assist_busy', 'assist_baseline', 'assist_hits', 'prepare'])
    defense = next(raw for action, raw, _ in accepted if action == 'defense_hits')
    current = next(raw for action, raw, _ in accepted if action == 'assist_hits')
    assert current['multiplayer']['scope_kind'] == 'assist'
    assert [p['observed_damage'] for p in current['multiplayer']['players']] == [1000, 2000, 3000, 4000]
    assert sorted(view_values(views['assist_hits'])) == [1000, 2000, 3000, 4000]
    assert views['assist_hits']['panels'][0]['scope_label'] == '当前协防战场'
    assert views['assist_hits']['multiplayer']['scope_kind'] == 'assist'
    assert_retained(views['prepare'], defense['multiplayer']['scope'])
    assert state.last is current  # Retention is a view, not rewriting the accepted sample.
    archived, = state.history.get(current['battle_id']).records
    assert archived.last_sample['multiplayer']['scope'] == current['multiplayer']['scope']
    assert archived.last_sample['multiplayer']['scope_kind'] == 'assist'
    assert [p['observed_damage'] for p in archived.last_sample['multiplayer']['players']] == [1000, 2000, 3000, 4000]
    assert state.records[current['battle_id']].to_dict() == archived.to_dict()


def test_unknown_native_context_does_not_promote_unproved_assist_or_erase_defense(tmp_path, monkeypatch):
    _, _, steps, events, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'unknown_assist'])
    step = next(step for step in steps if step['action'] == 'unknown_assist')
    delivered = events[step['event_start']:step['event_end']]
    assert not [value for kind, value in delivered if kind == 'sample']
    assert not [value for kind, value in delivered
                if kind == 'autochess_scene' and value is not None and value.phase == 5]
    assert not [raw for action, raw, _ in accepted if action == 'unknown_assist']
    assert view_values(views['unknown_assist']) == [300]
    assert four_values(views['unknown_assist']) == [[100], [200], [300], [400]]


def test_same_scope_late_terminal_improves_retained_defense_without_switching_scene(tmp_path, monkeypatch):
    _, _, _, _, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'deferred_terminal', 'assist_busy', 'prepare', 'late_terminal'])
    terminal = next(raw for action, raw, _ in accepted if action == 'deferred_terminal')
    assert terminal['statistics']['ended'] and terminal['autochess_context']['phase'] == 8
    assert_retained(views['late_terminal'], terminal['multiplayer']['scope'], (110, 210, 310, 410))


def test_new_defense_keeps_prior_result_until_its_first_settlement(tmp_path, monkeypatch):
    _, _, _, events, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'assist_busy', 'assist_baseline', 'prepare',
         'next_defense_baseline', 'next_defense_hits'])
    old = next(raw for action, raw, _ in accepted if action == 'defense_hits')
    new = next(raw for action, raw, _ in accepted if action == 'next_defense_baseline')
    assert_retained(views['prepare'], old['multiplayer']['scope'])
    snapshot = views['next_defense_baseline']
    assert new['multiplayer']['scope'] != old['multiplayer']['scope']
    assert_retained(snapshot, old['multiplayer']['scope'])
    assert '等待本轮防御数据' in snapshot['status_text']
    assert view_values(views['next_defense_hits']) == [21]
    assert four_values(views['next_defense_hits']) == [[7], [14], [21], [28]]
    assert views['next_defense_hits']['multiplayer']['scope'] == new['multiplayer']['scope']
    assert not views['next_defense_hits']['multiplayer'].get('previous_defense')
    # A valid scene marker is sent ahead of the corresponding accepted sample.
    index = next(i for i, (kind, value) in enumerate(events) if kind == 'sample' and value is new)
    last_scene = [value for kind, value in events[:index] if kind == 'autochess_scene'][-1]
    assert last_scene.phase == 4 and last_scene.round == 5
    assert last_scene.scope == new['multiplayer']['scope']


def test_real_zero_settlement_is_useful_and_replaces_prior_defense(tmp_path, monkeypatch):
    _, _, _, _, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'prepare', 'next_defense_baseline',
         'next_defense_zero_hits'])
    old = next(raw for action, raw, _ in accepted if action == 'defense_hits')
    assert_retained(views['next_defense_baseline'], old['multiplayer']['scope'])
    current = views['next_defense_zero_hits']['multiplayer']
    assert current['scope'] != old['multiplayer']['scope']
    assert not current.get('previous_defense')
    assert [p['observed_damage'] for p in current['players']] == [0, 0, 0, 0]
    raw = next(raw for action, raw, _ in accepted if action == 'next_defense_zero_hits')
    assert all(any(a['ledger']['nominal_hits'] > 0 for a in p['actor_ledgers'])
               for p in raw['multiplayer']['players'])


def test_guarded_absence_invalidates_defense_retention_instead_of_resurrecting_it(tmp_path, monkeypatch):
    state, _, _, events, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'deferred_terminal', 'assist_busy', 'prepare', 'absent'])
    assert any(kind == 'autochess_scene' and value is None for kind, value in events)
    assert view_values(views['absent']) == []
    terminal = next(raw for action, raw, _ in accepted if action == 'deferred_terminal')
    state.accept('sample', terminal)
    assert view_values(state.snapshot()) == []


def test_new_account_table_is_a_new_match_and_cannot_reuse_or_revive_prior_defense(tmp_path, monkeypatch):
    state, _, _, _, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'deferred_terminal', 'assist_busy', 'prepare',
         'new_match_prepare', 'late_terminal'])
    assert_retained(views['prepare'], accepted[1][1]['multiplayer']['scope'])
    assert view_values(views['new_match_prepare']) == []
    assert view_values(views['late_terminal']) == []
    assert state.last is None


def test_explicit_source_change_blocks_queued_evidence_and_clears_only_retention(tmp_path, monkeypatch):
    state, _, _, events, _, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'assist_busy', 'assist_baseline', 'assist_hits', 'prepare'])
    defense = next(raw for action, raw, _ in accepted if action == 'defense_hits')
    assist = next(raw for action, raw, _ in accepted if action == 'assist_hits')
    prepare_scene = next(value for kind, value in events
                         if kind == 'autochess_scene' and value is not None and value.phase == 3)
    original = digest(assist)
    config = replace(state.effective_connection, native_game_root='owned-new-source')
    state.expect_connection_request(config, 2)
    state.accept('autochess_scene', native_autochess_scene(defense))
    state.accept('sample', defense)  # Queued before the revision-2 FIFO marker.
    assert state._awaiting_source
    state.accept('connection_source', {'revision': 1, 'platform': 'native_pc'})
    state.accept('autochess_scene', native_autochess_scene(defense))
    assert state._awaiting_source
    state.accept('connection_source', {'revision': 2, 'platform': 'native_pc'})
    assert not state._awaiting_source
    state.accept('autochess_scene', native_autochess_scene(assist))
    state.accept('sample', assist)
    state.accept('autochess_scene', prepare_scene)
    snapshot = state.snapshot()
    assert view_values(snapshot) == []
    assert snapshot['multiplayer'].get('previous_defense') is not True
    assert digest(assist) == original
