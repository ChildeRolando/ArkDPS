import struct

import pytest

from acceptance import probe_native_pc_actions as actions


class Memory:
    def __init__(self, records):
        self.base = 0x10000
        self.data = bytearray(0x8000)
        self.logger = self.base
        self.journal = self.base + 0x100
        self.array = self.base + 0x1000
        self.actor = self.base + 0x6000
        self.reads = []
        for address, klass, name in (
            (self.logger, self.base + 0x400, b'BattleLogger'),
            (self.journal, self.base + 0x500, b'List`1'),
            (self.actor, self.base + 0x600, b'String'),
        ):
            self.put(address, '<Q', klass)
            self.put(klass + 0x10, '<Q', klass + 0x30)
            self.put(klass + 0x30, f'<{len(name) + 1}s', name + b'\0')
        self.put(self.logger + actions.LOGGER_LOGS, '<Q', self.journal)
        self.put(self.journal + 0x10, '<Qii', self.array, len(records), 3)
        self.put(self.array + 0x18, '<Q', len(records))
        self.set_actor('char_001')
        for index, (operation, timestamp, uid) in enumerate(records):
            self.put(self.record(index), '<f4xI4xQi', timestamp, uid, self.actor, operation)
            # Extra payload is deliberately invalid and must never be followed.
            self.put(self.record(index) + 0x28, '<Q', 1)

    def put(self, address, encoding, *values):
        struct.pack_into(encoding, self.data, address - self.base, *values)

    def set_actor(self, value):
        raw = value.encode('utf-16-le')
        self.put(self.actor + 0x10, '<i', len(value))
        self.put(self.actor + 0x14, f'<{len(raw)}s', raw)

    def record(self, index):
        return self.array + 0x20 + actions.RECORD_SIZE * index

    def read(self, address, size):
        self.reads.append((address, size))
        assert 0 < size <= 4096
        start = address - self.base
        assert 0 <= start <= start + size <= len(self.data)
        return bytes(self.data[start:start + size])

    def capture(self, read=None):
        return actions.capture_actions(read or self.read, {'battle_logger': self.logger})


def test_preserves_native_manual_operations_data_uid_and_time_basis():
    mem = Memory([(0, 1.25, 11), (1, 2.5, 11), (2, 3.5, 12)])
    result = mem.capture()
    assert result['actions_status'] == 'available'
    history = result['actions']
    assert history['timestamp_basis'] == 'BattleController.userFixedPlayTime'
    assert history['exact_frames_available'] is False
    assert history['tail_truncated'] is False
    assert history['records'] == [
        {'journal_index': 0, 'operation': 'spawn', 'user_game_time': 1.25,
         'data_unique_id': 11, 'id': 'char_001'},
        {'journal_index': 1, 'operation': 'withdraw', 'user_game_time': 2.5,
         'data_unique_id': 11, 'id': 'char_001'},
        {'journal_index': 2, 'operation': 'manual_skill', 'user_game_time': 3.5,
         'data_unique_id': 12, 'id': 'char_001'},
    ]


def test_reads_only_latest_64_records_and_reports_tail_truncation():
    mem = Memory([(0, float(index), index) for index in range(90)])
    history = mem.capture()['actions']
    assert history['total_journal_records'] == 90
    assert history['scanned_tail_records'] == 64
    assert history['tail_start_index'] == 26
    assert history['tail_truncated'] is True
    assert len(history['records']) == 64
    assert history['records'][0]['journal_index'] == 26
    assert history['records'][-1]['journal_index'] == 89
    tail_reads = [(address, size) for address, size in mem.reads if size == 64 * 48]
    assert tail_reads == [(mem.record(26), 3072), (mem.record(26), 3072)]


def test_unknown_operations_skip_actor_and_extra_payload_reads():
    mem = Memory([(3, float('nan'), 0), (99, float('nan'), 0)])
    mem.put(mem.record(0) + 0x10, '<Q', 1)
    mem.put(mem.record(1) + 0x10, '<Q', 1)
    history = mem.capture()['actions']
    assert history['records'] == []
    assert history['skipped_other_operation_records'] == 2
    assert not any(address == mem.actor or address == 1 for address, _ in mem.reads)


def test_empty_journal_does_not_require_an_array():
    mem = Memory([])
    mem.put(mem.journal + 0x10, '<Q', 0)
    result = mem.capture()
    assert result['actions_status'] == 'available'
    assert result['actions']['records'] == []
    assert result['actions']['total_journal_records'] == 0


@pytest.mark.parametrize('timestamp', [float('nan'), float('inf'), -1.0])
def test_invalid_allowed_operation_timestamp_keeps_actions_unknown(timestamp):
    result = Memory([(0, timestamp, 1)]).capture()
    assert result == {'actions_status': 'unavailable',
                      'actions_reason': 'invalid_manual_action_time', 'actions': None}


@pytest.mark.parametrize('mutation', ['pointer', 'header', 'capacity', 'tail'])
def test_racing_journal_is_rejected(mutation):
    mem = Memory([(0, 1.0, 1)])
    target = {
        'pointer': mem.logger + actions.LOGGER_LOGS,
        'header': mem.journal + 0x10,
        'capacity': mem.array + 0x18,
        'tail': mem.record(0),
    }[mutation]
    visits = 0

    def changing_read(address, size):
        nonlocal visits
        result = mem.read(address, size)
        if address == target:
            visits += 1
            if visits == 2:
                return bytes([result[0] ^ 1]) + result[1:]
        return result

    result = mem.capture(changing_read)
    assert result == {'actions_status': 'unavailable',
                      'actions_reason': 'manual_actions_changed_during_read', 'actions': None}


def test_short_array_is_rejected_before_tail_read():
    mem = Memory([(0, 1.0, 1)])
    mem.put(mem.array + 0x18, '<Q', 0)
    result = mem.capture()
    assert result['actions_status'] == 'unavailable'
    assert result['actions_reason'] == 'short_manual_action_array'
    assert not any(address == mem.record(0) for address, _ in mem.reads)


def test_invalid_actor_id_keeps_actions_unknown():
    mem = Memory([(0, 1.0, 1)])
    mem.set_actor('account@example.com')
    result = mem.capture()
    assert result['actions_status'] == 'unavailable'
    assert result['actions_reason'] == 'counter_id_format'
