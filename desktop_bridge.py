"""Private versioned stdio JSON Lines adapter for the native WPF client.

Python alone owns collection, ADB, metrics and archives. No Tk imports.
--offline is an explicit device-free acceptance boundary, never fallback.
"""
from __future__ import annotations
import argparse
import base64
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import traceback
from uuid import uuid4

from autochess_context import (NATIVE_AUTOCHESS_CHARACTER_COUNTERS, NATIVE_AUTOCHESS_CHARACTER_LABEL,
                               native_local_player as _native_local_player, valid_player_uid)
from battle_history import BattleHistory, HistoryResult
from battle_lifecycle import (BattleRuntime, MappingRetry, NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED,
                              autochess_scene)
from battle_share import (AUTOCHESS_SNAPSHOT_NOTE, json_bytes, record_display, record_statistics,
                         write_export)
from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE, WAITING_GAME_INIT
from collector import BattleChanged, NativeClockBusy, ProcessChanged, SnapshotGap, ROOT
from connection import (ConnectionConfig, ConnectionConfigStore, ConnectionDiagnostics,
                        DetectionStatus, detect_instances, resolve_configured)
from desktop_audit import DesktopCaptureJournal
from event_capture import CaptureRetry
from event_observer import ObserverReadRetry, ObserverStatus
from event_probe import BarrierBusy, NATIVE_PC_CONTRACT
from frida_provision import BUNDLED_SERVER_ARCHIVE, ensure_frida_endpoint, keep_frida_endpoint
from layout_store import LayoutStore, GuiGeometry, HudPlacement, HudStyle
from log_context import LogContext
from metrics import observation_age
from panel_model import (Category, EventAvailability, PanelConfig, Ranking, RateBasis, detail_rates,
                         live_damage_rates, panel_statistics, render_panel, rows_from_statistics,
                         settled_boss_damage_rates, team_status, format_compact)
import stage_names

PROTOCOL_VERSION = 1
MAX_MESSAGE = 32 * 1024 * 1024
BRIDGE_SLOW_PHASE_SECONDS = .5
BRIDGE_EVENT_LIMIT = 32
BRIDGE_EVENT_BUDGET = .025
COLLECTION_PERIOD = .2
COLLECTION_MIN_REST = .05
NATIVE_BUSY_ATTEMPTS = 3
NATIVE_BUSY_BUDGET = .04
NATIVE_BUSY_REST = .005
NATIVE_ACTION_REQUIRED_CODES = frozenset((
    'native_permission_denied', 'native_root_required', 'native_unsupported_build',
))
BRIDGE_COMMANDS = frozenset((
    'hello', 'snapshot', 'quit', 'detect', 'rescan', 'panel_add', 'panel_duplicate',
    'panel_update', 'panel_delete', 'theme', 'number_units', 'hud_style', 'placement',
    'history_refresh', 'history_select', 'history_config', 'export', 'connection_apply',
    'multiplayer_enabled', 'debug_mode',
))
BRIDGE_KEY_COMMANDS = frozenset(('hello', 'connection_apply', 'rescan', 'history_refresh', 'quit', 'debug_mode'))
PC_STARTUP_KEY_EVENTS = frozenset(('target_selected', 'loader_submitted', 'loader_completed', 'target_unavailable'))
PC_STARTUP_KEY_STATES = frozenset(('waiting_for_process', 'ready', 'failed', 'process_failed', 'cleanup_pending'))


def _write_diagnostic_event(category, event):
    try:
        sys.stderr.write(f'[{category}] ' + json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n')
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def event_label(status, error=None):
    labels = {'recording': '事件观察中 · 护盾仅含观察区间',
              'ready': '事件已就绪 · 等待同帧基线', 'attaching': '正在挂载事件观察',
              WAITING_GAME_INIT: '等待游戏初始化',
              'gap': '事件中断 · 伤害/治疗快照继续',
              'failed': '事件不可用 · 使用伤害/治疗快照',
              'incompatible': '观察者与游戏版本不兼容 · 使用伤害/治疗快照',
              'stopped': '观察者已停止 · 使用伤害/治疗快照',
              'disabled': '事件已关闭 · 使用伤害/治疗快照',
              'unavailable': '事件关闭或不可用 · 使用伤害/治疗快照',
              'finished': '事件观察结束 · 保留已覆盖区间'}
    labels['snapshot_only'] = '官服 PC 累计快照 · 逐事件不可用'
    return labels.get(status, '等待事件能力诊断') + (f'（{error}）' if error else '')


def _native_clock_busy(error):
    """Only typed acquisition evidence qualifies; messages and RPC retries do not."""
    leaf_busy = False
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 16:
        seen.add(id(error))
        if isinstance(error, (MappingRetry, SnapshotGap, BattleChanged)):
            return False
        if not isinstance(error, (CaptureRetry, ObserverReadRetry, BarrierBusy, NativeClockBusy)):
            return False
        leaf_busy = isinstance(error, (BarrierBusy, NativeClockBusy))
        error = error.__cause__
    return leaf_busy and error is None


def team_totals(statistics):
    """Whole-team metric strip. Unknown rows stay None; no partial sum is shown as a total."""
    rows = (statistics or {}).get('rows') or {}
    result = {}
    for key in ('damage', 'healing', 'shield_absorbed'):
        values = [row.get(key) for row in rows.values()]
        result[key] = (sum(values) if values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in values) else None)
    return result


def _event_only_statistics(statistics):
    # A detached display container opts into existing event-lane values. The
    # accepted sample and its four Statistics results retain their native None.
    return dict(statistics, native_counter_scope='unavailable',
                event_only_projection=True, totals_complete=False)


def _defense_has_settlement(sample):
    """A counter baseline is not a replacement for an observed defense result."""
    payload = sample.get('multiplayer') or {}
    for player in payload.get('players') or ():
        uid, statistics = player.get('player_uid'), player.get('statistics') or {}
        if (not valid_player_uid(uid) or statistics.get('session') != f"{payload.get('scope')}:{uid}"
                or statistics.get('game_time') != sample.get('game_time')):
            continue
        for actor in player.get('actor_ledgers') or ():
            if actor.get('player_uid') != uid:
                continue
            lane = actor.get('ledger') or {}
            for hits, amount in (('nominal_hits', 'observed_damage'), ('observed_hits', 'effective_damage')):
                value = lane.get(amount)
                if (type(lane.get(hits)) is int and lane[hits] > 0
                        and type(value) in (int, float) and math.isfinite(value) and value >= 0):
                    return True
    return False


def _native_display_source(sample):
    """Assist shows the evidenced battlefield; other phases still select self."""
    payload = sample.get('multiplayer')
    if not isinstance(payload, dict) or payload.get('scope_kind') != 'assist':
        return _native_local_player(sample)
    scope, when = payload.get('scope'), payload.get('game_time')
    if (sample.get('native_counter_scope') != 'events_only'
            or payload.get('schema_version') != 2 or payload.get('available') is not True
            or payload.get('native_phase') != 5 or type(payload.get('native_phase')) is not int
            or type(payload.get('native_round')) is not int or payload['native_round'] < 0
            or not isinstance(scope, str) or not scope
            or type(when) not in (int, float) or not math.isfinite(when) or when < 0):
        return None
    identity = payload.get('context_identity')
    if not isinstance(identity, dict) or set(identity) != {'scope_key', 'phase', 'round'}:
        return None
    root = identity['scope_key']
    if (not isinstance(root, str) or not 0 < len(root) <= 2048
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in root)
            or type(identity['phase']) is not int or identity['phase'] != payload['native_phase']
            or type(identity['round']) is not int or not 0 <= identity['round'] < 1 << 31
            or identity['round'] != payload['native_round']):
        return None
    players = payload.get('players')
    if not isinstance(players, (list, tuple)):
        return None
    if payload.get('collection_mode') == 'local':
        local = _native_local_player(sample)
        players = (local,) if local is not None else ()
    rows, actors = {}, []
    for player in players:
        if not isinstance(player, dict):
            continue
        uid, statistics = player.get('player_uid'), player.get('statistics')
        if (not valid_player_uid(uid) or sum(isinstance(other, dict)
                and other.get('player_uid') == uid for other in players) != 1
                or not isinstance(statistics, dict)
                or statistics.get('session') != f'{scope}:{uid}'
                or statistics.get('game_time') != when
                or not isinstance(statistics.get('rows'), dict)):
            continue
        # A row is displayed only with its own unambiguous actor witness. The
        # union retains existing owner keys and lanes; no Statistics is replayed.
        ledgers = {}
        raw_actors = player.get('actor_ledgers')
        if not isinstance(raw_actors, (list, tuple)):
            continue
        for actor in raw_actors:
            if isinstance(actor, dict) and isinstance(actor.get('owner_id'), str):
                owner = actor['owner_id']
                ledgers[owner] = None if owner in ledgers else actor
        for owner, row in statistics['rows'].items():
            actor = ledgers.get(owner)
            if not isinstance(row, dict) or actor is None:
                continue
            actor_uid, cid = actor.get('actor_uid'), actor.get('cid')
            if (actor.get('player_uid') != uid or row.get('player_uid') != uid
                    or type(actor_uid) is not int or not 0 < actor_uid <= 0xffffffff
                    or not isinstance(cid, str) or not cid
                    or type(row.get('unique_id')) is not int
                    or row.get('unique_id') != actor_uid or row.get('cid') != cid
                    or row.get('id') != owner or owner != f'{uid}:{cid}#{actor_uid}'):
                continue
            rows[owner] = row
            actors.append(dict(owner_id=owner, actor_uid=actor_uid, cid=cid,
                               player_uid=uid, player_side=player.get('player_side')))
    statistics = dict(sample.get('statistics') or {}, rows=rows, game_time=when,
                      display_scope_label='当前协防战场')
    return dict(statistics=statistics, actor_ledgers=actors)


def _display_statistics(sample, *, source=None):
    statistics = sample.get('statistics') or {}
    if sample.get('native_counter_scope') == NATIVE_AUTOCHESS_CHARACTER_COUNTERS:
        return dict(statistics, totals_complete=False, display_scope_label=NATIVE_AUTOCHESS_CHARACTER_LABEL)
    if sample.get('native_counter_scope') != 'events_only':
        return statistics
    player = source if source is not None else _native_display_source(sample)
    if player is not None:
        return _event_only_statistics(player['statistics'])
    return dict(rows={}, totals_complete=False, native_counter_scope='unavailable',
                game_time=sample.get('game_time'), ended=statistics.get('ended') is True)


def _display_analysis_identity(sample):
    battle_id = sample.get('battle_id', sample.get('session'))
    if sample.get('native_counter_scope') != 'events_only' or not isinstance(battle_id, str):
        return battle_id
    player = _native_local_player(sample)
    payload = sample.get('multiplayer') or {}
    return 'autochess:' + json.dumps([battle_id, payload.get('scope'),
        payload.get('scope_kind'), payload.get('native_phase'), payload.get('native_round'),
        'assist_scene' if payload.get('scope_kind') == 'assist' else
        player['player_uid'] if player else None])


def _unknown_player(slot):
    return dict(slot=slot, player_uid=None, player_name=None, player_side=None,
                label='玩家身份未知', is_local=None, status='unknown',
                damage=None, dps=None, recent_dps=None, observed_damage=None,
                observed_dps=None, observed_recent_dps=None, observed_window=None,
                coverage_complete=False, reasons=['玩家身份未确认'],
                statistics=None, actor_ledgers=[])


def _preparing_multiplayer():
    return dict(schema_version=2, available=False, status='preparing',
                scope_kind='prepare', scope=None, native_phase=None, native_round=None,
                round_id=None, game_time=None, coverage_start=None,
                note='战斗准备中，等待当前阶段的玩家与结算证据。',
                players=[_unknown_player(slot) for slot in range(1, 5)])


