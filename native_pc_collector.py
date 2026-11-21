"""Publish verified Windows counters through the shared battle lifecycle."""

import hashlib
import json
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
from native_pc.probe_native_pc_presence import capture_presence
from native_pc.probe_native_pc_roster import capture_roster
from native_pc.probe_native_pc_rows import capture_character_rows
from native_pc.probe_native_pc_stage_spawns import capture_stage_spawns
from roster import CharacterEntry, RosterSnapshot
from source_classification import classify_unmapped_source


BUILD_ID = 'win-x64:' + KNOWN_FILES['GameAssembly.dll']
SNAPSHOT_ONLY = '官服 PC 当前提供累计统计。逐次伤害、技能期与护盾吸收暂无数据。'
ENEMY_SAMPLE_INTERVAL = 1.0
ENEMY_MAX_AGE = 2.5
STAGE_PLAN_RETRY_INTERVAL = 1.0
STAGE_PLAN_RECHECK_INTERVAL = 5.0


def _flow(report):
    return report.get('game_flow') if report.get('game_flow_status') == 'available' else None


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


def _duration_membership_verified(raw, roster):
    if not roster.present_known or type(roster.local_player_side) is not int:
        return False
    side = roster.local_player_side
    local = {}
    for entry in roster.entries:
        if entry.player_side == side:
            previous = local.setdefault(entry.unique_id, entry)
            if previous != entry:
                return False
    for unit in raw['presence']['entities']:
        if unit['player_side'] != side or not unit['borned'] or unit['is_disappeared']:
            continue
        entry = local.get(unit['data_uid'])
        if unit.get('inited') is not True:
            return False
        if entry is None:
            # Known map devices do not contribute to a squad operator's time.
            if not classify_unmapped_source(unit.get('id')):
                return False
        elif (unit.get('id'), unit.get('is_token'), unit.get('is_predefined')) != (
                entry.cid, entry.is_token, entry.is_predefined):
            return False
    return True


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
        self._stage_plan_scope = None
        self._stage_plan_candidate = None
        self._stage_plan_confirmed = None
        self._next_stage_plan = float('-inf')
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
        report = self.process.sample(extra_reader=self._capture)
        status = report['status']
        if status == 'waiting_for_battle' and report.get('reason') == 'singleton_is_null':
            self._reset_enemy_observation()
            self._reset_stage_plan()
            return dict(battle=None, state=None, ready=False, transiting=False, source='', target='')
        if status in ('process_exited', 'process_changed'):
            self._reset_stage_plan()
            raise ProcessChanged('官服 PC 游戏进程已变化，正在重新连接。')
        if status in ('waiting_game_init', 'battle_not_ready', 'sample_retry'):
            raise SampleUnavailable(f'官服 PC 战局正在准备或跨帧重试（{report.get("reason")}）')
        if status != 'sample_available':
            self._reset_stage_plan()
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
        plan_scope = ((battle, stage_id) if isinstance(stage_id, str) and stage_id
                      and stage_id.strip() == stage_id else None)
        if plan_scope != self._stage_plan_scope:
            self._reset_stage_plan(plan_scope)
        if ('stage_spawns_status' in raw and raw['stage_spawns_status'] != 'available'):
            self._invalidate_stage_plan()
        flow = _flow(raw)
        abandoned = (raw['battle_state'] == 2 and raw.get('battle_result') == 0
                     and finish_transition(flow, stage_id))
        evidence = 'native_pc_guarded_memory'
        if abandoned:
            evidence += '+native_pc_game_flow'
        # The squad page hint also read True during a confirmed ordinary action.
        # Keep mode unknown until it can be established for the actual battle.
        metadata = dict(evidence=evidence, platform='native_pc', source_build=BUILD_ID,
                        stage_id=stage_id, practice=None,
                        outcome='abandoned' if abandoned else raw.get('outcome'))
        lifecycle = dict(battle=battle, state=raw['battle_state'], ready=True, transiting=abandoned,
                         source=flow['source_scene'].casefold() if abandoned else '',
                         target='battle_finish' if abandoned else '', metadata=metadata)
        self._pending = (lifecycle, report)
        return lifecycle

    def _capture(self, read, context):
        fields = _capture_fields(read, context)
        now = time.monotonic()
        enemy_due = now >= self._next_enemy
        if enemy_due:
            self._next_enemy = now + ENEMY_SAMPLE_INTERVAL
            fields.update(capture_enemies(read, context))
        fields.update(capture_game_flow(read, context))
        # Never combine the two expensive optional witnesses. Keep the existing
        # counters, roster, presence and flow ahead of configuration work.
        if not enemy_due and now >= self._next_stage_plan:
            interval = (STAGE_PLAN_RECHECK_INTERVAL if self._stage_plan_confirmed
                        else STAGE_PLAN_RETRY_INTERVAL)
            self._next_stage_plan = now + interval
            try:
                fields.update(capture_stage_spawns(read, context))
            except (ApiFailure, ResearchFailure):
                fields.update(stage_spawns_status='unavailable', stage_spawns=None,
                              stage_spawns_reason='optional_stage_plan_read_failed')
        return fields

    def _reset_stage_plan(self, scope=None):
        self._stage_plan_scope = scope
        self._stage_plan_candidate = self._stage_plan_confirmed = None
        self._next_stage_plan = float('-inf')

    def _invalidate_stage_plan(self, candidate=None):
        self._stage_plan_candidate = candidate
        self._stage_plan_confirmed = None
        self._next_stage_plan = min(self._next_stage_plan,
                                    time.monotonic() + STAGE_PLAN_RETRY_INTERVAL)

    def _observe_stage_plan(self, raw):
        if self._stage_plan_scope is None or 'stage_spawns_status' not in raw:
            return
        payload = raw.get('stage_spawns')
        try:
            if (raw['stage_spawns_status'] != 'available' or not isinstance(payload, dict)
                    or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1
                    or payload.get('scope') != 'current_stage_waves_spawn_structure'
                    or not isinstance(payload.get('spawn_actions'), list)):
                raise ValueError('unavailable stage structure')
            plan = dict(fixed_count=payload.get('scheduled_counted_spawn_count'),
                        conditional_actions=payload.get('conditional_spawn_action_count'),
                        branches_present=payload.get('branch_container_present'))
            if (any(type(plan[key]) is not int or plan[key] < 0
                    for key in ('fixed_count', 'conditional_actions'))
                    or type(plan['branches_present']) is not bool):
                raise ValueError('invalid stage structure counters')
            # Include all action positions, IDs and flags, not just the total.
            # The independent read-cost field is deliberately outside this hash.
            signature = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        except (ValueError, TypeError):
            self._invalidate_stage_plan()
            return
        previous = self._stage_plan_candidate
        if previous is None or previous[0] != signature:
            self._invalidate_stage_plan((signature, raw['frame']))
        elif raw['frame'] > previous[1]:
            self._stage_plan_confirmed = (raw['frame'], plan)
            self._next_stage_plan = max(self._next_stage_plan,
                                        time.monotonic() + STAGE_PLAN_RECHECK_INTERVAL)

    def _with_stage_plan(self, view, frame):
        confirmed = self._stage_plan_confirmed
        plan = (dict(confirmed[1]) if confirmed is not None and type(frame) is int
                and frame >= confirmed[0] else None)
        return dict(view, stage_plan=plan)

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
        self._observe_stage_plan(raw)
        rows = raw['actor_rows']
        battle = lifecycle['battle']
        if self._enemy_battle != battle:
            self._reset_enemy_observation()
            self._enemy_battle = battle
        if 'enemies_status' in raw:
            observed = time.monotonic()
            self._enemy_snapshot = (observed, raw['frame'], raw['game_time'], raw)
            self._next_enemy = max(self._next_enemy, observed + ENEMY_SAMPLE_INTERVAL)
        roster = _roster(raw)
        sample = dict(session=':'.join(map(str, (*self.process.process_instance, *lifecycle['battle']))),
                      frame=raw['frame'], game_time=raw['game_time'], battle_identity=lifecycle['battle'],
                      state=raw['battle_state'], speed=raw.get('speed_level'), paused=raw.get('paused'),
                      presence_duration_supported=_duration_membership_verified(raw, roster),
                      rows=rows, global_total=raw['total_damage'], global_heal=raw['total_heal'],
                      sum_damage=sum(row['damage'] for row in rows),
                      damage_basis='client character counters; includes overkill; excludes healing',
                      read_ms=report.get('acquisition', {}).get('duration_ms'),
                      observed_at=time.time(), captured_monotonic=time.monotonic())
        return sample, roster

    def read_enemies(self, *, game_time, frame=None, our_damage=None, **_events):
        saved = self._enemy_snapshot
        if saved is None:
            return self._with_stage_plan(native_enemy_view({}, frame=None, game_time=None, catalog={}), frame)
        observed, enemy_frame, enemy_time, raw = saved
        if (frame is None or game_time is None or frame < enemy_frame or game_time < enemy_time
                or time.monotonic() - observed > ENEMY_MAX_AGE):
            self._enemy_observations.note_gap()
            return self._with_stage_plan(native_enemy_view({'enemies_reason': 'enemy_sample_stale'},
                                         frame=enemy_frame, game_time=enemy_time, catalog={}), frame)
        if self._enemy_catalog is None and raw.get('enemies_status') == 'available':
            self._enemy_catalog = _load_enemy_db() or {}
        return self._with_stage_plan(self._enemy_observations.observe(
            raw, frame=enemy_frame, game_time=enemy_time, catalog=self._enemy_catalog or {}), frame)

    def close(self):
        self._pending = None
        self._reset_stage_plan()
        self._reset_enemy_observation()
        if self.process is not None:
            self.process.close()
            self.process = None
