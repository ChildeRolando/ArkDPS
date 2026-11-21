"""Bounded research reads of the fingerprinted official Windows client.

Only the checked BattleController clock and BattleLogger aggregate Float32
counters are returned. Native counter signs and scope are preserved. Operator
attribution, effective damage, shields and ordered events need separate proof.
This module performs no live acquisition when imported.
"""

import math
from pathlib import Path
import struct
import sys
import time

from . import probe_native_pc as access


BATTLE_CLASS_SLOT_RVA = 0x8B0FC40
CLASS_DEFINITION_TABLE_RVA = 0x8FD7890
BATTLE_TYPE_INDEX = 10185
CLASS_PARENT = 0x58
CLASS_STATICS = 0xB8
CLASS_FIELDS = 0x80
CLASS_FIELD_COUNT = 0x124
BATTLE_LOGGER = 0xE0
BATTLE_LEVEL = 0x160
BATTLE_STATE = 0x228
BATTLE_RESULT = 0x22C
BATTLE_SPEED = 0x230
BATTLE_SLOW_FLAG = 0x234
BATTLE_PAUSE = 0x280
BATTLE_ORIGIN_TIME_SCALE = 0x288
BATTLE_READ_SIZE = BATTLE_ORIGIN_TIME_SCALE + 4
PAUSE_GETTER_OVERRIDE = 0x490
SPEED_GETTER_OVERRIDE = 0x498
LEVEL_ID = 0x18
LOGGER_CONTROLLER = 0x18
LOGGER_STATS = 0x30
LOGGER_VERBOSE = 0x38
CLOCK_FRAME = 0x14
CLOCK_TIME = 0x18
STATS_TOTAL_HEAL = 0xB8
STATS_TOTAL_DAMAGE = 0xBC
MAX_READ_CALLS = 1024
MAX_READ_BYTES = 256 * 1024
MAX_SINGLE_READ = 4096
MAX_POINTER = 0x00007FFFFFFFFFFF
GUARD_READ_RESERVE = 64
GUARD_BYTE_RESERVE = 16 * 1024

# Declared field indices and offsets come from the matching metadata v29 and
# its GameAssembly field-offset table. Runtime FieldInfo records re-check them.
BATTLE_FIELDS = (
    (5, 's_fixedFrameCnt', CLOCK_FRAME), (6, 's_fixedPlayTime', CLOCK_TIME),
    (42, 'm_logger', BATTLE_LOGGER), (58, 'm_levelData', BATTLE_LEVEL),
    (80, 'm_state', BATTLE_STATE), (81, 'm_result', BATTLE_RESULT),
    (82, 'm_speedLevel', BATTLE_SPEED), (83, 'm_speedLevelSlowFlag', BATTLE_SLOW_FLAG),
    (89, 'm_pauseState', BATTLE_PAUSE), (90, 'm_originTimeScale', BATTLE_ORIGIN_TIME_SCALE),
    (242, '__Hotfix0_get_isPaused', PAUSE_GETTER_OVERRIDE),
    (243, '__Hotfix0_get_speedLevel', SPEED_GETTER_OVERRIDE),
)
LEVEL_FIELDS = ((1, 'levelId', LEVEL_ID),)
PAUSE_FIELDS = ((0, 'm_keys', 0x10),)
KEY_SET_FIELDS = ((0, 'm_items', 0x10),)
KEY_LIST_FIELDS = ((2, '_size', 0x18), (3, '_version', 0x1C))
STATE_NAMES = {0: 'NONE', 1: 'INITED_BUT_NOT_START', 2: 'PLAYING', 3: 'FINISHED'}
SPEED_NAMES = {0: 'SLOW_MOTION', 1: 'STANDARD', 2: 'FAST', 3: 'SUPER_FAST'}
LOGGER_FIELDS = ((4, 'm_controller', LOGGER_CONTROLLER), (7, 'm_stats', LOGGER_STATS),
                 (8, 'm_verboseRecorder', LOGGER_VERBOSE))
STATS_FIELDS = ((5, 'charAdvancedStats', 0x30),
                (23, 'totalHeal', STATS_TOTAL_HEAL), (24, 'totalDamage', STATS_TOTAL_DAMAGE))
LIMITATIONS = [
    'BattleLogger aggregate counter scope and signs require live reconciliation.',
    'Operator attribution, effective damage, shields and ordered events remain unverified.',
]


