"""Optional enemy membership/HP witnesses for the accepted Windows build.

Call only inside NativePcBattleProbe.sample's same-frame extra_reader. The
caller owns process/build verification and the outer clock/root bracket. This
module opens nothing and infers neither death nor complete stage coverage.

Disk evidence (verified 2026-11-21, ordinary file reads only):
GameAssembly.dll SHA-256
cf7956bf88a61986859ef55c026d231713e2d12cf69ffd9a19779ab491cf76ff;
global-metadata.dat SHA-256
e58fb854ae519bc22f7d8bc7ac83502425a51b896a907a3374f5215202f9caa5.
The outer access.verify_build also gates Arknights.exe before any memory read.
Metadata v29: 92-byte typedefs, strings/fields/typedef tables at header offsets
0x18/0x60/0xA0; DLL field-offset pointer table RVA 0x87CE000 and type pointer
table RVA 0x7F16AC0. UnitManager typedef 11604, Enemy 11538, Entity 10318,
LevelData.EnemyData 4732. FieldInfo checks below revalidate their exact fields.
Entity.get_hp (RVA 0x7364B0) returns m_hp when its hotfix getter is absent.
Torappu.FP's metadata constant FRACTIONAL_PLACES is 32. Dynamic maxHp uses
Attributes.GetValue and an unverified cached-value representation; omit it.
"""

import struct

from . import probe_native_pc_presence as presence


MAX_ENEMIES = 128
MAX_READ_CALLS = 512
MAX_READ_BYTES = 64 * 1024
ENEMIES = 0x20
ENEMY_DATA = 0x530
ENTITY_HP = 0x40
HP_GETTER_OVERRIDE = 0x268
MANAGER_FIELDS = ((2, '<enemies>k__BackingField', ENEMIES),)
ENEMY_FIELDS = ((104, '<data>k__BackingField', ENEMY_DATA),)
ENTITY_FIELDS = ((2, 'm_hp', ENTITY_HP),
                 (125, '__Hotfix0_get_hp', HP_GETTER_OVERRIDE))
DATA_FIELDS = ((2, 'key', 0x20),)
LIMITATIONS = (
    'UnitManager.enemies membership is not the full planned enemy roster.',
    'Managed flags and native-reference presence do not prove Unity liveness.',
    'Dynamic maximum HP, death/leak reason and phase boundaries are unverified.',
    'HP differences do not establish damage source, overkill or ordered events.',
)


class EnemyRead(presence.CheckedRead):
    def read(self, address, size):
        presence.require(self.calls < MAX_READ_CALLS and
                         self.bytes_requested + size <= MAX_READ_BYTES,
                         'enemy_read_budget_exhausted')
        return super().read(address, size)

    def enemy_lineage(self, address):
        klass = self.ref(address, guard=True)
        if klass in self.lineages:
            return self.lineages[klass]
        original = klass
        lineage, seen = [], set()
        for _ in range(presence.MAX_CLASS_DEPTH):
            presence.require(klass not in seen, 'enemy_class_cycle')
            seen.add(klass)
            lineage.append(klass)
            if self.is_class(klass, 'VisualObject'):
                break
            klass = self.ref(klass + presence.CLASS_PARENT, guard=True)
        else:
            raise presence.PresenceError('enemy_class_depth')
        expected = ('Enemy', 'Unit', 'Entity', 'BObject', 'VisualObject')
        presence.require(len(lineage) >= 5 and all(
            self.is_class(item, name) for item, name in zip(lineage[-5:], expected)),
            'enemy_inheritance')
        self.fields(lineage[-5], 408, ENEMY_FIELDS)
        self.fields(lineage[-3], 410, ENTITY_FIELDS)
        self.fields(lineage[-2], 9, presence.OBJECT_FIELDS)
        statics = self.ref(lineage[-3] + presence.native.CLASS_STATICS,
                           nullable=True, guard=True)
        override = self.ref(statics + HP_GETTER_OVERRIDE, nullable=True,
                            guard=True) if statics else None
        self.lineages[original] = override
        return override


