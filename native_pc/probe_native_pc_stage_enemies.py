"""Current-stage database-reference witnesses for the accepted Windows build.

Use only inside NativePcBattleProbe.sample's same-frame extra_reader, with
context['level_data'] set to its checked LevelData root. The outer probe owns
the build/process/clock and BattleController.m_levelData guards. No acquisition
occurs at import. This module reads no protected numeric values.

Disk-only verification, 2026-11-21:
GameAssembly.dll SHA-256
cf7956bf88a61986859ef55c026d231713e2d12cf69ffd9a19779ab491cf76ff;
global-metadata.dat SHA-256
e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5.
Metadata v29 has 92-byte typedefs; strings/fields/typedef header offsets are
0x18/0x60/0xA0. DLL field-offset/type-pointer tables: RVA0x87CE000/0x7F16AC0.
LevelData typedef4755/count23: field13 enemyDbRefs+0x78 has type34969,
SZARRAY(0x1D), with CLASS(0x12) element typedef4733. Thus its slots contain
references, not inline structs. EnemyDataDbReference typedef4733/count4 has
useDb+0x10 BOOL, id+0x18 STRING, level+0x20 I4, overwrittenData+0x28 CLASS.
FieldInfo and Il2CppType runtime prefixes are checked and reread below.

A reference records stage configuration. It is neither a per-instance dynamic
attribute snapshot nor proof that an external catalog matches these resources.
Only the presence of overwrittenData is inspected; its contents stay unread.
"""

import struct

from . import probe_native_pc_presence as presence


MAX_REFS = 32
MAX_READ_CALLS = 512
MAX_READ_BYTES = 64 * 1024
MAX_VARIANT_LEVEL = 1024
LEVEL_REFS = 0x78
TYPE_BOOL = 0x02
TYPE_I4 = 0x08
TYPE_STRING = 0x0E
TYPE_CLASS = 0x12
TYPE_SZARRAY = 0x1D
LEVEL_FIELDS = ((13, 'enemyDbRefs', LEVEL_REFS, TYPE_SZARRAY),)
REF_FIELDS = ((0, 'useDb', 0x10, TYPE_BOOL), (1, 'id', 0x18, TYPE_STRING),
              (2, 'level', 0x20, TYPE_I4), (3, 'overwrittenData', 0x28, TYPE_CLASS))


class StageRead(presence.CheckedRead):
    def read(self, address, size):
        presence.require(self.calls < MAX_READ_CALLS and
                         self.bytes_requested + size <= MAX_READ_BYTES,
                         'stage_enemy_refs_read_budget_exhausted')
        return super().read(address, size)

    def identity(self, klass):
        presence.pointer(klass)
        if klass not in self.known_classes:
            names = struct.unpack('<QQ', self.guarded(klass + 0x10, 16))
            parts = []
            for address in names:
                raw = self.guarded(address, 128)
                end = raw.find(b'\0')
                presence.require(end >= 0, 'stage_enemy_refs_metadata_name_length')
                try:
                    parts.append(raw[:end].decode('ascii', errors='strict'))
                except UnicodeDecodeError:
                    raise presence.PresenceError('stage_enemy_refs_metadata_name_encoding') from None
            self.known_classes[klass] = tuple(parts)
        return self.known_classes[klass]

    def typed_fields(self, klass, expected_count, fields):
        cache_key = klass, expected_count, fields
        if cache_key in self.known_fields:
            return
        count, = struct.unpack('<H', self.guarded(klass + presence.CLASS_FIELD_COUNT, 2))
        presence.require(count == expected_count, 'stage_enemy_refs_field_count')
        table = self.ref(klass + presence.CLASS_FIELDS, guard=True)
        for index, name, offset, code in fields:
            presence.require(0 <= index < count, 'stage_enemy_refs_field_index')
            name_ref, type_ref, owner, actual_offset, _ = struct.unpack(
                '<QQQiI', self.guarded(table + index * 32, 32))
            presence.require(owner == klass and actual_offset == offset and
                             self.guarded(name_ref, len(name) + 1) == name.encode('ascii') + b'\0',
                             'stage_enemy_refs_field_layout')
            type_data, bits = self.type_prefix(presence.pointer(type_ref))
            presence.require((bits >> 16) & 0xFF == code, 'stage_enemy_refs_field_type')
            # CLASS elements establish reference slots even for an empty array.
            if code == TYPE_SZARRAY:
                _, element_bits = self.type_prefix(presence.pointer(type_data))
                presence.require((element_bits >> 16) & 0xFF == TYPE_CLASS,
                                 'stage_enemy_refs_array_element_type')
        self.known_fields.add(cache_key)

    def type_prefix(self, address):
        return struct.unpack('<QI', self.guarded(address, 12))