class ResearchFailure(RuntimeError):
    def __init__(self, status, reason, diagnostic=None):
        self.status, self.reason, self.diagnostic = status, reason, diagnostic
        super().__init__(reason)


def _require(condition, reason, status='unsupported_layout'):
    if not condition:
        raise ResearchFailure(status, reason)


class BoundedReader:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle
        self.identities = {}
        self.verified_fields = set()
        self.reset_budget()

    def reset_budget(self):
        self.calls, self.bytes_requested = 0, 0

    def read(self, address, size):
        _require(type(address) is int and type(size) is int and 0x10000 <= address
                 and 0 < size <= MAX_SINGLE_READ and address + size - 1 <= MAX_POINTER,
                 'invalid_read_range')
        _require(self.calls < MAX_READ_CALLS
                 and self.bytes_requested + size <= MAX_READ_BYTES, 'read_budget_exhausted')
        self.calls += 1
        self.bytes_requested += size
        result = self.api.read(self.handle, address, size)
        _require(isinstance(result, bytes) and len(result) == size, 'short_read', 'read_failed')
        return result

    def read_optional(self, address, size):
        """Leave enough of the transaction budget for its closing checks."""
        _require(self.calls + GUARD_READ_RESERVE < MAX_READ_CALLS
                 and self.bytes_requested + size + GUARD_BYTE_RESERVE <= MAX_READ_BYTES,
                 'optional_read_budget_exhausted')
        return self.read(address, size)

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def pointer(self, address):
        value = self.u64(address)
        _require(value == 0 or (0x10000 <= value <= MAX_POINTER and value % 8 == 0),
                 'invalid_pointer')
        return value

    def cstring(self, address):
        raw = self.read(address, 128)
        end = raw.find(b'\0')
        _require(end >= 0, 'unterminated_metadata_name')
        try:
            return raw[:end].decode('ascii', errors='strict')
        except UnicodeDecodeError:
            raise ResearchFailure('unsupported_layout', 'invalid_metadata_name') from None

    def class_identity(self, klass):
        if klass not in self.identities:
            name, namespace = struct.unpack('<QQ', self.read(klass + 0x10, 16))
            identity = (self.cstring(name), self.cstring(namespace))
            self.identities[klass] = identity
        return self.identities[klass]

    def require_class(self, klass, name, namespace):
        _require(self.class_identity(klass) == (name, namespace), 'class_identity_mismatch')

    def verify_fields(self, klass, expected_count, fields):
        key = (klass, expected_count, fields)
        if key in self.verified_fields:
            return
        count = struct.unpack('<H', self.read(klass + CLASS_FIELD_COUNT, 2))[0]
        _require(count == expected_count, 'field_count_mismatch')
        table = self.pointer(klass + CLASS_FIELDS)
        _require(table != 0, 'field_table_missing')
        for index, name, offset in fields:
            _require(0 <= index < count, 'field_index_out_of_bounds')
            record = self.read(table + index * 32, 32)
            name_pointer, _, parent, actual, _ = struct.unpack('<QQQiI', record)
            _require(parent == klass and actual == offset and self.cstring(name_pointer) == name,
                     'field_layout_mismatch')
        self.verified_fields.add(key)


