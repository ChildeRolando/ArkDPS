"""Read-only battle lifecycle, independent of clock advancement and UI policy.

Versioned Android layout, checked against class identities on every binding.
Scene transitions are not atomic snapshots: re-read slots and scene scalars
before accepting a sample. A failure is unknown, never an observed null slot.
"""
import struct

from tools.enemy_health import game_structs as gs
from tools.stage_global_state.memory import MemoryAccess, ReadFailure

# Verified against the official Android 2.7.91 result and abandon controls.
RESULT_OFFSET = 0x22C
FINISHED_RESULTS = {1: 'victory', 2: 'defeat'}
SQUAD_PAGE = ('SquadPage', 'Torappu.UI')


# Official Android 2.7.91 ARM64 libil2cpp.so. These writable, file-backed slots
# match full class scans and are filled by IL2CPP with this process's addresses.
_BUILD_NOTE_RVA = 0x3980
_BUILD_NOTE = bytes.fromhex(
    '040000001400000003000000474e550077700271cee91e0d79e4e78f302a20b6aa54e6f9')
_CLASS_SLOT_RVAS = {
    'BattleController': 0xCF2E378,
    'GameFlowController': 0xCF37258,
    'SquadPage': 0xCF4AC00,
}

# The same build's il2cpp_image_get_class implementation looks up already
# materialized classes through this type-definition table. In the lobby
# its entries are populated before the individual usage slots are resolved.
_CLASS_DEFINITION_TABLE_RVA = 0xD38B4B8
_CLASS_DEFINITION_INDICES = {
    'BattleController': 10186,
    'GameFlowController': 1328,
    'SquadPage': 18479,
}


class LifecycleTracker:
    """Debounce readiness, not exit; emit each transition once per instance."""

    def __init__(self):
        self.active = None
        self.retired = None
        self.candidate = None
        self.stable = 0
        self.ready = False
        self.interval = .5

    def observe(self, value):
        self.ready = False
        if value is None:
            self.candidate, self.stable = None, 0
            self.interval = .5
            return []
        battle = value['battle']
        self.interval = .1 if battle or value['transiting'] else .5
        exiting = (value['transiting'] and value['source'].startswith('level_')
                   and value['target'] == 'battle_finish')
        events = []
        if self.active and (exiting or battle != self.active):
            events.append('exit')
            self.retired, self.active = self.active, None
        if battle is None:
            self.retired = None
        if (not value['ready'] or value['transiting'] or not battle
                or battle == self.retired):
            self.candidate, self.stable = None, 0
            return events
        self.stable = self.stable + 1 if battle == self.candidate else 1
        self.candidate = battle
        if self.stable >= 2:
            self.ready = True
            if self.active != battle:
                self.active = battle
                events.append('ready')
        return events


