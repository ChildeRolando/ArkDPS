"""Optional managed-unit membership witnesses for the fingerprinted PC build.

Call only from NativePcBattleProbe.sample's same-frame extra_reader callback.
The caller owns build verification, the read-only handle and the outer clock/root
bracket. This module opens nothing and performs no acquisition when imported.
Collection membership and raw lifecycle flags are observed; native Unity object
liveness, HP, exact deployment edges and per-instance damage are not inferred.
"""

import re
import struct

from . import probe_native_pc_battle as native


MAX_ENTITIES = 128
MAX_ARRAY_CAPACITY = 4096
MAX_READ_CALLS = 768
MAX_READ_BYTES = 96 * 1024
MAX_SINGLE_READ = 4096
MAX_CLASS_DEPTH = 10
MAX_ID_UNITS = 110
CLASS_PARENT = 0x58
CLASS_FIELDS = 0x80
CLASS_FIELD_COUNT = 0x124
ARRAY_LENGTH = 0x18
ARRAY_DATA = 0x20
BATTLE_SIDE = 0x18
BATTLE_UNIT_MANAGER = 0x2C0
MANAGER_CHARACTERS = 0x18
CONTAINER_HEADER = 0x10
CHARACTER_DATA = 0x538
ACTOR_ID = re.compile(r'[A-Za-z0-9_#.-]+')

# Indexed FieldInfo checks use the exact metadata/DLL pair already required by
# the outer probe (GameAssembly cf7956bf...76ff, metadata e58fb854...caa5).
BATTLE_FIELDS = ((4, '<playerSide>k__BackingField', BATTLE_SIDE),
                 (100, '<unitManager>k__BackingField', BATTLE_UNIT_MANAGER))
MANAGER_FIELDS = ((1, '<characters>k__BackingField', MANAGER_CHARACTERS),)
CONTAINER_FIELDS = ((0, 'm_items', 0x10), (2, '<count>k__BackingField', 0x20))
CHARACTER_FIELDS = ((78, '<data>k__BackingField', CHARACTER_DATA),)
OBJECT_FIELDS = ((1, '<instanceUid>k__BackingField', 0x18),
                 (2, '<side>k__BackingField', 0x1C),
                 (3, '<playerSide>k__BackingField', 0x20),
                 (4, '<inited>k__BackingField', 0x24),
                 (5, '<borned>k__BackingField', 0x25),
                 (6, '<isDisappeared>k__BackingField', 0x26))
DATA_FIELDS = ((4, 'prefabKey', 0x50), (8, 'uniqueId', 0x6C),
               (14, 'isToken', 0x90), (15, 'isPredefined', 0x91),
               (18, 'tokenOrHostKey', 0x98), (19, 'tokenOrHostUniqueId', 0xA0))
LIMITATIONS = (
    'UnitManager.characters membership and raw managed lifecycle flags only.',
    'Native Unity liveness, HP and exact deployment/removal times are unverified.',
    'A shared data UID/statistic key does not identify one summon instance.',
)


class PresenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise PresenceError(reason)


def pointer(value, nullable=False):
    require((nullable and value == 0) or
            (type(value) is int and 0x10000 <= value < (1 << 47) and value % 8 == 0),
            'presence_invalid_reference')
    return value