class NativePcBattleProbe:
    """Own one build-checked process handle and acquire guarded research samples."""

    def __init__(self, game_root, api=None):
        self.api, self.handle = api, None
        self.pid = None
        self.process_instance = None
        self.reader = None
        self.base = self.module_size = 0
        self._battle_class = None
        self._identity = self._last_clock = None
        self._stage_id = None
        self._serial = 0
        root = Path(game_root)
        if self.api is None:
            _require(sys.platform == 'win32' and access.ctypes.sizeof(access.ctypes.c_void_p) == 8,
                     'requires_windows_x64_python', 'unsupported_environment')
            self.api = access.WindowsApi()
        try:
            candidates = self.api.named_processes()
            matched = []
            for pid in candidates:
                query = None
                try:
                    query = self.api.open_process(pid, access.PROCESS_QUERY_LIMITED_INFORMATION)
                    if access.same_path(self.api.image_path(query), root / 'Arknights.exe'):
                        created, _ = self.api.process_times(query)
                        _require(type(created) is int and created > 0,
                                 'invalid_creation_time', 'process_unavailable')
                        matched.append((pid, created))
                finally:
                    if query is not None:
                        self.api.close(query)
            _require(len(matched) == 1, 'process_not_unique', 'process_unavailable')
            self.pid, created = matched[0]
            self.process_instance = (self.pid, created)
            self.handle = self.api.open_process(self.pid, access.READ_ACCESS)
            _require(access.same_path(self.api.image_path(self.handle), root / 'Arknights.exe'),
                     'process_path_changed', 'process_unavailable')
            self._require_process_alive()
            # Path/lifetime checks are ordinary queries. Defer expensive disk
            # hashing until there is a process, but still gate every module and
            # memory read on the exact accepted build.
            try:
                self.build, fixed_samples = access.verify_build(root)
            except (OSError, ValueError):
                raise ResearchFailure('unsupported_build', 'build_check_failed') from None
            _require(fixed_samples is not None, 'build_fingerprint_mismatch', 'unsupported_build')
            self._require_process_alive()
            modules = self.api.modules(self.handle)
            _require(len(modules) == 1, 'module_not_unique', 'module_unavailable')
            path, self.base, self.module_size = modules[0]
            _require(access.same_path(path, root / 'GameAssembly.dll'),
                     'module_path_mismatch', 'module_unavailable')
            self.reader = BoundedReader(self.api, self.handle)
            for _, rva, expected in fixed_samples:
                self._module_range(rva, len(expected))
                _require(self.reader.read(self.base + rva, len(expected)) == expected,
                         'module_bytes_mismatch', 'unsupported_build')
            self._module_range(BATTLE_CLASS_SLOT_RVA, 8)
            self._module_range(CLASS_DEFINITION_TABLE_RVA, 8)
            self._require_process_alive()
        except access.ApiFailure as error:
            self.close()
            raise ResearchFailure('process_access_failed', 'windows_api_failed',
                                  error.diagnostic()) from None
        except BaseException:
            self.close()
            raise

    @property
    def battle_identity(self):
        """Return checked roots in process without adding addresses to reports."""
        return self._identity

    def process_alive(self):
        """Return false only for confirmed exit; preserve query failures.

        The reserved exit code STILL_ACTIVE cannot prove a process is alive.
        A target that exits with that code will instead fail later reads.
        """
        if self.handle is None:
            return False
        created, _ = self.api.process_times(self.handle)
        _require(type(created) is int and created > 0,
                 'invalid_creation_time', 'process_unavailable')
        _require(self.process_instance == (self.pid, created),
                 'process_creation_time_changed', 'process_changed')
        code = self.api.exit_code(self.handle)
        _require(type(code) is int and 0 <= code <= 0xFFFFFFFF,
                 'invalid_exit_code', 'process_unavailable')
        return code == access.STILL_ACTIVE

    def _require_process_alive(self):
        _require(self.process_alive(), 'owned_process_exited', 'process_exited')

    def _module_range(self, rva, size):
        _require(self.base and 0 <= rva and rva + size <= self.module_size,
                 'module_range_mismatch')

    def _resolve_battle_class(self):
        klass = self.reader.u64(self.base + BATTLE_CLASS_SLOT_RVA)
        if klass & 1:
            table = self.reader.pointer(self.base + CLASS_DEFINITION_TABLE_RVA)
            if not table:
                return 0
            klass = self.reader.pointer(table + BATTLE_TYPE_INDEX * 8)
        _require(klass == 0 or (klass % 8 == 0 and 0x10000 <= klass <= MAX_POINTER),
                 'invalid_class_pointer')
        return klass

    def _binding(self):
        klass = self._resolve_battle_class()
        if not klass:
            raise ResearchFailure('waiting_game_init', 'battle_class_uninitialized')
        if self._battle_class is None:
            self.reader.require_class(klass, 'BattleController', 'Torappu.Battle')
            self.reader.verify_fields(klass, 515, BATTLE_FIELDS)
            parent = self.reader.pointer(klass + CLASS_PARENT)
            self.reader.require_class(parent, 'SingletonMonoBehaviour`1', 'Torappu')
            self._battle_class = klass
        _require(klass == self._battle_class, 'battle_class_changed')
        parent = self.reader.pointer(klass + CLASS_PARENT)
        singleton_fields = self.reader.pointer(parent + CLASS_STATICS)
        clock_fields = self.reader.pointer(klass + CLASS_STATICS)
        if not singleton_fields or not clock_fields:
            raise ResearchFailure('waiting_game_init', 'static_storage_uninitialized')
        return klass, parent, singleton_fields, clock_fields

    @staticmethod
    def _battle_fields(raw):
        return (struct.unpack_from('<Q', raw)[0], struct.unpack_from('<Q', raw, 0x10)[0],
                struct.unpack_from('<Q', raw, BATTLE_LOGGER)[0],
                struct.unpack_from('<Q', raw, BATTLE_LEVEL)[0],
                struct.unpack_from('<i', raw, BATTLE_STATE)[0])

    def _stage(self, level):
        """Keep optional stage evidence separate from the checked counter roots."""
        reader = self.reader
        try:
            klass = reader.pointer(level)
            reader.require_class(klass, 'LevelData', 'Torappu')
            reader.verify_fields(klass, 23, LEVEL_FIELDS)
            pointer_bytes = reader.read(level + LEVEL_ID, 8)
            pointer, = struct.unpack('<Q', pointer_bytes)
            guards = [(level, struct.pack('<Q', klass)), (level + LEVEL_ID, pointer_bytes)]
            if not pointer:
                return None, 'level_id_missing', guards
            header = reader.read(pointer, 0x14)
            reader.require_class(struct.unpack_from('<Q', header)[0], 'String', 'System')
            length, = struct.unpack_from('<i', header, 0x10)
            _require(0 < length <= 512, 'invalid_level_id_length')
            value = reader.read(pointer + 0x14, length * 2)
            try:
                stage_id = value.decode('utf-16-le', errors='strict')
            except UnicodeDecodeError:
                raise ResearchFailure('unsupported_layout', 'invalid_level_id_utf16') from None
            guards.extend(((pointer, header), (pointer + 0x14, value)))
            return stage_id, None, guards
        except ResearchFailure as error:
            return None, error.reason, []
        except access.ApiFailure:
            return None, 'level_id_read_failed', []

    def _pause(self, raw, clock_fields):
        # Stock get_isPaused (RVA 0x6714B0) negates ListSet.IsEmpty through
        # EnableStateWithKey<BattlePauseKey>. Delegate overrides remain unknown.
        reader = self.reader
        try:
            override_bytes = reader.read(clock_fields + PAUSE_GETTER_OVERRIDE, 8)
            guards = [(clock_fields + PAUSE_GETTER_OVERRIDE, override_bytes)]
            if struct.unpack('<Q', override_bytes)[0]:
                return None, None, 'pause_getter_override_present', guards
            wrapper, = struct.unpack_from('<Q', raw, BATTLE_PAUSE)
            if not wrapper:
                return None, None, 'pause_state_missing', guards
            klass = reader.pointer(wrapper)
            wrapper_class = klass
            reader.require_class(klass, 'EnableStateWithKey`1', 'Torappu')
            reader.verify_fields(klass, 1, PAUSE_FIELDS)
            set_bytes = reader.read(wrapper + 0x10, 8)
            key_set, = struct.unpack('<Q', set_bytes)
            _require(key_set != 0, 'pause_key_set_missing')
            klass = reader.pointer(key_set)
            set_class = klass
            reader.require_class(klass, 'ListSet`1', 'Torappu')
            reader.verify_fields(klass, 1, KEY_SET_FIELDS)
            list_bytes = reader.read(key_set + 0x10, 8)
            key_list, = struct.unpack('<Q', list_bytes)
            _require(key_list != 0, 'pause_key_list_missing')
            klass = reader.pointer(key_list)
            list_class = klass
            reader.require_class(klass, 'List`1', 'System.Collections.Generic')
            reader.verify_fields(klass, 6, KEY_LIST_FIELDS)
            count_bytes = reader.read(key_list + 0x18, 8)
            count, _version = struct.unpack('<ii', count_bytes)
            _require(0 <= count <= 128, 'invalid_pause_key_count')
            guards.extend(((wrapper, struct.pack('<Q', wrapper_class)),
                           (key_set, struct.pack('<Q', set_class)),
                           (key_list, struct.pack('<Q', list_class)),
                           (wrapper + 0x10, set_bytes), (key_set + 0x10, list_bytes),
                           (key_list + 0x18, count_bytes)))
            return count > 0, count, None, guards
        except ResearchFailure as error:
            return None, None, error.reason, []
        except access.ApiFailure:
            return None, None, 'pause_state_read_failed', []

    def _metadata(self, raw, level, state, clock_fields):
        stage_id, stage_reason, guards = self._stage(level)
        paused, pause_count, pause_reason, pause_guards = self._pause(raw, clock_fields)
        guards.extend(pause_guards)
        result, speed = struct.unpack_from('<ii', raw, BATTLE_RESULT)
        slow = raw[BATTLE_SLOW_FLAG]
        speed_reason = None
        try:
            override = self.reader.read(clock_fields + SPEED_GETTER_OVERRIDE, 8)
            guards.append((clock_fields + SPEED_GETTER_OVERRIDE, override))
            if struct.unpack('<Q', override)[0]:
                speed_reason = 'speed_getter_override_present'
        except (ResearchFailure, access.ApiFailure):
            speed_reason = 'speed_override_read_failed'
        if speed not in SPEED_NAMES or slow not in (0, 1):
            speed_reason = 'invalid_speed_field'
        effective_speed = None if speed_reason else (0 if slow else speed)
        outcome = {1: 'victory', 2: 'defeat'}.get(result) if state == 3 else None
        outcome_reason = None if outcome else (
            'battle_not_finished' if state != 3 else 'result_not_yet' if result == 0
            else 'unrecognized_result')
        origin_scale, = struct.unpack_from('<f', raw, BATTLE_ORIGIN_TIME_SCALE)
        origin_reason = None
        if not math.isfinite(origin_scale) or origin_scale < 0:
            origin_scale, origin_reason = None, 'invalid_origin_time_scale'
        metadata = {
            'stage_id': stage_id, 'stage_status': 'available' if stage_id else 'unavailable',
            'stage_reason': stage_reason, 'battle_state_name': STATE_NAMES[state],
            'battle_result': result, 'outcome': outcome, 'outcome_reason': outcome_reason,
            'speed_level_raw': speed, 'speed_level': effective_speed,
            'speed_name': SPEED_NAMES.get(effective_speed),
            'slow_motion_override': bool(slow) if slow in (0, 1) else None,
            'speed_status': 'unavailable' if speed_reason else 'available',
            'speed_reason': speed_reason, 'paused': paused, 'pause_key_count': pause_count,
            'pause_status': 'unavailable' if pause_reason else 'available',
            'pause_reason': pause_reason, 'origin_time_scale': origin_scale,
            'origin_time_scale_reason': origin_reason,
        }
        return metadata, guards

    def _read_sample(self, extra_reader):
        reader = self.reader
        klass, parent, singleton_fields, clock_fields = self._binding()
        battle = reader.pointer(singleton_fields)
        if not battle:
            # A null singleton ends a battle only after its complete binding
            # still agrees. Initialization/rebinding can cross this poll.
            after_class = self._resolve_battle_class()
            after_parent = reader.pointer(klass + CLASS_PARENT)
            after_singleton_fields = reader.pointer(parent + CLASS_STATICS)
            after_clock_fields = reader.pointer(klass + CLASS_STATICS)
            after_battle = reader.pointer(singleton_fields)
            _require(after_class == klass and after_parent == parent and
                     after_singleton_fields == singleton_fields and
                     after_clock_fields == clock_fields and after_battle == 0,
                     'frame_or_lifecycle_changed', 'sample_retry')
            self._identity = self._last_clock = None
            raise ResearchFailure('waiting_for_battle', 'singleton_is_null')
        clock_before = reader.read(clock_fields + CLOCK_FRAME, 12)
        raw = reader.read(battle, BATTLE_READ_SIZE)
        actual_class, native, logger, level, state = self._battle_fields(raw)
        _require(actual_class == klass, 'battle_object_class_mismatch')
        if state not in (2, 3) or not native or not logger or not level:
            self._identity = self._last_clock = None
            raise ResearchFailure('battle_not_ready', 'battle_roots_not_ready')
        metadata, metadata_guards = self._metadata(raw, level, state, clock_fields)
        logger_raw = reader.read(logger, 0x40)
        logger_class = struct.unpack_from('<Q', logger_raw)[0]
        reader.require_class(logger_class, 'BattleLogger', 'Torappu.Battle')
        reader.verify_fields(logger_class, 46, LOGGER_FIELDS)
        _require(struct.unpack_from('<Q', logger_raw, LOGGER_CONTROLLER)[0] == battle,
                 'logger_owner_mismatch')
        stats = struct.unpack_from('<Q', logger_raw, LOGGER_STATS)[0]
        if not stats:
            raise ResearchFailure('battle_not_ready', 'battle_stats_missing')
        stats_raw = reader.read(stats, STATS_TOTAL_DAMAGE + 4)
        stats_class = struct.unpack_from('<Q', stats_raw)[0]
        reader.require_class(stats_class, 'BattleStats', '')
        reader.verify_fields(stats_class, 44, STATS_FIELDS)
        heal, damage = struct.unpack_from('<ff', stats_raw, STATS_TOTAL_HEAL)
        _require(all(math.isfinite(value) and abs(value) <= 1e15 for value in (heal, damage)),
                 'invalid_aggregate_counter')
        frame, fixed_time = struct.unpack('<Iq', clock_before)
        game_time = fixed_time / (1 << 32)
        _require(0 <= game_time <= 864000, 'invalid_logic_clock')
        extra = {}
        if extra_reader is not None:
            extra = extra_reader(reader.read_optional, {'battle_controller': battle, 'battle_logger': logger,
                                              'battle_stats': stats, 'level_data': level, 'frame': frame,
                                              'game_time': game_time})
            _require(type(extra) is dict, 'invalid_extra_sample')
        # Every mutable source used above is re-read inside the same logic-clock bracket.
        after_raw = reader.read(battle, BATTLE_READ_SIZE)
        after_logger = reader.read(logger, 0x40)
        after_stats = reader.read(stats, 8)
        after_counters = reader.read(stats + STATS_TOTAL_HEAL, 8)
        after_singleton = reader.pointer(singleton_fields)
        after_class = self._resolve_battle_class()
        after_parent = reader.pointer(klass + CLASS_PARENT)
        after_singleton_fields = reader.pointer(parent + CLASS_STATICS)
        after_clock_fields = reader.pointer(klass + CLASS_STATICS)
        metadata_unchanged = all(reader.read(address, len(expected)) == expected
                                 for address, expected in metadata_guards)
        clock_after = reader.read(clock_fields + CLOCK_FRAME, 12)
        _require(clock_after == clock_before and after_singleton == battle and after_class == klass
                 and after_parent == parent and after_singleton_fields == singleton_fields
                 and after_clock_fields == clock_fields
                 and self._battle_fields(after_raw) == self._battle_fields(raw)
                 and after_raw[BATTLE_STATE:BATTLE_SLOW_FLAG + 1]
                 == raw[BATTLE_STATE:BATTLE_SLOW_FLAG + 1]
                 and after_raw[BATTLE_PAUSE:BATTLE_READ_SIZE] == raw[BATTLE_PAUSE:BATTLE_READ_SIZE]
                 and metadata_unchanged
                 and after_logger == logger_raw and after_stats == stats_raw[:8]
                 and after_counters == stats_raw[STATS_TOTAL_HEAL:STATS_TOTAL_DAMAGE + 4],
                 'frame_or_lifecycle_changed', 'sample_retry')
        identity = (battle, native, logger, stats, level)
        identity_changed = identity != self._identity
        stage_changed = (not identity_changed and metadata['stage_id'] is not None and self._stage_id is not None
                         and metadata['stage_id'] != self._stage_id)
        if identity_changed or stage_changed or (self._last_clock is not None
                and (frame < self._last_clock[0] or fixed_time < self._last_clock[1])):
            self._serial += 1
        self._identity, self._last_clock = identity, (frame, fixed_time)
        if identity_changed or metadata['stage_id'] is not None:
            self._stage_id = metadata['stage_id']
        sample = {'battle_serial': self._serial, 'frame': frame, 'game_time': game_time,
                  'battle_state': state, 'total_damage': damage, 'total_heal': heal,
                  'verbose_recorder_present': bool(struct.unpack_from('<Q', logger_raw, LOGGER_VERBOSE)[0])}
        sample.update(metadata)
        _require(not sample.keys() & extra.keys(), 'reserved_extra_field')
        sample.update(extra)
        return {'status': 'sample_available', 'research_only': True,
                'sample': sample, 'limitations': list(LIMITATIONS)}

    def sample(self, extra_reader=None):
        if self.handle is None:
            return {'status': 'closed', 'reason': 'probe_is_closed', 'research_only': True}
        self.reader.reset_budget()
        started = time.monotonic()
        try:
            self._require_process_alive()
            try:
                report = self._read_sample(extra_reader)
            finally:
                self._require_process_alive()
        except ResearchFailure as error:
            report = {'status': error.status, 'reason': error.reason, 'research_only': True}
        except access.ApiFailure as error:
            report = {'status': 'read_failed', 'reason': 'windows_api_failed',
                      'diagnostic': error.diagnostic(), 'research_only': True}
        finished = time.monotonic()
        report['acquisition'] = {'started_monotonic': started, 'finished_monotonic': finished,
                                 'duration_ms': (finished - started) * 1000}
        return report

    def close(self):
        if self.handle is not None:
            self.api.close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
