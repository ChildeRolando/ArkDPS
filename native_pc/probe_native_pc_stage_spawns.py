"""Bounded fixed-wave SPAWN structure for the fingerprinted Windows build.

Only call inside the parent's checked LevelData/battle/frame bracket. This is
configuration structure, not actual spawn/kill counts, timing or enemy HP.
The matching metadata/DLL have LevelData4755, WaveData4741, FragmentData4739,
ActionData4737; all three arrays contain CLASS references. SPAWN's compressed
metadata enum default is 0. No acquisition occurs at import.
"""

import struct

from . import probe_native_pc_stage_enemies as stage


MAX_WAVES = 32
MAX_FRAGMENT_OCCURRENCES = 128
MAX_ACTION_OCCURRENCES = 256
MAX_SPAWN_PER_ACTION = 4096
MAX_TOTAL_SPAWN = 65536
MAX_READ_CALLS = 512
MAX_READ_BYTES = 64 * 1024
TYPE_ENUM = 0x11
TYPE_GENERIC = 0x15
LEVEL_FIELDS = ((14, 'waves', 0x80, stage.TYPE_SZARRAY),
                (15, 'branches', 0x88, TYPE_GENERIC))
WAVE_FIELDS = ((3, 'fragments', 0x20, stage.TYPE_SZARRAY),)
FRAGMENT_FIELDS = ((1, 'actions', 0x18, stage.TYPE_SZARRAY),)
ACTION_FIELDS = (
    (0, 'actionType', 0x10, TYPE_ENUM),
    (1, 'managedByScheduler', 0x14, stage.TYPE_BOOL),
    (2, 'key', 0x18, stage.TYPE_STRING), (3, 'count', 0x20, stage.TYPE_I4),
    (12, 'hiddenGroup', 0x38, stage.TYPE_STRING),
    (13, 'randomSpawnGroupKey', 0x40, stage.TYPE_STRING),
    (14, 'randomSpawnGroupPackKey', 0x48, stage.TYPE_STRING),
    (20, 'isValid', 0x5E, stage.TYPE_BOOL),
    (21, 'notCountInTotal', 0x5F, stage.TYPE_BOOL),
)


