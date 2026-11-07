import struct
import pytest

from roster import (BATTLE_CHARACTER_HOST_UID, BATTLE_CHARACTER_IS_TOKEN,
                    BATTLE_CHARACTER_IS_PREDEFINED,
                    BATTLE_CHARACTER_READ_SIZE, BATTLE_CHARACTER_UNIQUE_ID, BATTLE_CHARACTER_HOST_KEY,
                    BATTLE_PLAYER_DATA_SIDE, build_operations, decode_string,
                    parse_results, RosterUnavailable,
                    BATTLE_PLAYER_DATA_CHARACTERS, BATTLE_PLAYER_DATA_TOKENS)


def string_bytes(value):
    raw = value.encode('utf-16-le')
    return b'\x00' * 0x10 + struct.pack('<i', len(value)) + raw


def data_bytes(cid, uid, *, token=False, host_uid=0, predefined=False):
    raw = bytearray(BATTLE_CHARACTER_READ_SIZE)
    struct.pack_into('<I', raw, BATTLE_CHARACTER_UNIQUE_ID, uid)
    raw[BATTLE_CHARACTER_IS_TOKEN] = 1 if token else 0
    raw[BATTLE_CHARACTER_IS_PREDEFINED] = 1 if predefined else 0
    struct.pack_into('<I', raw, BATTLE_CHARACTER_HOST_UID, host_uid)
    return bytes(raw)


def populated_player(results, layout, characters=(), tokens=()):
    results[layout['player_items']] = struct.pack('<Q', 0x1000)
    results[layout['live'][0]['items']] = b''
    player = layout['players'][0]
    raw = bytearray(0x28)
    for group, values, offset in (('char', characters, BATTLE_PLAYER_DATA_CHARACTERS),
                                  ('token', tokens, BATTLE_PLAYER_DATA_TOKENS)):
        struct.pack_into('<Q', raw, offset, 0x2000)
        results[player[group + '_count']] = struct.pack('<i', len(values))
        results[player[group + '_items']] = b''.join(struct.pack('<Q', v) for v in values)
    results[player['player']] = bytes(raw)
    return player


def test_build_operations_indices_are_forward_references():
    ops, layout = build_operations(0x1000)
    assert len(ops) > 100
    for index, op in enumerate(ops):
        if op[0] == 'deref':
            assert 0 <= op[1] < index
        elif op[0] == 'list_items':
            assert 0 <= op[1] < index
        else:
            assert op[0] == 'direct'
    player = layout['players'][0]
    assert player['player'] > 0
    assert player['char_count'] > player['player']
    assert layout['live'][0]['items'] < layout['live'][0]['slots'][0][0]


def test_parse_results_maps_characters_tokens_and_presence():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout, (0x3000,), (0x4000,))
    data_idx, key_idx, host_idx = player['characters'][0]
    results[data_idx] = data_bytes('char_242_otter', 11)
    results[key_idx] = string_bytes('char_242_otter')
    data_idx, key_idx, host_idx = player['tokens'][0]
    results[data_idx] = data_bytes('token_otter', 12, token=True, host_uid=11)
    results[key_idx] = string_bytes('token_otter')
    results[host_idx] = string_bytes('char_242_otter')
    live = layout['live'][0]
    results[live['items']] = struct.pack('<Q', 0x2000)
    results[live['slots'][0][0]] = data_bytes('char_242_otter', 11)
    snapshot = parse_results(results, layout, frame=77)
    assert snapshot.frame == 77
    assert {(e.cid, e.is_token, e.host_uid) for e in snapshot.entries} == {
        ('char_242_otter', False, None), ('token_otter', True, 11)}
    assert snapshot.present_unique_ids == frozenset({11})
    assert snapshot.present_known is True


def test_parse_results_rejects_garbled_key():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout, (0x3000,))
    data_idx, key_idx, host_idx = player['characters'][0]
    results[data_idx] = data_bytes('char_a', 1)
    results[key_idx] = b'not-an-il2cpp-string'
    with pytest.raises(RosterUnavailable):
        parse_results(results, layout, frame=1)


def test_parse_results_honours_array_length():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout)
    data_idx, key_idx, host_idx = player['characters'][0]
    results[data_idx] = data_bytes('char_a', 1)
    results[key_idx] = string_bytes('char_a')
    assert parse_results(results, layout, frame=1).entries == ()


def test_decode_string_validation():
    assert decode_string(string_bytes('char_242_otter')) == 'char_242_otter'
    assert decode_string(None) is None
    assert decode_string(b'\x00' * 20) is None
    assert decode_string(b'\x00' * 0x10 + struct.pack('<i', 2)
                         + '[]'.encode('utf-16-le')) is None


def test_occupied_live_slot_failure_is_rejected_but_empty_list_is_known():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    results[layout['player_items']] = b''
    live = layout['live'][1]
    results[live['items']] = struct.pack('<Q', 0x2000)
    with pytest.raises(RosterUnavailable):
        parse_results(results, layout, frame=1)
    results[live['slots'][0][0]] = b'\x00' * 4
    with pytest.raises(RosterUnavailable):
        parse_results(results, layout, frame=1)
    results[live['items']] = b''
    assert parse_results(results, layout, frame=1).present_known is True
    results[live['items']] = struct.pack('<Q', 0)
    assert parse_results(results, layout, frame=1).present_known is True
    results[live['items']] = b'\x01'
    with pytest.raises(RosterUnavailable):
        parse_results(results, layout, frame=1)


