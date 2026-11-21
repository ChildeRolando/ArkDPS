"""Synthetic ABI fixtures exercise only an independently owned IPC mapping."""

import ctypes
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
from uuid import uuid4

import pytest


SPEC = importlib.util.spec_from_file_location(
    'probe_arkhelper_feed', Path(__file__).parents[1] / 'acceptance' / 'probe_arkhelper_feed.py')
feed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feed)


def snapshot():
    value = feed.BattleSnapshot()
    value.magic, value.version, value.size = feed.MAGIC, feed.VERSION, feed.SNAPSHOT_BYTES
    value.sequence, value.published_ms = 2, 1000
    value.battle_serial, value.in_battle, value.battle_state = 7, 1, 2
    value.frame, value.time_seconds = 300, 10
    value.level_id = b'level_test'
    value.flags = 1 | 4 | 32
    value.stats_flags = 3
    value.damage_count = value.operator_count = 1
    value.team_metric_mask = (1 << 0) | (1 << 4)
    value.team_metrics[0], value.team_metrics[4], value.device_metrics[0] = 123, 50, 3
    for row in (value.damage[0], value.operators[0]):
        row.flags, row.actor_id, row.actor_name = 1, b'char_test', '测试'.encode()
        row.damage_type, row.total_damage, row.dps, row.share = 1, 123, 12.3, 0.5
        row.metric_mask, row.metrics[0] = 1, 123
    value.operator_ids[0] = 123456
    return value


class CopyMapping:
    def __init__(self, data):
        self.data = data
        self.closed = False
        self.calls = []

    def read(self, offset, size):
        self.calls.append((offset, size))
        return self.data[offset:offset + size]

    def now_ms(self):
        return 1100

    def close(self):
        self.closed = True


def test_abi_offsets_and_sizes_match_the_published_windows_contract():
    expected = ((feed.Vec2, 8), (feed.Float2, 8), (feed.SpawnEntry, 188),
                (feed.EnemyEntry, 248), (feed.DamageEntry, 296), (feed.UnitEntry, 176),
                (feed.PathPoint, 12), (feed.PathEntry, 1184), (feed.BattleSnapshot, 146792))
    assert [(kind.__name__, ctypes.sizeof(kind)) for kind, _ in expected] == [
        (kind.__name__, size) for kind, size in expected]
    assert feed.BattleSnapshot.level_id.offset == 88
    assert feed.BattleSnapshot.spawns.offset == 216
    assert feed.BattleSnapshot.team_metrics.offset == 127128
    assert feed.BattleSnapshot.operators.offset == 127240
    assert feed.BattleSnapshot.device_metrics.offset == 146696
    assert feed.UnitEntry.tracking_id.offset == 24


def test_normalizes_metrics_masks_and_preserves_all_coverage_flags():
    mapping = CopyMapping(bytes(snapshot()))
    result = feed.probe(mapping_factory=lambda: mapping)
    assert result['status'] == 'sample_available'
    sample = result['sample']
    assert sample['team_metrics']['damage'] == 123
    assert sample['team_metrics']['heal'] == 50
    assert sample['team_metrics']['arts'] is None
    assert sample['device_metrics']['damage'] == 3
    assert sample['flags']['paused'] and sample['flags']['enemies_truncated']
    assert sample['partial_or_lost_events'] and sample['actor_rows_truncated']
    assert sample['operator_instances'][0]['publisher_instance_id'] == '123456'
    assert sample['operator_instances'][0]['actor_name'] == '测试'
    assert len(sample['damage_rows']) == 1
    assert mapping.closed


@pytest.mark.parametrize('field,value,reason', [
    ('magic', 0, 'magic_mismatch'), ('version', 12, 'version_mismatch'),
    ('size', 262144, 'writer_size_mismatch'), ('in_battle', 2, 'invalid_in_battle'),
    ('battle_state', 4, 'invalid_battle_state'), ('flags', 128, 'unknown_battle_flags'),
    ('stats_flags', 4, 'unknown_stats_flags'), ('operator_reserved', 1, 'nonzero_reserved'),
    ('time_seconds', float('nan'), 'non_finite_number'),
    ('time_seconds', -1, 'invalid_battle_time'),
    ('published_ms', 1101, 'clock_mismatch'),
])
def test_rejects_invalid_snapshot_scalars(field, value, reason):
    sample = snapshot()
    setattr(sample, field, value)
    with pytest.raises(feed.FeedError, match=reason):
        feed.decode_snapshot(bytes(sample), now_ms=1100)


