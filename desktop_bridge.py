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
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import traceback
from uuid import uuid4

from battle_history import BattleHistory, BattleRecord
from battle_lifecycle import BattleRuntime
from battle_share import json_bytes, record_complete, record_labels, write_export
from collection_session import CollectionSession, DEFAULT_FRIDA_REMOTE
from collector import BattleChanged, ROOT
from connection import (ConnectionConfig, ConnectionConfigStore, detect_instances,
                        resolve_configured)
from event_capture import CaptureRetry
from layout_store import LayoutStore, GuiGeometry, HudPlacement
from metrics import observation_age
from panel_model import PanelConfig, render_panel, rows_from_statistics

PROTOCOL_VERSION = 1
MAX_MESSAGE = 32 * 1024 * 1024


def event_label(status, error=None):
    labels = {'recording': '事件观测中 · 护盾仅含观测区间',
              'ready': '事件已就绪 · 等待同帧基线', 'attaching': '正在挂载事件观测',
              'gap': '事件中断 · 伤害/治疗快照继续',
              'failed': '事件不可用 · 使用伤害/治疗快照',
              'unavailable': '事件关闭或不可用 · 使用伤害/治疗快照',
              'finished': '事件观测结束 · 保留已覆盖区间'}
    return labels.get(status, '等待事件能力诊断') + (f'（{error}）' if error else '')