def team_unassigned_hits(statistics, *, boss_scope):
    unassigned = statistics.get('settlement_unassigned') or {}
    key = 'boss_hits' if boss_scope else 'unscoped_hits'
    count = unassigned.get(key, unassigned.get('hits'))
    return count if type(count) is int and count > 0 else 0


def empty_text(config, source, complete, view):
    if config.boss_scope:
        if not view['boss_damage_available']:
            return view['boss_damage_reason']
        # The displayed metric can be a rate or effective damage even when
        # raw cumulative damage is known.
        complete_key = 'footer_rate_complete' if config.ranking.value == 'dps' else 'footer_total_complete'
        if not view[complete_key]:
            return view['boss_damage_reason'] or '当前首领伤害指标尚无可靠数据'
        return '暂无首领伤害（已知为零）'
    if not source:
        return '等待可靠战斗数据 · 未知不记零'
    if config.ranking is Ranking.DPS:
        # A known cumulative counter cannot certify a missing rate denominator
        # or event stream. Recent/full-cycle rates do not require events.
        if config.rate_basis is RateBasis.ENGAGEMENT and view.get('rate_available') is False:
            return view.get('rate_reason') or '当前指标尚无可靠数据 · 未知不记零'
        if view.get('footer_rate') is None:
            return view.get('share_reason') or '当前指标尚无可靠数据 · 未知不记零'
        if not complete or view.get('footer_rate_complete') is not True:
            return '已观察输出为零 · 未覆盖部分仍未知'
        return '暂无输出（已知为零）'
    if config.category.value == 'damage' and config.effective_only and any(
            row.get('effective_damage') is None for row in source.values()):
        return '当前有效伤害尚无可靠数据'
    key = {'damage': 'damage', 'healing': 'healing', 'shield': 'shield_absorbed'}[config.category.value]
    if config.ranking is Ranking.RECENT:
        key = 'recent_' + config.category.value
    if any(row.get(key) is None for row in source.values()):
        return '当前指标尚无可靠数据 · 未知不记零'
    coverage = 'event_coverage_complete' if config.category.value == 'shield' else 'counter_snapshot_complete'
    if not complete or not all(row.get(coverage, False) for row in source.values()):
        return '已观察输出为零 · 未覆盖部分仍未知'
    return '暂无输出（已知为零）'


