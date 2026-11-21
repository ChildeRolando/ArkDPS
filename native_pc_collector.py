"""Publish verified Windows counters through the shared battle lifecycle."""

import hashlib
from pathlib import Path
import struct
import time

from collector import ProcessChanged, SampleUnavailable, SnapshotGap, UnsupportedVersion
from connection import ConnectionDiagnostics, DetectionStatus, VersionStatus
from enemy_telemetry import NativeEnemyObservations, _load_enemy_db, native_enemy_view
from native_pc.probe_native_pc import ApiFailure, KNOWN_FILES
from native_pc.probe_native_pc_battle import NativePcBattleProbe, ResearchFailure
from native_pc.probe_native_pc_enemies import capture_enemies
from native_pc.probe_native_pc_flow import capture_game_flow, finish_transition
from native_pc.probe_native_pc_practice import capture_practice_selection
from native_pc.probe_native_pc_presence import capture_presence
from native_pc.probe_native_pc_roster import capture_roster
from native_pc.probe_native_pc_rows import capture_character_rows
from roster import CharacterEntry, RosterSnapshot


BUILD_ID = 'win-x64:' + KNOWN_FILES['GameAssembly.dll']
SNAPSHOT_ONLY = '官服 PC 当前提供累计统计。逐次伤害、技能期与护盾吸收暂无数据。'
ENEMY_SAMPLE_INTERVAL = 1.0
ENEMY_MAX_AGE = 2.5
OPENING_MAX_AGE = 15.0
OPENING_MAX_FRAME = 150


def _flow(report):
    return report.get('game_flow') if report.get('game_flow_status') == 'available' else None


def _battle_loader(flow):
    return flow and flow['source_scene'] == 'home' and flow['target_scene'] == 'battle_loader'