def empty_text(config, source, complete):
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
        self.warnings = [x for x in (loaded.warning, config.warning) if x]
        self.history = BattleHistory(directory / 'history')
        self.records = {}
        self.selected = None
        self.history_config = PanelConfig(panel_id='history')
        self.last = None
        self.last_frame = None
        self.last_progress = 0.
        self.error = ''
        self.connection_text = '等待定位本局'
        self.diagnostic = None
        self.event_status = self.event_error = self.lifecycle = None
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
            self.error = ''
            frame = (value.get('session'), value.get('frame'))
            if frame != self.last_frame:
                self.last_progress = time.monotonic()
                self.last_frame = frame
            self.last = value
            self.event_status, self.event_error = value.get('event_status'), value.get('event_error')
        elif kind == 'error':
            self.error = str(value)
        elif kind == 'warning':
            if value not in self.warnings:
                self.warnings.append(value)
        elif kind == 'connection':
            self.connection_text = value
        elif kind == 'diagnostic':
            self.diagnostic = value
        elif kind == 'event':
            self.event_status, self.event_error = value
        elif kind == 'lifecycle':
            self.lifecycle = value
        elif kind == 'history':
            # A record was committed by the collection owner: re-read the list
            # now instead of polling the directory on every snapshot.
            self.refresh_history()
        elif kind == 'scanning':
            self.scanning = value
            if value:
                self.error = ''

    def status(self):
        if self.scanning:
            return 'locating', '正在定位 · 保留最后有效记录'
        if self.error:
            return 'disconnected', '连接断开 / 读取中断 · 显示最后有效记录'
        if not self.last:
            return 'waiting', '等待战斗数据'
        if self.lifecycle in ('ended', 'finalizing', 'frozen'):
            return self.lifecycle, {'ended': '战斗结束', 'finalizing': '等待结算确认', 'frozen': '战局冻结'}[self.lifecycle]
        if observation_age(self.last) > 2:
            return 'stale', '数据已过期 · 请重新定位'
        if time.monotonic() - self.last_progress > .8:
            return 'paused', '暂停 / 等待战斗推进'
        if self.event_status in ('gap', 'failed', 'unavailable'):
            return 'degraded', '正在统计 · 事件降级'
        return 'live', '正在统计'

    @staticmethod
    def view(config, statistics, complete):
        source = statistics.get('rows') or {}
        view = asdict(render_panel(config, rows_from_statistics(statistics), team_complete=complete))
        view['empty_text'] = empty_text(config, source, complete)
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
            labels = record_labels(record)
            names = ' '.join(row.get('name', '') for row in record.statistics['rows'].values())
            history.append(dict(battle_id=record.battle_id, labels=labels,
                                search=(' '.join(labels) + ' ' + names).casefold()))
        selected = None
        if self.selected:
            selected = dict(battle_id=self.selected.battle_id, labels=record_labels(self.selected),
                            view=self.view(self.history_config, self.selected.statistics,
                                           record_complete(self.selected)), record=self.selected.to_dict())
        return dict(protocol=PROTOCOL_VERSION, layout=self.layout.to_dict(),
                    connection_config=self.connection.to_dict(), warnings=self.warnings,
                    status=status, status_text=label, error=self.error,
                    connection_text=self.connection_text, diagnostic=self.diagnostic,
                    event_text=event_label(self.event_status, self.event_error),
                    battle_id=sample.get('battle_id', sample.get('session')),
                    game_time=stats.get('game_time', sample.get('game_time')),
                    stage=(sample.get('battle_metadata') or {}).get('stage_id'),
                    panels=[self.view(p, stats, sample.get('statistics_complete', False)) for p in self.layout.panels],
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
            self.persist(replace(self.layout, panels=panels,
                                 huds=tuple(h for h in self.layout.huds if h.panel_id != cmd['panel_id'])))
        elif op == 'theme':
            self.persist(replace(self.layout, theme=cmd['theme']))
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
            self.cleanup_error = '旧采集会话清理失败；修复后重新定位：' + str(exc)
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
                    current_config, current_enabled = None, False
                    while not self.stop.is_set():
                        try:
                            config, enabled = self.requests.get_nowait()
                        except queue.Empty:
                            config = None
                        if config is not None:
                            current_config, current_enabled = config, enabled
                            usable = False
                            self.events.put(('scanning', True))
                            if self.release():
                                try:
                                    target = resolve_configured(config)
                                    self.events.put(('diagnostic', target.diagnostics.as_dict()))
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
                            try:
                                sample = self.session.read()
                                runtime = self.session.runtime
                                self.events.put(('lifecycle', runtime.sessions.state))
                                if sample:
                                    self.events.put(('sample', sample))
                            except CaptureRetry:
                                self.events.put(('lifecycle', self.session.runtime.sessions.state))
                            except BattleChanged as exc:
                                self.events.put(('error', str(exc)))
                                self.request(current_config, current_enabled)
                            except Exception as exc:
                                self.events.put(('error', str(exc)))
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
    while True:
        line = input_stream.readline(MAX_MESSAGE + 1)
        if not line:
            return
        if len(line.encode('utf-8')) > MAX_MESSAGE:
            raise ValueError('worker 消息长度越界')
        cmd = {}
        quit_requested = False
        try:
            cmd = json.loads(line)
            if not isinstance(cmd, dict) or type(cmd.get('version')) is not int or cmd.get('version') != PROTOCOL_VERSION:
                raise ValueError('不支持的 worker 协议版本')
            if worker:
                while True:
                    try:
                        state.accept(*worker.events.get_nowait())
                    except queue.Empty:
                        break
            op = cmd['op']
            if op == 'hello':
                handshake = True
                from importlib.metadata import version, PackageNotFoundError
                try:
                    frida_version = version('frida')
                except PackageNotFoundError:
                    frida_version = None
                result = dict(transport='stdio-jsonl', worker='ArkDPS Python', version=PROTOCOL_VERSION,
                              python=sys.version.split()[0], executable=sys.executable, frida=frida_version)
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
            reply = dict(version=PROTOCOL_VERSION, id=cmd.get('id'), kind=kind, ok=True,
                         result=result, state=state.snapshot())
        except Exception as exc:
            reply = dict(version=PROTOCOL_VERSION, id=cmd.get('id') if isinstance(cmd, dict) else None,
                         kind='error', ok=False, error=str(exc))
        payload = json.dumps(reply, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > MAX_MESSAGE:
            raise ValueError('worker 响应长度越界')
        try:
            output.write(payload + chr(10))
            output.flush()
        except (BrokenPipeError, OSError):
            return
        if quit_requested:
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