class LifecycleReader:
    """Small live reads on the supplied memory channel; no heap rescans."""

    def __init__(self, memory, battle_klass, flow_klass, locate_squads=None):
        self.m = memory
        self.battle_klass = battle_klass
        self.flow_klass = flow_klass
        self.battle_slot = self._slot(battle_klass, ('BattleController', 'Torappu.Battle'))
        self.flow_slot = self._slot(flow_klass, ('GameFlowController', 'Torappu'))
        self.locate_squads = locate_squads
        self.squad_page = None
        self.squad_args = None
        self.launch_pending = False
        self.practice = None

    def _slot(self, klass, identity):
        if self.m.class_identity(klass) != identity:
            raise ReadFailure('unsupported_layout')
        parent = self.m.pointer(klass + 0x58)
        if self.m.class_identity(parent) != ('SingletonMonoBehaviour`1', 'Torappu'):
            raise ReadFailure('unsupported_layout')
        slot = self.m.pointer(parent + 0xB8)
        if not self.m.mc.is_ptr(slot):
            raise ReadFailure('binding_invalid')
        return slot

    def _string(self, ptr):
        if not ptr:
            return ''
        size = self.m.integer(ptr + 0x10)
        if not 0 <= size <= 256:
            raise ReadFailure('unsupported_layout')
        return self.m.read(ptr + 0x14, size * 2).decode('utf-16-le') if size else ''

    def _squad_practice(self, stage):
        m = self.m
        candidates = (self.squad_page,) if self.squad_page else self.locate_squads()
        valid = []
        short_stage = stage.rsplit('/', 1)[-1].removeprefix('level_')
        for page in candidates:
            try:
                if m.identity(page)[1:] != SQUAD_PAGE:
                    continue
                args = m.pointer(page + 0x30)  # UIPage.m_args
                if not args or args == self.squad_args:
                    continue
                args_class, name, namespace = m.identity(args)
                if ((name, namespace) != ('Params', '') or
                        m.class_identity(m.pointer(args_class + 0x50)) != SQUAD_PAGE):
                    continue
                raw = m.read(args + 0x28, 0x28)
                stage_ptr = struct.unpack_from('<Q', raw, 0x20)[0]  # battleStageInfo.stageId
                if raw[0] not in (0, 1) or not stage_ptr:
                    continue
                m.identity(stage_ptr, 'String')
                if (self._string(stage_ptr).lower() == short_stage.lower()
                        and m.pointer(page + 0x30) == args and m.read(args + 0x28, 0x28) == raw):
                    valid.append((page, args, bool(raw[0])))
            except (ReadFailure, UnicodeError):
                continue
        if len(valid) == 1:
            self.squad_page, self.squad_args, practice = valid[0]
            return practice
        self.squad_page = None
        return None

    def sample(self):
        m = self.m
        slots = m.read_many([(self.battle_slot, 8), (self.flow_slot, 8)], structural_check=True)
        bc, flow = [struct.unpack('<Q', raw)[0] for raw in slots]
        raw = m.read(flow, 0x38)
        if struct.unpack_from('<Q', raw)[0] != self.flow_klass or not struct.unpack_from('<Q', raw, 0x10)[0]:
            raise ReadFailure('binding_invalid')
        if raw[0x20] not in (0, 1):
            raise ReadFailure('unsupported_layout')
        source, target = [self._string(struct.unpack_from('<Q', raw, offset)[0]) for offset in (0x28, 0x30)]
        battle, ready, state, metadata = None, False, None, None
        if bc:
            fields = gs.BattleControllerFields
            size = max(fields.M_STATE + 4, RESULT_OFFSET + 4, fields.SCHEDULER + 8,
                       fields.LEVEL_DATA + 8, fields.UNIT_MANAGER + 8, 0x18)
            block = m.read(bc, size)
            if struct.unpack_from('<Q', block)[0] != self.battle_klass:
                raise ReadFailure('binding_invalid')
            native = struct.unpack_from('<Q', block, 0x10)[0]
            if native:
                battle = (bc, native)
                state = struct.unpack_from('<i', block, fields.M_STATE)[0]
                if state not in (0, 1, 2, 3):
                    raise ReadFailure('unsupported_layout')
                # Optional stage evidence never turns a failed read into an
                # absent battle or a guessed outcome/practice flag.
                level = struct.unpack_from('<Q', block, fields.LEVEL_DATA)[0]
                if level:
                    try:
                        m.identity(level, 'LevelData')
                        slot = level + gs.LevelDataFields.LEVEL_ID
                        stage_ptr = m.pointer(slot)
                        if stage_ptr:
                            m.identity(stage_ptr, 'String')
                        stage = self._string(stage_ptr)
                        if stage and m.pointer(slot) == stage_ptr:
                            metadata = dict(stage_id=stage,
                                            evidence='native.LevelData.levelId:Android2.7.91')
                    except (ReadFailure, UnicodeError):
                        pass
                result = struct.unpack_from('<i', block, RESULT_OFFSET)[0]
                outcome = FINISHED_RESULTS.get(result) if state == 3 else None
                if state == 2 and result == 0 and raw[0x20] and source.startswith('level_') and target == 'battle_finish':
                    outcome = 'abandoned'
                if outcome:
                    if (m.integer(bc + fields.M_STATE) != state or
                            m.integer(bc + RESULT_OFFSET) != result):
                        raise ReadFailure('binding_changed')
                    metadata = dict(metadata or {}, outcome=outcome)
                    evidence = 'native.BattleController.m_result+GameFlow:Android2.7.91'
                    if metadata.get('evidence'):
                        evidence = metadata['evidence'] + '+' + evidence
                    metadata['evidence'] = evidence
                # No advancing-clock requirement: paused PLAYING is valid.
                if state == 2 and not raw[0x20]:
                    ready = True
                    for offset, name in ((fields.SCHEDULER, 'Scheduler'),
                                         (fields.LEVEL_DATA, 'LevelData'),
                                         (fields.UNIT_MANAGER, 'UnitManager')):
                        ptr = struct.unpack_from('<Q', block, offset)[0]
                        if not ptr:
                            ready = False
                            break
                        m.identity(ptr, name)
            # Reject teardown/rebinding while reading readiness roots.
            if m.read(bc, 0x18) != block[:0x18]:
                raise ReadFailure('binding_changed')
        after = m.read_many([(self.battle_slot, 8), (self.flow_slot, 8), (flow + 0x20, 0x18)], structural_check=True)
        if after[:2] != slots or after[2] != raw[0x20:0x38]:
            raise ReadFailure('binding_changed')
        if not battle:
            self.practice = None
            if raw[0x20] and source == 'home' and target == 'battle_loader':
                self.launch_pending = True
            elif target != 'battle_loader':
                self.launch_pending = False
        stage = metadata.get('stage_id') if metadata else None
        if ready and stage and self.launch_pending:
            self.launch_pending = False
            if self.locate_squads:
                practice = self._squad_practice(stage)
                if practice is not None:
                    self.practice = (battle, stage, practice)
            # The optional page scan can take longer than a battle frame.
            if self.practice:
                verified = m.read_many([(self.battle_slot, 8), (self.flow_slot, 8),
                                        (flow + 0x20, 0x18)], structural_check=True)
                if verified != after:
                    self.practice = None
                    raise ReadFailure('binding_changed')
        if self.practice and self.practice[:2] == (battle, stage):
            metadata['practice'] = self.practice[2]
            metadata['evidence'] += '+native.SquadPage.Params.isPractice:Android2.7.91'
        return dict(battle=battle, ready=ready, transiting=bool(raw[0x20]),
                    source=source, target=target, state=state, metadata=metadata)