class DesktopState:
    """Single-threaded command owner; collector input is queued by its worker."""
    def __init__(self, directory=None):
        directory = Path(directory) if directory else ROOT / 'runtime'
        self.directory = directory
        self.store = LayoutStore(directory / 'layout.json')
        loaded = self.store.load()
        self.layout = loaded.layout
        self.connection_store = ConnectionConfigStore(directory / 'connection.json')
        config = self.connection_store.load()
        self.connection = config.config
        self.effective_connection = self.connection
        self._source_request_revision = None
        self._source_request_platform = self.connection.platform
        self._awaiting_source = False
        self.warnings = [x for x in (loaded.warning, config.warning,
                                     stage_names.catalog().warning) if x]
        self.history = BattleHistory(directory / 'history')
        self.records = {}
        self.selected = None
        self._previous_selected = None
        self.history_config = PanelConfig(panel_id='history')
        self._history_index = []
        self._history_view_key = None
        self._history_view = None
        self.last = None
        self._autochess_scene = self._sample_scene = None
        self._autochess_scene_announced = False
        self._defense_sample = None
        self._defense_analysis = (None, None, None)
        self._requested_native_root = None
        self._requested_connection = self.connection
        self.last_frame = None
        self.last_progress = 0.
        self.live_analysis = None
        self.boss_live_analysis = None
        self.boss_effective_live_analysis = None
        self.analysis_interrupted = False
        self.error = ''
        self.connection_text = '等待定位本局'
        self.diagnostic = None
        self.event_status = self.event_error = self.lifecycle = None
        self.observer_status = self.observer_error = None
        self.native_pc_bootstrap = None
        self.sampling_rejection = None
        self._waiting_for_connection_sample = False
        self.battle_waiting = False
        self.scanning = False
        self.history_notice = ''
        self.refresh_history()

    def refresh_history(self):
        result = self.history.list_recent()
        self._install_history(result.records, result.diagnostics)

    def _install_history(self, records, diagnostics):
        self.records = {r.battle_id: r for r in records}
        history = []
        for record in records:
            item = record_display(record)
            # Readable labels and the native id both match, so old records stay searchable.
            if record.unverified_autochess_scope:
                rows = record_statistics(record, for_panel=True)['rows']
                names = (row.get('name') or cid for cid, row in rows.items())
            else:
                names = record.operator_names
            item['search'] = ' '.join((*item['labels'], item['date'], ' '.join(names),
                                      item['stage_raw'] or '')).casefold()
            history.append(item)
        self._history_index = history
        self.history_notice = '；'.join(f'{d.code}: {d.message[:160]}' for d in diagnostics)
        if self.selected is None and records:
            self.selected = records[0]

    def _history_committed(self, result):
        if not isinstance(result, HistoryResult):
            # Legacy in-process callers can still explicitly request a rescan;
            # the collection producer always publishes the committed result.
            self.refresh_history()
            return
        if not result.written:
            return
        if result.recent is not None:
            self._install_history(result.recent, result.diagnostics)
            return
        records = dict(self.records)
        records.update((record.battle_id, record) for record in result.records)
        def order(record):
            data = record.display_metadata()
            return datetime.fromisoformat(data['observed_at'] or data['recorded_at']), record.battle_id
        recent = sorted(records.values(), key=order, reverse=True)[:100]
        self._install_history(recent, result.diagnostics)

    def accept(self, kind, value):
        if kind == 'worker_error':
            # Setup/loop failure can precede a source marker. It is global to
            # the worker, rather than stale data from a retired session.
            self.error = str(value)
            self.scanning = False
            self.analysis_interrupted = True
            return
        if kind == 'connection_source':
            if (isinstance(value, dict) and type(value.get('revision')) is int
                    and value['revision'] == self._source_request_revision
                    and value.get('platform') == self._source_request_platform):
                self._awaiting_source = False
            return
        # The acquisition thread is the sole source-event producer. Its FIFO
        # marker precedes all events for the newly accepted request, so earlier
        # queued events cannot repopulate a superseded connection's status.
        # Archive notifications are global and must survive source transitions.
        if self._awaiting_source and kind != 'history':
            return
        if kind == 'autochess_scene':
            self._autochess_scene_announced = True
            self._set_autochess_scene(value)
        elif kind == 'sample':
            scene = autochess_scene(value) if self._autochess_scene_announced else None
            if scene != self._autochess_scene and value.get('native_counter_scope') != NATIVE_AUTOCHESS_CHARACTER_COUNTERS:
                # A late terminal DTO may finish the cached defense. It cannot
                # take over the live assist or reset the current presentation.
                self._remember_defense(value, scene, late=True)
                return
            self.sampling_rejection = None
            self._waiting_for_connection_sample = False
            if self.last is not None and observation_age(self.last) > 2:
                self.analysis_interrupted = True
            self.error = ''
            frame = (value.get('session'), value.get('frame'))
            if frame != self.last_frame:
                self.last_progress = time.monotonic()
                self.last_frame = frame
            self.last = value
            self._sample_scene = scene
            self.event_status, self.event_error = value.get('event_status'), value.get('event_error')
            self.update_live_analysis(value)
            self._remember_defense(value, scene)
        elif kind == 'error':
            self.error = str(value)
            self.analysis_interrupted = True
        elif kind == 'warning':
            if value not in self.warnings:
                self.warnings.append(value)
        elif kind == 'connection':
            self.connection_text = value
        elif kind == 'diagnostic':
            self.diagnostic = value
        elif kind == 'event':
            self.event_status, self.event_error = value
        elif kind == 'observer':
            self.observer_status, self.observer_error = value
        elif kind == 'native_pc_bootstrap':
            self.native_pc_bootstrap = value
        elif kind == 'sampling_rejection':
            self.sampling_rejection = (dict(code='mapping_unavailable', reason=value['reason'][:200])
                if isinstance(value, dict) and value.get('code') == 'mapping_unavailable'
                and isinstance(value.get('reason'), str) and value['reason'].strip() else None)
            if (self.sampling_rejection and self.effective_connection.platform == 'native_pc'
                    and value.get('scope_code') == NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED):
                self.sampling_rejection['scope_code'] = NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED
                self.analysis_interrupted = True
        elif kind == 'lifecycle':
            self.lifecycle = value
        elif kind == 'battle_waiting':
            self.battle_waiting = value
        elif kind == 'history':
            # The writer already validated/read back this durable record. Do
            # not synchronously rescan every archive on the IPC response path.
            self._history_committed(value)
        elif kind == 'scanning':
            self.scanning = value
            if value:
                self.error = ''
                self.observer_status = self.observer_error = None
                # Retain the last battle data, including its coverage gaps, as
                # a stale view. It is not the new connection's event status.
                self.event_status = self.event_error = None
                self._waiting_for_connection_sample = True
                self.analysis_interrupted = True

    def expect_connection_request(self, config, revision):
        prior = self._requested_connection
        changed = (config.platform != prior.platform or
                   (config.native_game_root != prior.native_game_root if config.platform == 'native_pc' else
                    (config.adb_path, config.serial) != (prior.adb_path, prior.serial)))
        if changed:
            self._set_autochess_scene(None)
        self._requested_connection = config
        self._requested_native_root = config.native_game_root
        self._source_request_platform = config.platform
        self._source_request_revision = revision if type(revision) is int else None
        # Keep the last battle/history, but never use the prior connection's
        # readiness as permission to start a game for this new request.
        self._awaiting_source = True
        self.native_pc_bootstrap = None
        self.sampling_rejection = None
        self.observer_status = self.observer_error = None
        self.event_status = self.event_error = None
        self.diagnostic = None
        self.error = ''
        self.connection_text = '正在准备采集连接'
        self.scanning = True
        self._waiting_for_connection_sample = True
        self.analysis_interrupted = True

    def finish_offline_connection(self):
        self._awaiting_source = False
        self.scanning = False
        self.connection_text = '离线模式 · 未连接采集源'

    def _clear_live_source(self):
        self._set_autochess_scene(None)
        self.last = self.last_frame = None
        self.last_progress = 0.
        self.live_analysis = self.boss_live_analysis = self.boss_effective_live_analysis = None
        self.analysis_interrupted = True
        self.error = ''
        self.connection_text = '正在切换采集平台'
        self.diagnostic = None
        self.event_status = self.event_error = self.lifecycle = None
        self.observer_status = self.observer_error = None
        self.native_pc_bootstrap = None
        self.sampling_rejection = None
        self.battle_waiting = None
        self.scanning = True
        self._source_request_revision = None
        self._awaiting_source = True

    def _set_autochess_scene(self, scene):
        prior = self._autochess_scene
        if scene == prior:
            return
        self._autochess_scene = scene
        if prior is not None and (scene is None or scene.match != prior.match):
            self._defense_sample = None
            self._defense_analysis = (None, None, None)
            if self._sample_scene is not None:
                self.last = self.last_frame = self._sample_scene = None
                self.live_analysis = self.boss_live_analysis = self.boss_effective_live_analysis = None
        self.analysis_interrupted = True

    def _remember_defense(self, sample, scene, *, late=False):
        active = self._autochess_scene
        if scene is None or active is None or scene.match != active.match:
            return
        payload = sample.get('multiplayer')
        player = _native_local_player(sample)
        if (player is None or payload.get('scope_kind') != 'defense'
                or sample.get('event_status') not in ('recording', 'finished')
                or player['statistics'].get('session') != f"{payload['scope']}:{player['player_uid']}"
                or player['statistics'].get('game_time') != sample.get('game_time')):
            return
        old = self._defense_sample
        same_scope = old is not None and old['multiplayer']['scope'] == payload['scope']
        if same_scope and (sample['frame'] < old['frame'] or sample['game_time'] < old['game_time']):
            return
        current = scene == active and scene.phase == 4 and scene.scope == payload['scope']
        if not current and not (same_scope and sample.get('state') == 3):
            return
        if old is not None and _defense_has_settlement(old) and not _defense_has_settlement(sample):
            # The next native phase can precede its actual combat. Retain the
            # useful prior result through empty baselines, including reconnects.
            return
        # The worker transfers an owned DTO. Retain only the presentation
        # fields, since the shared world statistics can contain a large journal.
        self._defense_sample = {key: sample[key] for key in (
            'native_counter_scope', 'multiplayer', 'battle_id', 'session', 'game_time',
            'battle_metadata', 'event_status', 'event_error', 'frame', 'state', 'enemy'
        ) if key in sample}
        if not late:
            self._defense_analysis = (self.live_analysis, self.boss_live_analysis, self.boss_effective_live_analysis)
        else:
            self._defense_analysis = (None, None, None)

    def _presented_battle(self):
        sample = self.last or {}
        analysis = (self.live_analysis, self.boss_live_analysis, self.boss_effective_live_analysis)
        if sample.get('native_counter_scope') == NATIVE_AUTOCHESS_CHARACTER_COUNTERS:
            return sample, None, analysis, None
        scene, defense = self._autochess_scene, self._defense_sample
        if scene is None:
            multiplayer = _preparing_multiplayer() if self.lifecycle == 'preparing' else sample.get('multiplayer')
            return sample, multiplayer, analysis, None
        if scene.phase in (5, 6):
            multiplayer = sample.get('multiplayer') or {}
            kind = 'assist' if scene.phase == 5 else 'boss'
            if (self._sample_scene == scene and multiplayer.get('scope') == scene.scope
                    and multiplayer.get('scope_kind') == kind and multiplayer.get('available') is True):
                return sample, multiplayer, analysis, None
            label = '当前协防战场' if scene.phase == 5 else '当前首领战场'
            return dict(statistics=dict(rows={}, display_scope_label=label)), None, (None,) * 3, label + ' · 等待数据'
        if defense is not None:
            multiplayer = defense['multiplayer']
            if (scene.phase == 4 and scene.scope == multiplayer['scope']
                    and self._sample_scene == scene and sample.get('frame') == defense.get('frame')
                    and sample.get('multiplayer') is multiplayer
                    and sample.get('event_status') in ('recording', 'finished')
                    and defense.get('state') != 3 and self.lifecycle not in ('preparing', 'ended', 'finalizing')):
                return defense, multiplayer, self._defense_analysis, None
            note = f"上一场防御 · 第 {multiplayer['native_round']} 回合"
            waiting = scene.phase == 4 and scene.scope != multiplayer['scope']
            return (defense, dict(multiplayer, previous_defense=True, note=note),
                    self._defense_analysis, ('等待本轮防御数据 · ' if waiting else '') + note)
        if scene.phase == 4:
            return {}, None, (None,) * 3, '等待本轮防御数据'
        return {}, _preparing_multiplayer(), (None,) * 3, None

    def update_live_analysis(self, sample):
        """Keep one chart sample and its in-process observed peak per battle."""
        statistics = _display_statistics(sample)
        battle_id = _display_analysis_identity(sample)
        game_time = statistics.get('game_time', sample.get('game_time'))
        previous = self.live_analysis
        same_battle = previous is not None and previous['battle_id'] == battle_id
        if not same_battle:
            self.live_analysis = None
            self.boss_live_analysis = None
            self.boss_effective_live_analysis = None
            self.analysis_interrupted = False
        if (not isinstance(battle_id, str) or not battle_id or
                type(game_time) not in (float, int) or not math.isfinite(game_time) or game_time < 0 or
                observation_age(sample) > 2 or (same_battle and game_time < previous['game_time'])):
            self.analysis_interrupted = True
            return

        terminal = statistics.get('ended') is True or self.lifecycle in ('ended', 'frozen', 'finalizing')
        team_recent, operators = live_damage_rates(statistics, game_time)
        scope_label = statistics.get('display_scope_label')
        character_counters = sample.get('native_counter_scope') == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
        if scope_label and not character_counters:
            for operator in operators:
                row = statistics['rows'][operator['owner_id']]
                operator.update(player_uid=row['player_uid'], actor_uid=row['unique_id'])
        complete, _, _ = team_status(statistics)
        team_total = team_totals(statistics)['damage'] if complete else None
        if team_total is not None and (not math.isfinite(team_total) or team_total < 0):
            team_total = None
        if not complete or terminal:
            team_recent = None
        if terminal:
            for operator in operators:
                operator['recent_dps'] = None

        segment = previous['segment'] if same_battle else 0
        peak = previous['observed_peak_dps'] if same_battle else None
        if same_battle:
            lost_coverage = (
                previous['team_total_damage'] is not None and team_total is None
                or previous['team_recent_dps'] is not None and team_recent is None and not terminal
            )
            if self.analysis_interrupted or lost_coverage:
                segment += 1
        if team_recent is not None:
            peak = team_recent if peak is None else max(peak, team_recent)
        average = team_total / game_time if team_total is not None and game_time > 0 else None
        self.live_analysis = dict(
            battle_id=battle_id, game_time=game_time, segment=segment,
            team_recent_dps=team_recent,
            team_average_dps=average if average is not None and math.isfinite(average) else None,
            observed_peak_dps=peak, team_total_damage=team_total, operators=operators)
        if scope_label:
            self.live_analysis['scope_label'] = scope_label
        rows = statistics.get('rows') or {}
        if not character_counters and any('partial_damage' in row for row in rows.values()):
            known_damage = [row['damage'] for row in rows.values()
                            if row.get('counter_snapshot_complete') and type(row.get('damage')) in (int, float)
                            and math.isfinite(row['damage']) and row['damage'] >= 0]
            known_rates = [row['recent_dps'] for row in operators if row['recent_dps'] is not None]
            if team_total is None and known_damage:
                observed_total = sum(known_damage)
                self.live_analysis['observed_team_total_damage'] = observed_total
                if game_time > 0:
                    self.live_analysis['observed_team_average_dps'] = observed_total / game_time
            if team_recent is None and not terminal and known_rates:
                windows = {tuple(rows[row['owner_id']].get('recent_damage_window') or
                                 (rows[row['owner_id']].get('settled_damage') or {}).get(
                                     'observed_recent_damage_window')) for row in operators
                           if row['recent_dps'] is not None}
                if len(windows) == 1:
                    self.live_analysis['observed_team_recent_dps'] = sum(known_rates)
        boss_coverage = sample.get('event_status') in (None, 'ready', 'recording', 'finished')
        for effective, state_field in ((False, 'boss_live_analysis'), (True, 'boss_effective_live_analysis')):
            config = PanelConfig(boss_only=True, effective_only=effective)
            boss_statistics = panel_statistics(config, statistics)
            previous_boss = getattr(self, state_field)
            if boss_statistics['boss_damage_available'] or previous_boss is not None:
                setattr(self, state_field, self.boss_trend_point(
                    boss_statistics, battle_id, game_time, previous_boss,
                    terminal=terminal, covered=boss_coverage, effective=effective))
        self.analysis_interrupted = False

    def boss_trend_point(self, statistics, battle_id, game_time, previous, *,
                         terminal, covered, effective=False):
        settled = settled_boss_damage_rates(statistics, game_time, effective=effective)
        if settled is None:
            team_recent, operators = live_damage_rates(statistics, game_time, effective=effective)
            complete, _, _ = team_status(statistics)
        else:
            team_recent, operators = settled
            complete = team_recent is not None
        if not complete or terminal or not covered or team_unassigned_hits(statistics, boss_scope=True):
            team_recent = None
        if terminal or not covered:
            for operator in operators:
                operator['recent_dps'] = None
                if 'observed_recent_dps' in operator:
                    operator['observed_recent_dps'] = None
        segment = 0
        if previous is not None:
            segment = previous['segment']
            def operator_rate(row):
                return row['recent_dps'] if row['recent_dps'] is not None else row.get('observed_recent_dps')
            current_rates = {row['owner_id']: operator_rate(row) for row in operators}
            lost_coverage = not terminal and (
                previous['team_recent_dps'] is not None and team_recent is None
                or any(operator_rate(row) is not None and current_rates.get(row['owner_id']) is None
                       for row in previous['operators']))
            if self.analysis_interrupted or lost_coverage:
                segment += 1
        return dict(battle_id=battle_id, game_time=game_time, segment=segment,
                    team_recent_dps=team_recent, operators=operators)

    def _sampling_rejection_label(self):
        rejection = self.sampling_rejection
        if rejection.get('scope_code') != NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED:
            return '当前样本未通过归属校验：' + rejection['reason']
        if self.observer_status == 'snapshot_only':
            return '卫戍小局 · 暂无可靠的原生角色累计'
        if self.observer_status in ('failed', 'incompatible', 'stopped'):
            detail = (self.observer_error or '').partition('\n')[0][:120]
            return '卫戍小局 · 事件观察不可用' + (f'（{detail}）' if detail else '')
        return '卫戍小局 · 等待确认阶段和玩家的逐次事件归属'

    def status(self):
        if self.scanning:
            return 'locating', '正在定位 · 保留最后有效记录'
        if self.error:
            return 'disconnected', '连接断开 / 读取中断 · 显示最后有效记录'
        if self.sampling_rejection:
            label = self._sampling_rejection_label()
            if self.last:
                label += ' · 保留最后有效记录'
                if observation_age(self.last) > 2:
                    return 'stale', '数据已过期 · ' + label
            return 'sampling_rejected', label
        if self._waiting_for_connection_sample:
            return ('frozen', '等待新连接的可靠样本 · 上次快照') if self.last else (
                'waiting', '等待新连接的可靠样本')
        if self.lifecycle == 'preparing':
            return 'preparing', ('战斗准备中 · 保留上一局统计' if self.last else '战斗准备中')
        if not self.last:
            return 'waiting', '等待战斗数据'
        if self.lifecycle in ('ended', 'finalizing', 'frozen'):
            return self.lifecycle, {'ended': '战斗结束', 'finalizing': '等待结算确认', 'frozen': '战局冻结'}[self.lifecycle]
        if observation_age(self.last) > 2:
            return 'stale', '数据已过期 · 等待下一次可靠采样 · 保留最后有效记录'
        if time.monotonic() - self.last_progress > .8:
            return 'paused', '暂停 / 等待战斗推进'
        if (self.event_status in ('gap', 'failed', 'unavailable')
                and self.effective_connection.platform != 'native_pc'):
            return 'degraded', '正在统计 · 事件降级'
        return 'live', '正在统计'

    def view(self, config, statistics, complete=None, event=None):
        statistics = panel_statistics(config, statistics, history=event is not None and not event.live)
        source = statistics.get('rows') or {}
        number_units = self.layout.number_units
        if complete is None or config.boss_scope:
            complete, reason, note = team_status(statistics, number_units)
        else:
            reason, note = '', ''
        if statistics.get('event_only_projection') is True:
            scope_label = statistics.get('display_scope_label') or '当前玩家'
            reason = f'原生累计未知，以下仅含{scope_label}的已观察结算'
            note = '已观察结算口径；未覆盖区间保持未知'
        if config.boss_scope:
            reason = statistics['boss_damage_reason'] or reason
            complete = complete and statistics['boss_damage_available']
            note = ''
        team_total_complete = team_rate_complete = True
        coverage_note = ''
        if config.category is Category.DAMAGE:
            unassigned = team_unassigned_hits(statistics, boss_scope=config.boss_scope)
            if unassigned:
                team_total_complete = not (config.effective_only or config.boss_scope)
                settlement = statistics.get('settlement_unassigned') or {}
                team_rate_complete = team_total_complete and not (
                    config.rate_basis is RateBasis.ENGAGEMENT and 'unscoped_hits' in settlement)
                if config.boss_scope:
                    coverage_note = f'首领伤害覆盖不完整 · {unassigned} 次命中的归属或目标待确认'
                elif not team_total_complete or not team_rate_complete:
                    scope = '有效伤害' if config.effective_only else '接敌伤害'
                    pending = ('次伤害命中未归属' if 'unscoped_hits' in settlement
                               else '次命中的归属或目标待确认')
                    coverage_note = f'全队{scope}覆盖不完整 · {unassigned} {pending}'
        rows = rows_from_statistics(statistics)
        view = asdict(render_panel(config, rows,
                                   team_complete=complete, team_reason=reason,
                                   team_note=note, team_coverage_note=coverage_note,
                                   team_total_complete=team_total_complete,
                                   team_rate_complete=team_rate_complete,
                                   event=event, number_units=number_units))
        if config.boss_scope:
            view['boss_damage_available'] = statistics['boss_damage_available']
            view['boss_damage_reason'] = statistics['boss_damage_reason']
        ranking_complete = team_rate_complete if config.ranking is Ranking.DPS else team_total_complete
        view['empty_text'] = empty_text(config, source, complete and ranking_complete, view)
        view['rates'] = detail_rates(
            rows, event, effective=config.effective_only and config.category is Category.DAMAGE,
            number_units=number_units)
        for entry in view['entries']:
            entry['bar_color'] = {'damage': '#F2AC7A', 'healing': '#73B78A', 'shield': '#9ABFCE'}[config.category.value]
        view['source'] = source
        if scope_label := statistics.get('display_scope_label'):
            view['scope_label'] = scope_label
            view['title'] = f"{scope_label} · {view['title']}"
        return view

    def _actor_view(self, config, statistics, actors, *, side=None, player_uid=None, event=None):
        view = self.view(config, statistics, event=event)
        view['source'] = dict(view['source'])
        for actor in actors or ():
            if not isinstance(actor, dict):
                continue
            owner = actor.get('owner_id') or actor.get('actor_id')
            if owner not in view['source']:
                continue
            uid = actor.get('uid', actor.get('actor_uid', actor.get('unique_id')))
            identity = dict(actor_uid=uid, player_side=side if side is not None else actor.get('player_side'),
                            portrait_id=actor.get('cid'))
            if (owner_uid := player_uid if player_uid is not None else actor.get('player_uid')) is not None:
                identity['player_uid'] = owner_uid
            view['source'][owner] = dict(view['source'][owner], **identity)
            for entry in view['entries']:
                if entry['owner_id'] == owner:
                    entry.update(identity)
        return view

    def _selected_history(self):
        if self.selected is None:
            self._history_view_key = self._history_view = None
            return None
        key = self.selected, self.history_config, self.layout.number_units, self.layout.multiplayer_enabled
        if key != self._history_view_key:
            event_status = self.selected.event_status
            if self.selected.metadata.get('platform') == 'native_pc' and event_status == 'unavailable':
                event_status = 'snapshot_only'
            event = EventAvailability(event_status, self.selected.event_error, live=False)
            sample = self.selected.last_sample
            unverified = self.selected.unverified_autochess_scope
            multiplayer = None if unverified else sample.get('multiplayer')
            if (sample.get('native_counter_scope') == 'events_only'
                    and isinstance(multiplayer, dict) and multiplayer.get('schema_version') == 2):
                player = _native_display_source(sample)
                statistics = _display_statistics(sample, source=player)
                view = (self._actor_view(self.history_config, statistics, player.get('actor_ledgers'),
                            side=player.get('player_side'), player_uid=player.get('player_uid'), event=event)
                        if player else self.view(self.history_config, statistics, event=event))
            else:
                view = self.view(self.history_config, record_statistics(self.selected, for_panel=True), event=event)
            if unverified and not view['source']:
                view['empty_text'] = '这条卫戍快照没有可核实的角色累计记录。' + AUTOCHESS_SNAPSHOT_NOTE
            self._history_view = dict(
                record_display(self.selected),
                view=view, multiplayer=(self.multiplayer_view(multiplayer, event=event)
                                        if self.layout.multiplayer_enabled and
                                         self.selected.metadata.get('platform') == 'native_pc' and
                                        (multiplayer or {}).get('collection_mode') != 'local' else None),
                event_status=self.selected.event_status, event_error=event.error)
            self._history_view_key = key
        return self._history_view

    def multiplayer_view(self, payload, *, event=None):
        """Render the same statistics model in a player/actor-scoped view."""
        if not isinstance(payload, dict) or payload.get('schema_version') not in (1, 2):
            return None
        result = dict(payload)
        native = payload['schema_version'] == 2
        raw_players = payload.get('players') or ()
        if native:
            slots = {}
            for raw in raw_players:
                slot = raw.get('slot') if isinstance(raw, dict) else None
                if type(slot) is int and 1 <= slot <= 4:
                    slots[slot] = None if slot in slots else raw
            raw_players = [slots.get(slot) or _unknown_player(slot) for slot in range(1, 5)]
        players = []
        for raw in raw_players:
            if not isinstance(raw, dict):
                continue
            player = dict(raw)
            side = player.get('player_side')
            position = player.get('slot') if native else side
            if type(position) is not int or not 1 <= position <= 4:
                continue
            if native:
                uid = player.get('player_uid')
                if (not valid_player_uid(uid) or sum(row.get('player_uid') == uid
                        for row in raw_players if isinstance(row, dict)) != 1):
                    player = _unknown_player(position)
                elif payload.get('available') is not True:
                    player.update({key: value for key, value in _unknown_player(position).items()
                                   if key not in ('slot', 'player_uid', 'player_name', 'label')})
            statistics = player.get('statistics') or {'rows': {}}
            if native and isinstance(player.get('statistics'), dict):
                statistics = _event_only_statistics(statistics)
            config = PanelConfig(panel_id=f'multiplayer-{position}', effective_only=True,
                                 ranking=Ranking.TOTAL, rate_basis=RateBasis.ENGAGEMENT,
                                 title='已观察有效伤害 · 接敌 DPS')
            player['view'] = self._actor_view(config, statistics, player.get('actor_ledgers'),
                                            side=player.get('player_side'),
                                            player_uid=player.get('player_uid') if native else None,
                                            event=event)
            def display(exact_key, observed_key):
                exact, observed = player.get(exact_key), player.get(observed_key)
                valid = lambda value: (type(value) in (int, float)
                                       and math.isfinite(value) and value >= 0)
                if valid(exact):
                    return format_compact(exact, self.layout.number_units)
                if valid(observed):
                    return '已观察 ' + format_compact(observed, self.layout.number_units)
                return '未知'
            player['display'] = dict(
                damage=display('damage', 'observed_damage'),
                dps=display('dps', 'observed_dps'),
                recent_dps=display('recent_dps', 'observed_recent_dps'),
                coverage='；'.join(player.get('reasons') or ())
                    or ('已观察结算事件' if player.get('observed_damage') is not None else '尚无可靠输出证据'))
            window = player.get('observed_window')
            if (isinstance(window, (list, tuple)) and len(window) == 2
                    and all(type(value) in (int, float) and math.isfinite(value) for value in window)):
                player['window_text'] = f'观察 {window[0]:.1f}—{window[1]:.1f} 游戏秒'
            # The rendered view owns the wire-facing source and details. Keep
            # the raw statistics/actor ledgers only in samples and archives.
            player.pop('statistics', None)
            player.pop('actor_ledgers', None)
            players.append(player)
        result['players'] = players
        return result

    def snapshot(self):
        sample, multiplayer, analysis, presentation_note = self._presented_battle()
        desired = self.layout.multiplayer_enabled
        supported = self.effective_connection.platform == 'native_pc'
        counter_scope = sample.get('native_counter_scope')
        character_counters = counter_scope == NATIVE_AUTOCHESS_CHARACTER_COUNTERS
        scene = None if character_counters else self._autochess_scene
        active = desired
        if scene is not None and scene.phase in (4, 5, 6, 7, 8) and scene.collection_mode in ('all', 'local'):
            active = scene.collection_mode == 'all'
        active = supported and active
        scope_unverified = (supported and scene is None and counter_scope != 'events_only'
                            and 'AUTOCHESS' in str((sample.get('battle_metadata') or {}).get('stage_id', '')).upper())
        local_player = _native_display_source(sample)
        stats = _display_statistics(sample, source=local_player)
        if multiplayer and multiplayer.get('previous_defense') is True:
            stats = dict(stats, display_scope_label=multiplayer['note'])
        status, label = self.status()
        if character_counters and status == 'live':
            label = NATIVE_AUTOCHESS_CHARACTER_LABEL
        if presentation_note is not None:
            if self.scanning or self.error or self._waiting_for_connection_sample or self.sampling_rejection:
                label += ' · ' + presentation_note
            else:
                status = 'preparing' if multiplayer and multiplayer.get('previous_defense') else 'waiting'
                label = presentation_note
        live_stage = stage_names.resolve((sample.get('battle_metadata') or {}).get('stage_id'))
        has_live_stage = bool((sample.get('battle_metadata') or {}).get('stage_id'))
        live_event_status = (self.observer_status if self._waiting_for_connection_sample
                             else sample.get('event_status', self.event_status))
        live_event_error = (self.observer_error if self._waiting_for_connection_sample
                            else sample.get('event_error', self.event_error))
        if self.effective_connection.platform == 'native_pc' and live_event_status in (None, 'unavailable'):
            live_event_status = self.observer_status or 'snapshot_only'
            if self.observer_status not in (None, 'snapshot_only'):
                live_event_error = self.observer_error
        live_event = EventAvailability(status=live_event_status,
                                       error=live_event_error, live=True)
        event_status, event_error = self.event_status, self.event_error
        if event_status is None or self.observer_status in (
                WAITING_GAME_INIT, 'disabled', 'failed', 'incompatible', 'stopped', 'snapshot_only'):
            event_status, event_error = self.observer_status, self.observer_error
        return dict(protocol=PROTOCOL_VERSION, layout=self.layout.to_dict(),
                    connection_config=self.connection.to_dict(), warnings=self.warnings,
                    effective_connection_config=self.effective_connection.to_dict(),
                    totals=team_totals({} if character_counters else stats), native_counter_scope=counter_scope,
                    status=status, status_text=label, error=self.error,
                    battle_waiting=self.battle_waiting,
                    connection_text=self.connection_text, diagnostic=self.diagnostic,
                    observer_status=self.observer_status, event_status=self.event_status,
                    native_pc_bootstrap=self.native_pc_bootstrap,
                    sampling_rejection=self.sampling_rejection,
                    event_text=event_label(event_status, event_error),
                    battle_id=sample.get('battle_id', sample.get('session')),
                    game_time=stats.get('game_time', sample.get('game_time')),
                    stage=live_stage.label if has_live_stage else None,
                    stage_code=live_stage.code or None, stage_name=live_stage.name or None,
                    stage_raw=live_stage.raw, stage_detail=live_stage.detail,
                    stage_known=live_stage.known,
                    panels=[self._actor_view(p, stats, local_player.get('actor_ledgers'),
                                side=local_player.get('player_side'), player_uid=local_player.get('player_uid'),
                                event=live_event) if local_player else self.view(p, stats, None, live_event)
                            for p in self.layout.panels],
                    multiplayer_supported=supported, multiplayer_active=active,
                    multiplayer_mode_pending=supported and active != desired,
                    multiplayer_scope_unverified=scope_unverified,
                    multiplayer_scope_detail=('仅显示按角色汇总的原生累计' if character_counters else
                                              sample.get('autochess_scope_detail') if scope_unverified else None),
                    multiplayer=(self.multiplayer_view(multiplayer, event=live_event)
                                 if active and (multiplayer or {}).get('collection_mode') != 'local' else None),
                    live_analysis=analysis[0],
                    boss_live_analysis=analysis[1],
                    boss_effective_live_analysis=analysis[2],
                    enemy=sample.get('enemy') or dict(status='starting', error=None,
                        ready=False, rows=[], totals={}, series=[]),
                    history=self._history_index, history_selected=self._selected_history(),
                    history_notice=self.history_notice)

    def persist(self, layout):
        self.store.save(layout)
        self.layout = layout

    def command(self, cmd):
        if type(cmd.get('version')) is not int or cmd.get('version') != PROTOCOL_VERSION:
            raise ValueError('不支持的 worker 协议版本')
        op = cmd['op']
        if op == 'snapshot':
            return None
        if op in ('panel_add', 'panel_duplicate'):
            config = PanelConfig(panel_id=str(uuid4()))
            if op == 'panel_duplicate':
                old = next(p for p in self.layout.panels if p.panel_id == cmd['panel_id'])
                config = replace(old, panel_id=config.panel_id)
            self.persist(replace(self.layout, panels=self.layout.panels + (config,)))
        elif op == 'panel_update':
            config = PanelConfig.from_dict(cmd['config'])
            if config.panel_id not in {p.panel_id for p in self.layout.panels}:
                raise ValueError('源面板已删除')
            self.persist(replace(self.layout, panels=tuple(config if p.panel_id == config.panel_id else p
                                                          for p in self.layout.panels)))
        elif op == 'panel_delete':
            panels = tuple(p for p in self.layout.panels if p.panel_id != cmd['panel_id'])
            if not panels:
                raise ValueError('至少保留一个面板')
            panel_id = cmd['panel_id']
            self.persist(replace(self.layout, panels=panels,
                                 huds=tuple(h for h in self.layout.huds if h.panel_id != panel_id),
                                 hud_styles=tuple((key, style) for key, style in self.layout.hud_styles
                                                  if key != panel_id)))
        elif op == 'theme':
            self.persist(replace(self.layout, theme=cmd['theme']))
        elif op == 'number_units':
            self.persist(replace(self.layout, number_units=cmd['number_units']))
        elif op == 'multiplayer_enabled':
            enabled = cmd.get('enabled')
            if type(enabled) is not bool:
                raise ValueError('卫戍队友DPS开关必须是布尔值')
            if enabled != self.layout.multiplayer_enabled:
                self.persist(replace(self.layout, multiplayer_enabled=enabled))
        elif op == 'debug_mode':
            enabled = cmd.get('enabled')
            if type(enabled) is not bool:
                raise ValueError('调试模式开关必须是布尔值')
            if enabled != self.layout.debug_mode:
                self.persist(replace(self.layout, debug_mode=enabled))
        elif op == 'hud_style':
            panel_id = cmd['panel_id']
            if panel_id not in {p.panel_id for p in self.layout.panels}:
                raise ValueError('HUD 源面板已删除')
            styles = dict(self.layout.hud_styles)
            if cmd['style'] is None:
                styles.pop(panel_id, None)
            else:
                styles[panel_id] = HudStyle.from_dict(cmd['style'])
            self.persist(replace(self.layout, hud_styles=tuple(styles.items())))
        elif op == 'placement':
            known = {p.panel_id for p in self.layout.panels}
            huds = tuple(HudPlacement.from_dict(h) for h in cmd['huds'])
            if any(h.panel_id not in known for h in huds):
                raise ValueError('HUD 源面板已删除')
            self.persist(replace(self.layout, gui=GuiGeometry.from_dict(cmd['gui']), huds=huds,
                                 side_mode=cmd.get('side_mode', self.layout.side_mode)))
        elif op == 'history_refresh':
            self.refresh_history()
        elif op == 'history_select':
            selected = self.records[cmd['battle_id']]
            if self.selected is None or selected.battle_id != self.selected.battle_id:
                self._previous_selected, self.selected = self.selected, selected
        elif op == 'history_config':
            self.history_config = PanelConfig.from_dict(dict(cmd['config'], panel_id='history'))
        elif op == 'export':
            battle_id = cmd['battle_id']
            # The save dialog can outlive a pending selection or an archive refresh.
            record = next((record for record in (self.selected, self._previous_selected)
                           if record is not None and record.battle_id == battle_id), None)
            if record is None:
                record = self.records.get(battle_id)
            if record is None:
                raise ValueError('无法找到要导出的战绩')
            if cmd['format'] == 'json':
                payload = json_bytes(record)
            elif cmd['format'] == 'png':
                payload = base64.b64decode(cmd['payload'], validate=True)
                if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('无效 PNG')
            else:
                raise ValueError('未知导出格式')
            write_export(cmd['path'], payload, history_directory=self.history.directory,
                         overwrite=bool(cmd.get('overwrite', False)))
            return '已保存：' + Path(cmd['path']).name
        elif op == 'connection_apply':
            config = ConnectionConfig.from_dict(cmd['config'])
            if config.platform == 'mumu' and not config.auto_detect and not config.serial:
                raise ValueError('自动检测关闭时必须填写端口地址')
            enabled = cmd['events']
            if not isinstance(enabled, bool):
                raise ValueError('事件开关必须为布尔值')
            old_layout = self.layout
            candidate_layout = replace(old_layout, event_observation=enabled)
            self.store.save(candidate_layout)
            try:
                self.connection_store.save(config)
            except Exception as exc:
                try:
                    self.store.save(old_layout)
                except Exception as rollback:
                    raise RuntimeError(
                        f'连接配置保存失败：{exc}；旧布局回滚失败：{rollback}') from exc
                raise
            self.layout = candidate_layout
            self.connection = config
            if config.platform != self.effective_connection.platform:
                self._clear_live_source()
            self.effective_connection = config
        else:
            raise ValueError('未知命令：' + str(op))
        return None


