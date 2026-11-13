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

from battle_history import BattleHistory, BattleRecord
from battle_lifecycle import BattleRuntime, MappingRetry
from battle_share import (json_bytes, record_display, record_statistics,
                         write_export)
from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE
from collector import BattleChanged, ROOT
from connection import (ConnectionConfig, ConnectionConfigStore, detect_instances,
                        resolve_configured)
from event_capture import CaptureRetry
from event_observer import ObserverStatus
from frida_provision import ensure_frida_endpoint, keep_frida_endpoint
from layout_store import LayoutStore, GuiGeometry, HudPlacement, HudStyle
from metrics import observation_age
from panel_model import (Category, EventAvailability, PanelConfig, detail_rates,
                         live_damage_rates, panel_statistics, render_panel, rows_from_statistics,
                         team_status)
import stage_names

PROTOCOL_VERSION = 1
MAX_MESSAGE = 32 * 1024 * 1024
BRIDGE_SLOW_PHASE_SECONDS = .5
BRIDGE_COMMANDS = frozenset((
    'hello', 'snapshot', 'quit', 'detect', 'rescan', 'panel_add', 'panel_duplicate',
    'panel_update', 'panel_delete', 'theme', 'number_units', 'hud_style', 'placement',
    'history_refresh', 'history_select', 'history_config', 'export', 'connection_apply',
))


