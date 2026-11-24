"""Bound the desktop capture journal and encode cumulative enemy state as deltas."""

from datetime import datetime, timezone
from enum import Enum
import json
import math
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

NATIVE_HOOK_STARTUP_EVENTS = frozenset((
    'native_prepare_started', 'native_prepare_failed', 'native_prepare_completed', 'native_prepare_closed'))
NATIVE_HOOK_STARTUP_OVERFLOW = 'native_prepare_trace_overflow'
NATIVE_HOOK_STARTUP_FIELDS = (
    'utc', 'event', 'provider', 'frida_required', 'status', 'code', 'pid', 'created_filetime',
    'game_client_version', 'source_build', 'ready_generation', 'process_age_seconds',
    'preparation_age_seconds', 'source_sha256', 'dll_sha256', 'error_type', 'cleanup_ok',
    'preparation_snapshot_phase', 'preparation', 'loader',
    'armed_filetime', 'watch_generation', 'material_ready')
_NATIVE_STATUSES = frozenset(('stopped', 'starting', 'waiting_for_process', 'loading', 'ready',
                             'process_failed', 'failed', 'stopping', 'cleanup_pending', 'closed'))
_NATIVE_CODES = frozenset((
    'native_permission_denied', 'native_unsupported_build', 'native_build_changed', 'native_material_not_ready', 'process_identity_invalid',
    'process_query_pending', 'process_changed', 'native_not_ready', 'cleanup_pending', 'restart_required',
    'ambiguous_process', 'preparation_expired', 'observer_cleanup_pending', 'startup_failed',
    'unsupported_environment', 'module_query_failed', 'loader_not_ready', 'existing_component',
    'loader_already_submitted', 'loader_failed', 'loader_preparation_timeout', 'loader_verification_timeout',
    'loader_verification_failed', 'loader_result_mismatch', 'loader_process_exited', 'watcher_start_failed'))
_NATIVE_ERROR_TYPES = frozenset(('Exception', 'RuntimeFailure', 'RuntimeError', 'OSError', 'ValueError',
    'TypeError', 'TimeoutError', 'InterruptedError', 'PermissionError', 'ProcessLookupError', 'FileNotFoundError',
    'KeyboardInterrupt', 'SystemExit', 'MemoryError'))
_LOADER_STAGES = frozenset(('initializing', 'initialized', 'preparing', 'allocating', 'writing_path',
                           'submitted', 'verifying', 'complete', 'closed'))
_LOADER_OPERATIONS = frozenset((
    'EnumProcessModulesEx', 'GetModuleFileNameExW', 'GetModuleInformation', 'GetModuleInformation(local)',
    'GetProcAddress/GetModuleHandleExW', 'GetModuleFileNameW', 'find_module', 'VirtualAllocEx',
    'WriteProcessMemory', 'CreateRemoteThread', 'WaitForSingleObject', 'GetExitCodeThread', 'VirtualFreeEx',
    'GetProcessTimes', 'GetExitCodeProcess', 'OpenProcess', 'IsWow64Process2', 'GetModuleHandleW',
    'GetProcAddress', 'GetModuleHandleExW', 'CloseHandle'))
_LOADER_CHECKS = frozenset(('existing_component', 'loader_export', 'loaded_component'))
_LOADER_TIMINGS = ('load_started', 'submitted', 'thread_completed', 'verification_started',
                   'verification_completed', 'closed')