class CheckedRead:
    def __init__(self, read):
        self.source = read
        self.calls = self.bytes_requested = 0
        self.guards = []
        self.known_classes = {}
        self.known_fields = set()
        self.strings = {}
        self.lineages = {}
        self.character_data = {}

    def read(self, address, size):
        require(type(address) is int and type(size) is int and
                0x10000 <= address and 0 < size <= MAX_SINGLE_READ and
                address + size <= (1 << 47), 'presence_invalid_read_range')
        require(self.calls < MAX_READ_CALLS and self.bytes_requested + size <= MAX_READ_BYTES,
                'presence_read_budget_exhausted')
        self.calls += 1
        self.bytes_requested += size
        raw = self.source(address, size)
        require(isinstance(raw, bytes) and len(raw) == size, 'presence_short_read')
        return raw

    def guarded(self, address, size):
        raw = self.read(address, size)
        self.guards.append((address, raw))
        return raw

    def ref(self, address, nullable=False, guard=False):
        value, = struct.unpack('<Q', self.guarded(address, 8) if guard else self.read(address, 8))
        return pointer(value, nullable)

    def named(self, address, expected):
        require(0x10000 <= address < (1 << 47), 'presence_invalid_metadata_reference')
        return self.read(address, len(expected) + 1) == expected.encode('ascii') + b'\0'

    def identity(self, klass):
        pointer(klass)
        if klass not in self.known_classes:
            name, namespace = struct.unpack('<QQ', self.read(klass + 0x10, 16))
            parts = []
            for address in (name, namespace):
                raw = self.read(address, 128)
                end = raw.find(b'\0')
                require(end >= 0, 'presence_metadata_name_length')
                try:
                    parts.append(raw[:end].decode('ascii', errors='strict'))
                except UnicodeDecodeError:
                    raise PresenceError('presence_metadata_name_encoding') from None
            self.known_classes[klass] = tuple(parts)
        return self.known_classes[klass]

    def is_class(self, klass, name, namespace='Torappu.Battle'):
        return self.identity(klass) == (name, namespace)

    def expect_class(self, address, name, namespace='Torappu.Battle'):
        klass = self.ref(address, guard=True)
        require(self.is_class(klass, name, namespace), 'presence_class_identity')
        return klass

    def fields(self, klass, expected_count, fields):
        key = klass, expected_count, fields
        if key in self.known_fields:
            return
        count, = struct.unpack('<H', self.read(klass + CLASS_FIELD_COUNT, 2))
        require(count == expected_count, 'presence_field_count')
        table = self.ref(klass + CLASS_FIELDS)
        for index, name, expected_offset in fields:
            require(0 <= index < count, 'presence_field_index')
            name_ref, _, owner, offset, _ = struct.unpack('<QQQiI', self.read(table + index * 32, 32))
            require(owner == klass and offset == expected_offset and self.named(name_ref, name),
                    'presence_field_layout')
        self.known_fields.add(key)

    def actor_id(self, address):
        pointer(address)
        if address not in self.strings:
            self.expect_class(address, 'String', 'System')
            length_raw = self.guarded(address + 0x10, 4)
            length, = struct.unpack('<i', length_raw)
            require(0 < length <= MAX_ID_UNITS, 'presence_id_length')
            raw = self.guarded(address + 0x14, length * 2)
            try:
                text = raw.decode('utf-16-le', errors='strict')
            except UnicodeDecodeError:
                raise PresenceError('presence_id_encoding') from None
            require(ACTOR_ID.fullmatch(text), 'presence_id_format')
            self.strings[address] = text
        return self.strings[address]

    def lineage(self, address):
        klass = self.ref(address, guard=True)
        if klass in self.lineages:
            return self.lineages[klass]
        original_class = klass
        lineage, seen = [], set()
        for _ in range(MAX_CLASS_DEPTH):
            require(klass not in seen, 'presence_class_cycle')
            seen.add(klass)
            lineage.append(klass)
            if self.is_class(klass, 'VisualObject'):
                break
            klass = self.ref(klass + CLASS_PARENT, guard=True)
        else:
            raise PresenceError('presence_class_depth')
        # UnitManager's collection can also contain other Unit subclasses.
        tail = ('Unit', 'Entity', 'BObject', 'VisualObject')
        require(len(lineage) >= len(tail) and
                all(self.is_class(klass, name) for klass, name in zip(lineage[-4:], tail)),
                'presence_unit_inheritance')
        self.fields(lineage[-2], 9, OBJECT_FIELDS)
        character = next((klass for klass in lineage[:-4] if self.is_class(klass, 'Character')), None)
        if character is not None:
            require(character == lineage[-5], 'presence_character_inheritance')
            self.fields(character, 356, CHARACTER_FIELDS)
        self.lineages[original_class] = character
        return character

    def validate(self):
        require(all(self.read(address, len(raw)) == raw for address, raw in self.guards),
                'presence_changed_during_read')


