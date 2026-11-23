"""Bound the desktop capture journal and encode cumulative enemy state as deltas."""

from datetime import datetime, timezone
from enum import Enum
import json
import re
import stat
import time
from uuid import uuid4

from log_context import LogContext


ENEMY_STATE_DELTA = 'desktop_enemy_state_delta_v1'
ENEMY_STATE_RECORDS = frozenset(('published', 'lifecycle_summary', 'coverage_closed'))
CAPTURE_SEGMENT_BYTES = 64 * 1024 ** 2
CAPTURE_TOTAL_BYTES = 512 * 1024 ** 2
CAPTURE_NAME = re.compile(
    r'(?P<run>wpf-\d{8}-\d{6}-\d{6}-[0-9a-f]{32})(?:-part-(?P<part>\d{6,}))?\.jsonl',
    re.ASCII)

KEY_EVENTS = {
    'worker_started': ('platform', 'event_observation'),
    'worker_stopped': ('pending_history_count', 'cleanup_ok'),
    'connection_attempt': ('revision', 'platform', 'event_observation'),
    'connection_failed': ('revision', 'platform', 'code', 'error_type', 'win32_error'),
    'connected': ('pid',),
    'environment': None,  # LogContext owns and allowlists these fields.
    'native_pc_bootstrap': ('status', 'code', 'ready_generation'),
    'frida_endpoint': ('ok',),
    'frida_keepalive': ('ok',),
    'observer_waiting_game_init': ('pid',),
    'observer_game_init_ready': ('pid', 'source'),
    'observer_attach_blocked': ('code', 'pid'),
    'observer_ready': (),
    'observer_error': ('error_type', 'code'),
    'observer_cleanup_error': ('error_type', 'code'),
    'observer_retry': ('generation', 'previous_status', 'status'),
    'lifecycle_state': ('previous', 'state'),
    'event_gap': ('error_type', 'code'),
    'capture_retry': ('error_type', 'code'),
    'capture_error': ('error_type', 'code'),
    'history_error': ('code',),
    'history_saved': ('written',),
    'stopped': (),
}
REPEATED_KEY_EVENTS = frozenset(('connection_failed', 'frida_keepalive', 'native_pc_bootstrap',
                                'capture_retry', 'capture_error', 'history_error', 'observer_error'))