def _read_lifecycle_class_slots(memory, namespaces):
    offsets = [_CLASS_SLOT_RVAS[name] for name in namespaces]
    module = [region for region in memory.mc.regions if region[3].endswith('/libil2cpp.so')]
    # MuMu can map several read-only copies. Only the loaded image has
    # these slot RVAs inside a writable segment of the same file.
    bases = [
        base for base, end, perms, path in module
        if 'r' in perms and base + _BUILD_NOTE_RVA + len(_BUILD_NOTE) <= end
        and any(name == path and 'rw' in flags and
                lo <= base + min(offsets) and base + max(offsets) + 8 <= hi
                for lo, hi, flags, name in module)
    ]
    if len(bases) != 1:
        raise ReadFailure('class_slot_image_unavailable')
    base = bases[0]
    if memory.read_code(base + _BUILD_NOTE_RVA, len(_BUILD_NOTE)) != _BUILD_NOTE:
        raise ReadFailure('class_slot_build_mismatch')
    values = memory.read_many([(base + offset, 8) for offset in offsets], structural_check=True)
    addresses = [struct.unpack('<Q', raw)[0] for raw in values]
    # The low bit marks an unresolved usage token, not a live pointer.
    pending = [(index, name) for index, name in enumerate(namespaces) if addresses[index] & 1]
    if pending:
        table_slot = base + _CLASS_DEFINITION_TABLE_RVA
        if not memory.mc.is_ptr(table_slot + 7):
            raise ReadFailure('class_definition_table_unavailable')
        raw, = memory.read_many([(table_slot, 8)], structural_check=True)
        table, = struct.unpack('<Q', raw)
        entries = [table + 8 * _CLASS_DEFINITION_INDICES[name] for _, name in pending]
        if (table & 7 or not memory.mc.is_ptr(table) or
                any(not memory.mc.is_ptr(entry + 7) for entry in entries)):
            raise ReadFailure('class_definition_table_unavailable')
        values = memory.read_many([(entry, 8) for entry in entries], structural_check=True)
        for (index, _), raw in zip(pending, values):
            addresses[index], = struct.unpack('<Q', raw)

    classes = {}
    for (name, namespace), address in zip(namespaces.items(), addresses):
        if address & 7 or not memory.mc.is_ptr(address):
            raise ReadFailure('class_slot_identity_unavailable')
        if memory.class_identity(address) != (name, namespace):
            raise ReadFailure('class_slot_identity_unavailable')
        classes[address] = name
    return classes


def _bind_lifecycle(memory, battle_klass, locator, classes):
    if battle_klass is None:
        battles = [addr for addr, name in classes.items() if name == 'BattleController']
        if len(battles) != 1:
            raise ReadFailure('无法唯一定位战局单例类型；保留未知状态')
        battle_klass = battles[0]
    flows = [addr for addr, name in classes.items() if name == 'GameFlowController']
    if len(flows) != 1:
        raise ReadFailure('flow_class_unavailable')
    squads = {addr: name for addr, name in classes.items() if name == SQUAD_PAGE[0]}

    def locate_squads():
        # Borrow the product channel for the bounded, once-per-launch lookup.
        locator._channel = memory.channel
        try:
            return (locator._scan_objects_for_classes(squads) or {}).get(SQUAD_PAGE[0], ())
        except (OSError, RuntimeError):
            return ()
        finally:
            locator._channel = None

    return LifecycleReader(memory, battle_klass, flows[0], locate_squads if squads else None)


def locate_lifecycle(mc, battle_klass, locator, *, channel=None):
    """Locate singleton slots using the verified build metadata when available."""
    namespaces = {'GameFlowController': 'Torappu', SQUAD_PAGE[0]: SQUAD_PAGE[1]}
    if battle_klass is None:
        namespaces['BattleController'] = 'Torappu.Battle'
    if channel is not None:
        memory = MemoryAccess(mc, channel)
        try:
            classes = _read_lifecycle_class_slots(memory, namespaces)
            reader = _bind_lifecycle(memory, battle_klass, locator, classes)
        except (OSError, RuntimeError):
            locator._status('[定位] 构建元数据未通过校验，使用批量扫描')
        else:
            locator._status('[定位] libil2cpp 构建元数据校验通过')
            return reader
    try:
        classes = locator.scan_class_metadata(namespaces) or {}
    except (OSError, RuntimeError):
        # The optional squad metadata must not prevent the live battle from binding.
        del namespaces[SQUAD_PAGE[0]]
        classes = locator.scan_class_metadata(namespaces) or {}
    memory = MemoryAccess(mc, mc.channel() if channel is None else channel)
    return _bind_lifecycle(memory, battle_klass, locator, classes)
