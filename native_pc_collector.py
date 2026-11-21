"""Publish verified Windows counters through the shared battle lifecycle."""

import hashlib
from pathlib import Path
import struct
import time

from collector import ProcessChanged, SampleUnavailable, SnapshotGap, UnsupportedVersion
from connection import ConnectionDiagnostics, DetectionStatus, VersionStatus
from native_pc.probe_native_pc import ApiFailure, KNOWN_FILES
from native_pc.probe_native_pc_battle import NativePcBattleProbe, ResearchFailure
from native_pc.probe_native_pc_presence import capture_presence
from native_pc.probe_native_pc_roster import capture_roster
from native_pc.probe_native_pc_rows import capture_character_rows
from roster import CharacterEntry, RosterSnapshot


BUILD_ID = 'win-x64:' + KNOWN_FILES['GameAssembly.dll']
SNAPSHOT_ONLY = '官服 PC 当前提供累计统计。逐次伤害、技能期与护盾吸收暂无数据。'


def _capture_fields(read, context):
    fields = {}
    for key, capture in (('actor_rows', capture_character_rows), ('roster', capture_roster),
                         ('presence', capture_presence)):
        try:
            fields.update(capture(read, context))
        except (ApiFailure, ResearchFailure):
            fields.update({key: None, f'{key}_status': 'unavailable',
                           f'{key}_reason': 'optional_read_failed'})
    return fields


def _roster(sample):
    raw = sample['roster']
    side = raw['local_side']
    presence = sample.get('presence')
    present_known = (sample.get('presence_status') == 'available'
                     and presence is not None and presence['local_side'] == side)
    present = (frozenset(unit['data_uid'] for unit in presence['entities']
                         if unit['player_side'] == side and unit['borned']
                         and not unit['is_disappeared']) if present_known else frozenset())
    return RosterSnapshot(
        frame=sample['frame'], local_player_side=side,
        entries=tuple(CharacterEntry(cid=entry['id'], unique_id=entry['unique_id'],
                                    is_token=entry['is_token'], is_predefined=entry['is_predefined'],
                                    host_key=entry['host_key'], host_uid=entry['host_uid'],
                                    player_side=entry['side']) for entry in raw['entries']),
        present_unique_ids=present, present_known=present_known,
    )


