"""Owned defense and assist data through the host and desktop projection."""

from copy import deepcopy
from dataclasses import replace

import pytest

from battle_history import BattleHistory
from battle_lifecycle import autochess_scene
from capture_battles import replay
from connection import ConnectionConfig
from desktop_bridge import DesktopState
from native_pc.internal_capture import CID, _builtin_fixture, run_internal_capture
from test_internal_capture import _fixture_file, native_host


TOKEN = 'token_10000_synthetic'


def defense_fixture(mode):
    fixture = _builtin_fixture('autochess')
    baseline, defense, original_terminal, absent = fixture['frames']
    base_context = baseline['mark']['native_autochess_context']
    base_context.update(collection_mode=mode, memberships=[*base_context['memberships'],
        dict(cid=TOKEN, data_uid=199, player_index=1, player_side=1,
             is_token=True, is_predefined=False, host_key=CID, host_uid=101),
        dict(cid=TOKEN, data_uid=399, player_index=3, player_side=3,
             is_token=True, is_predefined=False, host_key=CID, host_uid=301),
        dict(cid=TOKEN, data_uid=398, player_index=3, player_side=3,
             is_token=True, is_predefined=False, host_key=CID, host_uid=101),
    ])

    def event_scope(context):
        return {key: context[key] for key in ('protocol_version', 'status', 'verified_same_frame',
            'scope_key', 'phase', 'round', 'frame', 'game_time', 'collection_mode')}

    sample_hit = defense['events'][0]

    def hit(number, source, uid, damage, context):
        record = dict(sample_hit, sequence=number, source=source, amount=damage, nominal_amount=damage,
                      target_instance=f'controlled-target-{number}', target_uid=str(number),
                      frame=context['frame'], game_time=context['game_time'],
                      native_autochess_scope=event_scope(context))
        if uid is None:
            record.pop('source_character_uid')
        else:
            record['source_character_uid'] = uid
        return record

    def tick(seconds, sequence, *, assist=False, end=False):
        frame, state = seconds * 30, (3 if end else 2)
        snapshot = dict(original_terminal['snapshot'], game_time=seconds, frame=frame, state=state)
        lifecycle = dict(original_terminal['lifecycle'], state=state, metadata=dict(
            original_terminal['lifecycle']['metadata'], outcome='victory' if end else None))
        context = dict(base_context, game_time=seconds, frame=frame)
        if assist:
            context.update(phase=5, round=2, scope_key=base_context['scope_key'] + '0',
                memberships=[dict(member, player_side=1) if member['player_index'] == 3 else member
                             for member in base_context['memberships'] if member['data_uid'] != 398])
        snapshot['controlled_autochess_scope'] = {key: context[key] for key in ('scope_key', 'phase', 'round')}
        mark = dict(original_terminal['mark'], game_time=seconds, frame=frame,
                    sequence=sequence, epoch=sequence, native_autochess_context=context)
        return dict(lifecycle=lifecycle, snapshot=snapshot, roster=[], mark=mark, events=[]), context

    defense_tick, context = tick(1, 8)
    defense_tick['events'] = [hit(number, CID, 100 * number + 1, 100 * number, context)
                             for number in range(1, 5)] + [
        hit(5, TOKEN, 199, 15, context), hit(6, TOKEN, 399, 35, context),
        hit(7, TOKEN, 398, 800, context), hit(8, CID, None, 900, context),
    ]
    assist_baseline, _ = tick(2, 8, assist=True)
    assist_tick, assist = tick(3, 11, assist=True)
    old_scope = dict(context, frame=assist['frame'], game_time=assist['game_time'])
    assist_tick['events'] = [hit(9, CID, 301, 1000, old_scope),
                             hit(10, CID, 301, 50, assist), hit(11, CID, 101, 11, assist)]
    terminal, terminal_context = tick(4, 12, assist=True, end=True)
    terminal['events'] = [hit(12, TOKEN, 399, 25, terminal_context)]
    absent = dict(absent, mark=terminal['mark'], terminal_mark=terminal['mark'])
    baseline['snapshot']['controlled_autochess_scope'] = {
        key: base_context[key] for key in ('scope_key', 'phase', 'round')
    }
    fixture['frames'] = [baseline, defense_tick, assist_baseline, assist_tick, terminal, absent, absent]
    return fixture


def amounts(players):
    return [player['observed_damage'] for player in players]