@pytest.mark.parametrize('field,maximum', [('spawn_count', 48), ('enemy_count', 48),
    ('damage_count', 64), ('unit_count', 64), ('path_count', 64), ('operator_count', 64)])
def test_rejects_every_oversized_count(field, maximum):
    sample = snapshot()
    setattr(sample, field, maximum + 1)
    with pytest.raises(feed.FeedError, match='invalid_' + field):
        feed.decode_snapshot(bytes(sample), now_ms=1100)


@pytest.mark.parametrize('field,value,reason', [
    ('flags', 8, 'unknown_damage_flags'), ('damage_type', 6, 'invalid_damage_type'),
    ('metric_mask', 4096, 'unknown_metric_mask'), ('reserved', 1, 'nonzero_reserved'),
    ('dps', float('inf'), 'non_finite_number'), ('share', 2, 'invalid_damage_value'),
    ('total_damage', -1, 'invalid_damage_value'),
    ('actor_id', b'x' * 96, 'unterminated_utf8'),
    ('actor_name', b'\xff', 'invalid_utf8'),
])
@pytest.mark.parametrize('collection', ['damage', 'operators'])
def test_checks_aggregates_and_operator_instances_equally(collection, field, value, reason):
    sample = snapshot()
    setattr(getattr(sample, collection)[0], field, value)
    with pytest.raises(feed.FeedError, match=reason):
        feed.decode_snapshot(bytes(sample), now_ms=1100)


def test_nonfinite_metric_and_unknown_team_mask_are_rejected():
    sample = snapshot()
    sample.damage[0].metrics[0] = float('nan')
    with pytest.raises(feed.FeedError, match='non_finite_number'):
        feed.decode_snapshot(bytes(sample), now_ms=1100)
    sample = snapshot()
    sample.team_metric_mask = 4096
    with pytest.raises(feed.FeedError, match='unknown_metric_mask'):
        feed.decode_snapshot(bytes(sample), now_ms=1100)


def test_nonfinite_device_metrics_and_unterminated_level_are_rejected():
    sample = snapshot()
    sample.device_metrics[3] = float('inf')
    with pytest.raises(feed.FeedError, match='non_finite_number'):
        feed.decode_snapshot(bytes(sample), now_ms=1100)
    sample = snapshot()
    sample.level_id = b'x' * 128
    with pytest.raises(feed.FeedError, match='unterminated_utf8'):
        feed.decode_snapshot(bytes(sample), now_ms=1100)


@pytest.mark.parametrize('by_flag', [True, False])
def test_stale_publisher_is_explicit_and_never_reported_as_live(by_flag):
    sample = snapshot()
    sample.flags |= 2 if by_flag else 0
    mapping = CopyMapping(bytes(sample))
    mapping.now_ms = lambda: 1100 if by_flag else 3001
    result = feed.probe(mapping_factory=lambda: mapping)
    assert result['status'] == 'stale'
    assert result['sample']['fresh'] is False


def test_idle_snapshot_is_a_publisher_signal():
    sample = snapshot()
    sample.in_battle, sample.battle_state = 0, -1
    mapping = CopyMapping(bytes(sample))
    assert feed.probe(mapping_factory=lambda: mapping)['status'] == 'publisher_idle'


def test_truncation_and_unknown_writer_layout_fail_closed():
    data = bytes(snapshot())
    with pytest.raises(feed.FeedError, match='snapshot_size_mismatch'):
        feed.decode_snapshot(data[:-1], now_ms=1100)
    mapping = CopyMapping(data[:-1])
    result = feed.probe(mapping_factory=lambda: mapping)
    assert result['status'] == 'invalid_feed'
    assert result['reason'] == 'mapping_truncated'
    assert mapping.closed