def _event_fields(data, fields):
    if not isinstance(data, dict):
        return {}
    result = {}
    for name in fields:
        if name not in data:
            continue
        value = data[name]
        if isinstance(value, Enum):
            value = value.value
        if value is None or type(value) in (bool, int) or (
                isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', value)):
            result[name] = value
    return result


class DesktopCaptureJournal:
    """Best-effort disk audit for the single live desktop worker."""

    def __init__(self, directory, *, encoder, warn, segment_bytes=CAPTURE_SEGMENT_BYTES,
                 total_bytes=CAPTURE_TOTAL_BYTES, header=None, debug_mode=False):
        self.directory, self.encoder, self.warn = directory, encoder, warn
        self.segment_bytes, self.total_bytes = segment_bytes, total_bytes
        self.run = f'wpf-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}-{uuid4().hex}'
        self.segment = 0
        self.stream = None
        self.size = self.available = 0
        self.retry_at = 0.
        self.header = header if header is not None else LogContext(directory.parent).header
        self.debug_enabled = debug_mode if callable(debug_mode) else lambda: debug_mode
        self._detailed = None
        self._last_key_events = {}
        self.writer = DesktopAuditWriter(self._emit)

    def __enter__(self):
        try:
            self._make_room(0)
        except OSError as exc:
            self._on_error(exc)
        return self

    def __exit__(self, *_exc):
        try:
            self._close_segment()
        except OSError as exc:
            self.warn(f'采集日志关闭失败：{exc}')

    def _close_segment(self):
        stream, self.stream = self.stream, None
        self.size = 0
        self.writer = DesktopAuditWriter(self._emit)
        if stream is not None:
            stream.close()

    def _make_room(self, required):
        self.directory.mkdir(parents=True, exist_ok=True)
        files = []
        for path in self.directory.iterdir():
            if not CAPTURE_NAME.fullmatch(path.name):
                continue
            try:
                metadata = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                files.append((metadata.st_mtime_ns, path, metadata.st_size))
        available = self.total_bytes - sum(size for _, _, size in files)
        for _, path, size in sorted(files, key=lambda item: (item[0], item[1].name)):
            if available >= required:
                break
            if self.stream is not None and str(path) == self.stream.name:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Windows can hold a log open while it is being inspected.
                continue
            available += size
        if available < required:
            raise OSError(f'采集日志目录无法腾出 {required} 字节（上限 {self.total_bytes} 字节）')
        self.available = available

    def _encode(self, record):
        return (json.dumps(record, ensure_ascii=False, default=self.encoder,
                           separators=(',', ':')) + '\n').encode('utf-8')

    def _emit(self, record):
        payload = self._encode(record)
        if self.stream is None:
            payload = self._encode(self.header('desktop_capture', debug_mode=self._detailed)) + payload
        if len(payload) > self.total_bytes:
            raise OSError('单条采集记录超出目录容量上限')
        if self.stream is None or self.available < len(payload):
            self._make_room(len(payload))
        if self.stream is None:
            self.segment += 1
            path = self.directory / f'{self.run}-part-{self.segment:06d}.jsonl'
            self.stream = path.open('xb', buffering=0)
        try:
            if self.stream.write(payload) != len(payload):
                raise OSError('采集日志写入不完整')
        except OSError:
            try:
                self.stream.truncate(self.size)
            except OSError:
                pass
            raise
        self.size += len(payload)
        self.available -= len(payload)

    def _on_error(self, exc):
        # Leave a missing segment number so the replayer can detect lost records.
        self.segment += 1
        self.retry_at = time.monotonic() + 10.
        try:
            self._close_segment()
        except OSError:
            pass
        self.warn(f'采集日志暂不可写，稍后重试：{exc}')

    def write(self, kind, data):
        if self.retry_at and time.monotonic() < self.retry_at:
            return
        try:
            detailed = bool(self.debug_enabled())
            if detailed != self._detailed:
                previous = self._detailed
                self._detailed = detailed
                if previous is not None:
                    # A resumed recording needs its own delta baseline.
                    self.writer = DesktopAuditWriter(self._emit)
                    self._last_key_events.clear()
                    self.writer.write('debug_mode', {'enabled': detailed,
                        'utc': datetime.now(timezone.utc).isoformat()})
            if not detailed:
                if kind not in KEY_EVENTS:
                    return
                if kind == 'frida_keepalive' and data.get('ok') and kind not in self._last_key_events:
                    return
                cleanup_ok = kind == 'stopped' and not bool(data.get('cleanup_errors'))
                fields = KEY_EVENTS[kind]
                data = dict(data) if fields is None else _event_fields(data, fields)
                if kind == 'stopped':
                    data['cleanup_ok'] = cleanup_ok
                if kind == 'connected':
                    self._last_key_events.pop('connection_failed', None)
                if kind == 'history_saved':
                    self._last_key_events.pop('history_error', None)
                if kind in REPEATED_KEY_EVENTS and self._last_key_events.get(kind) == data:
                    return
                if kind in REPEATED_KEY_EVENTS:
                    self._last_key_events[kind] = data.copy()
                data['utc'] = datetime.now(timezone.utc).isoformat()
            if self.size >= self.segment_bytes:
                self._close_segment()
            self.writer.write(kind, data)
        except (OSError, TypeError, ValueError) as exc:
            self._last_key_events.pop(kind, None)
            self._on_error(exc)
        else:
            self.retry_at = 0.


def _statistics(kind, data):
    return data if kind == 'coverage_closed' else data.get('statistics')


def _replace_events(kind, data, events):
    statistics = _statistics(kind, data)
    statistics = dict(statistics, enemy_state=dict(statistics['enemy_state'], events=events))
    return statistics if kind == 'coverage_closed' else dict(data, statistics=statistics)


class DesktopAuditWriter:
    def __init__(self, emit):
        self.emit = emit
        self.key = None
        self.length = 0
        self.first = self.last = None

    def write(self, kind, data):
        if kind not in ENEMY_STATE_RECORDS:
            self.emit(dict(kind=kind, data=data))
            return
        statistics = _statistics(kind, data)
        state = (statistics or {}).get('enemy_state')
        if state is None:
            self.emit(dict(kind=kind, data=data))
            return
        key = statistics['session'], state['start_frame']
        events = state['events']
        # Statistics appends validated events without revising the prefix.
        # A smaller or different prefix starts a new series.
        offset = self.length
        if (key != self.key or len(events) < offset
                or offset and (events[0] != self.first or events[offset - 1] != self.last)):
            offset = 0
        self.emit(dict(kind=ENEMY_STATE_DELTA, data=dict(
            kind=kind, offset=offset, value=_replace_events(kind, data, events[offset:]))))
        self.key, self.length = key, len(events)
        self.first, self.last = (events[0], events[-1]) if events else (None, None)


class DesktopAuditReader:
    def __init__(self):
        self.key = None
        self.events = []

    def restore(self, record):
        if record['kind'] != ENEMY_STATE_DELTA:
            return record
        try:
            kind, offset, value = (record['data'][field] for field in ('kind', 'offset', 'value'))
            if kind not in ENEMY_STATE_RECORDS or type(offset) is not int or offset < 0:
                raise ValueError('invalid enemy state delta')
            statistics = _statistics(kind, value)
            state = statistics['enemy_state']
            key = statistics['session'], state['start_frame']
            tail = state['events']
            if not isinstance(tail, list):
                raise ValueError('invalid enemy state delta events')
            if offset and (key != self.key or offset != len(self.events)):
                raise ValueError('missing enemy state delta prefix')
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError('invalid enemy state delta') from exc
        self.events = self.events + tail if offset else tail
        self.key = key
        return dict(kind=kind, data=_replace_events(kind, value, self.events))