class SpawnRead(stage.StageRead):
    def __init__(self, read):
        super().__init__(read)
        self.arrays = {}
        self.actions = {}
        self.condition_keys = {}
        self.comparisons = []
        self.type_prefixes = {}

    def read(self, address, size):
        stage.presence.require(self.calls < MAX_READ_CALLS and
                               self.bytes_requested + size <= MAX_READ_BYTES,
                               'stage_spawns_read_budget_exhausted')
        return super().read(address, size)

    def compare_later(self, address, raw, regions):
        self.comparisons.append((address, raw, regions))
        return raw

    def coalesced(self, address, size, regions):
        return self.compare_later(address, self.read(address, size), regions)

    def validate(self):
        super().validate()
        for address, before, regions in self.comparisons:
            after = self.read(address, len(before))
            stage.presence.require(all(before[start:end] == after[start:end] for start, end in regions),
                                   'presence_changed_during_read')

    def type_prefix(self, address):
        if address not in self.type_prefixes:
            self.type_prefixes[address] = super().type_prefix(address)
        return self.type_prefixes[address]

    def typed_fields(self, klass, expected_count, fields):
        # StageRead's same FieldInfo/kind checks, with adjacent records grouped.
        # The live Il2CppType union is never treated as a nominal typedef index.
        cache_key = klass, expected_count, fields
        if cache_key in self.known_fields:
            return
        first = stage.presence.CLASS_FIELDS
        last = stage.presence.CLASS_FIELD_COUNT
        raw = self.coalesced(klass + first, last + 2 - first,
                             ((0, 8), (last - first, last + 2 - first)))
        table = stage.presence.pointer(struct.unpack_from('<Q', raw)[0])
        count, = struct.unpack_from('<H', raw, last - first)
        stage.presence.require(count == expected_count, 'stage_enemy_refs_field_count')
        groups = []
        for field in fields:
            stage.presence.require(0 <= field[0] < count, 'stage_enemy_refs_field_index')
            if not groups or field[0] != groups[-1][-1][0] + 1:
                groups.append([])
            groups[-1].append(field)
        for group in groups:
            start = group[0][0]
            records = self.guarded(table + start * 32, len(group) * 32)
            for index, name, offset, code in group:
                name_ref, type_ref, owner, actual, _ = struct.unpack_from('<QQQiI', records, (index - start) * 32)
                stage.presence.require(owner == klass and actual == offset and
                    self.guarded(name_ref, len(name) + 1) == name.encode('ascii') + b'\0',
                    'stage_enemy_refs_field_layout')
                data, bits = self.type_prefix(stage.presence.pointer(type_ref))
                stage.presence.require((bits >> 16) & 0xFF == code, 'stage_enemy_refs_field_type')
                if code == stage.TYPE_SZARRAY:
                    _, element_bits = self.type_prefix(stage.presence.pointer(data))
                    stage.presence.require((element_bits >> 16) & 0xFF == stage.TYPE_CLASS,
                                           'stage_enemy_refs_array_element_type')
        self.known_fields.add(cache_key)

    def prefixed_class(self, raw, name, namespace=''):
        klass = stage.presence.pointer(struct.unpack_from('<Q', raw)[0])
        stage.presence.require(self.is_class(klass, name, namespace), 'presence_class_identity')
        return klass

    def object_reference(self, address, name, count, fields, offset):
        raw = self.coalesced(address, offset + 8, ((0, 8), (offset, offset + 8)))
        klass = self.prefixed_class(raw, name)
        self.typed_fields(klass, count, fields)
        return stage.presence.pointer(struct.unpack_from('<Q', raw, offset)[0])

    def array(self, address, name, cap):
        stage.presence.pointer(address)
        key = address, name
        if key not in self.arrays:
            header = self.read(address, stage.presence.ARRAY_DATA)
            self.prefixed_class(header, name)
            count, = struct.unpack_from('<Q', header, stage.presence.ARRAY_LENGTH)
            stage.presence.require(count <= cap, 'stage_spawns_array_count')
            raw = self.read(address + stage.presence.ARRAY_DATA, count * 8) if count else b''
            self.compare_later(address, header + raw,
                               ((0, 8), (stage.presence.ARRAY_LENGTH, len(header) + len(raw))))
            self.arrays[key] = tuple(stage.presence.pointer(value)
                                     for value, in struct.iter_unpack('<Q', raw))
        result = self.arrays[key]
        stage.presence.require(len(result) <= cap, 'stage_spawns_array_count')
        return result

    def managed_string(self, address):
        stage.presence.pointer(address)
        if address not in self.strings:
            header = self.read(address, 0x14)
            self.prefixed_class(header, 'String', 'System')
            length, = struct.unpack_from('<i', header, 0x10)
            stage.presence.require(0 <= length <= stage.presence.MAX_ID_UNITS,
                                   'stage_spawns_condition_key_length')
            raw = self.read(address + 0x14, length * 2) if length else b''
            self.compare_later(address, header + raw, ((0, 8), (0x10, 0x14 + len(raw))))
            try:
                self.strings[address] = raw.decode('utf-16-le', errors='strict')
            except UnicodeDecodeError:
                raise stage.presence.PresenceError('stage_spawns_condition_key_encoding') from None
        return self.strings[address]

    def actor_id(self, address):
        value = self.managed_string(address)
        stage.presence.require(value and stage.presence.ACTOR_ID.fullmatch(value), 'presence_id_format')
        return value

    def optional_key(self, address):
        if not address:
            return None
        return self.managed_string(address) or None

    def action(self, address):
        if address not in self.actions:
            raw = self.read(address, 0x60)
            klass = self.prefixed_class(raw, 'ActionData')
            self.typed_fields(klass, 24, ACTION_FIELDS)
            action_type, = struct.unpack_from('<i', raw, 0x10)
            stage.presence.require(0 <= action_type < 14, 'stage_spawns_action_type')
            regions = [(0, 8), (0x10, 0x14)]
            if action_type != 0:
                result = {'kind': 'other'}
            else:
                valid = raw[0x5E]
                regions.append((0x5E, 0x5F))
                stage.presence.require(valid in (0, 1), 'stage_spawns_valid_flag')
                if not valid:
                    result = {'kind': 'invalid_spawn'}
                else:
                    managed, excluded = raw[0x14], raw[0x5F]
                    regions.extend(((0x14, 0x15), (0x18, 0x24), (0x38, 0x50), (0x5F, 0x60)))
                    stage.presence.require(managed in (0, 1) and excluded in (0, 1),
                                           'stage_spawns_boolean_flag')
                    key, count = struct.unpack_from('<Qi', raw, 0x18)
                    stage.presence.require(0 < count <= MAX_SPAWN_PER_ACTION,
                                           'stage_spawns_spawn_count')
                    hidden, group, pack = struct.unpack_from('<QQQ', raw, 0x38)
                    condition = tuple(self.optional_key(stage.presence.pointer(ref, nullable=True))
                                      for ref in (hidden, group, pack))
                    result = {'kind': 'conditional' if any(condition) else 'scheduled',
                              'enemy_key': self.actor_id(stage.presence.pointer(key)), 'count': count,
                              'managed_by_scheduler': bool(managed), 'not_count_in_total': bool(excluded),
                              'hidden_group': condition[0], 'random_spawn_group': condition[1],
                              'random_spawn_pack': condition[2]}
            self.compare_later(address, raw, tuple(regions))
            self.actions[address] = result
        return self.actions[address]