def test_seqlock_retries_odd_or_changed_writes_with_bounded_attempts():
    data = bytes(snapshot())
    mapping = CopyMapping(data)
    responses = iter([struct.pack('<I', 1), struct.pack('<I', 2), data,
                      struct.pack('<I', 4), struct.pack('<I', 2), data, struct.pack('<I', 2)])
    mapping.read = lambda offset, size: next(responses)
    assert feed.read_consistent(mapping) == data
    busy = snapshot()
    busy.sequence = 1
    mapping = CopyMapping(bytes(busy))
    with pytest.raises(feed.FeedError, match='writer_busy'):
        feed.read_consistent(mapping)
    assert len(mapping.calls) == 8


def test_copy_sequence_must_match_both_live_sequence_reads():
    data = bytes(snapshot())
    mapping = CopyMapping(data)
    mapping.read = lambda offset, size: data if size != 4 else struct.pack('<I', 4)
    with pytest.raises(feed.FeedError, match='writer_busy'):
        feed.read_consistent(mapping)


def test_busy_publisher_is_transient_without_a_sample():
    sample = snapshot()
    sample.sequence = 1
    mapping = CopyMapping(bytes(sample))
    result = feed.probe(mapping_factory=lambda: mapping)
    assert result['status'] == 'publisher_busy'
    assert 'sample' not in result
    assert mapping.closed


def test_missing_or_denied_feed_remains_unavailable():
    def missing():
        raise feed.MappingError('OpenFileMappingW', 2)
    def denied():
        raise feed.MappingError('OpenFileMappingW', 5)
    assert feed.probe(mapping_factory=missing)['status'] == 'feed_missing'
    assert feed.probe(mapping_factory=denied)['status'] == 'feed_access_denied'


def test_cleanup_failure_prevents_a_success_report():
    mapping = CopyMapping(bytes(snapshot()))
    def fail_close():
        raise feed.MappingError('UnmapViewOfFile', 6)
    mapping.close = fail_close
    result = feed.probe(mapping_factory=lambda: mapping)
    assert result['status'] == 'cleanup_failed'
    assert 'sample' not in result
    assert result['error'] == {'api': 'UnmapViewOfFile', 'win32_error': 6}


PUBLISHER = r'''
import ctypes,sys
from ctypes import wintypes as w
name,size=sys.argv[1],int(sys.argv[2])
data=sys.stdin.buffer.read(size)
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileMappingW.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,w.DWORD,w.DWORD,w.LPCWSTR]
k.CreateFileMappingW.restype=w.HANDLE
k.MapViewOfFile.argtypes=[w.HANDLE,w.DWORD,w.DWORD,w.DWORD,ctypes.c_size_t]
k.MapViewOfFile.restype=ctypes.c_void_p
k.UnmapViewOfFile.argtypes=[ctypes.c_void_p]
k.CloseHandle.argtypes=[w.HANDLE]
h=k.CreateFileMappingW(ctypes.c_void_p(-1),None,4,0,size,name)
if not h:raise ctypes.WinError(ctypes.get_last_error())
view=k.MapViewOfFile(h,2,0,0,size)
if not view:
 k.CloseHandle(h)
 raise ctypes.WinError(ctypes.get_last_error())
try:
 ctypes.memmove(view,data,len(data))
 print('READY',flush=True)
 sys.stdin.buffer.read(1)
finally:
 k.UnmapViewOfFile(view)
 k.CloseHandle(h)
'''


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows named mapping API')
def test_reads_a_unique_synthetic_mapping_from_an_independent_process():
    name = 'Local\\ArkDPS.Test.ArkHelper.' + uuid4().hex
    child = subprocess.Popen([sys.executable, '-B', '-c', PUBLISHER, name,
                              str(feed.SNAPSHOT_BYTES)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=0x08000000)
    mapping = None
    try:
        child.stdin.write(bytes(snapshot()))
        child.stdin.flush()
        assert child.stdout.readline() == b'READY\r\n'
        mapping = feed._WindowsMapping(name)
        data = feed.read_consistent(mapping)
        result = feed.decode_snapshot(data, now_ms=1100)
        assert result['battle_serial'] == 7
        assert result['team_metrics']['damage'] == 123
    finally:
        if mapping is not None:
            mapping.close()
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0, child.stderr.read().decode(errors='replace')


def test_cli_accepts_no_mapping_override(capsys):
    with pytest.raises(SystemExit) as error:
        feed.main(['--mapping', 'anything'])
    assert error.value.code == 2
    assert 'unrecognized arguments' in capsys.readouterr().err