@pytest.mark.parametrize('mode', ['all', 'local'])
def test_native_host_projects_four_accounts_verified_summons_and_assist_tail(native_host, tmp_path, mode):
    state_dir = tmp_path / 'state'
    state = DesktopState(state_dir)
    state.effective_connection = ConnectionConfig(platform='native_pc')
    state.layout = replace(state.layout, multiplayer_enabled=True)
    samples, views = [], []

    def publish(sample):
        samples.append(deepcopy(sample))
        state.accept('autochess_scene', autochess_scene(sample))
        state.accept('sample', sample)
        views.append(state.snapshot())

    fixture = defense_fixture(mode)
    result = run_internal_capture(native_host, _fixture_file(tmp_path / 'defense.json', fixture), state_dir,
                                  publish=publish)
    assert result['status'] == 'ok', result['last_sample']['event_error']
    assert [sample['frame'] for sample in samples] == [0, 30, 60, 90, 120]
    assert result['archives'] == 2

    defense = samples[1]['multiplayer']
    accounts = [str(910000000 + i) for i in range(1, 5)]
    assert [p['player_uid'] for p in defense['players']] == accounts
    assert amounts(defense['players']) == ([115, 200, 335, 400] if mode == 'all' else [None, None, 335, None])
    local = defense['players'][2]
    own = f'{accounts[2]}:{CID}#301'
    assert [actor['owner_id'] for actor in local['actor_ledgers']] == [own]
    assert local['actor_ledgers'][0]['ledger']['nominal_hits'] == 2
    assert not local['coverage_complete']
    assert [(item['data_uid'], item['reason']) for item in defense['unknown_memberships']] == [
        (398, 'token_host_unresolved'),
    ]
    assert [(item['first_sequence'], item['last_sequence'], item['reason'])
            for item in defense['unknown_attribution']] == [
        (7, 7, 'source_uid_unmapped'), (8, 8, 'source_uid_missing'),
    ]
    assert all(p['damage'] is None for p in defense['players'])

    live = views[1]
    assert {entry['owner_id'] for panel in live['panels'] for entry in panel['entries']} == {own}
    assert live['multiplayer_active'] is (mode == 'all')
    if mode == 'all':
        for index, player in enumerate(live['multiplayer']['players'], 1):
            actor, = player['view']['entries']
            assert actor['owner_id'] == f'{accounts[index - 1]}:{CID}#{100 * index + 1}'
            assert actor['value'] == (115, 200, 335, 400)[index - 1]
    else:
        assert live['multiplayer'] is None
        assert all(account['statistics_status'] == 'not_collected'
                   for account in defense['players'] if not account['is_local'])

    opening = samples[2]['multiplayer']
    assert opening['scope_kind'] == 'assist' and opening['native_round'] == 2
    assert opening['scope'] != defense['scope']
    assert amounts(opening['players']) == [None, None, None, None]
    assist = samples[3]['multiplayer']
    rejected, = assist['unknown_attribution']
    assert (rejected['first_sequence'], rejected['last_sequence'], rejected['reason']) == (
        9, 9, 'event_scope_mismatch',
    )
    assert assist['players'][0]['player_side'] == assist['players'][2]['player_side'] == 1
    assert [p['player_uid'] for p in assist['players']] == accounts
    assert amounts(assist['players']) == ([11, None, 50, None] if mode == 'all' else [None, None, 50, None])

    tail = result['last_sample']['multiplayer']
    assert tail['status'] == 'ended'
    assert result['last_sample']['event_watermark']['sequence'] == 12
    assert amounts(tail['players']) == ([11, None, 75, None] if mode == 'all' else [None, None, 75, None])
    assert tail['players'][2]['observed_recent_dps'] is None
    actors = {entry['owner_id'] for panel in views[-1]['panels'] for entry in panel['entries']}
    assert actors == ({f'{accounts[0]}:{CID}#101', own} if mode == 'all' else {own})
    history = BattleHistory(state_dir / 'history').list_recent().records
    assert len(history) == 2 and {record.battle_id for record in history} == set(result['battle_ids'])
    archived = {record.last_sample['multiplayer']['scope_kind']: record.last_sample['multiplayer']
                for record in history}
    assert amounts(archived['defense']['players']) == amounts(defense['players'])
    assert amounts(archived['assist']['players']) == amounts(tail['players'])
    assert replay(result['journal'])['archives'] == 2