def character_data(reader, address):
    if address in reader.character_data:
        return reader.character_data[address]
    klass = reader.expect_class(address, 'BattleCharacterData')
    reader.fields(klass, 36, DATA_FIELDS)
    raw = reader.guarded(address + 0x50, 0x54)
    key, = struct.unpack_from('<Q', raw)
    uid, = struct.unpack_from('<I', raw, 0x1C)
    is_token, is_predefined = struct.unpack_from('<BB', raw, 0x40)
    host_key, host_uid = struct.unpack_from('<QI', raw, 0x48)
    require(is_token in (0, 1) and is_predefined in (0, 1), 'presence_data_flags')
    result = {
        'id': reader.actor_id(pointer(key)), 'data_uid': uid,
        'is_token': bool(is_token), 'is_predefined': bool(is_predefined),
        'host_key': reader.actor_id(pointer(host_key)) if is_token and host_key else None,
        'host_uid': host_uid if is_token else None,
    }
    reader.character_data[address] = result
    return result


def capture_presence(read, context):
    """Return optional witnesses; unavailable fields never fabricate absence.

    No state persists between calls. The consumer must correlate instance_uid
    within the enclosing battle_serial, retain gaps, and avoid allocating shared
    per-key damage counters to individual live instances.
    """
    reader = CheckedRead(read)
    try:
        battle = pointer(context['battle_controller'])
        klass = reader.expect_class(battle, 'BattleController')
        reader.fields(klass, 515, BATTLE_FIELDS)
        side, = struct.unpack('<i', reader.guarded(battle + BATTLE_SIDE, 4))
        manager = reader.ref(battle + BATTLE_UNIT_MANAGER, guard=True)
        klass = reader.expect_class(manager, 'UnitManager')
        reader.fields(klass, 7, MANAGER_FIELDS)
        container = reader.ref(manager + MANAGER_CHARACTERS, guard=True)
        klass = reader.expect_class(container, 'UnorderedArray`1', 'Torappu')
        reader.fields(klass, 3, CONTAINER_FIELDS)
        raw = reader.guarded(container + CONTAINER_HEADER, 20)
        items, _item_map, count = struct.unpack('<QQi', raw)
        require(0 <= count <= MAX_ENTITIES, 'presence_entity_count')
        if items:
            pointer(items)
            capacity, = struct.unpack('<Q', reader.guarded(items + ARRAY_LENGTH, 8))
            require(count <= capacity <= MAX_ARRAY_CAPACITY, 'presence_array_capacity')
        else:
            require(count == 0, 'presence_missing_array')
        slots = reader.guarded(items + ARRAY_DATA, count * 8) if count else b''
        entities, non_characters, instance_uids, objects = [], 0, set(), set()
        for address, in struct.iter_unpack('<Q', slots):
            pointer(address)
            require(address not in objects, 'presence_duplicate_object')
            objects.add(address)
            character = reader.lineage(address)
            raw = reader.guarded(address + 0x18, 15)
            uid, entity_side, player_side, inited, borned, disappeared = struct.unpack('<IiiBBB', raw)
            require(all(flag in (0, 1) for flag in (inited, borned, disappeared)),
                    'presence_lifecycle_flags')
            require(uid not in instance_uids, 'presence_duplicate_instance_uid')
            instance_uids.add(uid)
            if character is None:
                non_characters += 1
                continue
            data = reader.ref(address + CHARACTER_DATA, guard=True)
            entities.append({
                'instance_uid': uid, 'side': entity_side, 'player_side': player_side,
                'inited': bool(inited), 'borned': bool(borned),
                'is_disappeared': bool(disappeared), **character_data(reader, data),
            })
        reader.validate()
    except PresenceError as error:
        reason = str(error)
    except native.ResearchFailure:
        reason = 'presence_parent_reader_unavailable'
    except (native.access.ApiFailure, OSError):
        reason = 'presence_read_failed'
    else:
        return {'presence_status': 'available', 'presence': {
            'scope': 'unit_manager_characters_membership', 'local_side': side,
            'collection_count': count, 'non_character_count': non_characters,
            'entities': entities, 'limitations': list(LIMITATIONS),
        }}
    return {'presence_status': 'unavailable', 'presence_reason': reason, 'presence': None}