@pytest.mark.parametrize('failure', ['data', 'short_data', 'key', 'invalid_key', 'lists'])
def test_unread_live_npc_cannot_hide_shared_key_conflict(failure):
    from snapshot_adapter import BattleStatisticsAdapter, build_owner_map

    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout, (0x100,))
    data, key, _ = player['characters'][0]
    results[data] = data_bytes('char_a', 100)
    results[key] = string_bytes('char_a')
    live = layout['live'][1]
    results[live['items']] = b''
    adapter = BattleStatisticsAdapter()
    sample = {'session': 'battle', 'frame': 1, 'game_time': 0, 'rows': [
        {'id': 'char_a', 'damage': 100, 'healing': 0}]}
    adapter.update(sample, parse_results(results, layout, 1))
    before = adapter.result()

    results[live['items']] = struct.pack('<Q', 0x200)
    data, _, key = live['slots'][0]
    results[data] = data_bytes('char_a', 1, predefined=True)
    results[key] = string_bytes('char_a')
    assert 'char_a' in build_owner_map(parse_results(results, layout, 2)).ambiguous_cids
    if failure == 'lists':
        for group in layout['live']:
            results[group['items']] = None
    else:
        results[data if failure in ('data', 'short_data') else key] = (
            b'bad' if failure in ('short_data', 'invalid_key') else None)
    with pytest.raises(RosterUnavailable):
        adapter.update(dict(sample, frame=2, game_time=1, rows=[
            {'id': 'char_a', 'damage': 500, 'healing': 0}]),
            parse_results(results, layout, 2))
    assert adapter.result() == before


def test_live_predefined_character_maps_itself_and_keeps_total_after_removal():
    from snapshot_adapter import BattleStatisticsAdapter

    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    populated_player(results, layout)
    live = layout['live'][1]
    results[live['items']] = struct.pack('<QQ', 0x2000, 0x3000)
    for i, (cid, token) in enumerate((('char_map', False), ('token_map', True))):
        data, _, key = live['slots'][i]
        results[data] = data_bytes(cid, i + 1, token=token, predefined=True)
        results[key] = string_bytes(cid)
    adapter = BattleStatisticsAdapter()
    sample = {'session': 'map', 'frame': 1, 'game_time': 0, 'rows': [
        {'id': 'char_map', 'damage': 373, 'healing': 0},
        {'id': 'token_map', 'damage': 14, 'healing': 0}]}
    adapter.update(sample, parse_results(results, layout, 1))
    observed = adapter.result()
    assert observed['rows']['char_map']['sources'] == ['char_map']
    assert observed['rows']['char_map']['present'] is True
    assert observed['unknown_present_unique_ids'] == [2]
    assert observed['unknown_sources'] == ['token_map']

    results[live['items']] = b''
    adapter.update(dict(sample, frame=2, game_time=1), parse_results(results, layout, 2))
    absent = adapter.result()['rows']['char_map']
    assert absent['damage'] == 373
    assert absent['present'] is False


@pytest.mark.parametrize('failure', ['data', 'key', 'count', 'items'])
def test_incomplete_conflicting_roster_cannot_publish_shared_counter(failure):
    from snapshot_adapter import BattleStatisticsAdapter

    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout, (0x100, 0x200), (0x300, 0x400))
    for index, cid in enumerate(('char_a', 'char_b')):
        data, key, _ = player['characters'][index]
        results[data] = data_bytes(cid, index + 1)
        results[key] = string_bytes(cid)
    for index, host in enumerate(('char_a', 'char_b')):
        data, key, host_key = player['tokens'][index]
        raw = bytearray(data_bytes('token_a', index + 3, token=True, host_uid=index + 1))
        struct.pack_into('<Q', raw, BATTLE_CHARACTER_HOST_KEY, 0x9000)
        results[data] = bytes(raw)
        results[key] = string_bytes('token_a')
        results[host_key] = string_bytes(host)

    sample = {'session': 'battle', 'frame': 1, 'game_time': 0, 'rows': [
        {'id': 'char_a', 'damage': 100, 'healing': 0},
        {'id': 'token_a', 'damage': 500, 'healing': 0}]}
    adapter = BattleStatisticsAdapter()
    adapter.update(sample, parse_results(results, layout, 1))
    assert adapter.result()['rows']['char_a']['damage'] == 100
    assert adapter.result()['ambiguous_sources'] == ['token_a']
    before = adapter.result()
    broken = list(results)
    if failure in ('data', 'key'):
        broken[player['tokens'][1][0 if failure == 'data' else 1]] = None
    else:
        broken[player['token_' + failure]] = None
    with pytest.raises(RosterUnavailable):
        adapter.update(dict(sample, frame=2, game_time=1), parse_results(broken, layout, 2))
    assert adapter.result() == before


def test_empty_slot_is_distinct_from_unread_occupied_slot():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    populated_player(results, layout, (0,))
    assert parse_results(results, layout, 1).entries == ()


def test_unread_host_key_is_rejected_but_null_host_stays_unknown():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = populated_player(results, layout, (), (0x3000,))
    data, key, _ = player['tokens'][0]
    raw = bytearray(data_bytes('token_a', 3, token=True, host_uid=1))
    results[key] = string_bytes('token_a')
    results[data] = bytes(raw)
    assert parse_results(results, layout, 1).entries[0].host_key is None
    struct.pack_into('<Q', raw, BATTLE_CHARACTER_HOST_KEY, 0x9000)
    results[data] = bytes(raw)
    with pytest.raises(RosterUnavailable):
        parse_results(results, layout, 1)
