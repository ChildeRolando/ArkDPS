import struct

from roster import (BATTLE_CHARACTER_HOST_UID, BATTLE_CHARACTER_IS_TOKEN,
                    BATTLE_CHARACTER_READ_SIZE, BATTLE_CHARACTER_UNIQUE_ID,
                    BATTLE_PLAYER_DATA_SIDE, build_operations, decode_string,
                    parse_results)


def string_bytes(value):
    raw = value.encode('utf-16-le')
    return b'\x00' * 0x10 + struct.pack('<i', len(value)) + raw


def data_bytes(cid, uid, *, token=False, host_uid=0):
    raw = bytearray(BATTLE_CHARACTER_READ_SIZE)
    struct.pack_into('<I', raw, BATTLE_CHARACTER_UNIQUE_ID, uid)
    raw[BATTLE_CHARACTER_IS_TOKEN] = 1 if token else 0
    struct.pack_into('<I', raw, BATTLE_CHARACTER_HOST_UID, host_uid)
    return bytes(raw)


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
    player = layout['players'][0]
    raw_player = bytearray(0x28)
    struct.pack_into('<i', raw_player, BATTLE_PLAYER_DATA_SIDE, 0)
    results[player['player']] = bytes(raw_player)
    results[player['char_count']] = struct.pack('<i', 1)
    results[player['token_count']] = struct.pack('<i', 1)
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


def test_parse_results_skips_garbled_key_and_missing_data():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = layout['players'][0]
    results[player['player']] = b'\x00' * 0x28
    results[player['char_count']] = struct.pack('<i', 1)
    results[player['token_count']] = struct.pack('<i', 0)
    data_idx, key_idx, host_idx = player['characters'][0]
    results[data_idx] = data_bytes('char_a', 1)
    results[key_idx] = b'not-an-il2cpp-string'
    snapshot = parse_results(results, layout, frame=1)
    assert snapshot.entries == ()
    assert snapshot.present_known is False


def test_parse_results_honours_array_length():
    ops, layout = build_operations(0x1000)
    results = [None] * len(ops)
    player = layout['players'][0]
    results[player['player']] = b'\x00' * 0x28
    results[player['char_count']] = struct.pack('<i', 0)
    results[player['token_count']] = struct.pack('<i', 0)
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
