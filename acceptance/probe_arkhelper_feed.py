"""Read one existing ArkHelper v1.4.2 battle snapshot as research telemetry.

The public ABI is documented at
https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/include/arkhelp/ipc.h
(tagged commit bdc7951638bde5c85f0a64c8aac9ff9771f94667). This independent
consumer opens the battle mapping read-only. The reported counters retain the
publisher's semantics and availability flags.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import struct
import sys


MAPPING_NAME = r'Local\ArkHelp.Battle.v1'
FILE_MAP_READ = 0x0004
MAGIC = 0x42484841
VERSION = 11
SNAPSHOT_BYTES = 146792
MAPPING_BYTES = 256 * 1024
SEQUENCE_OFFSET = 12
MAX_ATTEMPTS = 8
MAX_AGE_MS = 2000
METRICS = ('damage', 'physical', 'arts', 'true', 'heal', 'elemental',
           'cost', 'buildup', 'taken', 'shield', 'dps', 'share')
METRIC_MASK = (1 << len(METRICS)) - 1
BATTLE_FLAG_NAMES = ('level_known', 'stale', 'paused', 'runtime_stage',
                     'spawn_plan', 'enemies_truncated', 'spawn_conditional')
U32, I32 = ctypes.c_uint32, ctypes.c_int32
U64, I64 = ctypes.c_uint64, ctypes.c_int64
F32, F64 = ctypes.c_float, ctypes.c_double


class Vec2(ctypes.LittleEndianStructure):
    _fields_ = [('x', I32), ('y', I32)]


class Float2(ctypes.LittleEndianStructure):
    _fields_ = [('x', F32), ('y', F32)]


class SpawnEntry(ctypes.LittleEndianStructure):
    _fields_ = [('planned_frame', U32), ('count', U32), ('route_index', U32),
                ('flags', U32), ('screen', Vec2), ('remaining_seconds', F32),
                ('enemy_key', ctypes.c_char * 96), ('enemy_name', ctypes.c_char * 64)]


class EnemyEntry(ctypes.LittleEndianStructure):
    _fields_ = [('flags', U32), ('reserved', U32), ('screen', Vec2),
                ('hp', I64), ('max_hp', I64), ('atk', I32), ('defense', I32),
                ('magic_resistance', I32), ('move_speed', I32), ('attack_speed', I32),
                ('base_attack_time', I32), ('weight', I32), ('block_count', I32),
                ('life_point_reduce', I32), ('damage_type_mask', U32), ('stats_mask', U32),
                ('enemy_key', ctypes.c_char * 96), ('enemy_name', ctypes.c_char * 64),
                ('tracking_id', U64)]


class DamageEntry(ctypes.LittleEndianStructure):
    _fields_ = [('flags', U32), ('damage_type', U32), ('screen', Vec2),
                ('total_damage', I64), ('dps', F32), ('share', F32),
                ('actor_id', ctypes.c_char * 96), ('actor_name', ctypes.c_char * 64),
                ('metrics', F64 * 12), ('metric_mask', U32), ('reserved', U32)]


class UnitEntry(ctypes.LittleEndianStructure):
    _fields_ = [('x', F32), ('y', F32), ('hp', F32), ('max_hp', F32),
                ('flags', U32), ('tracking_id', U64), ('ground_x', F32),
                ('ground_y', F32), ('range_radius', F32), ('range_point_count', U32),
                ('range_points', Float2 * 16)]


class PathPoint(ctypes.LittleEndianStructure):
    _fields_ = [('x', F32), ('y', F32), ('flags', U32)]


class PathEntry(ctypes.LittleEndianStructure):
    _fields_ = [('tracking_id', U64), ('sampled_ms', U64), ('battle_serial', U32),
                ('flags', U32), ('point_count', U32), ('route_revision', U32),
                ('points', PathPoint * 96)]


class BattleSnapshot(ctypes.LittleEndianStructure):
    _fields_ = [('magic', U32), ('version', U32), ('size', U32), ('sequence', U32),
                ('published_ms', U64), ('battle_serial', U32), ('in_battle', U32),
                ('battle_state', I32), ('flags', U32), ('frame', U32),
                ('spawn_count', U32), ('enemy_count', U32), ('damage_count', U32),
                ('unit_count', U32), ('path_count', U32), ('time_seconds', F64),
                ('unit_sample_ms', U64), ('unit_sample_battle_time', F64),
                ('level_id', ctypes.c_char * 128), ('spawns', SpawnEntry * 48),
                ('enemies', EnemyEntry * 48), ('damage', DamageEntry * 64),
                ('units', UnitEntry * 64), ('paths', PathEntry * 64),
                ('team_metrics', F64 * 12), ('team_metric_mask', U32), ('stats_flags', U32),
                ('operator_count', U32), ('operator_reserved', U32),
                ('operators', DamageEntry * 64), ('operator_ids', U64 * 64),
                ('device_metrics', F64 * 12)]


class FeedError(ValueError):
    pass


class MappingError(OSError):
    def __init__(self, operation, code):
        self.operation = operation
        self.code = code
        super().__init__(operation)


def _require(condition, reason):
    if not condition:
        raise FeedError(reason)


def _mask(value, allowed, reason):
    _require(value & ~allowed == 0, reason)


def _finite(*values):
    _require(all(math.isfinite(value) for value in values), 'non_finite_number')


def _text(record, field, capacity):
    offset = getattr(type(record), field).offset
    raw = ctypes.string_at(ctypes.addressof(record) + offset, capacity)
    end = raw.find(b'\0')
    _require(end >= 0, 'unterminated_utf8')
    try:
        return raw[:end].decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise FeedError('invalid_utf8') from None


def _metrics(values, mask):
    _mask(mask, METRIC_MASK, 'unknown_metric_mask')
    _finite(*values)
    return {name: float(values[index]) if mask & (1 << index) else None
            for index, name in enumerate(METRICS)}


def _damage(row):
    _mask(row.flags, 7, 'unknown_damage_flags')
    _require(row.reserved == 0, 'nonzero_reserved')
    _require(row.damage_type <= 5, 'invalid_damage_type')
    _finite(row.dps, row.share)
    _require(row.total_damage >= 0 and row.dps >= 0 and 0 <= row.share <= 1,
             'invalid_damage_value')
    return {'actor_id': _text(row, 'actor_id', 96),
            'actor_name': _text(row, 'actor_name', 64),
            'flags': row.flags, 'valid': bool(row.flags & 1),
            'screen_valid': bool(row.flags & 2), 'device': bool(row.flags & 4),
            'damage_type': row.damage_type, 'publisher_total_damage': row.total_damage,
            'publisher_dps': float(row.dps), 'publisher_share': float(row.share),
            'metric_mask': row.metric_mask, 'metrics': _metrics(row.metrics, row.metric_mask)}


def decode_snapshot(data, *, now_ms, max_age_ms=MAX_AGE_MS):
    """Validate a copied ABI v11 snapshot and preserve publisher metric identities."""
    _require(ctypes.sizeof(BattleSnapshot) == SNAPSHOT_BYTES, 'local_abi_mismatch')
    _require(len(data) == SNAPSHOT_BYTES, 'snapshot_size_mismatch')
    snapshot = BattleSnapshot.from_buffer_copy(data)
    _require(snapshot.magic == MAGIC, 'magic_mismatch')
    _require(snapshot.version == VERSION, 'version_mismatch')
    _require(snapshot.size == SNAPSHOT_BYTES, 'writer_size_mismatch')
    _require(snapshot.sequence % 2 == 0, 'writer_busy')
    _require(snapshot.in_battle in (0, 1), 'invalid_in_battle')
    _require(-1 <= snapshot.battle_state <= 3, 'invalid_battle_state')
    for field, maximum in (('spawn_count', 48), ('enemy_count', 48), ('damage_count', 64),
                           ('unit_count', 64), ('path_count', 64), ('operator_count', 64)):
        _require(getattr(snapshot, field) <= maximum, 'invalid_' + field)
    _mask(snapshot.flags, 127, 'unknown_battle_flags')
    _mask(snapshot.stats_flags, 3, 'unknown_stats_flags')
    _require(snapshot.operator_reserved == 0, 'nonzero_reserved')
    _finite(snapshot.time_seconds, snapshot.unit_sample_battle_time)
    _require(snapshot.time_seconds >= 0 and snapshot.unit_sample_battle_time >= 0,
             'invalid_battle_time')
    _require(type(now_ms) is int and now_ms >= snapshot.published_ms, 'clock_mismatch')
    _require(type(max_age_ms) is int and max_age_ms >= 0, 'invalid_max_age')
    damage = [_damage(row) for row in snapshot.damage[:snapshot.damage_count]]
    operators = [_damage(row) for row in snapshot.operators[:snapshot.operator_count]]
    for index, row in enumerate(operators):
        row['publisher_instance_id'] = str(snapshot.operator_ids[index])
    flags = {name: bool(snapshot.flags & (1 << index))
             for index, name in enumerate(BATTLE_FLAG_NAMES)}
    age = now_ms - snapshot.published_ms
    stale = flags['stale'] or age > max_age_ms
    return {
        'source': 'ArkHelper v1.4.2 public battle IPC', 'ipc_version': VERSION,
        'sequence': snapshot.sequence, 'published_ms': snapshot.published_ms,
        'age_ms': age, 'fresh': not stale, 'battle_serial': snapshot.battle_serial,
        'in_battle': bool(snapshot.in_battle), 'battle_state': snapshot.battle_state,
        'frame': snapshot.frame, 'game_time': snapshot.time_seconds,
        'level_id': _text(snapshot, 'level_id', 128), 'flags': flags,
        'stats_flags': snapshot.stats_flags,
        'partial_or_lost_events': bool(snapshot.stats_flags & 1),
        'actor_rows_truncated': bool(snapshot.stats_flags & 2),
        'counts': {name: getattr(snapshot, name + '_count')
                   for name in ('spawn', 'enemy', 'damage', 'unit', 'path', 'operator')},
        'team_metric_mask': snapshot.team_metric_mask,
        'team_metrics': _metrics(snapshot.team_metrics, snapshot.team_metric_mask),
        'device_metrics': _metrics(snapshot.device_metrics, snapshot.team_metric_mask),
        'damage_rows': damage, 'operator_instances': operators,
    }


class _WindowsMapping:
    def __init__(self, name=MAPPING_NAME):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = (
            ('OpenFileMappingW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('MapViewOfFile', [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.c_size_t], ctypes.c_void_p),
            ('UnmapViewOfFile', [ctypes.c_void_p], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
            ('GetTickCount64', [], ctypes.c_uint64),
        )
        for operation, arguments, result in signatures:
            function = getattr(self.kernel, operation)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.OpenFileMappingW(FILE_MAP_READ, False, name)
        self.view = None
        if not self.handle:
            raise MappingError('OpenFileMappingW', ctypes.get_last_error())
        self.view = self.kernel.MapViewOfFile(self.handle, FILE_MAP_READ, 0, 0, SNAPSHOT_BYTES)
        if not self.view:
            error = ctypes.get_last_error()
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise MappingError('MapViewOfFile', error)

    def read(self, offset, size):
        if self.view is None or offset < 0 or size < 0 or offset + size > SNAPSHOT_BYTES:
            raise FeedError('mapping_read_out_of_bounds')
        return ctypes.string_at(self.view + offset, size)

    def now_ms(self):
        return self.kernel.GetTickCount64()

    def close(self):
        error = None
        if self.view is not None:
            if not self.kernel.UnmapViewOfFile(self.view):
                error = MappingError('UnmapViewOfFile', ctypes.get_last_error())
            else:
                self.view = None
        if self.handle is not None:
            if not self.kernel.CloseHandle(self.handle):
                if error is None:
                    error = MappingError('CloseHandle', ctypes.get_last_error())
            else:
                self.handle = None
        if error:
            raise error


def read_consistent(mapping, *, max_attempts=MAX_ATTEMPTS):
    _require(type(max_attempts) is int and 1 <= max_attempts <= MAX_ATTEMPTS,
             'invalid_attempt_limit')
    for _ in range(max_attempts):
        before = mapping.read(SEQUENCE_OFFSET, 4)
        _require(len(before) == 4, 'mapping_truncated')
        sequence, = struct.unpack('<I', before)
        if sequence & 1:
            continue
        data = mapping.read(0, SNAPSHOT_BYTES)
        _require(len(data) == SNAPSHOT_BYTES, 'mapping_truncated')
        after = mapping.read(SEQUENCE_OFFSET, 4)
        _require(len(after) == 4, 'mapping_truncated')
        if before == after == data[SEQUENCE_OFFSET:SEQUENCE_OFFSET + 4]:
            return data
    raise FeedError('writer_busy')


def probe(*, mapping_factory=None):
    report = {'schema_version': 1, 'status': 'pending', 'research_only': True,
              'publisher_release': 'v1.4.2', 'ipc_version': VERSION}
    if mapping_factory is None:
        if sys.platform != 'win32' or ctypes.sizeof(ctypes.c_void_p) != 8:
            report['status'] = 'requires_windows_x64_python'
            return report
        mapping_factory = _WindowsMapping
    mapping = None
    try:
        mapping = mapping_factory()
        data = read_consistent(mapping)
        sample = decode_snapshot(data, now_ms=mapping.now_ms())
        report['sample'] = sample
        report['status'] = 'stale' if not sample['fresh'] else (
            'sample_available' if sample['in_battle'] else 'publisher_idle')
    except MappingError as error:
        report['status'] = ('feed_missing' if error.operation == 'OpenFileMappingW'
                            and error.code == 2 else 'feed_access_denied' if error.code == 5
                            else 'mapping_failed')
        report['error'] = {'api': error.operation, 'win32_error': error.code}
    except FeedError as error:
        report['status'] = 'publisher_busy' if str(error) == 'writer_busy' else 'invalid_feed'
        report['reason'] = str(error)
    finally:
        if mapping is not None:
            try:
                mapping.close()
            except MappingError as error:
                report.pop('sample', None)
                report['status'] = 'cleanup_failed'
                report['error'] = {'api': error.operation, 'win32_error': error.code}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = probe()
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report['status'] in ('sample_available', 'publisher_idle') else 2


if __name__ == '__main__':
    raise SystemExit(main())