class CollectionWorker:
    """Owns the sole CollectionSession, never references WPF or pipe objects."""
    def __init__(self, state, *, endpoint=DEFAULT_FRIDA_REMOTE, native_pc_observer_factory=None,
                 arkframework_root=None):
        self.directory = state.directory
        self.history, self.endpoint = state.history, endpoint
        self.native_pc_observer_factory = native_pc_observer_factory
        self.arkframework_root = Path(arkframework_root) if arkframework_root is not None else None
        self.requests, self.events = queue.Queue(), queue.Queue()
        self.effective = (state.connection, state.layout.event_observation)
        self._request_lock = threading.Lock()
        self._autochess_enabled = state.layout.multiplayer_enabled
        self._debug_mode = threading.Event()
        self.set_debug_mode(state.layout.debug_mode)
        self._request_revision = 0
        self.stop = threading.Event()
        self.session = None
        self.cleanup_error = ''
        self.retired_errors = []
        self.pc_runtime = None
        self.pc_runtime_key = None
        self.pc_runtime_dir = state.history.directory.parent
        self.pc_cleanup_error = ''
        self._last_pc_bootstrap = None
        self._pc_bootstrap_revision = None
        self._bootstrap_audit = None
        self.pending_history_count = 0
        self.last_connect_attempt = 0.0
        self.last_endpoint_check = 0.0
        self.thread = threading.Thread(target=self.run, name='ArkDPS collection')

    def set_multiplayer_enabled(self, enabled):
        with self._request_lock:
            self._autochess_enabled = enabled

    def autochess_enabled(self):
        with self._request_lock:
            return self._autochess_enabled

    @property
    def debug_mode(self):
        return self._debug_mode.is_set()

    def set_debug_mode(self, enabled):
        if enabled:
            self._debug_mode.set()
        else:
            self._debug_mode.clear()

    def _trace_pc_startup(self, record):
        if (self.debug_mode or record.get('event') in PC_STARTUP_KEY_EVENTS
                or record.get('status') in PC_STARTUP_KEY_STATES):
            _write_diagnostic_event('pc-startup', record)

    def request(self, config, enabled):
        with self._request_lock:
            if self.stop.is_set():
                raise RuntimeError('采集器正在关闭，不能重新连接')
            self._request_revision += 1
            self.effective = (config, enabled)
            self.requests.put((self._request_revision, config, enabled))
            return self._request_revision

    def _retry_connection(self, revision):
        """An old session's recovery cannot supersede a newer user request."""
        with self._request_lock:
            if self.stop.is_set() or revision != self._request_revision:
                return False
            self.requests.put((revision, *self.effective))
            return True

    def _next_request(self):
        with self._request_lock:
            while True:
                request = self.requests.get_nowait()
                if request[0] == self._request_revision:
                    return request

    def release(self):
        if self.session is None:
            return True
        try:
            self.session.close()
        except Exception as exc:
            self.cleanup_error = f'旧采集会话清理失败（{exc}）。请解决问题后重连观察者。'
            self.events.put(('error', self.cleanup_error))
            return False
        if self.session.cleanup_errors or self.cleanup_error:
            error = '已释放旧会话，保留历史清理异常：' + str(self.session.cleanup_errors or self.cleanup_error)
            self.retired_errors.append(error)
            self.events.put(('warning', error))
        self.session = None
        self.cleanup_error = ''
        return True

    def _connect_session(self, adb, serial, runtime, **kwargs):
        # Publishing ownership and admitting the connection are one local
        # commit against close(). Session setup opens no device connection.
        # Once admitted, connect is in flight and shutdown owns its cancellation
        # and release. Never hold this gate across the blocking transport call.
        with self._request_lock:
            if self.stop.is_set():
                raise InterruptedError('采集线程正在关闭，不再建立采集会话')
            if self.session is not None:
                raise RuntimeError('旧采集会话尚未释放')
            self.session = CollectionSession(adb, serial, **kwargs)
            self.session.enable_lifecycle(runtime)
            session = self.session
        session.connect()
        return session

    def _pc_provider(self, config, enabled, *, explicit_retry=False):
        with self._request_lock:
            if self.stop.is_set():
                raise InterruptedError('采集器正在关闭')
        needed = (config.platform == 'native_pc' and enabled and bool(config.native_game_root)
                  and self.native_pc_observer_factory is None)
        key = str(Path(config.native_game_root).expanduser().resolve()) if needed and config.native_game_root else None
        if self.pc_runtime is not None and (not needed or key != self.pc_runtime_key):
            if not self._close_pc_runtime():
                raise RuntimeError(self.pc_cleanup_error)
        if not needed:
            return self.native_pc_observer_factory if config.platform == 'native_pc' and enabled else None
        if self.pc_runtime is None:
            from capture_cli import _native_pc_frida_runtime
            candidate = _native_pc_frida_runtime(config.native_game_root, self.pc_runtime_dir,
                                                startup_trace=self._trace_pc_startup)
            with self._request_lock:
                if self.stop.is_set():
                    candidate.close()
                    raise InterruptedError('采集器在启动运行时创建期间关闭')
                self.pc_runtime = candidate
                self.pc_runtime_key = key
                try:
                    # Publish the owner before starting it, under the same
                    # gate used by shutdown, so cancellation cannot miss it.
                    self.pc_runtime.start()
                except Exception as exc:
                    self.events.put(('warning', str(exc)))
        elif explicit_retry:
            # A user request reconciles the existing owner. Automatic retries
            # preserve its loading window, lease and attempted process births.
            self.pc_runtime.retry()
        return self.pc_runtime.observer_factory

    def _close_pc_runtime(self):
        if self.pc_runtime is None:
            return True
        try:
            self.pc_runtime.close()
        except Exception as exc:
            self.pc_cleanup_error = f'PC 启动加载器清理失败：{exc}'
            self.events.put(('error', self.pc_cleanup_error))
            return False
        if self.pc_cleanup_error:
            self.retired_errors.append('已释放 PC 启动加载器，保留历史清理异常：' + self.pc_cleanup_error)
        self.pc_runtime = None
        self.pc_runtime_key = None
        self._pc_bootstrap_revision = None
        self.pc_cleanup_error = ''
        self._emit_pc_bootstrap(None)
        return True

    def _emit_pc_bootstrap(self, value, *, force=False):
        if force or value != self._last_pc_bootstrap:
            self._last_pc_bootstrap = value
            self.events.put(('native_pc_bootstrap', value))
            if self._bootstrap_audit is not None:
                self._bootstrap_audit('native_pc_bootstrap', value)

    def _pc_observer_recovery_needed(self, session, generation):
        if self.pc_runtime is None:
            return False
        bootstrap = self.pc_runtime.describe()
        self._emit_pc_bootstrap(bootstrap)
        observer_status = getattr(session.observer, 'status', None)
        failed = (session.event_status in ('failed', 'incompatible', 'stopped')
                  or observer_status in (ObserverStatus.FAILED, ObserverStatus.INCOMPATIBLE, ObserverStatus.STOPPED))
        if not failed:
            return False
        return (self.pc_runtime.is_pending or
                (bootstrap['status'] == 'ready' and bootstrap.get('ready_generation') is not None
                 and bootstrap['ready_generation'] != generation))

    def run(self):
        try:
            from capture_cli import json_value

            runtime = BattleRuntime()
            capture_dir = self.directory / 'captures'
            context = LogContext(self.directory)
            saved_connection_path = self.directory / 'connection.json'
            observed_config, observed_signature, observed_environment = None, None, None
            current_config, current_enabled = None, False

            def header(source, *, debug_mode):
                latest_config, latest_events = (self.effective if current_config is None
                                                else (current_config, current_enabled))
                result = context.header(source, debug_mode=debug_mode)
                data = result['data']
                data['settings'].update(platform=latest_config.platform,
                    auto_detect=latest_config.auto_detect, event_observation=latest_events,
                    adb_configured=bool(latest_config.adb_path), manual_target_configured=bool(latest_config.serial),
                    native_game_root_configured=bool(latest_config.native_game_root))
                if latest_config == observed_config and observed_environment is not None:
                    data['environment'] = observed_environment
                elif latest_config != ConnectionConfigStore(saved_connection_path).load().config:
                    data['environment'] = {'platform': latest_config.platform, 'provenance': 'not_observed'}
                return result

            with DesktopCaptureJournal(capture_dir, encoder=json_value,
                                       warn=lambda message: self.events.put(('warning', message)),
                                       header=header, debug_mode=lambda: self.debug_mode) as journal:
                sampling_observation = last_lifecycle = None
                last_transient_transition = {}

                def observe_environment(data, config):
                    nonlocal observed_config, observed_signature, observed_environment
                    if not data.get('version_actual'):
                        return
                    data = dict(data, platform=config.platform)
                    signature = tuple(data.get(key) for key in (
                        'version_actual', 'version_expected', 'version_status', 'adb_path', 'selected_serial'))
                    if config == observed_config and signature == observed_signature:
                        return
                    connection_bytes = None
                    try:
                        with saved_connection_path.open('rb') as stream:
                            raw = stream.read(262145)
                        if len(raw) <= 262144 and ConnectionConfig.from_dict(json.loads(raw)) == config:
                            connection_bytes = raw
                    except (OSError, ValueError, RecursionError):
                        pass
                    environment = context.update_environment(data, cache=connection_bytes is not None,
                                                             connection_bytes=connection_bytes)
                    observed_config, observed_signature = config, signature
                    if environment != observed_environment:
                        observed_environment = environment
                        journal.write('environment', environment)
                        _write_diagnostic_event('environment', environment)

                def audit(kind, value):
                    nonlocal sampling_observation, last_lifecycle
                    if kind == 'connected' and current_config is not None:
                        observe_environment(value.get('diagnostics', {}), current_config)
                    if kind == 'lifecycle_observation':
                        sampling_observation = (value.feed, value.sequence, value.kind, value.reason)
                        if value.kind != last_lifecycle:
                            transition = (last_lifecycle, value.kind)
                            now = time.monotonic()
                            if (self.debug_mode or 'gap' not in transition or
                                    now - last_transient_transition.get(transition, float('-inf')) >= 30):
                                journal.write('lifecycle_state', {'previous': last_lifecycle, 'state': value.kind})
                                if 'gap' in transition:
                                    last_transient_transition[transition] = now
                            last_lifecycle = value.kind
                    journal.write(kind, value)
                self._bootstrap_audit = audit
                regeneration = None
                try:
                    usable = False
                    retry_allowed = True
                    current_target = None
                    current_revision = None
                    explicit_retry_pending = False
                    session_bootstrap_generation = None
                    last_battle_waiting = None
                    last_observer_state = None
                    endpoint_bound = False
                    retries = 0
                    last_retry_audit = float('-inf')
                    last_live_frame = None
                    last_sampling_rejection = None
                    last_autochess_scene = object()
                    clock_burst = None
                    bundled_observer = self.endpoint == DEFAULT_FRIDA_REMOTE and BUNDLED_SERVER_ARCHIVE.is_file()
                    initial_config, initial_events = self.effective
                    audit('worker_started', {'platform': initial_config.platform, 'event_observation': initial_events})

                    def publish_sampling_rejection(value):
                        nonlocal last_sampling_rejection
                        if value != last_sampling_rejection:
                            last_sampling_rejection = value
                            self.events.put(('sampling_rejection', value))

                    def publish_autochess_scene(runtime, *, exited=False):
                        nonlocal last_autochess_scene
                        feed = runtime.feed
                        scene = None if exited else getattr(feed, 'autochess_scene', None)
                        if scene is None and not (exited or getattr(feed, 'autochess_exited', False)):
                            return  # A short read is not an exit or a phase transition.
                        if scene != last_autochess_scene:
                            last_autochess_scene = scene
                            self.events.put(('autochess_scene', scene))

                    def bind_observer_endpoint(target):
                        nonlocal endpoint_bound
                        ok, detail = ensure_frida_endpoint(target.adb_path, target.serial, remote=self.endpoint)
                        endpoint_bound = ok
                        self.last_endpoint_check = time.monotonic()
                        audit('frida_endpoint', {'ok': ok, 'detail': detail})
                        if not ok:
                            self.events.put(('warning', '事件端点：' + detail))
                        return ok, detail

                    while not self.stop.is_set():
                        next_live_read = None
                        clock_retry_delay = None
                        try:
                            revision, config, enabled = self._next_request()
                        except queue.Empty:
                            config = None
                        if config is not None:
                            changed = revision != current_revision
                            current_revision = revision
                            current_config, current_enabled = config, enabled
                            if changed:
                                explicit_retry_pending = True
                                last_lifecycle = None
                                last_transient_transition.clear()
                                audit('connection_attempt', {'revision': revision, 'platform': config.platform,
                                                             'event_observation': enabled})
                            current_target = None
                            usable = False
                            retry_allowed = True
                            last_observer_state = None
                            endpoint_bound = False
                            retries = 0
                            last_retry_audit = float('-inf')
                            last_live_frame = None
                            last_battle_waiting = None
                            session_bootstrap_generation = None
                            self.last_connect_attempt = time.monotonic()
                            self.events.put(('connection_source', {
                                'revision': revision, 'platform': config.platform}))
                            last_sampling_rejection = None
                            last_autochess_scene = object()
                            clock_burst = None
                            self.events.put(('sampling_rejection', None))
                            self.events.put(('scanning', True))
                            if self.release():
                                try:
                                    session_kwargs = {'autochess_enabled': self.autochess_enabled,
                                                      'debug_mode': lambda: self.debug_mode}
                                    pc_provider = self._pc_provider(config, enabled,
                                        explicit_retry=explicit_retry_pending)
                                    explicit_retry_pending = False
                                    if self.pc_runtime is not None:
                                        bootstrap = self.pc_runtime.describe()
                                        self._emit_pc_bootstrap(bootstrap, force=revision != self._pc_bootstrap_revision)
                                        self._pc_bootstrap_revision = revision
                                        if self.pc_runtime.is_pending:
                                            self.events.put(('observer', ('attaching', bootstrap.get('error'))))
                                            self.events.put(('connection', bootstrap.get('error') or
                                                'PC 事件采集正在准备，请先运行 ArkDPS，再启动游戏。'))
                                            self.events.put(('scanning', False))
                                            self.stop.wait(COLLECTION_PERIOD)
                                            continue
                                        session_bootstrap_generation = bootstrap.get('ready_generation')
                                    if config.platform == 'native_pc':
                                        from native_pc_collector import NativePcCollector
                                        session_kwargs['collector'] = NativePcCollector(
                                            config.native_game_root, collect_actions=enabled,
                                            event_identity_only=(enabled and self.arkframework_root is None
                                                and self.native_pc_observer_factory is None))
                                        if pc_provider is not None:
                                            session_kwargs['observer_factory'] = pc_provider
                                        adb, serial = None, None
                                        remote = None if enabled else ''
                                    else:
                                        target = resolve_configured(config)
                                        current_target = target
                                        self.events.put(('diagnostic', target.diagnostics.as_dict()))
                                        if enabled and not bundled_observer:
                                            bind_observer_endpoint(target)
                                        if enabled and bundled_observer:
                                            session_kwargs['prepare_observer'] = (
                                                lambda target=target: bind_observer_endpoint(target))
                                        adb, serial = target.adb_path, target.serial
                                        remote = self.endpoint if enabled else None
                                    self._connect_session(adb, serial, runtime,
                                        frida_remote=remote, **session_kwargs,
                                        history=self.history, audit=audit, state_dir=self.directory,
                                        on_history_result=lambda result: self.events.put(('history', result)),
                                        log=lambda x: print(x, file=sys.stderr, flush=True))
                                    if self.stop.is_set():
                                        continue
                                    diag = self.session.collector.diagnostics
                                    diag_dict = diag.as_dict()
                                    observe_environment(diag_dict, config)
                                    self.events.put(('diagnostic', diag_dict))
                                    text = (diag.message or '已连接官服 PC · 累计快照'
                                            if config.platform == 'native_pc' else
                                            f'已连接 {diag.selected_serial} · 游戏 {diag.version_actual}')
                                    self.events.put(('connection', text))
                                    usable = True
                                except Exception as exc:
                                    diag = getattr(exc, 'diagnostics', None)
                                    if diag is None and self.session is not None:
                                        diag = getattr(self.session.collector, 'diagnostics', None)
                                    diag_dict = diag.as_dict() if diag is not None else {}
                                    audit('connection_failed', {'revision': revision, 'platform': config.platform,
                                          'code': diag_dict.get('code'), 'error_type': type(exc).__name__,
                                          'win32_error': (diag_dict.get('details') or {}).get('win32_error',
                                                                                             getattr(exc, 'winerror', None))})
                                    if diag:
                                        observe_environment(diag_dict, config)
                                        self.events.put(('diagnostic', diag_dict))
                                    retry_allowed = not (
                                        config.platform == 'native_pc'
                                        and getattr(diag, 'code', None) in NATIVE_ACTION_REQUIRED_CODES)
                                    self.events.put(('error', str(exc)))
                                    self.release()
                            self.events.put(('scanning', False))
                        if (not usable and self.pc_runtime is not None
                                and self._pc_bootstrap_revision == current_revision):
                            # Startup telemetry must not wait for the five-second
                            # connection retry or alter its owner/arming boundary.
                            self._emit_pc_bootstrap(self.pc_runtime.describe())
                        if usable and self.session is not None:
                            if self._pc_observer_recovery_needed(self.session, session_bootstrap_generation):
                                # Reauthentication waits until the old observer
                                # has released its session and script. A changed
                                # authenticated generation can repair a fallback
                                # session; an unchanged healthy bootstrap cannot
                                # spin on a permanently incompatible probe.
                                usable = False
                                retry_allowed = True
                                self.last_connect_attempt = time.monotonic()
                                if self.release():
                                    self.events.put(('observer', ('attaching', None)))
                                    self._retry_connection(current_revision)
                                self.stop.wait(COLLECTION_PERIOD)
                                continue
                            # Attachment can be confirmed in the lobby, before any battle sample.
                            observer_state = (('snapshot_only', None) if current_config.platform == 'native_pc'
                                              else ('disabled', None))
                            if current_config.platform == 'native_pc' and current_enabled:
                                observer_state = (self.session.event_status, self.session.event_error)
                                observer = self.session.observer
                                provider_status = getattr(observer, 'status', ObserverStatus.READY)
                                if provider_status in (
                                        ObserverStatus.FAILED, ObserverStatus.INCOMPATIBLE, ObserverStatus.STOPPED):
                                    observer_state = (provider_status,
                                                      getattr(observer, 'error', None) or self.session.event_error)
                            if current_enabled and current_config.platform == 'mumu':
                                observer = self.session.observer
                                if getattr(self.session, 'event_status', None) == WAITING_GAME_INIT:
                                    observer_state = (WAITING_GAME_INIT, self.session.event_error)
                                elif observer is not None:
                                    observer_state = (observer.status, observer.error)
                                else:
                                    observer_state = ('failed', self.session.event_error)
                            if observer_state != last_observer_state:
                                last_observer_state = observer_state
                                self.events.put(('observer', observer_state))
                            runtime = self.session.runtime
                            feed, sessions = runtime.feed, runtime.sessions
                            consume_sequence = sessions.sequence
                            clock_retry = (current_config.platform == 'native_pc' and current_enabled
                                and self.native_pc_observer_factory is None and self.arkframework_root is None
                                and getattr(self.session.observer, 'native_clock_busy_retry', False) is True
                                and getattr(self.session.observer, 'contract', None) is NATIVE_PC_CONTRACT)
                            if clock_retry:
                                owner = (current_revision, self.session, self.session.observer, runtime, feed, sessions)
                                if clock_burst is not None and (self.stop.is_set() or current_revision != self._request_revision):
                                    clock_burst = None
                                    continue
                                same_owner = (clock_burst is not None and owner[0] == clock_burst[0][0]
                                    and all(left is right for left, right in zip(owner[1:], clock_burst[0][1:])))
                                read_perf = time.perf_counter()
                                if same_owner:
                                    if read_perf - clock_burst[1] >= NATIVE_BUSY_BUDGET:
                                        clock_burst = None
                                        self.stop.wait(COLLECTION_PERIOD)
                                        continue
                                    clock_burst = (owner, clock_burst[1], clock_burst[2] + 1)
                                else:
                                    clock_burst = (owner, read_perf, 1)
                            else:
                                clock_burst = None
                            started = time.monotonic()
                            sampling_observation = None
                            try:
                                sample = self.session.read()
                                publish_autochess_scene(runtime)
                                self.events.put(('lifecycle', runtime.sessions.state))
                                if sample:
                                    publish_sampling_rejection(None)
                                    self.events.put(('sample', sample))
                                    if sample.get('state') == 2 and sample.get('frame') is not None:
                                        frame = (sample.get('battle_id'), sample['frame'])
                                        if frame != last_live_frame:
                                            next_live_read = started + COLLECTION_PERIOD
                                        last_live_frame = frame
                            except CaptureRetry as exc:
                                publish_autochess_scene(runtime)
                                lifecycle = runtime.sessions.state
                                self.events.put(('lifecycle', lifecycle))
                                # A failed read may already have consumed a native
                                # pre-observer frame. Never accelerate that path.
                                if (clock_retry and self.session is owner[1]
                                        and self.session.observer is owner[2]
                                        and self.session.runtime is runtime and runtime.feed is feed
                                        and runtime.sessions is sessions and sessions.sequence == consume_sequence
                                        and sampling_observation is None
                                        and not self.stop.is_set() and current_revision == self._request_revision
                                        and _native_clock_busy(exc)
                                        and clock_burst[2] < NATIVE_BUSY_ATTEMPTS
                                        and time.perf_counter() + NATIVE_BUSY_REST < clock_burst[1] + NATIVE_BUSY_BUDGET):
                                    clock_retry_delay = NATIVE_BUSY_REST
                                observed_feed, observed_sequence, observed_kind, observed_reason = (
                                    sampling_observation or (None, None, None, None))
                                current_observation = observed_feed == feed.id and observed_sequence == sessions.sequence
                                if isinstance(exc, MappingRetry):
                                    reason = str(exc).partition('\n')[0].strip()[:200]
                                    publish_sampling_rejection(dict(code='mapping_unavailable',
                                                                    reason=reason or '归属证据尚未确认'))
                                elif (current_config.platform == 'native_pc' and current_observation
                                        and observed_kind == 'scope_unavailable'
                                        and observed_reason == NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED):
                                    publish_sampling_rejection(dict(code='mapping_unavailable',
                                        reason='卫戍小局的阶段和玩家身份尚未确认',
                                        scope_code=NATIVE_PC_AUTOCHESS_SCOPE_UNVERIFIED))
                                elif current_observation and observed_kind in ('preparing', 'waiting', 'end', 'terminal'):
                                    # _read_lifecycle reports successfully consumed non-sample
                                    # observations as retries. A gap or an old state is not proof.
                                    publish_sampling_rejection(None)
                                if lifecycle == 'playing' or isinstance(exc, MappingRetry):
                                    retries += 1
                                    now = time.monotonic()
                                    if now - last_retry_audit >= 5:
                                        reason = str(exc).partition('\n')[0].strip()
                                        if reason.startswith('observer ') and ' request failed:' in reason:
                                            reason = reason.partition(':')[0]
                                        audit('capture_retry', {
                                            'error_type': type(exc).__name__,
                                            'reason': reason[:200], 'count': retries,
                                            'duration_ms': round((now - started) * 1000, 1),
                                            'monotonic': now})
                                        retries = 0
                                        last_retry_audit = now
                            except BattleChanged as exc:
                                if isinstance(exc, ProcessChanged):
                                    publish_autochess_scene(runtime, exited=True)
                                self.events.put(('error', str(exc)))
                                usable = False
                                self.last_connect_attempt = time.monotonic()
                                self._retry_connection(current_revision)
                            except Exception as exc:
                                audit('capture_error', {'error_type': type(exc).__name__,
                                      'reason': str(exc).partition('\n')[0][:200],
                                      'monotonic': time.monotonic()})
                                self.events.put(('error', str(exc)))
                                diag = getattr(exc, 'diagnostics', None)
                                if diag is None:
                                    diag = getattr(self.session.collector, 'diagnostics', None)
                                if diag is not None:
                                    self.events.put(('diagnostic', diag.as_dict()))
                                retry_allowed = not (
                                    current_config.platform == 'native_pc'
                                    and getattr(diag, 'code', None) in NATIVE_ACTION_REQUIRED_CODES)
                                usable = False
                            if clock_retry_delay is None:
                                clock_burst = None
                            if usable:
                                battle_waiting = runtime.feed.battle_waiting
                                if battle_waiting is not None and battle_waiting != last_battle_waiting:
                                    last_battle_waiting = battle_waiting
                                    self.events.put(('battle_waiting', battle_waiting))
                        if (self.arkframework_root is not None and current_enabled
                                and current_config.platform == 'native_pc'):
                            if regeneration is None:
                                from native_pc.arkframework_replay import RegenerationRecorder
                                regeneration = RegenerationRecorder(
                                    self.arkframework_root / 'battle_timeline.json',
                                    capture_dir / f'{journal.run}.arkframework')
                            regeneration.poll_reporting(time.monotonic(), audit)
                        if (usable and current_enabled and current_target is not None
                                and (not bundled_observer or self.session.event_status != WAITING_GAME_INIT)
                                and time.monotonic() - self.last_endpoint_check >= 5):
                            started = time.monotonic()
                            observer = self.session.observer
                            # ADB may still accept TCP while the device-side route is
                            # stale. Only a live attached observer permits the fast path.
                            observer_ready = observer is not None and observer.status == ObserverStatus.READY
                            check = keep_frida_endpoint if endpoint_bound and observer_ready else ensure_frida_endpoint
                            ok, detail = check(current_target.adb_path, current_target.serial,
                                               remote=self.endpoint)
                            endpoint_bound = ok
                            finished = time.monotonic()
                            self.last_endpoint_check = finished
                            audit('frida_keepalive', {'ok': ok, 'detail': detail,
                                  'duration_ms': round((finished - started) * 1000, 1),
                                  'monotonic': finished})
                            if not ok:
                                self.events.put(('warning', '事件端点保活：' + detail))
                        if (not usable and retry_allowed and current_config is not None
                                and time.monotonic() - self.last_connect_attempt >= 5):
                            self.last_connect_attempt = time.monotonic()
                            self._retry_connection(current_revision)
                        # Pace successful live reads start-to-start, with a short
                        # rest even if capture took longer than the whole period.
                        # Idle, paused and retry paths keep their existing backoff.
                        delay = (clock_retry_delay if clock_retry_delay is not None else
                                 COLLECTION_PERIOD if next_live_read is None else
                                 max(COLLECTION_MIN_REST, next_live_read - time.monotonic()))
                        self.stop.wait(delay)
                finally:
                    # Session shutdown writes final audit records; keep its sink open.
                    # A transient cleanup failure gets one retry before the sink closes.
                    if not self.release():
                        self.release()
                    if not self._close_pc_runtime():
                        self._close_pc_runtime()
                    config, enabled = self.effective
                    if regeneration is not None and enabled and config.platform == 'native_pc':
                        regeneration.poll_reporting(time.monotonic(), audit, force=True)
                    self.pending_history_count = len(runtime.pending_history)
                    audit('worker_stopped', {'pending_history_count': self.pending_history_count,
                        'cleanup_ok': not bool(self.cleanup_error or self.pc_cleanup_error or self.retired_errors)})
        except Exception as exc:
            self.events.put(('worker_error', str(exc)))
            traceback.print_exc(file=sys.stderr)

    def close(self, *, require_history_saved=False):
        with self._request_lock:
            self.stop.set()
            startup = self.pc_runtime
        if startup is not None:
            startup.cancel()
        if self.thread.ident is not None:
            self.thread.join()  # Never orphan device cleanup on UI exit / pipe EOF.
        else:
            self.release()
            self._close_pc_runtime()
        errors = self.retired_errors + [error for error in (self.cleanup_error, self.pc_cleanup_error) if error]
        if require_history_saved and self.pending_history_count:
            errors.append(f'{self.pending_history_count} 局战绩尚未保存，更新未应用。')
        if errors:
            raise RuntimeError('；'.join(errors))