def event_label(status, error=None):
    labels = {'recording': '事件观察中 · 护盾仅含观察区间',
              'ready': '事件已就绪 · 等待同帧基线', 'attaching': '正在挂载事件观察',
              'gap': '事件中断 · 伤害/治疗快照继续',
              'failed': '事件不可用 · 使用伤害/治疗快照',
              'incompatible': '观察者与游戏版本不兼容 · 使用伤害/治疗快照',
              'stopped': '观察者已停止 · 使用伤害/治疗快照',
              'disabled': '事件已关闭 · 使用伤害/治疗快照',
              'unavailable': '事件关闭或不可用 · 使用伤害/治疗快照',
              'finished': '事件观察结束 · 保留已覆盖区间'}
    return labels.get(status, '等待事件能力诊断') + (f'（{error}）' if error else '')


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
    key = {'damage': 'damage', 'healing': 'healing', 'shield': 'shield_absorbed'}[config.category.value]
    if config.ranking.value == 'recent':
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
        self.store = LayoutStore(directory / 'layout.json')
        loaded = self.store.load()
        self.layout = loaded.layout
        self.connection_store = ConnectionConfigStore(directory / 'connection.json')
        config = self.connection_store.load()
        self.connection = config.config
        self.warnings = [x for x in (loaded.warning, config.warning,
                                     stage_names.catalog().warning) if x]
        self.history = BattleHistory(directory / 'history')
        self.records = {}
        self.selected = None
        self.history_config = PanelConfig(panel_id='history')
        self.last = None
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
        self.battle_waiting = False
        self.scanning = False
        self.history_notice = ''
        self.refresh_history()

    def refresh_history(self):
        result = self.history.list_recent()
        self.records = {r.battle_id: r for r in result.records}
        self.history_notice = '；'.join(f'{d.code}: {d.message[:160]}' for d in result.diagnostics)
        if self.selected is None and result.records:
            self.selected = result.records[0]

    def accept(self, kind, value):
        if kind == 'sample':
            if self.last is not None and observation_age(self.last) > 2:
                self.analysis_interrupted = True
            self.error = ''
            frame = (value.get('session'), value.get('frame'))
            if frame != self.last_frame:
                self.last_progress = time.monotonic()
                self.last_frame = frame
            self.last = value
            self.event_status, self.event_error = value.get('event_status'), value.get('event_error')
            self.update_live_analysis(value)
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
        elif kind == 'lifecycle':
            self.lifecycle = value
        elif kind == 'battle_waiting':
            self.battle_waiting = value
        elif kind == 'history':
            # A record was committed by the collection owner: re-read the list
            # now instead of polling the directory on every snapshot.
            self.refresh_history()
        elif kind == 'scanning':
            self.scanning = value
            if value:
                self.error = ''
                self.observer_status = self.observer_error = None
                self.analysis_interrupted = True

    def update_live_analysis(self, sample):
        """Keep one chart sample and its in-process observed peak per battle."""
        statistics = sample.get('statistics') or {}
        battle_id = sample.get('battle_id', sample.get('session'))
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
        boss_statistics = panel_statistics(PanelConfig(boss_only=True), statistics)
        if boss_statistics['boss_damage_available'] or self.boss_live_analysis is not None:
            boss_coverage = sample.get('event_status') in (None, 'ready', 'recording', 'finished')
            self.boss_live_analysis = self.boss_trend_point(
                boss_statistics, battle_id, game_time, self.boss_live_analysis,
                terminal=terminal, covered=boss_coverage)
            self.boss_effective_live_analysis = self.boss_trend_point(
                boss_statistics, battle_id, game_time, self.boss_effective_live_analysis,
                terminal=terminal, covered=boss_coverage, effective=True)
        self.analysis_interrupted = False

    def boss_trend_point(self, statistics, battle_id, game_time, previous, *,
                         terminal, covered, effective=False):
        team_recent, operators = live_damage_rates(statistics, game_time, effective=effective)
        complete, _, _ = team_status(statistics)
        if not complete or terminal or not covered:
            team_recent = None
        if terminal or not covered:
            for operator in operators:
                operator['recent_dps'] = None
        segment = 0
        if previous is not None:
            segment = previous['segment']
            current_rates = {row['owner_id']: row['recent_dps'] for row in operators}
            lost_coverage = not terminal and (
                previous['team_recent_dps'] is not None and team_recent is None
                or any(row['recent_dps'] is not None and current_rates.get(row['owner_id']) is None
                       for row in previous['operators']))
            if self.analysis_interrupted or lost_coverage:
                segment += 1
        return dict(battle_id=battle_id, game_time=game_time, segment=segment,
                    team_recent_dps=team_recent, operators=operators)

    def status(self):
        if self.scanning:
            return 'locating', '正在定位 · 保留最后有效记录'
        if self.error:
            return 'disconnected', '连接断开 / 读取中断 · 显示最后有效记录'
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
        if self.event_status in ('gap', 'failed', 'unavailable'):
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
        if config.boss_scope:
            reason = statistics['boss_damage_reason'] or reason
            complete = complete and statistics['boss_damage_available']
            note = ''
        rows = rows_from_statistics(statistics)
        view = asdict(render_panel(config, rows,
                                   team_complete=complete, team_reason=reason,
                                   team_note=note, event=event, number_units=number_units))
        if config.boss_scope:
            view['boss_damage_available'] = statistics['boss_damage_available']
            view['boss_damage_reason'] = statistics['boss_damage_reason']
        view['empty_text'] = empty_text(config, source, complete, view)
        view['rates'] = detail_rates(
            rows, event, effective=config.effective_only and config.category is Category.DAMAGE,
            number_units=number_units)
        for entry in view['entries']:
            entry['bar_color'] = {'damage': '#F2AC7A', 'healing': '#73B78A', 'shield': '#9ABFCE'}[config.category.value]
        view['source'] = source
        return view

    def snapshot(self):
        sample = self.last or {}
        stats = sample.get('statistics') or {}
        status, label = self.status()
        history = []
        for record in self.records.values():
            item = record_display(record)
            names = ' '.join(row.get('name', '') for row in record.statistics['rows'].values())
            # Readable labels and the native id both match, so old records stay searchable.
            item['search'] = ' '.join((*item['labels'], item['date'], names, item['stage_raw'] or '')).casefold()
            history.append(item)
        selected = None
        if self.selected:
            selected_stats = record_statistics(self.selected)
            selected = dict(
                record_display(self.selected),
                view=self.view(self.history_config, selected_stats, None,
                               EventAvailability(status=self.selected.event_status,
                                                 error=self.selected.event_error, live=False)),
                record=self.selected.to_dict())
        live_stage = stage_names.resolve((sample.get('battle_metadata') or {}).get('stage_id'))
        has_live_stage = bool((sample.get('battle_metadata') or {}).get('stage_id'))
        live_event = EventAvailability(status=sample.get('event_status', self.event_status),
                                       error=sample.get('event_error', self.event_error), live=True)
        event_status, event_error = self.event_status, self.event_error
        if event_status is None or self.observer_status in ('disabled', 'failed', 'incompatible', 'stopped'):
            event_status, event_error = self.observer_status, self.observer_error
        return dict(protocol=PROTOCOL_VERSION, layout=self.layout.to_dict(),
                    connection_config=self.connection.to_dict(), warnings=self.warnings,
                    totals=team_totals(stats),
                    status=status, status_text=label, error=self.error,
                    battle_waiting=self.battle_waiting,
                    connection_text=self.connection_text, diagnostic=self.diagnostic,
                    observer_status=self.observer_status, event_status=self.event_status,
                    event_text=event_label(event_status, event_error),
                    battle_id=sample.get('battle_id', sample.get('session')),
                    game_time=stats.get('game_time', sample.get('game_time')),
                    stage=live_stage.label if has_live_stage else None,
                    stage_code=live_stage.code or None, stage_name=live_stage.name or None,
                    stage_raw=live_stage.raw, stage_detail=live_stage.detail,
                    stage_known=live_stage.known,
                    panels=[self.view(p, stats, None, live_event) for p in self.layout.panels],
                    live_analysis=self.live_analysis,
                    boss_live_analysis=self.boss_live_analysis,
                    boss_effective_live_analysis=self.boss_effective_live_analysis,
                    enemy=sample.get('enemy') or dict(status='starting', error=None,
                        ready=False, rows=[], totals={}, series=[]),
                    history=history, history_selected=selected, history_notice=self.history_notice)

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
            self.persist(replace(self.layout, gui=GuiGeometry.from_dict(cmd['gui']), huds=huds))
        elif op == 'history_refresh':
            self.refresh_history()
        elif op == 'history_select':
            self.selected = self.records[cmd['battle_id']]
        elif op == 'history_config':
            self.history_config = PanelConfig.from_dict(dict(cmd['config'], panel_id='history'))
        elif op == 'export':
            record = BattleRecord.from_dict(cmd['record'])
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
            if not config.auto_detect and not config.serial:
                raise ValueError('自动检测关闭时必须填写实例地址')
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
        else:
            raise ValueError('未知命令：' + str(op))
        return None


