"""Independent one-second client Present measurements for the desktop worker."""

from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
from datetime import datetime, timezone
import ntpath
import os
import threading
import time
import uuid


_DXGI = uuid.UUID('ca11c036-0102-4a2d-a6ad-f03cfed5d3c9').bytes_le
_DXGI_PRESENT_START = 42
_DXGI_PRESENT_STOP = 43
_DXGI_PRESENT_TEST = 1
_DXGI_PRESENT_KEYWORD = 2
_TRACE_REALTIME = 0x100
_TRACE_SINGLE_BUFFER = 0x10000000  # EVENT_TRACE_NO_PER_PROCESSOR_BUFFERING
_TRACE_RAW_RECORDS = 0x10000000 | 0x1000 | _TRACE_REALTIME
_PID_FILTER = 0x80000004
_WNODE_TRACED_GUID = 0x00020000
_U16, _U32, _U64, _I64 = C.c_uint16, C.c_uint32, C.c_uint64, C.c_int64
_GUID = C.c_ubyte * 16
_CALLBACK = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)


class _Wnode(C.Structure):
    _fields_ = [('size', _U32), ('provider', _U32), ('context', _U64),
                ('timestamp', _I64), ('guid', _GUID), ('clock', _U32), ('flags', _U32)]


class _TraceProperties(C.Structure):
    _fields_ = [('wnode', _Wnode), ('buffer_size', _U32), ('min_buffers', _U32),
                ('max_buffers', _U32), ('max_file_size', _U32), ('mode', _U32),
                ('flush_seconds', _U32), ('enable_flags', _U32), ('age_limit', C.c_int32),
                ('buffer_count', _U32), ('free_buffers', _U32), ('events_lost', _U32),
                ('buffers_written', _U32), ('log_buffers_lost', _U32),
                ('realtime_buffers_lost', _U32), ('logger_thread', C.c_void_p),
                ('file_name_offset', _U32), ('logger_name_offset', _U32)]


class _Filter(C.Structure):
    _fields_ = [('ptr', _U64), ('size', _U32), ('type', _U32)]


class _EnableParameters(C.Structure):
    _fields_ = [('version', _U32), ('enable_property', _U32), ('control_flags', _U32),
                ('source_id', _GUID), ('filters', C.POINTER(_Filter)), ('filter_count', _U32)]


class _EventDescriptor(C.Structure):
    _fields_ = [('id', _U16), ('version', C.c_ubyte), ('channel', C.c_ubyte),
                ('level', C.c_ubyte), ('opcode', C.c_ubyte), ('task', _U16), ('keyword', _U64)]


class _EventHeader(C.Structure):
    _fields_ = [('size', _U16), ('type', _U16), ('flags', _U16), ('property', _U16),
                ('tid', _U32), ('pid', _U32), ('timestamp', _I64), ('provider', _GUID),
                ('descriptor', _EventDescriptor), ('processor_time', _U64), ('activity', _GUID)]


class _EventRecord(C.Structure):
    _fields_ = [('header', _EventHeader), ('buffer_context', _U32), ('extended_count', _U16),
                ('data_size', _U16), ('extended_data', C.c_void_p), ('data', C.c_void_p),
                ('context', C.c_void_p)]


_EventCallback = _CALLBACK(None, C.POINTER(_EventRecord))


class _TraceLogfile(C.Structure):
    # Embedded EVENT_TRACE and TRACE_LOGFILE_HEADER are opaque here. Sizes and
    # alignment follow the Windows x64 SDK (88 and 280 bytes, 8-byte alignment).
    _fields_ = [('filename', C.c_wchar_p), ('logger_name', C.c_wchar_p), ('current_time', _I64),
                ('buffers_read', _U32), ('mode', _U32), ('current_event', _U64 * 11),
                ('logfile_header', _U64 * 35), ('buffer_callback', C.c_void_p),
                ('buffer_size', _U32), ('filled', _U32), ('events_lost', _U32),
                ('event_callback', _EventCallback), ('is_kernel', _U32), ('context', C.c_void_p)]


