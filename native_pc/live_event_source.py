"""Read completed frame fences from an explicitly selected cooperative feed."""

import json
import os
import stat
from pathlib import Path
from time import monotonic

from event_observer import ObserverStatus
from event_probe import NATIVE_PC_LIVE_CONTRACT, NativePcBinding, ObserverReadRetry
from native_pc.internal_event_source import InternalEventProbe, _finite_number, _reject_constant, _unique_fields


FEED_FORMAT = 'arkdps-pc-events'
FEED_VERSION = 1
_HEADER_FIELDS = {'format', 'version', 'producer', 'producer_version', 'process_instance', 'source_build'}
_UPDATE_FIELDS = {'mark', 'events', 'terminal_mark'}


class LiveNativeEventProbe:
    """Bind one append-only journal to one game process, build and battle.

    The producer must publish a frame fence only when all work for that frame
    is complete, and continue to publish unchanged fences while paused. A file
    reader can validate a fence against the native snapshot but cannot create it.
    """

    contract = NATIVE_PC_LIVE_CONTRACT
    transfers_event_records = True
    stale_timeout = 5.0
    max_line_bytes = 2 * 1024 * 1024
    max_file_bytes = 512 * 1024 * 1024
    max_poll_bytes = 4 * 1024 * 1024
    max_poll_updates = 32
    poll_seconds = .05

    def __init__(self, collector, feed_path, host_path):
        self.collector = collector
        self.feed_path = Path(feed_path).expanduser().resolve()
        self.host_path = Path(host_path)
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.cleanup_errors = {}
        self.probe_modes = {}
        self._binding = NativePcBinding(collector)
        self._stream = self._probe = self._header = self._file_identity = None
        self._size_seen = self._last_update = 0
        self._pending_update = self._generation = self._session = None
        self._first_unverified_frame = None
        self._has_mark = self._terminal = False

    def start(self):
        if self.status != ObserverStatus.STOPPED or self._header is not None:
            raise RuntimeError('cooperative PC event observer already opened a feed')
        self.status = ObserverStatus.ATTACHING
        try:
            selected = self.feed_path.stat()
            if not stat.S_ISREG(selected.st_mode):
                raise RuntimeError('cooperative PC event feed must be a regular file')
            self._file_identity = selected.st_dev, selected.st_ino
            self._stream = self.feed_path.open('rb')
            self._check_file()
            header = self._read_line()
            producer = header.get('producer') if header else None
            if (not header or set(header) != _HEADER_FIELDS or header['format'] != FEED_FORMAT
                    or type(header['version']) is not int or header['version'] != FEED_VERSION
                    or type(header['producer_version']) is not int or header['producer_version'] <= 0
                    or not isinstance(producer, str) or not 0 < len(producer) <= 80 or not producer.isprintable()):
                raise RuntimeError('missing or unsupported cooperative PC event feed header')
            self._header = header
            self._check_owner()
            self._probe = InternalEventProbe(self.host_path)
            self._probe.start()
            self._last_update = monotonic()
            self.probe_modes = {'probe_source': dict(provider='cooperative-pc-jsonl', protocol=FEED_VERSION,
                                                     producer=producer, source_version=header['producer_version'],
                                                     native_host=self._probe.probe_modes.get('probe_source', {}))}
            self.status = ObserverStatus.READY
        except BaseException as exc:
            self._fail(exc)
            raise

    def _check_owner(self):
        process = self.collector.process
        instance = self._header['process_instance']
        if (not isinstance(instance, list) or len(instance) != 2
                or any(type(part) is not int or part <= 0 for part in instance)
                or process is None or tuple(instance) != process.process_instance
                or instance[0] != process.pid or not process.process_alive()):
            raise RuntimeError('cooperative PC event feed belongs to a different or exited process')
        build = self._header['source_build']
        if not isinstance(build, str) or not build or build != self.collector.diagnostics.version_actual:
            raise RuntimeError('cooperative PC event feed belongs to a different or unverified build')

    def _check_file(self):
        selected = self.feed_path.stat()
        held = os.fstat(self._stream.fileno())
        if (self._file_identity != (selected.st_dev, selected.st_ino)
                or self._file_identity != (held.st_dev, held.st_ino)
                or not stat.S_ISREG(held.st_mode) or held.st_size < self._size_seen
                or held.st_size < self._stream.tell()):
            raise RuntimeError('cooperative PC event feed was replaced or rewound')
        if max(selected.st_size, held.st_size) > self.max_file_bytes:
            raise RuntimeError('cooperative PC event feed exceeds the file limit')
        self._size_seen = held.st_size
        return held.st_size

    def _read_line(self):
        offset = self._stream.tell()
        line = self._stream.readline(self.max_line_bytes + 2)
        if not line:
            return None
        complete = line.endswith(b'\n')
        if len(line) - int(complete) > self.max_line_bytes:
            raise RuntimeError('cooperative PC event feed record exceeds the line limit')
        if not complete:
            self._stream.seek(offset)
            return None
        try:
            value = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_fields,
                               parse_float=_finite_number, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            raise RuntimeError('invalid cooperative PC event feed JSON') from exc
        if not isinstance(value, dict):
            raise RuntimeError('cooperative PC event feed record must be an object')
        return value

    def _bind_mark(self, mark):
        if (not isinstance(mark, dict) or any(mark.get(key) for key in ('error', 'stopped', 'retry'))
                or any(type(mark.get(key)) is not int or mark[key] < 0
                       for key in ('sequence', 'epoch', 'active', 'frame', 'native_frame'))
                or type(mark.get('game_time')) not in (int, float)):
            raise RuntimeError('invalid cooperative PC event watermark')
        generation = mark.get('native_generation')
        if (type(generation) is not int or generation < 0
                or (self._generation is not None and generation != self._generation)):
            raise RuntimeError('cooperative PC event feed changed battle generation')
        if ('native_process_created_filetime' in mark
                and (type(mark['native_process_created_filetime']) is not int
                     or mark['native_process_created_filetime'] != self._header['process_instance'][1])):
            raise RuntimeError('cooperative PC event watermark belongs to a different process instance')
        bound = self._binding.bind('mark', mark)
        for key in ('native_session', 'source_build'):
            if key in mark and mark[key] != bound[key]:
                raise RuntimeError('cooperative PC event watermark claims a different battle or build')
        if self._session is not None and bound['native_session'] != self._session:
            raise RuntimeError('cooperative PC event feed changed battle identity')
        return bound

    def _advance(self, update):
        if (not {'mark', 'events'} <= update.keys() <= _UPDATE_FIELDS or not isinstance(update['events'], list)
                or not all(isinstance(event, dict) for event in update['events'])):
            raise RuntimeError('invalid cooperative PC event feed update')
        if self._terminal:
            raise RuntimeError('cooperative PC event feed continued after a finished battle')
        mark = self._bind_mark(update['mark'])
        terminal = update.get('terminal_mark')
        if terminal is not None:
            terminal = self._bind_mark(terminal)
            if any(terminal[key] != mark[key] for key in ('native_generation', 'native_session', 'source_build')):
                raise RuntimeError('cooperative PC terminal watermark belongs to a different battle')
        for event in update['events']:
            for key in ('native_generation', 'native_session', 'source_build', 'native_process_pid'):
                if key in event and (type(event[key]) is not type(mark[key]) or event[key] != mark[key]):
                    raise RuntimeError('cooperative PC event belongs to a different stream')
        if not self._probe.advance(mark, update['events'], terminal_mark=terminal):
            raise RuntimeError('native event host rejected the cooperative PC event update')
        self._generation, self._session = mark['native_generation'], mark['native_session']
        self._has_mark = True
        self._terminal = terminal is not None
        self._last_update = monotonic()

    def _poll_feed(self):
        self._check_owner()
        self._check_file()
        start = self._stream.tell()
        deadline = monotonic() + self.poll_seconds
        for _ in range(self.max_poll_updates):
            if self._pending_update is None:
                self._pending_update = self._read_line()
                if self._pending_update is None:
                    break
            self._advance(self._pending_update)
            self._pending_update = None
            if self._stream.tell() - start >= self.max_poll_bytes or monotonic() >= deadline:
                break
        remaining = self._check_file() > self._stream.tell()
        if self._terminal and remaining:
            raise RuntimeError('cooperative PC event feed continued after a finished battle')
        if (self._has_mark and not self._terminal and monotonic() - self._last_update >= self.stale_timeout):
            raise RuntimeError('cooperative PC event feed stopped advancing')
        if remaining or not self._has_mark:
            raise ObserverReadRetry('waiting for a completed cooperative PC event watermark')

    def _ensure_ready(self):
        if self.status != ObserverStatus.READY:
            raise RuntimeError(self.error or 'cooperative PC event observer is unavailable')

    def _request(self, operation, *args, poll=False):
        self._ensure_ready()
        try:
            if poll:
                self._poll_feed()
            else:
                self._check_owner()
            return getattr(self._probe, operation)(*args)
        except ObserverReadRetry:
            if self._has_mark and monotonic() - self._last_update >= self.stale_timeout:
                exc = RuntimeError('cooperative PC event feed could not provide a verified watermark')
                self._fail(exc)
                raise exc
            raise
        except Exception as exc:
            self._fail(exc)
            raise

    def mark(self):
        return self._request('mark', poll=True)

    def read_terminal_mark(self):
        return self._request('read_terminal_mark', poll=True)

    def read_through(self, sequence):
        return self._request('read_through', sequence)

    def read_baseline(self, sequence):
        return self._request('read_baseline', sequence)

    def reject_unverified_frame(self, error):
        """Bound retries while a guarded battle is present but fences fail to align."""
        now = monotonic()
        if self._first_unverified_frame is None:
            self._first_unverified_frame = now
        if now - self._first_unverified_frame < self.stale_timeout:
            return None
        failure = RuntimeError(f'cooperative PC event feed did not match the current native frame: {error}')
        self._fail(failure)
        return self.error

    def accept_verified_frame(self):
        self._first_unverified_frame = None

    def commit_events(self, sequence):
        try:
            confirmed = self._request('commit_events', sequence)
        except (OSError, RuntimeError, ValueError):
            return False
        if not confirmed and self._probe.status == ObserverStatus.FAILED:
            self._fail(RuntimeError(self._probe.error or 'native event host stopped during acknowledgement'))
        if confirmed:
            self.accept_verified_frame()
        return confirmed

    def _fail(self, error):
        self.status, self.error = ObserverStatus.FAILED, str(error)
        try:
            self._release()
        except RuntimeError:
            # Keep resources and cleanup details for the owner's stop retry.
            pass

    def _release(self):
        errors = {}
        for name, close in (('_stream', 'close'), ('_probe', 'stop')):
            resource = getattr(self, name)
            if resource is not None:
                try:
                    getattr(resource, close)()
                except (OSError, RuntimeError) as exc:
                    errors[name] = str(exc)
                else:
                    setattr(self, name, None)
        self.cleanup_errors = errors
        if errors:
            raise RuntimeError('; '.join(errors.values()))

    def stop(self):
        previous_errors = self.cleanup_errors
        self.status = ObserverStatus.STOPPED
        self._release()
        if previous_errors:
            raise RuntimeError('; '.join(previous_errors.values()))