def _drain_worker_events(worker, state):
    """Bound admission, retaining FIFO samples, transitions and archive commits.

    Every admitted sample still updates analysis. This neither coalesces raw
    observations nor refreshes their captured_monotonic. A running producer
    cannot prevent a reply by keeping the queue nonempty. One admitted update
    may finish after the budget; this is not an interruption of that update.
    """
    started = time.perf_counter()
    for index in range(BRIDGE_EVENT_LIMIT):
        if index and time.perf_counter() - started >= BRIDGE_EVENT_BUDGET:
            break
        try:
            event = worker.events.get_nowait()
        except queue.Empty:
            break
        state.accept(*event)


def serve(input_stream, output, state, worker=None, *, internal_capture=False):
    """Versioned UTF-8 JSON Lines: hello, snapshot/state/error, quit/bye."""
    handshake = False
    forced_trace = os.environ.get('ARKDPS_BRIDGE_TRACE') == '1'
    last_reply: tuple[int, str, str, int] | None = None
    while True:
        trace = forced_trace or state.layout.debug_mode
        line = input_stream.readline(MAX_MESSAGE + 2)
        if not line:
            return
        delimiter_size = 2 if line.endswith('\r\n') else int(line.endswith('\n'))
        if len(line.encode('utf-8')) - delimiter_size > MAX_MESSAGE:
            raise ValueError('worker 消息长度越界')
        if not line.endswith('\n'):
            # EOF can interrupt a write after the JSON object but before its delimiter.
            return
        cmd = {}
        log_op, request_id = 'unknown', None
        phase = 'request'
        phase_start = started = time.perf_counter()
        response_bytes = None
        quit_requested = False
        cache_response = False

        def diagnostic(event, *, error=None):
            now = time.perf_counter()
            size = '' if response_bytes is None else f' bytes={response_bytes}'
            failure = '' if error is None else f' error={error}'
            try:
                print(f'[bridge] {datetime.now(timezone.utc).isoformat()} op={log_op} id={request_id} '
                      f'phase={phase} event={event} elapsed_ms={(now - started) * 1000:.1f} '
                      f'phase_ms={(now - phase_start) * 1000:.1f}{size}{failure}', file=sys.stderr, flush=True)
            except OSError:
                pass

        def advance(next_phase):
            nonlocal phase, phase_start
            now = time.perf_counter()
            if now - phase_start >= BRIDGE_SLOW_PHASE_SECONDS:
                diagnostic('slow')
            phase, phase_start = next_phase, now
            if trace:
                diagnostic('begin')

        def send(payload):
            advance('write')
            if log_op in BRIDGE_KEY_COMMANDS and not trace:
                diagnostic('reply_ready')
            try:
                output.write(payload + '\n')
                advance('flush')
                output.flush()
            except OSError as exc:
                diagnostic('failed', error=type(exc).__name__)
                return False
            advance('complete')
            if log_op in BRIDGE_KEY_COMMANDS or trace or time.perf_counter() - started >= BRIDGE_SLOW_PHASE_SECONDS:
                diagnostic('sent')
            return True

        try:
            cmd = json.loads(line)
            if isinstance(cmd, dict):
                request_op, raw_id = cmd.get('op'), cmd.get('id')
                if isinstance(request_op, str) and request_op in BRIDGE_COMMANDS:
                    log_op = request_op
                if type(raw_id) is int:
                    request_id = raw_id
            if log_op in BRIDGE_KEY_COMMANDS or trace:
                diagnostic('received')
            if not isinstance(cmd, dict) or type(cmd.get('version')) is not int or cmd.get('version') != PROTOCOL_VERSION:
                raise ValueError('不支持的 worker 协议版本')
            if request_id is None and ('id' in cmd or last_reply is not None):
                raise ValueError('worker 命令缺少整数编号')
            if request_id is not None and last_reply is not None:
                last_id, last_line, last_payload, last_size = last_reply
                if request_id < last_id:
                    raise ValueError('worker 命令编号已过期')
                if request_id == last_id:
                    if line != last_line:
                        raise ValueError('worker 重试内容与原命令不一致')
                    response_bytes = last_size
                    diagnostic('replay')
                    if not send(last_payload):
                        return
                    continue
            cache_response = request_id is not None
            advance('events')
            if worker:
                _drain_worker_events(worker, state)
            advance('command')
            op = cmd['op']
            if op == 'hello':
                handshake = True
                from importlib.metadata import version, PackageNotFoundError
                try:
                    frida_version = version('frida')
                except PackageNotFoundError:
                    frida_version = None
                result = dict(transport='stdio-jsonl', worker='ArkDPS Python', version=PROTOCOL_VERSION,
                              python=sys.version.split()[0], executable=sys.executable, frida=frida_version,
                              request_replay=True)
                kind = 'hello'
            elif not handshake:
                raise ValueError('必须先完成 hello 握手')
            elif op == 'quit':
                require_history_saved = cmd.get('require_history_saved', False)
                if type(require_history_saved) is not bool:
                    raise ValueError('require_history_saved 必须是布尔值')
                if worker and require_history_saved:
                    worker.close(require_history_saved=True)
                elif worker:
                    worker.close()
                result, kind, quit_requested = None, 'bye', True
            elif op == 'detect':
                if internal_capture:
                    raise ValueError('内部事件候选不连接游戏或模拟器。')
                effective = getattr(worker, 'effective', None)
                config = effective[0] if effective is not None else state.effective_connection
                platform = cmd.get('platform', config.platform)
                if platform == 'native_pc':
                    native_root = cmd.get('native_game_root', config.native_game_root)
                    ConnectionConfig.from_dict({'platform': platform, 'native_game_root': native_root})
                    result = ConnectionDiagnostics(
                        status=DetectionStatus.NO_INSTANCE, platform='native_pc',
                        native_game_root=native_root,
                        code='native_root_required' if not native_root else 'native_unavailable',
                        message='官服 PC 无需 ADB 检测；请填写游戏目录并应用连接设置以检查构建和读取权限。',
                        version_expected=None).as_dict()
                elif platform == 'mumu':
                    result = detect_instances(cmd.get('adb') or None,
                                              register_mumu=worker is not None).diagnostics().as_dict()
                else:
                    raise ValueError('platform 必须是 mumu 或 native_pc')
                kind = 'state'
            elif op == 'rescan':
                if worker:
                    config, enabled = worker.effective
                    revision = worker.request(config, enabled)
                    state.expect_connection_request(config, revision)
                result, kind = None, 'state'
            else:
                result = state.command(cmd)
                if op == 'connection_apply' and worker:
                    revision = worker.request(state.connection, state.layout.event_observation)
                    state.expect_connection_request(state.connection, revision)
                elif op == 'connection_apply':
                    state.finish_offline_connection()
                elif op == 'multiplayer_enabled' and worker:
                    worker.set_multiplayer_enabled(state.layout.multiplayer_enabled)
                elif op == 'debug_mode' and worker:
                    worker.set_debug_mode(state.layout.debug_mode)
                kind = 'snapshot' if op == 'snapshot' else 'state'
            advance('snapshot')
            if getattr(worker, 'effective', None) is not None:
                state.effective_connection = worker.effective[0]
            reply = dict(version=PROTOCOL_VERSION, id=request_id, kind=kind, ok=True,
                         result=result, state=state.snapshot())
        except Exception as exc:
            diagnostic('failed', error=type(exc).__name__)
            reply = dict(version=PROTOCOL_VERSION, id=request_id,
                         kind='error', ok=False, error=str(exc))
        advance('encode')
        encode_error = None
        try:
            payload = json.dumps(reply, ensure_ascii=False, allow_nan=False)
            response_bytes = len(payload.encode('utf-8'))
            if response_bytes > MAX_MESSAGE:
                diagnostic('failed', error='oversize')
                encode_error = 'worker 响应长度越界'
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            diagnostic('failed', error=type(exc).__name__)
            encode_error = 'worker 无法编码响应'
        if encode_error is not None:
            payload = json.dumps(dict(version=PROTOCOL_VERSION, id=request_id, kind='error',
                                      ok=False, error=encode_error), ensure_ascii=False)
            response_bytes = len(payload.encode('utf-8'))
        # A completed command can outlive the parent's wait for its response.
        # Cache the actual reply, including errors, before the first pipe write.
        if cache_response:
            last_reply = (request_id, line, payload, response_bytes)
        if not send(payload) or quit_requested:
            return