def capture_stage_enemy_refs(read, context):
    """Read bounded stage references without selecting or changing a catalog.

    Duplicate keys are retained because different variants/overrides can make
    a key ambiguous. Missing roots or invalid entries never fabricate an empty
    reference list. The maximum supported variant is a fail-closed bound.
    """
    reader = StageRead(read)
    try:
        presence.require(isinstance(context, dict) and 'level_data' in context,
                         'stage_enemy_refs_level_data_missing')
        level = presence.pointer(context['level_data'])
        klass = reader.expect_class(level, 'LevelData', 'Torappu')
        reader.typed_fields(klass, 23, LEVEL_FIELDS)
        array = reader.ref(level + LEVEL_REFS, guard=True)
        reader.expect_class(array, 'EnemyDataDbReference[]', '')
        count, = struct.unpack('<Q', reader.guarded(array + presence.ARRAY_LENGTH, 8))
        presence.require(count <= MAX_REFS, 'stage_enemy_refs_count')
        slots = reader.guarded(array + presence.ARRAY_DATA, count * 8) if count else b''
        entries, objects = [], set()
        for address, in struct.iter_unpack('<Q', slots):
            presence.pointer(address)
            presence.require(address not in objects, 'stage_enemy_refs_duplicate_object')
            objects.add(address)
            klass = reader.expect_class(address, 'EnemyDataDbReference', '')
            reader.typed_fields(klass, 4, REF_FIELDS)
            raw = reader.guarded(address + 0x10, 0x20)
            use_db = raw[0]
            key_ref, = struct.unpack_from('<Q', raw, 8)
            variant, = struct.unpack_from('<i', raw, 0x10)
            overwrite, = struct.unpack_from('<Q', raw, 0x18)
            presence.require(use_db in (0, 1), 'stage_enemy_refs_use_db_flag')
            presence.require(0 <= variant <= MAX_VARIANT_LEVEL, 'stage_enemy_refs_variant_level')
            presence.pointer(overwrite, nullable=True)
            entries.append({
                'enemy_key': reader.actor_id(presence.pointer(key_ref)),
                'variant_level': variant, 'use_db': bool(use_db),
                'has_overwrite': bool(overwrite),
            })
        reader.validate()
    except presence.PresenceError as error:
        reason = str(error).replace('presence_', 'stage_enemy_refs_', 1)
    except presence.native.ResearchFailure as error:
        reason = ('stage_enemy_refs_read_budget_exhausted' if error.reason in
                  ('read_budget_exhausted', 'optional_read_budget_exhausted')
                  else 'stage_enemy_refs_parent_reader_unavailable')
    except (presence.native.access.ApiFailure, OSError):
        reason = 'stage_enemy_refs_read_failed'
    else:
        return {'stage_enemy_refs_status': 'available', 'stage_enemy_refs': {
            'scope': 'current_stage_enemy_db_refs', 'collection_count': count,
            'entries': entries,
        }}
    return {'stage_enemy_refs_status': 'unavailable',
            'stage_enemy_refs_reason': reason, 'stage_enemy_refs': None}
