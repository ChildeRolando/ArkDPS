"""Publish verified Windows counters through the shared battle lifecycle."""

import hashlib
import json
import math
from pathlib import Path
import re
import struct
import time

from collector import NativeClockBusy, ProcessChanged, SampleUnavailable, SnapshotGap, UnsupportedVersion
from connection import ConnectionDiagnostics, DetectionStatus, VersionStatus
from enemy_telemetry import EnemyTelemetry, NativeEnemyObservations, _load_enemy_db, native_enemy_view
from event_probe import native_autochess_context_bound
from native_pc.probe_native_pc import ApiFailure, KNOWN_FILES
from native_pc.probe_native_pc_actions import capture_actions
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
SNAPSHOT_ONLY = '官服 PC 事件观察尚未连接，当前显示原生累计统计。'
ENEMY_SAMPLE_INTERVAL = 1.0
ENEMY_MAX_AGE = 2.5
STAGE_PLAN_RETRY_INTERVAL = 1.0
STAGE_PLAN_RECHECK_INTERVAL = 5.0
ACTION_JOURNAL_SAMPLE_INTERVAL = 1.0


def _flow(report):
    return report.get('game_flow') if report.get('game_flow_status') == 'available' else None


def _capture_fields(read, context, *, event_identity_only=False):
    fields = {}
    for key, capture in (('actor_rows', capture_character_rows), ('roster', capture_roster),
                         ('presence', capture_presence)):
        if event_identity_only and key in ('roster', 'presence'):
            fields.update({key: None, f'{key}_status': 'not_requested',
                           f'{key}_reason': 'native_event_identity'})
            continue
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

    def __init__(self, game_root, *, probe_factory=None, collect_actions=True, event_identity_only=False):
        self.game_root = Path(game_root).expanduser() if game_root else None
        self.probe_factory = probe_factory or NativePcBattleProbe
        self.collect_actions = collect_actions
        self.event_identity_only = event_identity_only
        self.process = None
        self._pending = None
        self._enemy_battle = None
        self._enemy_snapshot = None
        self._enemy_snapshot_pair = self._enemy_view_scope = self._enemy_scoped_snapshot = None
        self._enemy_catalog = None
        self._enemy_observations = NativeEnemyObservations()
        self._enemy_events = EnemyTelemetry(None)
        self._next_enemy = float('-inf')
        self._stage_plan_scope = None
        self._stage_plan_candidate = None
        self._stage_plan_confirmed = None
        self._next_stage_plan = float('-inf')
        self._next_action_journal = float('-inf')
        self._optional_attempt = self._optional_recovery = None
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

    def observer_binding(self, *, frame, game_time, native_identity, native_stage_id,
                         native_process_pid, native_fixed_time):
        """Name a probe mark for independent validation by the guarded sample.

        The opening mark precedes the native read. It predicts the sampler's
        next serial using the same transition rule, without updating its state.
        The capture barrier then requires the independently sampled identity,
        exact clock and both marks to agree before any event is accepted.
        """
        process = self.process
        if process is None or not process.process_alive():
            raise ProcessChanged('官服 PC 游戏进程已退出，无法确认事件归属。')
        if (type(native_process_pid) is not int or native_process_pid != process.pid
                or not isinstance(native_identity, dict)
                or set(native_identity) != {'controller', 'native', 'logger', 'stats', 'level'}):
            raise SnapshotGap('官服 PC Frida 的进程或战局根无效。')
        roots = []
        for key in ('controller', 'native', 'logger', 'stats', 'level'):
            value = native_identity[key]
            if not isinstance(value, str) or re.fullmatch(r'0x[0-9a-fA-F]{1,16}', value) is None:
                raise SnapshotGap('官服 PC Frida 的战局根格式无效。')
            address = int(value, 16)
            if not 0x10000 <= address <= 0x00007FFFFFFFFFFF or address % 8:
                raise SnapshotGap('官服 PC Frida 的战局根范围无效。')
            roots.append(address)
        if (type(frame) is not int or not 0 <= frame <= 0xFFFFFFFF
                or type(game_time) not in (int, float) or not math.isfinite(game_time)
                or not 0 <= game_time <= 864000
                or not isinstance(native_fixed_time, str)
                or re.fullmatch(r'[0-9]{1,20}', native_fixed_time) is None
                or int(native_fixed_time) > 0x7FFFFFFFFFFFFFFF
                or int(native_fixed_time) / (1 << 32) != game_time
                or not isinstance(native_stage_id, str) or not native_stage_id):
            raise SnapshotGap('官服 PC Frida 的原始逻辑时钟或关卡无效。')
        identity = tuple(roots)
        serial = process.battle_serial_for(identity, frame, int(native_fixed_time), native_stage_id)
        words = (*identity, serial)
        digest = hashlib.sha256(struct.pack(f'<{len(words)}Q', *words)).hexdigest()
        return dict(native_session=':'.join(map(str, (*process.process_instance, digest))),
                    source_build=BUILD_ID)

    def observer_boot_ready(self):
        if self.process is None:
            raise SampleUnavailable('官服 PC 采集器尚未连接。')
        return self.process.observer_boot_ready()

    def locate_lifecycle(self):
        if self.process is None:
            raise SampleUnavailable('官服 PC 采集器尚未连接')

    def read_lifecycle(self):
        self._rollback_optional()
        self._pending = None
        self._enemy_snapshot_pair = None
        self.locate_lifecycle()
        try:
            report = self.process.sample(extra_reader=self._capture)
        except BaseException:
            self._rollback_optional()
            raise
        status = report['status']
        if status != 'sample_available':
            self._rollback_optional()
        if status == 'waiting_for_battle' and report.get('reason') == 'singleton_is_null':
            self._reset_enemy_observation()
            self._reset_stage_plan()
            self._next_action_journal = float('-inf')
            return dict(battle=None, state=None, ready=False, transiting=False, source='', target='')
        if status in ('process_exited', 'process_changed'):
            self._optional_recovery = None
            self._reset_stage_plan()
            raise ProcessChanged('官服 PC 游戏进程已变化，正在重新连接。')
        if status == 'sample_retry' and report.get('reason') == 'frame_or_lifecycle_changed':
            raise NativeClockBusy('官服 PC 战局正在准备或跨帧重试（frame_or_lifecycle_changed）')
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
        event_identity = (self.event_identity_only and context.get('stage_status') == 'available'
                          and 'AUTOCHESS' in str(context.get('stage_id', '')).upper())
        if event_identity:
            # The independent PC event envelope owns the account/actor table.
            # Skip the redundant legacy identity walks. Retain actor counters
            # for the existing initial-skill baseline check and diagnostics;
            # the event-only adapter never splits them into player amounts.
            fields = _capture_fields(read, context, event_identity_only=True)
        else:
            fields = _capture_fields(read, context)
        scope = (tuple(context.get(key) for key in (
            'battle_controller', 'battle_logger', 'battle_stats', 'level_data', 'stage_id')),
            context.get('frame'), context.get('game_time'))
        recovery, self._optional_recovery = self._optional_recovery, None
        if (recovery is not None and recovery[0] == scope[0]
                and all(type(current) in (int, float) and type(previous) in (int, float)
                        and current >= previous for current, previous in zip(scope[1:], recovery[1:]))):
            # A rejected expensive optional read must not make every retry
            # expensive. One ordinary core/flow attempt precedes its next try.
            fields.update(capture_game_flow(read, context))
            return fields
        now = time.monotonic()
        enemy_due = now >= self._next_enemy
        if enemy_due:
            self._stage_optional(scope)
            self._next_enemy = now + ENEMY_SAMPLE_INTERVAL
            fields.update(capture_enemies(read, context))
        fields.update(capture_game_flow(read, context))
        # Give each optional witness its own sampling slot and keep it behind
        # the counters, roster, presence and flow.
        if not enemy_due and now >= self._next_stage_plan:
            self._stage_optional(scope)
            interval = (STAGE_PLAN_RECHECK_INTERVAL if self._stage_plan_confirmed
                        else STAGE_PLAN_RETRY_INTERVAL)
            self._next_stage_plan = now + interval
            try:
                fields.update(capture_stage_spawns(read, context))
            except (ApiFailure, ResearchFailure):
                fields.update(stage_spawns_status='unavailable', stage_spawns=None,
                              stage_spawns_reason='optional_stage_plan_read_failed')
        elif not enemy_due and self.collect_actions and not event_identity and now >= self._next_action_journal:
            self._stage_optional(scope)
            self._next_action_journal = now + ACTION_JOURNAL_SAMPLE_INTERVAL
            try:
                fields.update(capture_actions(read, context))
            except (ApiFailure, ResearchFailure):
                fields.update(actions_status='unavailable', actions=None,
                              actions_reason='optional_action_journal_read_failed')
        return fields

    def _stage_optional(self, scope):
        self._optional_attempt = ((self._next_enemy, self._next_stage_plan, self._next_action_journal),
                                  self._stage_plan_scope, scope)

    def _rollback_optional(self):
        attempt, self._optional_attempt = self._optional_attempt, None
        if attempt is not None:
            deadlines, plan_scope, scope = attempt
            self._next_enemy, plan_deadline, self._next_action_journal = deadlines
            if plan_scope == self._stage_plan_scope:
                self._next_stage_plan = plan_deadline
            self._optional_recovery = scope

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
        self._enemy_snapshot_pair = self._enemy_view_scope = self._enemy_scoped_snapshot = None
        self._optional_recovery = None
        self._next_enemy = float('-inf')
        self._enemy_observations.reset()
        self._enemy_events.reset()

    def read_guarded_pair(self, lifecycle):
        pending, self._pending = self._pending, None
        if pending is None or pending[0] is not lifecycle:
            self._rollback_optional()
            raise SnapshotGap('官服 PC 的同帧累计及编队数据已失效')
        report = pending[1]
        raw = report['sample']
        missing = [key for key in ('actor_rows', 'roster')
                   if raw.get(f'{key}_status') != 'available' or raw.get(key) is None]
        event_candidate = bool(missing and raw.get('stage_status') == 'available'
                               and 'AUTOCHESS' in str(raw.get('stage_id', '')).upper())
        if missing and not event_candidate:
            self._rollback_optional()
            key = missing[0]
            raise SnapshotGap(f'官服 PC 同帧数据不完整（{raw.get(f"{key}_reason", key)}）')
        self._observe_stage_plan(raw)
        rows = [] if 'actor_rows' in missing else raw['actor_rows']
        battle = lifecycle['battle']
        if self._enemy_battle != battle:
            self._reset_enemy_observation()
            self._enemy_battle = battle
        if 'enemies_status' in raw:
            observed = time.monotonic()
            self._enemy_snapshot = (observed, raw['frame'], raw['game_time'], raw)
            self._next_enemy = max(self._next_enemy, observed + ENEMY_SAMPLE_INTERVAL)
        # BattlePlayerData is a legacy witness, not the AutoChess account table.
        # A candidate still needs the existing independent event binding before
        # the adapter can accept it; an empty roster never certifies ownership.
        roster = RosterSnapshot(raw['frame']) if event_candidate else _roster(raw)
        sample = dict(session=':'.join(map(str, (*self.process.process_instance, *lifecycle['battle']))),
                      frame=raw['frame'], game_time=raw['game_time'], battle_identity=lifecycle['battle'],
                      state=raw['battle_state'], speed=raw.get('speed_level'), paused=raw.get('paused'),
                      presence_duration_supported=_duration_membership_verified(raw, roster),
                      rows=rows, global_total=raw['total_damage'], global_heal=raw['total_heal'],
                      sum_damage=sum(row['damage'] for row in rows),
                      damage_basis='client character counters; includes overkill; excludes healing',
                      read_ms=report.get('acquisition', {}).get('duration_ms'),
                      observed_at=time.time(), captured_monotonic=time.monotonic())
        if event_candidate:
            sample['native_counter_scope'] = 'events_only'
            sample['native_snapshot_witnesses'] = {
                key: dict(status=raw.get(f'{key}_status'), reason=raw.get(f'{key}_reason'))
                for key in ('actor_rows', 'roster')}
        if self.collect_actions and 'actions_status' in raw:
            sample['native_action_journal'] = dict(status=raw['actions_status'],
                                                   reason=raw.get('actions_reason'),
                                                   snapshot=raw.get('actions'))
        self._enemy_snapshot_pair = sample if 'enemies_status' in raw else None
        self._optional_attempt = None
        return sample, roster

    @staticmethod
    def _event_scope_key(identity):
        if (isinstance(identity, dict) and set(identity) == {'scope_key', 'phase', 'round'}
                and isinstance(identity['scope_key'], str) and identity['scope_key']
                and type(identity['phase']) is int and type(identity['round']) is int
                and identity['round'] >= 0):
            return identity['scope_key'], identity['phase'], identity['round']
        return ()

    def bind_enemy_scope(self, sample):
        """Bind only this pair's native observation after both host marks passed."""
        paired, self._enemy_snapshot_pair = self._enemy_snapshot_pair, None
        if paired is sample and native_autochess_context_bound(sample):
            self._enemy_scoped_snapshot = (
                self._event_scope_key(sample['autochess_context']['identity']), self._enemy_snapshot)

    def read_enemies(self, *, game_time, frame=None, our_damage=None, event_scope=None, **events):
        saved = self._enemy_snapshot
        if event_scope is not None:
            key = self._event_scope_key(event_scope)
            if key != self._enemy_view_scope:
                self._enemy_observations.reset()
                self._enemy_events.reset()
                self._enemy_view_scope = key
            if not key:
                return self._with_stage_plan(native_enemy_view(
                    {'enemies_reason': 'native_enemy_scope_unverified'},
                    frame=frame, game_time=game_time, catalog={}), frame)
            committed = self._enemy_scoped_snapshot
            saved = committed[1] if committed is not None and committed[0] == key else None
        if saved is None:
            raw = ({'enemies_reason': 'native_enemy_scope_unverified'} if event_scope is not None else {})
            snapshot = native_enemy_view(raw, frame=None, game_time=None, catalog={})
            projected = self._enemy_events.sample_native_snapshot(
                None, frame=frame, game_time=game_time, **events)
            return self._with_stage_plan(projected if projected is not None else snapshot, frame)
        observed, enemy_frame, enemy_time, raw = saved
        if frame is None or game_time is None or frame < enemy_frame or game_time < enemy_time:
            self._enemy_observations.note_gap()
            return self._with_stage_plan(native_enemy_view({'enemies_reason': 'enemy_sample_stale'},
                                         frame=enemy_frame, game_time=enemy_time, catalog={}), frame)
        if time.monotonic() - observed > ENEMY_MAX_AGE:
            self._enemy_observations.note_gap()
            snapshot = native_enemy_view({'enemies_reason': 'enemy_sample_stale'},
                                         frame=enemy_frame, game_time=enemy_time, catalog={})
            projected = self._enemy_events.sample_native_snapshot(
                None, frame=frame, game_time=game_time, **events)
            return self._with_stage_plan(projected if projected is not None else snapshot, frame)
        if self._enemy_catalog is None and raw.get('enemies_status') == 'available':
            self._enemy_catalog = _load_enemy_db() or {}
        snapshot = self._enemy_observations.observe(
            raw, frame=enemy_frame, game_time=enemy_time, catalog=self._enemy_catalog or {})
        projected = self._enemy_events.sample_native_snapshot(
            snapshot, frame=frame, game_time=game_time, **events)
        return self._with_stage_plan(projected if projected is not None else snapshot, frame)

    def close(self):
        self._pending = None
        self._optional_attempt = self._optional_recovery = None
        self._next_action_journal = float('-inf')
        self._reset_stage_plan()
        self._reset_enemy_observation()
        if self.process is not None:
            self.process.close()
            self.process = None