class _FpsError(Exception):
    def __init__(self, code, winerror=None):
        super().__init__(code)
        self.code = code
        self.winerror = winerror
        self.cleanup_trace = None


class _WindowsEtw:
    def __init__(self):
        if os.name != 'nt' or C.sizeof(C.c_void_p) != 8:
            raise _FpsError('dxgi_etw_unsupported')
        advapi = C.WinDLL('advapi32', use_last_error=True)
        kernel = C.WinDLL('kernel32', use_last_error=True)
        props = C.POINTER(_TraceProperties)
        ptr_handle = C.POINTER(_U64)
        for name, lib, result, args in (
            ('StartTraceW', advapi, _U32, [ptr_handle, C.c_wchar_p, props]),
            ('EnableTraceEx2', advapi, _U32, [_U64, C.POINTER(_GUID), _U32, C.c_ubyte,
                                           _U64, _U64, _U32, C.POINTER(_EnableParameters)]),
            ('ControlTraceW', advapi, _U32, [_U64, C.c_wchar_p, props, _U32]),
            ('OpenTraceW', advapi, _U64, [C.POINTER(_TraceLogfile)]),
            ('ProcessTrace', advapi, _U32, [ptr_handle, _U32, C.c_void_p, C.c_void_p]),
            ('CloseTrace', advapi, _U32, [_U64]),
            ('OpenProcess', kernel, C.c_void_p, [_U32, C.c_int, _U32]),
            ('GetProcessTimes', kernel, C.c_int, [C.c_void_p, *([C.POINTER(_U64)] * 4)]),
            ('WaitForSingleObject', kernel, _U32, [C.c_void_p, _U32]),
            ('CloseHandle', kernel, C.c_int, [C.c_void_p]),
            ('QueryPerformanceCounter', kernel, C.c_int, [C.POINTER(_I64)]),
            ('QueryPerformanceFrequency', kernel, C.c_int, [C.POINTER(_I64)]),
        ):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
            setattr(self, name, fn)

    @staticmethod
    def check(status, code):
        if status:
            raise _FpsError(code, status)

    def qpc(self, *, frequency=False):
        value = _I64()
        query = self.QueryPerformanceFrequency if frequency else self.QueryPerformanceCounter
        if not query(C.byref(value)):
            raise _FpsError('dxgi_etw_clock', C.get_last_error())
        return value.value

    def open_instance(self, pid, birth):
        # SYNCHRONIZE plus PROCESS_QUERY_LIMITED_INFORMATION grants no memory access.
        handle = self.OpenProcess(0x101000, 0, pid)
        if not handle:
            raise _FpsError('game_process_unavailable', C.get_last_error())
        try:
            self.verify_instance(handle, birth)
        except _FpsError:
            self.CloseHandle(handle)
            raise
        return handle

    def verify_instance(self, handle, birth):
        wait = self.WaitForSingleObject(handle, 0)
        if wait == 0:
            raise _FpsError('game_process_exited')
        if wait != 258:
            raise _FpsError('game_process_unavailable', C.get_last_error())
        created, exited, kernel, user = (_U64() for _ in range(4))
        if not self.GetProcessTimes(handle, C.byref(created), C.byref(exited),
                                    C.byref(kernel), C.byref(user)):
            raise _FpsError('game_process_unavailable', C.get_last_error())
        if created.value != birth:
            raise _FpsError('game_process_changed')


