import struct

from acceptance import probe_native_pc_roster as roster


class Memory:
    def __init__(self):
        self.objects = bytearray(0x3000)

    def write(self, address, encoding, *items):
        struct.pack_into(encoding, self.objects, address - 0x10000, *items)

    def read(self, address, size):
        start = address - 0x10000
        assert 0 <= start <= start + size <= len(self.objects)
        return bytes(self.objects[start:start + size])

    def text(self, address, value):
        self.write(address, '<Q8xi', 0x10c00, len(value))
        raw = value.encode('utf-16-le')
        self.write(address + 0x14, f'<{len(raw)}s', raw)


def prepared_roster():
    mem = Memory()
    for klass, name in ((0x10900, b'List`1'), (0x10a00, b'BattlePlayerData'),
                        (0x10b00, b'BattleCharacterData'), (0x10c00, b'String')):
        mem.write(klass + 0x10, '<Q', klass + 0x30)
        mem.write(klass + 0x30, f'<{len(name) + 1}s', name + b'\0')
    mem.write(0x10018, '<i', 1)
    mem.write(0x10170, '<Q', 0x10200)
    mem.write(0x10200, '<Q8xQii', 0x10900, 0x10300, 1, 3)
    mem.write(0x10318, '<QQ', 4, 0x10400)
    mem.write(0x10400, '<Q8xi4xQQ', 0x10a00, 1, 0x10500, 0x10600)
    mem.write(0x10518, '<QQ', 1, 0x10700)
    mem.write(0x10618, '<QQ', 1, 0x10800)
    mem.write(0x10700, '<Q', 0x10b00)
    mem.write(0x10750, '<Q20xI', 0x11000, 120)
    mem.write(0x10800, '<Q', 0x10b00)
    mem.write(0x10850, '<Q20xI32xBB6xQI', 0x11100, 121, 1, 0, 0x11000, 120)
    mem.text(0x11000, 'char_test')
    mem.text(0x11100, 'token_test')
    return mem


def test_rows_preserve_token_host_witnesses():
    mem = prepared_roster()
    result = roster.capture_roster(mem.read, {'battle_controller': 0x10000})
    assert result['roster_status'] == 'available'
    assert result['roster']['local_side'] == 1
    actor, token = result['roster']['entries']
    assert (actor['id'], actor['unique_id'], actor['is_token']) == ('char_test', 120, False)
    assert (token['id'], token['unique_id'], token['is_token']) == ('token_test', 121, True)
    assert (token['host_key'], token['host_uid']) == ('char_test', 120)


def test_unverified_roster_limit_keeps_data_unknown():
    mem = prepared_roster()
    mem.write(0x10618, '<Q', 65)
    result = roster.capture_roster(mem.read, {'battle_controller': 0x10000})
    assert result == {'roster_status': 'unavailable', 'roster_reason': 'roster_array_size', 'roster': None}