def capture_enemies(read, context):
    """Return bounded current membership; gaps never become an empty roster.

    Instance UIDs are scoped to the caller's battle identity. A successful
    empty collection is different from an unavailable collection. max_hp stays
    None, so consumers must retain unknown HP ratios and stage HP totals.
    """
    reader = EnemyRead(read)
    try:
        battle = presence.pointer(context['battle_controller'])
        klass = reader.expect_class(battle, 'BattleController')
        reader.fields(klass, 515, presence.BATTLE_FIELDS)
        side, = struct.unpack('<i', reader.guarded(battle + presence.BATTLE_SIDE, 4))
        manager = reader.ref(battle + presence.BATTLE_UNIT_MANAGER, guard=True)
        klass = reader.expect_class(manager, 'UnitManager')
        reader.fields(klass, 7, MANAGER_FIELDS)
        container = reader.ref(manager + ENEMIES, guard=True)
        klass = reader.expect_class(container, 'UnorderedArray`1', 'Torappu')
        reader.fields(klass, 3, presence.CONTAINER_FIELDS)
        items, _map, count = struct.unpack('<QQi', reader.guarded(container + 0x10, 20))
        presence.require(0 <= count <= MAX_ENEMIES, 'enemy_count')
        if items:
            presence.pointer(items)
            capacity, = struct.unpack('<Q', reader.guarded(items + presence.ARRAY_LENGTH, 8))
            presence.require(count <= capacity <= presence.MAX_ARRAY_CAPACITY,
                             'enemy_array_capacity')
        else:
            presence.require(count == 0, 'enemy_missing_array')
        slots = reader.guarded(items + presence.ARRAY_DATA, count * 8) if count else b''
        entities, objects, uids = [], set(), set()
        for address, in struct.iter_unpack('<Q', slots):
            presence.pointer(address)
            presence.require(address not in objects, 'enemy_duplicate_object')
            objects.add(address)
            hp_override = reader.enemy_lineage(address)
            native = reader.ref(address + 0x10, nullable=True, guard=True)
            raw = reader.guarded(address + 0x18, 15)
            uid, entity_side, player_side, inited, borned, disappeared = struct.unpack('<IiiBBB', raw)
            presence.require(uid not in uids, 'enemy_duplicate_instance_uid')
            uids.add(uid)
            presence.require(all(flag in (0, 1) for flag in (inited, borned, disappeared)),
                             'enemy_lifecycle_flags')
            data = reader.ref(address + ENEMY_DATA, guard=True)
            klass = reader.expect_class(data, 'EnemyData', '')
            reader.fields(klass, 18, DATA_FIELDS)
            key = reader.actor_id(reader.ref(data + 0x20, guard=True))
            hp_raw, = struct.unpack('<q', reader.guarded(address + ENTITY_HP, 8))
            hp = hp_raw / (1 << 32)
            hp_reason = ('hp_getter_storage_uninitialized' if hp_override is None else
                         'hp_getter_override_present' if hp_override else None)
            if not -1e9 <= hp <= 1e9:
                hp_reason = 'hp_out_of_supported_range'
            entities.append({
                'instance_uid': uid, 'enemy_key': key,
                'side': entity_side, 'player_side': player_side,
                'inited': bool(inited), 'borned': bool(borned),
                'is_disappeared': bool(disappeared),
                'native_object_present': bool(native),
                'hp': hp if hp_reason is None else None,
                'hp_status': 'unavailable' if hp_reason else 'available',
                'hp_reason': hp_reason, 'max_hp': None,
                'max_hp_status': 'unavailable',
                'max_hp_reason': 'dynamic_max_hp_not_verified',
            })
        reader.validate()
    except presence.PresenceError as error:
        reason = str(error).replace('presence_', 'enemy_', 1)
    except presence.native.ResearchFailure:
        reason = 'enemy_parent_reader_unavailable'
    except (presence.native.access.ApiFailure, OSError):
        reason = 'enemy_read_failed'
    else:
        return {'enemies_status': 'available', 'enemies': {
            'scope': 'unit_manager_enemies_membership', 'local_side': side,
            'collection_count': count, 'entities': entities,
            'limitations': list(LIMITATIONS),
        }}
    return {'enemies_status': 'unavailable', 'enemies_reason': reason, 'enemies': None}