class CollectionWorker:
    """Owns the sole CollectionSession, never references WPF or pipe objects."""
    def __init__(self, state, *, endpoint=DEFAULT_FRIDA_REMOTE):
        self.history, self.endpoint = state.history, endpoint
        self.requests, self.events = queue.Queue(), queue.Queue()
        self.effective = (state.connection, state.layout.event_observation)
        self.stop = threading.Event()
        self.session = None
        self.cleanup_error = ''
        self.retired_errors = []
        self.last_connect_attempt = 0.0
        self.last_endpoint_check = 0.0
        self.thread = threading.Thread(target=self.run, name='ArkDPS collection')

    def request(self, config, enabled):
        self.effective = (config, enabled)
        self.requests.put(self.effective)

    def release(self):
        if self.session is None:
            return True
        try:
            self.session.close()
        except Exception as exc:
            self.cleanup_error = f'旧采集会话清理失败（{exc}）。请解决问题后重连观察者。'
            self.events.put(('error', self.cleanup_error))
            return False
        if self.session.cleanup_errors:
            error = '已释放旧会话，保留历史清理异常：' + str(self.session.cleanup_errors)
            self.retired_errors.append(error)
            self.events.put(('warning', error))
        self.session = None
        self.cleanup_error = ''
        return True

    def run(self):
        try:
            runtime = BattleRuntime()
            capture_dir = ROOT / 'runtime' / 'captures'
            capture_dir.mkdir(parents=True, exist_ok=True)
            path = capture_dir / (
                f'wpf-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}-{uuid4().hex}.jsonl')
            with path.open('x', encoding='utf-8') as stream:
                from capture_cli import json_value
                def audit(kind, data):
                    stream.write(json.dumps(dict(kind=kind, data=data), ensure_ascii=False,
                                            default=json_value) + '\n')
                    stream.flush()
                try:
                    usable = False
                    current_config, current_enabled, current_target = None, False, None
                    last_battle_waiting = None
                    last_observer_state = None
                    endpoint_bound = False
                    retries = 0
                    last_retry_audit = float('-inf')
                    while not self.stop.is_set():
                        try:
                            config, enabled = self.requests.get_nowait()
                        except queue.Empty:
                            config = None
                        if config is not None:
                            current_config, current_enabled = config, enabled
                            usable = False
                            last_observer_state = None
                            endpoint_bound = False
                            retries = 0
                            last_retry_audit = float('-inf')
                            self.last_connect_attempt = time.monotonic()
                            self.events.put(('scanning', True))
                            if self.release():
                                try:
                                    target = resolve_configured(config)
                                    current_target = target
                                    self.events.put(('diagnostic', target.diagnostics.as_dict()))
                                    if enabled:
                                        ok, detail = ensure_frida_endpoint(target.adb_path, target.serial,
                                                                           remote=self.endpoint)
                                        endpoint_bound = ok
                                        self.last_endpoint_check = time.monotonic()
                                        audit('frida_endpoint', {'ok': ok, 'detail': detail})
                                        if not ok:
                                            self.events.put(('warning', '事件端点：' + detail))
                                    self.session = CollectionSession(target.adb_path, target.serial,
                                        frida_remote=self.endpoint if enabled else None,
                                        history=self.history, audit=audit,
                                        on_history=lambda: self.events.put(('history', None)),
                                        log=lambda x: print(x, file=sys.stderr, flush=True))
                                    self.session.enable_lifecycle(runtime)
                                    self.session.connect()
                                    diag = self.session.collector.diagnostics
                                    self.events.put(('diagnostic', diag.as_dict()))
                                    self.events.put(('connection', f'已连接 {diag.selected_serial} · 游戏 {diag.version_actual}'))
                                    usable = True
                                except Exception as exc:
                                    diag = getattr(exc, 'diagnostics', None)
                                    if diag:
                                        self.events.put(('diagnostic', diag.as_dict()))
                                    self.events.put(('error', str(exc)))
                                    self.release()
                            self.events.put(('scanning', False))
                        if usable and self.session is not None:
                            # Attachment can be confirmed in the lobby, before any battle sample.
                            observer_state = ('disabled', None)
                            if current_enabled:
                                observer = self.session.observer
                                observer_state = ((observer.status, observer.error) if observer is not None
                                                  else ('failed', self.session.event_error))
                            if observer_state != last_observer_state:
                                last_observer_state = observer_state
                                self.events.put(('observer', observer_state))
                            runtime = self.session.runtime
                            started = time.monotonic()
                            try:
                                sample = self.session.read()
                                self.events.put(('lifecycle', runtime.sessions.state))
                                if sample:
                                    self.events.put(('sample', sample))
                            except CaptureRetry as exc:
                                lifecycle = runtime.sessions.state
                                self.events.put(('lifecycle', lifecycle))
                                if lifecycle == 'playing' or isinstance(exc, MappingRetry):
                                    retries += 1
                                    now = time.monotonic()
                                    if now - last_retry_audit >= 5:
                                        reason = str(exc).partition('\n')[0].strip()
                                        if reason.startswith('observer ') and ' request failed:' in reason:
                                            reason = reason.partition(':')[0]
                                        audit('capture_retry', {
                                            'reason': reason[:200], 'count': retries,
                                            'duration_ms': round((now - started) * 1000, 1),
                                            'monotonic': now})
                                        retries = 0
                                        last_retry_audit = now
                            except BattleChanged as exc:
                                self.events.put(('error', str(exc)))
                                usable = False
                                self.last_connect_attempt = time.monotonic()
                                self.request(current_config, current_enabled)
                            except Exception as exc:
                                audit('capture_error', {'error_type': type(exc).__name__,
                                      'reason': str(exc).partition('\n')[0][:200],
                                      'monotonic': time.monotonic()})
                                self.events.put(('error', str(exc)))
                                usable = False
                            if usable:
                                battle_waiting = runtime.feed.battle_waiting
                                if battle_waiting is not None and battle_waiting != last_battle_waiting:
                                    last_battle_waiting = battle_waiting
                                    self.events.put(('battle_waiting', battle_waiting))
                        if (usable and current_enabled and current_target is not None
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
                        if (not usable and current_config is not None
                                and time.monotonic() - self.last_connect_attempt >= 5):
                            self.last_connect_attempt = time.monotonic()
                            self.request(current_config, current_enabled)
                        self.stop.wait(.2)
                finally:
                    # Session shutdown writes final audit records; keep its sink open.
                    # A transient cleanup failure gets one retry before the sink closes.
                    if not self.release():
                        self.release()
        except Exception as exc:
            self.events.put(('error', str(exc)))
            traceback.print_exc(file=sys.stderr)

    def close(self):
        self.stop.set()
        self.thread.join()  # Never orphan device cleanup on UI exit / pipe EOF.
        errors = self.retired_errors + ([self.cleanup_error] if self.cleanup_error else [])
        if errors:
            raise RuntimeError('；'.join(errors))


def serve(input_stream, output, state, worker=None):
    """Versioned UTF-8 JSON Lines: hello, snapshot/state/error, quit/bye."""
    handshake = False
    trace = os.environ.get('ARKDPS_BRIDGE_TRACE') == '1'
    last_reply: tuple[int, str, str, int] | None = None
    while True:
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
            if log_op != 'snapshot' and not trace:
                diagnostic('reply_ready')
            try:
                output.write(payload + '\n')
                advance('flush')
                output.flush()
            except OSError as exc:
                diagnostic('failed', error=type(exc).__name__)
                return False
            advance('complete')
            if log_op != 'snapshot' or trace or time.perf_counter() - started >= BRIDGE_SLOW_PHASE_SECONDS:
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
            if log_op != 'snapshot' or trace:
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
                while True:
                    try:
                        state.accept(*worker.events.get_nowait())
                    except queue.Empty:
                        break
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
                if worker:
                    worker.close()
                result, kind, quit_requested = None, 'bye', True
            elif op == 'detect':
                result = detect_instances(cmd.get('adb') or None).diagnostics().as_dict()
                kind = 'state'
            elif op == 'rescan':
                if worker:
                    worker.request(*worker.effective)
                result, kind = None, 'state'
            else:
                result = state.command(cmd)
                if op == 'connection_apply' and worker:
                    worker.request(state.connection, state.layout.event_observation)
                kind = 'snapshot' if op == 'snapshot' else 'state'
            advance('snapshot')
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--adb')
    parser.add_argument('--serial')
    parser.add_argument('--no-events', action='store_true')
    parser.add_argument('--frida-remote')
    args = parser.parse_args()
    if args.offline and args.state_dir is None:
        parser.error('--offline requires an isolated --state-dir')
    if args.fixture and not args.offline:
        parser.error('--fixture requires --offline')
    instance = None
    if not args.offline:
        instance = socket.socket()
        try:
            instance.bind(('127.0.0.1', 27380))
            instance.listen(1)
        except OSError:
            raise SystemExit('ArkDPS 采集已在运行；请先退出原窗口。')
    output = sys.stdout
    sys.stdout = sys.stderr  # stdout is exclusively the protocol stream
    state = DesktopState(args.state_dir)
    if args.fixture:
        state.accept('sample', json.loads(args.fixture.read_text('utf-8')))
    worker = None if args.offline else CollectionWorker(state, endpoint=args.frida_remote or DEFAULT_FRIDA_REMOTE)
    if worker:
        worker.request(replace(state.connection, adb_path=args.adb or state.connection.adb_path,
                               serial=args.serial or state.connection.serial,
                               auto_detect=False if args.serial else state.connection.auto_detect),
                       False if args.no_events else (True if args.frida_remote is not None else state.layout.event_observation))
        worker.thread.start()
    try:
        serve(sys.stdin, output, state, worker)
    finally:
        if worker:
            worker.close()
        if instance:
            instance.close()


if __name__ == '__main__':
    main()
