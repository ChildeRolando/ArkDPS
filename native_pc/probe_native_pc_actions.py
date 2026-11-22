"""Optional bounded manual-operation journal witness for the checked PC build.

BattleLogger timestamps use userFixedPlayTime and identify BattleCharacterData
UIDs. This journal covers recorded manual operations. It supplies no exact
logic frames, automatic-skill timeline, or damage/healing/shield events.
The caller owns the process access and outer battle/root/frame guard.
"""

import math
import struct

from . import probe_native_pc_rows as rows


LOGGER_LOGS = 0x20
RECORD_SIZE = 48
MAX_RECENT_RECORDS = 64
OPERATION_NAMES = {0: 'spawn', 1: 'withdraw', 2: 'manual_skill'}


def capture_actions(read, context):
    """Read a stable recent tail through the caller's bounded read function."""
    logger = context['battle_logger']
    known = set()
    try:
        rows.expect_class(read, logger, b'BattleLogger', known)
        journal = rows.pointer(rows.read_uint64(read, logger + LOGGER_LOGS))
        rows.expect_class(read, journal, b'List`1', known)
        header = read(journal + rows.LIST_HEADER, 16)
        array, count, _version = struct.unpack('<Qii', header)
        rows.require(count >= 0, 'manual_action_list_size')
        recent_count = min(count, MAX_RECENT_RECORDS)
        first_index = count - recent_count
        capacity_raw = None
        tail_address = None
        tail = b''
        if count:
            array = rows.pointer(array)
            capacity_raw = read(array + rows.ARRAY_LENGTH, 8)
            capacity, = struct.unpack('<Q', capacity_raw)
            rows.require(capacity >= count, 'short_manual_action_array')
            tail_address = rows.pointer(array + rows.ARRAY_DATA + first_index * RECORD_SIZE)
            tail = read(tail_address, recent_count * RECORD_SIZE)
        records = []
        skipped = 0
        for index in range(recent_count):
            offset = index * RECORD_SIZE
            operation, = struct.unpack_from('<i', tail, offset + 0x18)
            if operation not in OPERATION_NAMES:
                skipped += 1
                continue
            timestamp, = struct.unpack_from('<f', tail, offset)
            rows.require(math.isfinite(timestamp) and timestamp >= 0,
                         'invalid_manual_action_time')
            data_uid, = struct.unpack_from('<I', tail, offset + 8)
            actor_pointer = rows.pointer(struct.unpack_from('<Q', tail, offset + 0x10)[0])
            actor_id = rows.read_actor_id(read, actor_pointer, known)
            records.append({
                'journal_index': first_index + index,
                'operation': OPERATION_NAMES[operation],
                'user_game_time': timestamp,
                'data_unique_id': data_uid,
                'id': actor_id,
            })
        rows.require(
            rows.read_uint64(read, logger + LOGGER_LOGS) == journal
            and read(journal + rows.LIST_HEADER, 16) == header
            and (capacity_raw is None
                 or (read(array + rows.ARRAY_LENGTH, 8) == capacity_raw
                     and read(tail_address, len(tail)) == tail)),
            'manual_actions_changed_during_read')
    except rows.RowLayoutError as error:
        return {'actions_status': 'unavailable', 'actions_reason': str(error), 'actions': None}
    return {
        'actions_status': 'available',
        'actions': {
            'scope': 'manual_spawn_withdraw_skill_journal',
            'timestamp_basis': 'BattleController.userFixedPlayTime',
            'exact_frames_available': False,
            'total_journal_records': count,
            'scanned_tail_records': recent_count,
            'tail_start_index': first_index,
            'tail_truncated': first_index > 0,
            'skipped_other_operation_records': skipped,
            'records': records,
        },
    }