def main(*, native_pc_observer_factory=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--internal-capture', metavar='SCENARIO')
    parser.add_argument('--adb')
    parser.add_argument('--serial')
    parser.add_argument('--platform', choices=('mumu', 'native_pc'))
    parser.add_argument('--native-game-root')
    parser.add_argument('--arkframework-root', type=Path)
    parser.add_argument('--no-events', action='store_true')
    parser.add_argument('--debug-mode', action='store_true')
    parser.add_argument('--frida-remote')
    args = parser.parse_args()
    from native_pc.internal_entry import portable_manifest
    manifest = portable_manifest(ROOT)
    if manifest and manifest.get('internal_event_backend') and args.internal_capture is None:
        parser.error('内部候选需要显式选择 --internal-capture，启动方式见随包说明。')
    if args.internal_capture is not None:
        if (not args.offline or args.no_events or any(value is not None for value in
                (args.fixture, args.adb, args.serial, args.platform, args.native_game_root,
                 args.arkframework_root, args.frida_remote))):
            parser.error('--internal-capture requires --offline and excludes live or other fixture options')
        from native_pc.internal_entry import internal_host, internal_state_dir
        try:
            host = internal_host(ROOT)
            args.state_dir = internal_state_dir(ROOT, args.state_dir, desktop=True)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.offline and args.state_dir is None:
        parser.error('--offline requires an isolated --state-dir')
    if args.fixture and not args.offline:
        parser.error('--fixture requires --offline')
    instance = None
    if not args.offline:
        instance = socket.socket()
        operation = 'bind'
        try:
            instance.bind(('127.0.0.1', 27380))
            operation = 'listen'
            instance.listen(1)
        except OSError as error:
            instance.close()
            _write_diagnostic_event('listener', {'operation': operation, 'port': 27380,
                'utc': datetime.now(timezone.utc).isoformat(),
                'error_type': type(error).__name__, 'system_error': getattr(error, 'winerror', None) or error.errno})
            raise SystemExit('ArkDPS 无法启动单实例监听；请查看日志。') from error
    output = sys.stdout
    sys.stdout = sys.stderr  # stdout is exclusively the protocol stream
    state = DesktopState(args.state_dir)
    if args.debug_mode:
        state.layout = replace(state.layout, debug_mode=True)
    effective_config = replace(
        state.connection, adb_path=args.adb or state.connection.adb_path,
        serial=args.serial or state.connection.serial,
        auto_detect=False if args.serial else state.connection.auto_detect,
        platform=args.platform or ('native_pc' if args.native_game_root is not None or args.arkframework_root is not None
                                   else state.connection.platform),
        native_game_root=(args.native_game_root if args.native_game_root is not None
                          else state.connection.native_game_root))
    state.effective_connection = effective_config
    if args.fixture:
        state.accept('sample', json.loads(args.fixture.read_text('utf-8')))
    if args.internal_capture is not None:
        from native_pc.internal_capture import run_internal_capture
        state.effective_connection = replace(effective_config, platform='native_pc')
        state.accept('connection', '内部 PC 事件候选 · 自有测试数据')

        def publish_internal_sample(sample):
            scene = autochess_scene(sample)
            if scene is not None or state._autochess_scene_announced:
                state.accept('autochess_scene', scene)
            state.accept('sample', sample)

        try:
            result = run_internal_capture(host, args.internal_capture, state.directory,
                                          publish=publish_internal_sample)
            if result.get('status') != 'ok':
                state.accept('error', str(result.get('error') or '内部采集未通过'))
            elif state.last is not None:
                state.accept('lifecycle', 'frozen')
        except (OSError, RuntimeError, ValueError) as exc:
            state.accept('error', str(exc))
        state.refresh_history()
    worker = None if args.offline else CollectionWorker(
        state, endpoint=args.frida_remote or DEFAULT_FRIDA_REMOTE,
        arkframework_root=args.arkframework_root,
        native_pc_observer_factory=native_pc_observer_factory)
    if worker:
        revision = worker.request(effective_config,
            False if args.no_events else (True if args.frida_remote is not None else state.layout.event_observation))
        state.expect_connection_request(effective_config, revision)
        worker.thread.start()
    try:
        if args.internal_capture is not None:
            serve(sys.stdin, output, state, internal_capture=True)
        else:
            serve(sys.stdin, output, state, worker)
    finally:
        if worker:
            worker.close()
        if instance:
            instance.close()


if __name__ == '__main__':
    main()