def _capture_fields(read, context):
    fields = {}
    for key, capture in (('actor_rows', capture_character_rows), ('roster', capture_roster),
                         ('presence', capture_presence)):
        try:
            fields.update(capture(read, context))
        except (ApiFailure, ResearchFailure) as error:
            reason = (error.reason if isinstance(error, ResearchFailure)
                      else f'{error.operation}({error.code})')
            fields.update({key: None, f'{key}_status': 'unavailable',
                           f'{key}_reason': reason})
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

    enemy_frame_bound = True

    def __init__(self, game_root, *, probe_factory=None):
        self.game_root = Path(game_root).expanduser() if game_root else None
        self.probe_factory = probe_factory or NativePcBattleProbe
        self.process = None
        self._pending = None
        self._enemy_battle = None
        self._enemy_snapshot = None
        self._enemy_catalog = None
        self._enemy_observations = NativeEnemyObservations()
        self._next_enemy = float('-inf')
        self._opening_choice = None
        self._practice_battle = None
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
        diag.native_game_root = str(self.game_root.resolve())
        diag.process_pid = self.process.pid
        diag.process_created_filetime = self.process.process_instance[1]
        diag.message = '官服 PC 已连接 · 只读累计统计'
        diag.details = {'build': self.process.build, 'fixed_module_bytes_match': True}

    def locate_lifecycle(self):
        if self.process is None:
            raise SampleUnavailable('官服 PC 采集器尚未连接')

    def read_lifecycle(self):
        self._pending = None
        self.locate_lifecycle()
        report = self.process.sample(extra_reader=self._capture, idle_reader=self._capture_idle)
        status = report['status']
        if status == 'waiting_for_battle' and report.get('reason') == 'singleton_is_null':
            self._observe_opening(report)
            self._reset_enemy_observation()
            return dict(battle=None, state=None, ready=False, transiting=False, source='', target='')
        if status in ('process_exited', 'process_changed'):
            raise ProcessChanged('官服 PC 游戏进程已变化，正在重新连接。')
        if status in ('waiting_game_init', 'battle_not_ready', 'sample_retry'):
            raise SampleUnavailable(f'官服 PC 战局正在准备或跨帧重试（{report.get("reason")}）')
        if status != 'sample_available':
            diagnostic = report.get('diagnostic')
            detail = (f'{diagnostic["api"]}({diagnostic["win32_error"]})'
                      if diagnostic else report.get('reason'))
            raise SnapshotGap(f'官服 PC 采样暂不可用（{detail}）')
        raw = report['sample']
        # The probe can see a reset while the roster is unavailable. Keep that
        # boundary even if the next publishable frame has caught up to the old one.
        identity_words = (*self.process.battle_identity, raw['battle_serial'])
        identity = hashlib.sha256(struct.pack(f'<{len(identity_words)}Q', *identity_words)).hexdigest()
        battle = (identity,)
        stage_id = raw.get('stage_id') if raw.get('stage_status') == 'available' else None
        flow = _flow(raw)
        practice = self._battle_practice(raw, flow, battle, stage_id)
        abandoned = (raw['battle_state'] == 2 and raw.get('battle_result') == 0
                     and finish_transition(flow, stage_id))
        evidence = 'native_pc_guarded_memory'
        if practice is not None:
            evidence += '+native_pc_squad_loader'
        if abandoned:
            evidence += '+native_pc_game_flow'
        metadata = dict(evidence=evidence, platform='native_pc', source_build=BUILD_ID,
                        stage_id=stage_id, practice=practice,
                        outcome='abandoned' if abandoned else raw.get('outcome'))
        lifecycle = dict(battle=battle, state=raw['battle_state'], ready=True, transiting=abandoned,
                         source=flow['source_scene'].casefold() if abandoned else '',
                         target='battle_finish' if abandoned else '', metadata=metadata)
        self._pending = (lifecycle, report)
        return lifecycle

    def _capture(self, read, context):
        fields = _capture_fields(read, context)
        now = time.monotonic()
        if now >= self._next_enemy:
            self._next_enemy = now + ENEMY_SAMPLE_INTERVAL
            fields.update(capture_enemies(read, context))
        fields.update(capture_game_flow(read, context))
        return fields

    def _capture_idle(self, read, context):
        fields = capture_game_flow(read, context)
        flow = _flow(fields)
        if _battle_loader(flow) and flow['transiting']:
            selection = capture_practice_selection(read, context)
            if (selection['practice_selection_status'] == 'available'
                    and _flow(capture_game_flow(read, context)) == flow):
                fields.update(selection)
        return fields

    def _observe_opening(self, report):
        self._practice_battle = None
        flow = _flow(report)
        if flow is not None and not _battle_loader(flow):
            self._opening_choice = None
        if (_battle_loader(flow) and flow['transiting']
                and report.get('practice_selection_status') == 'available'):
            raw = report['practice_selection']
            self._opening_choice = (raw['stage_id'].casefold(), raw['practice'], time.monotonic())

    def _battle_practice(self, raw, flow, identity, stage):
        choice, self._opening_choice = self._opening_choice, None
        if self._practice_battle is not None and self._practice_battle[0] != identity:
            self._practice_battle = None
        if not stage:
            return None
        short_stage = stage.rsplit('/', 1)[-1].casefold().removeprefix('level_')
        if (choice and choice[0] == short_stage and _battle_loader(flow) and not flow['transiting']
                and raw['battle_state'] == 2 and raw['frame'] <= OPENING_MAX_FRAME
                and time.monotonic() - choice[2] <= OPENING_MAX_AGE):
            self._practice_battle = (identity, stage, choice[1])
        if self._practice_battle is not None and self._practice_battle[:2] == (identity, stage):
            return self._practice_battle[2]
        return None

    def _reset_enemy_observation(self):
        self._enemy_battle = None
        self._enemy_snapshot = None
        self._next_enemy = float('-inf')
        self._enemy_observations.reset()

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
        battle = lifecycle['battle']
        if self._enemy_battle != battle:
            self._reset_enemy_observation()
            self._enemy_battle = battle
        if 'enemies_status' in raw:
            observed = time.monotonic()
            self._enemy_snapshot = (observed, raw['frame'], raw['game_time'], raw)
            self._next_enemy = max(self._next_enemy, observed + ENEMY_SAMPLE_INTERVAL)
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

    def read_enemies(self, *, game_time, frame=None, our_damage=None, **_events):
        saved = self._enemy_snapshot
        if saved is None:
            return native_enemy_view({}, frame=None, game_time=None, catalog={})
        observed, enemy_frame, enemy_time, raw = saved
        if (frame is None or game_time is None or frame < enemy_frame or game_time < enemy_time
                or time.monotonic() - observed > ENEMY_MAX_AGE):
            return native_enemy_view({'enemies_reason': 'enemy_sample_stale'},
                                     frame=enemy_frame, game_time=enemy_time, catalog={})
        if self._enemy_catalog is None and raw.get('enemies_status') == 'available':
            self._enemy_catalog = _load_enemy_db() or {}
        return self._enemy_observations.observe(raw, frame=enemy_frame, game_time=enemy_time,
                                                catalog=self._enemy_catalog or {})

    def close(self):
        self._pending = None
        self._opening_choice = self._practice_battle = None
        self._reset_enemy_observation()
        if self.process is not None:
            self.process.close()
            self.process = None