def sanitize_native_hook_startup(record, *, event=None):
    """One deep allowlist for both native startup stderr and journal records.

    These facts explain a local startup attempt; they do not authenticate a
    source or prove event coverage. Never stringify unknown objects, exception
    messages, credentials, configuration, program text or full file paths.
    """
    if (type(record) is not dict or type(record.get('event')) is not str
            or record.get('event') not in NATIVE_HOOK_STARTUP_EVENTS | {NATIVE_HOOK_STARTUP_OVERFLOW}
            or event is not None and record.get('event') != event
            or type(record.get('provider')) is not str or record['provider'] != 'arkdps-native-hooks'):
        return None

    def integer(value, maximum=0xffffffffffffffff, minimum=0):
        return type(value) is int and minimum <= value <= maximum

    def seconds(value):
        try:
            return type(value) in (int, float) and math.isfinite(value) and value >= 0
        except (ValueError, TypeError, OverflowError):
            return False

    def member(choices):
        return lambda value: type(value) is str and value in choices

    def digest(value):
        return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None

    def build(value):
        return type(value) is str and re.fullmatch(r'win-x64:[0-9a-f]{64}', value) is not None

    def boolean(value):
        return type(value) is bool

    def utc(value):
        if (type(value) is not str or re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\+00:00|Z)', value) is None):
            return False
        try:
            return datetime.fromisoformat(value).utcoffset().total_seconds() == 0
        except (ValueError, AttributeError, OverflowError):
            return False

    if record['event'] == NATIVE_HOOK_STARTUP_OVERFLOW:
        if (not integer(record.get('dropped_records'), minimum=1)
                or not integer(record.get('total_dropped_records'), minimum=1)
                or record['total_dropped_records'] < record['dropped_records']
                or record.get('diagnostic_records_complete') is not False):
            return None
        result = {key: record[key] for key in ('event', 'provider', 'dropped_records',
                                               'total_dropped_records', 'diagnostic_records_complete')}
        if utc(record.get('utc')):
            result['utc'] = record['utc']
        return result

    def project(source, rules):
        if type(source) is not dict:
            return None
        return {name: source[name] for name, valid in rules.items() if name in source
                and (source[name] is None or valid(source[name]))}

    identity = dict(pid=lambda v: integer(v, 0xffffffff, 1),
                    created_filetime=lambda v: integer(v, minimum=1))
    top = dict(utc=utc, event=member(NATIVE_HOOK_STARTUP_EVENTS), provider=member(('arkdps-native-hooks',)),
        frida_required=lambda v: v is False, status=member(_NATIVE_STATUSES), code=member(_NATIVE_CODES),
        **identity, game_client_version=build, source_build=build, ready_generation=integer,
        armed_filetime=lambda v: integer(v, minimum=1), watch_generation=integer, material_ready=boolean,
        process_age_seconds=seconds, preparation_age_seconds=seconds, source_sha256=digest, dll_sha256=digest,
        error_type=member(_NATIVE_ERROR_TYPES), cleanup_ok=boolean,
        preparation_snapshot_phase=member(('before_prepare', 'after_prepare', 'last_safe_read')))
    result = project(record, top)
    if 'preparation' in record:
        rules = dict(prepared=boolean, claimed=boolean, closed=boolean, hook_disable_confirmed=boolean,
            process_instance=lambda v: type(v) in (list, tuple) and len(v) == 2
                and integer(v[0], 0xffffffff, 1) and integer(v[1], minimum=1),
            prepared_at=seconds, prepared_wall_time=seconds,
            expires_at=seconds, valid_seconds=seconds, source_build=build, source_sha256=digest, dll_sha256=digest)
        preparation = project(record['preparation'], rules)
        if preparation is not None:
            if 'process_instance' in preparation and preparation['process_instance'] is not None:
                preparation['process_instance'] = list(preparation['process_instance'])
            result['preparation'] = preparation
        elif record['preparation'] is None:
            result['preparation'] = None
    if 'loader' in record:
        rules = dict(**identity, stage=member(_LOADER_STAGES), operation=member(_LOADER_OPERATIONS),
            check=member(_LOADER_CHECKS), submitted=boolean, verification_complete=boolean,
            thread_exit_code=lambda v: integer(v, 0xffffffff), preparation_retries=lambda v: integer(v, 1000000),
            verification_retries=lambda v: integer(v, 1000000), handle_closed=boolean, verification_alive=boolean,
            process_exit_code=lambda v: integer(v, 0xffffffff), module_query_attempted=boolean,
            module_query_succeeded=boolean, module_found=boolean, module_base_low32=lambda v: integer(v, 0xffffffff),
            return_matches=boolean, path_utf16_units=lambda v: integer(v, 32768),
            path_utf16_bytes=lambda v: integer(v, 65536), path_buffer_bytes=lambda v: integer(v, 65538),
            loader_module_basename=lambda v: type(v) is str and v.casefold() in ('kernelbase.dll', 'kernel32.dll', 'ntdll.dll'),
            loader_export_rva=lambda v: integer(v, 0xffffffff))
        loader = project(record['loader'], rules)
        if loader is not None:
            raw = record['loader']
            if type(raw.get('last_error')) is dict:
                loader['last_error'] = project(raw['last_error'], dict(stage=member(_LOADER_STAGES),
                    operation=member(_LOADER_OPERATIONS), check=member(_LOADER_CHECKS),
                    win32_error=lambda v: integer(v, 0xffffffff)))
            elif raw.get('last_error', False) is None:
                loader['last_error'] = None
            if type(raw.get('timing_seconds')) is dict:
                loader['timing_seconds'] = project(raw['timing_seconds'], {key: seconds for key in _LOADER_TIMINGS})
            elif raw.get('timing_seconds', False) is None:
                loader['timing_seconds'] = None
            result['loader'] = loader
        elif record['loader'] is None:
            result['loader'] = None
    return result


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
KEY_EVENTS.update({event: NATIVE_HOOK_STARTUP_FIELDS for event in NATIVE_HOOK_STARTUP_EVENTS})
KEY_EVENTS[NATIVE_HOOK_STARTUP_OVERFLOW] = ('utc', 'event', 'provider', 'dropped_records',
                                           'total_dropped_records', 'diagnostic_records_complete')
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
            native_startup = kind in NATIVE_HOOK_STARTUP_EVENTS or kind == NATIVE_HOOK_STARTUP_OVERFLOW
            if native_startup:
                data = sanitize_native_hook_startup(data, event=kind)
                if data is None:
                    return
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
                data = ({name: data[name] for name in fields if name in data} if native_startup else
                        dict(data) if fields is None else _event_fields(data, fields))
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
                if not native_startup:
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