class NativePcCollector:
    """Own the read handle and stage the pair acquired under one frame guard."""

    def __init__(self, game_root, *, probe_factory=None):
        self.game_root = Path(game_root).expanduser() if game_root else None
        self.probe_factory = probe_factory or NativePcBattleProbe
        self.process = None
        self._pending = None
        self.event_capability = dict(status='unavailable', error=SNAPSHOT_ONLY)
        self.diagnostics = ConnectionDiagnostics(platform='native_pc', version_expected=BUILD_ID,
            native_game_root=str(self.game_root) if self.game_root is not None else None)

    def connect(self, *, locate=False):
        if self.process is not None:
            raise RuntimeError('官服 PC 采集器已连接')
        if self.game_root is None:
            self.diagnostics.status = DetectionStatus.ERROR
            self.diagnostics.code = 'native_root_required'
            self.diagnostics.error = '请在设置中填写包含 Arknights.exe 的官服 PC 游戏目录。'
            raise SampleUnavailable(self.diagnostics.error)
        try:
            self.process = self.probe_factory(self.game_root)
        except ResearchFailure as exc:
            diag = self.diagnostics
            diag.details = dict(reason=exc.reason, **(exc.diagnostic or {}))
            diag.status = DetectionStatus.ERROR
            diag.code = 'native_unavailable'
            if exc.status == 'unsupported_build':
                diag.code = 'native_unsupported_build'
                diag.version_status = VersionStatus.INCOMPATIBLE
                diag.error = '官服 PC 客户端版本未通过校验。请确认游戏目录，或等待本版本适配。'
                raise UnsupportedVersion(diag.error, diagnostics=diag) from exc
            if diag.details.get('win32_error') == 5 and exc.status == 'process_access_failed':
                diag.code = 'native_permission_denied'
                diag.error = 'Windows 拒绝打开游戏只读句柄。请在管理员 PowerShell 中启动 ArkDPS。'
            elif exc.reason == 'process_not_unique':
                diag.status = DetectionStatus.NO_INSTANCE
                diag.error = '请打开设置目录对应的官服 PC 客户端，并保持一个游戏实例。'
            else:
                diag.error = f'官服 PC 客户端暂不可读取（{exc.reason}）。'
            raise SampleUnavailable(diag.error) from exc
        diag = self.diagnostics
        diag.status = DetectionStatus.READY_UNIQUE
        diag.connection_ok = True
        diag.version_status = VersionStatus.COMPATIBLE
        diag.version_actual = BUILD_ID
        diag.process_pid = self.process.pid
        diag.message = '官服 PC 已连接 · 只读累计统计'
        diag.details = {'build': self.process.build, 'fixed_module_bytes_match': True}

    def locate_lifecycle(self):
        if self.process is None:
            raise SampleUnavailable('官服 PC 采集器尚未连接')

    def read_lifecycle(self):
        self._pending = None
        self.locate_lifecycle()
        report = self.process.sample(extra_reader=_capture_fields)
        status = report['status']
        if status == 'waiting_for_battle' and report.get('reason') == 'singleton_is_null':
            return dict(battle=None, state=None, ready=False, transiting=False, source='', target='')
        if status in ('process_exited', 'process_changed'):
            raise ProcessChanged('官服 PC 游戏进程已变化，正在重新连接。')
        if status in ('waiting_game_init', 'battle_not_ready', 'sample_retry'):
            raise SampleUnavailable(f'官服 PC 战局正在准备或跨帧重试（{report.get("reason")}）')
        if status != 'sample_available':
            raise SnapshotGap(f'官服 PC 采样暂不可用（{report.get("reason")}）')
        raw = report['sample']
        roots = self.process.battle_identity
        identity = hashlib.sha256(struct.pack(f'<{len(roots)}Q', *roots)).hexdigest()
        battle = (identity,)
        stage_id = raw.get('stage_id') if raw.get('stage_status') == 'available' else None
        metadata = dict(evidence='native_pc_guarded_memory', platform='native_pc', source_build=BUILD_ID,
                        stage_id=stage_id, practice=None, outcome=raw.get('outcome'))
        lifecycle = dict(battle=battle, state=raw['battle_state'], ready=True, transiting=False,
                         source='', target='', metadata=metadata)
        self._pending = (lifecycle, report)
        return lifecycle

    def read_guarded_pair(self, lifecycle):
        pending, self._pending = self._pending, None
        if pending is None or pending[0] is not lifecycle:
            raise SnapshotGap('官服 PC 的同帧累计及编队数据已失效')
        report = pending[1]
        raw = report['sample']
        for key in ('actor_rows', 'roster'):
            if raw.get(f'{key}_status') != 'available' or raw.get(key) is None:
                raise SnapshotGap(f'官服 PC 同帧数据不完整（{raw.get(f"{key}_reason", key)}）')
        rows = raw['actor_rows']
        sample = dict(session=':'.join(map(str, (*self.process.process_instance, *lifecycle['battle']))),
                      frame=raw['frame'], game_time=raw['game_time'], battle_identity=lifecycle['battle'],
                      state=raw['battle_state'], speed=raw.get('speed_level'), paused=raw.get('paused'),
                      presence_duration_supported=False,
                      rows=rows, global_total=raw['total_damage'], global_heal=raw['total_heal'],
                      sum_damage=sum(row['damage'] for row in rows),
                      damage_basis='client character counters; includes overkill; excludes healing',
                      read_ms=report.get('acquisition', {}).get('duration_ms'),
                      observed_at=time.time(), captured_monotonic=time.monotonic())
        return sample, _roster(raw)

    def close(self):
        self._pending = None
        if self.process is not None:
            self.process.close()
            self.process = None
