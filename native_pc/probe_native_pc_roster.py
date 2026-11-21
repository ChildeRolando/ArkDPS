"""Read optional roster witnesses for source attribution on the fingerprinted PC build."""

import struct

from . import probe_native_pc_rows as rows


PLAYER_SIDE = 0x18
PLAYER_DATA_LIST = 0x170
MAX_PLAYERS = 4
MAX_PLAYER_ENTRIES = 64


def character_array(read, address, verify):
    if not address:
        return ()
    capacity = read(address + rows.ARRAY_LENGTH, 8)
    count, = struct.unpack('<Q', capacity)
    rows.require(count <= MAX_PLAYER_ENTRIES, 'roster_array_size')
    verify.append((address + rows.ARRAY_LENGTH, capacity))
    if not count:
        return ()
    raw = read(address + rows.ARRAY_DATA, count * 8)
    verify.append((address + rows.ARRAY_DATA, raw))
    return struct.unpack(f'<{count}Q', raw)


def character_entry(read, address, side, known):
    rows.expect_class(read, address, b'BattleCharacterData', known)
    data = read(address + 0x50, 0x54)
    key = rows.pointer(struct.unpack_from('<Q', data)[0])
    uid = struct.unpack_from('<I', data, 0x1C)[0]
    is_token, is_predefined = struct.unpack_from('<BB', data, 0x40)
    rows.require(is_token in (0, 1) and is_predefined in (0, 1), 'roster_character_flags')
    host_key, host_uid = struct.unpack_from('<QI', data, 0x48)
    return {
        'id': rows.read_actor_id(read, key, known), 'unique_id': uid,
        'is_token': bool(is_token), 'is_predefined': bool(is_predefined),
        'host_key': rows.read_actor_id(read, rows.pointer(host_key), known) if host_key and is_token else None,
        'host_uid': host_uid if is_token else None, 'side': side,
    }, data


def capture_roster(read, context):
    battle = context['battle_controller']
    known = set()
    try:
        side_raw = read(battle + PLAYER_SIDE, 4)
        side, = struct.unpack('<i', side_raw)
        players = rows.pointer(rows.read_uint64(read, battle + PLAYER_DATA_LIST))
        rows.expect_class(read, players, b'List`1', known)
        array, count, header = rows.checked_list(read, players, MAX_PLAYERS)
        rows.require(count > 0, 'roster_no_players')
        player_bytes = read(array + rows.ARRAY_DATA, count * 8)
        pointers = struct.unpack(f'<{count}Q', player_bytes)
        entries = []
        verify = [(array + rows.ARRAY_DATA, player_bytes)]
        for player in pointers:
            rows.expect_class(read, rows.pointer(player), b'BattlePlayerData', known)
            data = read(player + 0x10, 24)
            player_side, characters, tokens = struct.unpack('<i4xQQ', data)
            verify.append((player + 0x10, data))
            for source, group in (('characters', characters), ('tokens', tokens)):
                for address in character_array(read, rows.pointer(group) if group else 0, verify):
                    if not address:
                        continue
                    entry, raw = character_entry(read, rows.pointer(address), player_side, known)
                    entries.append({**entry, 'array': source})
                    verify.append((address + 0x50, raw))
        rows.require(read(players + rows.LIST_HEADER, 16) == header
                     and rows.read_uint64(read, battle + PLAYER_DATA_LIST) == players
                     and read(battle + PLAYER_SIDE, 4) == side_raw
                     and all(read(address, len(data)) == data for address, data in verify),
                     'roster_changed_during_read')
    except rows.RowLayoutError as error:
        return {'roster_status': 'unavailable', 'roster_reason': str(error), 'roster': None}
    return {'roster_status': 'available', 'roster': {'local_side': side, 'entries': entries}}