def capture_stage_spawns(read, context):
    """Count action occurrences in fixed waves; a gap never becomes zero.

    Reused object/array references are read once but counted at every position.
    Branch-container presence does not mean a nonempty branch or execution.
    Conditional counts are configured multiplicities, never actual outcomes.
    """
    reader = SpawnRead(read)
    try:
        stage.presence.require(isinstance(context, dict) and 'level_data' in context,
                               'stage_spawns_level_data_missing')
        level = stage.presence.pointer(context['level_data'])
        klass = reader.expect_class(level, 'LevelData', 'Torappu')
        reader.typed_fields(klass, 23, LEVEL_FIELDS)
        waves, branches = struct.unpack('<QQ', reader.guarded(level + 0x80, 16))
        stage.presence.pointer(branches, nullable=True)
        wave_refs = reader.array(waves, 'WaveData[]', MAX_WAVES)
        result = {'schema_version': 1, 'scope': 'current_stage_waves_spawn_structure',
                  'wave_count': len(wave_refs), 'fragment_occurrences': 0, 'action_occurrences': 0,
                  'valid_spawn_action_occurrences': 0, 'invalid_spawn_action_occurrences': 0,
                  'scheduled_spawn_count': 0, 'conditional_spawn_count': 0,
                  'conditional_spawn_action_count': 0, 'scheduled_counted_spawn_count': 0,
                  'not_count_in_total_spawn_count': 0, 'branch_container_present': bool(branches),
                  'branches_counted': False, 'dynamic_spawns_counted': False, 'spawn_actions': []}
        for wi, wave in enumerate(wave_refs):
            reference = reader.object_reference(wave, 'WaveData', 5, WAVE_FIELDS, 0x20)
            fragments = reader.array(reference, 'FragmentData[]',
                                     MAX_FRAGMENT_OCCURRENCES)
            result['fragment_occurrences'] += len(fragments)
            stage.presence.require(result['fragment_occurrences'] <= MAX_FRAGMENT_OCCURRENCES,
                                   'stage_spawns_fragment_occurrence_count')
            for fi, fragment in enumerate(fragments):
                reference = reader.object_reference(fragment, 'FragmentData', 2, FRAGMENT_FIELDS, 0x18)
                actions = reader.array(reference, 'ActionData[]',
                                       MAX_ACTION_OCCURRENCES)
                result['action_occurrences'] += len(actions)
                stage.presence.require(result['action_occurrences'] <= MAX_ACTION_OCCURRENCES,
                                       'stage_spawns_action_occurrence_count')
                for ai, action in enumerate(actions):
                    item = reader.action(action)
                    if item['kind'] == 'other':
                        continue
                    if item['kind'] == 'invalid_spawn':
                        result['invalid_spawn_action_occurrences'] += 1
                        continue
                    result['valid_spawn_action_occurrences'] += 1
                    result[item['kind'] + '_spawn_count'] += item['count']
                    if item['kind'] == 'conditional':
                        result['conditional_spawn_action_count'] += 1
                    elif not item['not_count_in_total']:
                        result['scheduled_counted_spawn_count'] += item['count']
                    if item['not_count_in_total']:
                        result['not_count_in_total_spawn_count'] += item['count']
                    stage.presence.require(result['scheduled_spawn_count'] + result['conditional_spawn_count']
                                           <= MAX_TOTAL_SPAWN, 'stage_spawns_total_spawn_count')
                    result['spawn_actions'].append(dict(item, wave_index=wi, fragment_index=fi, action_index=ai))
        reader.validate()
    except stage.presence.PresenceError as error:
        reason = str(error).replace('presence_', 'stage_spawns_', 1).replace('stage_enemy_refs_', 'stage_spawns_', 1)
    except stage.presence.native.ResearchFailure:
        reason = 'stage_spawns_parent_reader_unavailable'
    except (stage.presence.native.access.ApiFailure, OSError):
        reason = 'stage_spawns_read_failed'
    else:
        return {'stage_spawns_status': 'available', 'stage_spawns': result,
                'stage_spawns_read_cost': {'calls': reader.calls, 'bytes': reader.bytes_requested}}
    return {'stage_spawns_status': 'unavailable', 'stage_spawns_reason': reason, 'stage_spawns': None,
            'stage_spawns_read_cost': {'calls': reader.calls, 'bytes': reader.bytes_requested}}