class _PresentWindows:
    """Pair same-thread native Present calls and count successful stops by QPC second."""

    def __init__(self, origin, frequency):
        self.origin = origin
        self.frequency = frequency
        self.lock = threading.Lock()
        self.next_window = 0
        self.pending = {}
        self.counts = {}
        self.primary = None
        self.candidate = None
        self.candidate_window = None
        self.invalid = None
        self.drop_through = -1

    def start(self, tid, ticks, chain, flags):
        with self.lock:
            if tid in self.pending or len(self.pending) >= 256:
                self.pending.clear()
                self.invalid = ('dxgi_etw_present_pair', (ticks - self.origin) // self.frequency)
            self.pending[tid] = ticks, chain, flags

    def malformed(self, ticks):
        with self.lock:
            self.invalid = ('dxgi_etw_present_format', (ticks - self.origin) // self.frequency)

    def stop(self, tid, ticks, result):
        with self.lock:
            start = self.pending.pop(tid, None)
            window = (ticks - self.origin) // self.frequency
            if result != 0 or window < 0 or window <= self.drop_through:
                return
            if start is None:
                if window > 0 and self.invalid is None:
                    self.invalid = ('dxgi_etw_present_pair', window)
                return
            started, chain, flags = start
            if not chain or flags & _DXGI_PRESENT_TEST or ticks < started:
                return
            if window < self.next_window:
                if self.invalid is None or window < self.invalid[1]:
                    self.invalid = ('dxgi_etw_late_delivery', window)
                return
            if window not in self.counts and len(self.counts) >= 8:
                oldest = min(self.counts)
                if window < oldest:
                    return
                del self.counts[oldest]
            counts = self.counts.setdefault(window, {})
            counts[chain] = counts.get(chain, 0) + 1

    def collect(self, ticks, loss=False):
        # Real-time ETW flushes its buffers once per second. Group by QPC event
        # timestamps and leave 1.25 seconds for delivery before closing a bucket.
        elapsed = ticks - self.origin
        latest = (elapsed - self.frequency * 5 // 4) // self.frequency - 1
        with self.lock:
            if loss or self.invalid:
                code, affected = self.invalid or ('dxgi_etw_lost', None)
                self.drop_through = elapsed // self.frequency
                self.next_window = self.drop_through + 1
                self.counts.clear()
                self.pending.clear()
                self.candidate = self.candidate_window = None
                self.invalid = None
                return 'lost', None, affected, code
            if latest < self.next_window:
                return 'warming', None, None, None
            # On a long worker pause publish only the most recent completed
            # window instead of backfilling apparently live samples.
            for window in tuple(self.counts):
                if window < latest:
                    del self.counts[window]
            counts = self.counts.pop(latest, {})
            self.next_window = latest + 1
            best = max(counts, key=counts.get) if counts else None
            if best is not None and sum(value == counts[best] for value in counts.values()) > 1:
                best = None
            primary_count = counts.get(self.primary, 0)
            # A swap chain may change at a mode/size transition. Require the
            # same dominant candidate in two adjacent complete windows. In a
            # stall, a tiny secondary/overlay chain can't replace the primary.
            candidate = (best is not None and best != self.primary
                         and (self.primary is None or counts[best] >= max(5, primary_count * 3)))
            if candidate:
                if best == self.candidate and self.candidate_window == latest - 1:
                    self.primary = best
                    primary_count = counts[best]
                else:
                    self.candidate, self.candidate_window = best, latest
                    return 'warming', None, latest, None
            self.candidate = self.candidate_window = None
            if self.primary is None:
                return 'waiting', None, latest, 'awaiting_presents'
            return 'ok', primary_count, latest, None


def _consume_present(record, pid, windows):
    header = record.header
    if header.pid != pid:
        return
    descriptor = header.descriptor
    if descriptor.id not in (_DXGI_PRESENT_START, _DXGI_PRESENT_STOP):
        return
    if bytes(header.provider) != _DXGI:
        return
    if not record.data or descriptor.version != 0:
        windows.malformed(header.timestamp)
        return
    if descriptor.id == _DXGI_PRESENT_START:
        pointer = _U32 if header.flags & 0x20 else _U64
        offset = C.sizeof(pointer)
        if record.data_size >= offset + 4:
            # Event payloads need not be naturally aligned.
            payload = C.string_at(record.data, offset + 4)
            chain = int.from_bytes(payload[:offset], 'little')
            flags = int.from_bytes(payload[offset:], 'little')
            windows.start(header.tid, header.timestamp, chain, flags)
        else:
            windows.malformed(header.timestamp)
    elif record.data_size >= 4:
        result = int.from_bytes(C.string_at(record.data, 4), 'little')
        windows.stop(header.tid, header.timestamp, result)
    else:
        windows.malformed(header.timestamp)


class _DxgiTrace:
    """Controller and consumer of a unique, process-filtered real-time ETW session."""

    def __init__(self, target):
        self.api = _WindowsEtw()
        self.target = target
        self.process = self.api.open_instance(target.pid, target.birth)
        self.name = f'ArkDPS-GameFPS-{os.getpid()}-{uuid.uuid4().hex}'
        self.session = _U64()
        self.consumer = None
        self.consumer_closed = False
        self.thread = None
        self.process_result = None
        self.callback_failed = False
        self.loss = (0, 0)
        try:
            self._start()
        except BaseException:
            try:
                self.close()
            except _FpsError as cleanup:
                cleanup.cleanup_trace = self
                raise
            raise

    def _properties(self):
        name = C.create_unicode_buffer(self.name)
        # ControlTrace uses the storage to return session and log-file names.
        size = C.sizeof(_TraceProperties)
        storage = C.create_string_buffer(size + C.sizeof(name) + 2048)
        props = _TraceProperties.from_buffer(storage)
        props.wnode.size = C.sizeof(storage)
        props.logger_name_offset = size
        C.memmove(C.addressof(storage) + size, name, C.sizeof(name))
        return props

    def _start(self):
        api = self.api
        props = self._properties()
        props.wnode.clock = 1  # QPC, shared with the window boundaries below.
        props.wnode.flags = _WNODE_TRACED_GUID
        props.buffer_size, props.min_buffers, props.max_buffers = 64, 4, 32
        # One low-volume, PID-filtered buffer avoids real-time ETW delaying
        # sparse per-CPU buffers by over two seconds when the game thread moves.
        props.mode, props.flush_seconds = _TRACE_REALTIME | _TRACE_SINGLE_BUFFER, 1
        api.check(api.StartTraceW(C.byref(self.session), self.name, C.byref(props)),
                  'dxgi_etw_start')

        self.logfile = _TraceLogfile()
        self.logfile.logger_name = self.name
        self.logfile.mode = _TRACE_RAW_RECORDS
        self.logfile.event_callback = _EventCallback(self._event)
        consumer = api.OpenTraceW(C.byref(self.logfile))
        if consumer == (1 << 64) - 1:
            raise _FpsError('dxgi_etw_open', C.get_last_error())
        self.consumer = _U64(consumer)

        self.windows = _PresentWindows(api.qpc(), api.qpc(frequency=True))
        self.origin_utc = time.time()
        pids = (_U32 * 1)(self.target.pid)
        pid_filter = _Filter(C.addressof(pids), C.sizeof(pids), _PID_FILTER)
        params = _EnableParameters(version=2, filters=C.pointer(pid_filter), filter_count=1)
        provider = _GUID.from_buffer_copy(_DXGI)
        api.check(api.EnableTraceEx2(self.session, C.byref(provider), 1, 5, _DXGI_PRESENT_KEYWORD,
                                    0, 0, C.byref(params)), 'dxgi_etw_pid_filter')
        self.thread = threading.Thread(target=self._process, name='ArkDPS DXGI ETW', daemon=True)
        self.thread.start()

    def _event(self, pointer):
        try:
            _consume_present(pointer.contents, self.target.pid, self.windows)
        except Exception:
            self.callback_failed = True

    def _process(self):
        self.process_result = self.api.ProcessTrace(C.byref(self.consumer), 1, None, None)

    def poll(self):
        self.api.verify_instance(self.process, self.target.birth)
        if self.process_result is not None:
            raise _FpsError('dxgi_etw_stopped', self.process_result)
        if self.callback_failed:
            raise _FpsError('dxgi_etw_decode')
        props = self._properties()
        self.api.check(self.api.ControlTraceW(self.session, None, C.byref(props), 0), 'dxgi_etw_query')
        loss = (props.events_lost, props.realtime_buffers_lost)
        status, count, window, code = self.windows.collect(self.api.qpc(), loss != self.loss)
        self.loss = loss
        if window is None:
            return status, count, None, None, code
        return status, count, self.origin_utc + window, self.origin_utc + window + 1, code

    def close(self):
        if self.session.value:
            props = self._properties()
            result = self.api.ControlTraceW(self.session, None, C.byref(props), 1)
            if result not in (0, 4201):  # ERROR_WMI_INSTANCE_NOT_FOUND
                raise _FpsError('dxgi_etw_stop', result)
            self.session.value = 0
        if self.consumer is not None and not self.consumer_closed:
            result = self.api.CloseTrace(self.consumer)
            if result not in (0, 7007):  # ERROR_CTX_CLOSE_PENDING
                raise _FpsError('dxgi_etw_close', result)
            self.consumer_closed = True
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                raise _FpsError('dxgi_etw_join')
        if self.process is not None:
            self.api.CloseHandle(self.process)
            self.process = None


@dataclass(frozen=True)
class _Target:
    source: str | None = None
    pid: int | None = None
    birth: int | None = None
    adb: str | None = None
    serial: str | None = None
    reason: str | None = None


def _resolve_target(connection, diagnostic):
    connection = connection or {}
    diagnostic = diagnostic or {}
    platform = connection.get('platform', 'mumu')
    if platform == 'native_pc':
        pid = diagnostic.get('process_pid')
        birth = diagnostic.get('process_created_filetime')
        root = connection.get('native_game_root')
        detected = diagnostic.get('native_game_root')
        if (diagnostic.get('platform') != platform or diagnostic.get('status') != 'ready_unique'
                or not diagnostic.get('connection_ok')
                or type(pid) is not int or not 0 < pid < 2**32
                or type(birth) is not int or birth <= 0
                or (root and (not detected or ntpath.normcase(ntpath.abspath(root))
                              != ntpath.normcase(ntpath.abspath(detected))))):
            return _Target(source='dxgi_present', reason='awaiting_game_process')
        return _Target(source='dxgi_present', pid=pid, birth=birth)
    if platform == 'mumu':
        adb = diagnostic.get('adb_path')
        serial = diagnostic.get('selected_serial')
        if (diagnostic.get('platform', 'mumu') != platform or not diagnostic.get('connection_ok')
                or not adb or not serial or (connection.get('serial') and connection['serial'] != serial)):
            return _Target(source='surfaceflinger', reason='awaiting_android_device')
        return _Target(source='surfaceflinger', adb=adb, serial=serial)
    return _Target(reason='game_fps_unsupported')


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds')


def _sample(seq, target, status, *, fps=None, count=None, start=None, end=None, code=None,
            winerror=None, duration_ms=None, bucket_end_ns=None, guest_pid=None):
    if start is not None and end is not None:
        duration_ms = round((end - start) * 1000)
    bucket_start_ns = None
    if bucket_end_ns is not None and duration_ms is not None:
        bucket_start_ns = bucket_end_ns - duration_ms * 1_000_000
    return dict(sample_seq=seq, status=status, source=target.source, fps=fps, frame_count=count,
                observed_at_utc=_utc(time.time()),
                window_start_utc=None if start is None else _utc(start),
                window_end_utc=None if end is None else _utc(end),
                bucket_start_ns=bucket_start_ns, bucket_end_ns=bucket_end_ns, duration_ms=duration_ms,
                clock_source={'dxgi_present': 'windows_qpc', 'surfaceflinger': 'android_monotonic'}.get(target.source),
                error_code=code, winerror=winerror, pid=target.pid, process_created_filetime=target.birth,
                serial=target.serial, guest_pid=guest_pid)


def _error_sample(sequence, target, error):
    unavailable = error.code.startswith('game_process_') or error.code == 'dxgi_etw_unsupported'
    return _sample(sequence, target, 'unavailable' if unavailable else 'error',
                   code=error.code, winerror=error.winerror)


class GameFpsMonitor:
    """Owns the sample clock and collectors, independently of battle collection."""

    def __init__(self, *, emit=None, _trace_factory=_DxgiTrace, _android_factory=None):
        self._emit = emit
        self._trace_factory = _trace_factory
        self._android_factory = _android_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._target = _Target(reason='awaiting_connection')
        self._snapshot = _sample(0, self._target, 'waiting', code=self._target.reason)
        self._cleanup_trace = None
        self._thread = threading.Thread(target=self._run, name='ArkDPS client FPS', daemon=True)
        self._thread.start()

    def configure(self, connection, diagnostic):
        target = _resolve_target(connection, diagnostic)
        with self._lock:
            if target != self._target:
                self._target = target
                self._snapshot = _sample(self._snapshot['sample_seq'], target, 'waiting', code=target.reason)

    def snapshot(self):
        with self._lock:
            return self._snapshot.copy()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise _FpsError('game_fps_monitor_join')
        if self._cleanup_trace is not None:
            self._cleanup_trace.close()
            self._cleanup_trace = None

    def _new_android(self, target):
        factory = self._android_factory
        if factory is None:
            from game_fps_android import AndroidGameFps
            factory = AndroidGameFps
        return factory(target.adb, target.serial)

    def _run(self):
        selected = None
        trace = android = None
        trace_valid = False
        sequence = 0
        next_poll = time.monotonic() + 1.0
        retry_at = 0.0
        prior_failure = None
        try:
            while not self._stop.wait(max(0.0, next_poll - time.monotonic())):
                with self._lock:
                    target = self._target
                sequence += 1
                try:
                    if target != selected:
                        trace_valid = False
                        android = None
                        retry_at = 0.0
                        prior_failure = None
                        selected = target
                    if trace is not None and not trace_valid:
                        trace.close()
                        trace = None
                    if target.reason:
                        status = 'waiting' if target.source else 'unavailable'
                        sample = _sample(sequence, target, status, code=target.reason)
                    elif target.pid is not None and trace is None and time.monotonic() < retry_at:
                        sample = _error_sample(sequence, target, prior_failure)
                    elif target.pid is not None:
                        if trace is None:
                            trace = self._trace_factory(target)
                            trace_valid = True
                        status, count, start, end, code = trace.poll()
                        sample = _sample(sequence, target, status, fps=count, count=count,
                                         start=start, end=end, code=code)
                    else:
                        if android is None:
                            android = self._new_android(target)
                        report = android.poll()
                        status = 'ok' if report.fps is not None else 'waiting'
                        duration = None if report.window_seconds is None else round(report.window_seconds * 1000)
                        sample = _sample(sequence, target, status, fps=report.fps, count=report.frames,
                                         duration_ms=duration, bucket_end_ns=report.bucket_end_ns,
                                         guest_pid=getattr(report, 'pid', None),
                                         code=None if status == 'ok' else report.reason)
                except _FpsError as exc:
                    retry_failure = trace is None or trace_valid
                    trace_valid = False
                    if exc.cleanup_trace is not None:
                        trace = exc.cleanup_trace
                    if retry_failure:
                        retry_at = time.monotonic() + 5.0
                        prior_failure = exc
                    sample = _error_sample(sequence, target, exc)
                except Exception:
                    trace_valid = False
                    sample = _sample(sequence, target, 'error', code='game_fps_collector_error')
                with self._lock:
                    current = target == self._target
                    if current:
                        self._snapshot = sample
                if current and self._emit is not None:
                    try:
                        self._emit(sample.copy())
                    except Exception:
                        pass  # Logging can't stop the FPS collector.
                next_poll += 1.0
                now = time.monotonic()
                if next_poll < now:
                    next_poll = now + 1.0
        finally:
            if trace is not None:
                try:
                    trace.close()
                except _FpsError:
                    self._cleanup_trace = trace
