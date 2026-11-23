"""Publish caller-supplied battle evidence without acquiring it from a target."""

import json
from math import isfinite
from pathlib import Path
import re
from threading import Lock

from native_pc.live_event_source import FEED_FORMAT, FEED_VERSION, LiveNativeEventProbe


_MAX_U64 = (1 << 64) - 1
_ROOT_NAMES = ('controller', 'native', 'logger', 'stats', 'level')
_FIXED_TIME = re.compile(r'[0-9]{1,20}')
_ROOT = re.compile(r'0x[0-9a-fA-F]{1,16}')
_MAX_BATCH_EVENTS = 4096  # Native event host queue capacity.
_MAX_MARK_BYTES = 256 * 1024


def _uint(value, maximum=_MAX_U64):
    return type(value) is int and 0 <= value <= maximum


def _clock(record):
    frame, seconds = record.get('frame'), record.get('game_time')
    if (not _uint(frame, 0xFFFFFFFF) or type(seconds) not in (int, float)
            or not 0 <= seconds <= 864000 or not isfinite(seconds)):
        raise ValueError('invalid event or watermark clock')
    return frame, seconds


def _clock_follows(current, previous):
    return (current[0] >= previous[0] and current[1] >= previous[1]
            and (current[0] != previous[0] or current[1] == previous[1]))


def _roots(value):
    if not isinstance(value, dict) or value.keys() != set(_ROOT_NAMES):
        raise ValueError('invalid watermark battle identity')
    result = []
    for name in _ROOT_NAMES:
        address = value[name]
        if not isinstance(address, str) or not _ROOT.fullmatch(address):
            raise ValueError('invalid watermark battle identity')
        number = int(address, 16)
        if not 0x10000 <= number <= 0x7FFFFFFFFFFF or number % 8:
            raise ValueError('invalid watermark battle identity')
        result.append(number)
    return tuple(result)


def _validate_json(value, depth=0):
    # The host wraps these objects in a request and has a 64-level limit.
    if depth > 60:
        raise ValueError('event feed JSON nesting limit exceeded')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('event feed JSON object keys must be strings')
            _validate_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
    elif type(value) is int:
        if not -(1 << 63) <= value <= _MAX_U64:
            raise ValueError('event feed integer is outside the host JSON range')
    elif value is not None and type(value) not in (str, bool, float):
        raise ValueError('event feed values must be JSON data')


def _encode(value, *, sort_keys=False):
    _validate_json(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'),
                      sort_keys=sort_keys).encode('utf-8')


class CooperativeEventWriter:
    """Write one new JSONL file per battle for an authorized telemetry source.

    Callers supply verified identities, clocks, sequences, coverage declarations,
    and complete frames. This class checks consistency but cannot attest to the
    source or coverage. Calls are serialized; schedule heartbeats while paused.
    Closing without a caller-supplied terminal frame leaves an unsealed stream.
    """

    max_line_bytes = LiveNativeEventProbe.max_line_bytes
    max_file_bytes = LiveNativeEventProbe.max_file_bytes

    def __init__(self, path, *, producer, producer_version, process_instance, source_build):
        if not isinstance(producer, str) or not 0 < len(producer) <= 80 or not producer.isprintable():
            raise ValueError('invalid event producer name')
        if not _uint(producer_version) or producer_version == 0:
            raise ValueError('invalid event producer version')
        if (not isinstance(process_instance, (list, tuple)) or len(process_instance) != 2
                or not all(_uint(part) and part > 0 for part in process_instance)):
            raise ValueError('invalid event producer process instance')
        if not isinstance(source_build, str) or not source_build:
            raise ValueError('invalid event producer source build')
        self.path = Path(path).expanduser()
        self._instance = tuple(process_instance)
        self._build = source_build
        self._session_prefix = f'{self._instance[0]}:{self._instance[1]}:'
        self._header = _encode(dict(format=FEED_FORMAT, version=FEED_VERSION, producer=producer,
                                   producer_version=producer_version, process_instance=list(self._instance),
                                   source_build=source_build))
        self._lock = Lock()
        self._stream = self._last = self._last_mark = self._heartbeat = self._claimed_session = None
        self._bytes = 0
        self._opened = self._terminal = self._failed = False

    def __enter__(self):
        with self._lock:
            if self._opened:
                raise RuntimeError('event feed writer cannot be reopened')
            self._check_size(self._header)
            self._stream = self.path.open('xb', buffering=0)
            self._opened = True
            try:
                self._append(self._header)
            except BaseException:
                self._close()
                raise
            return self

    def __exit__(self, *exc):
        with self._lock:
            self._close()

    def _close(self):
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.close()

    def _ensure_writable(self):
        if self._stream is None or self._failed:
            raise RuntimeError('event feed writer is not open for writing')
        if self._terminal:
            raise RuntimeError('event feed has already finished')

    def _check_size(self, data):
        if len(data) > self.max_line_bytes:
            raise ValueError('event feed record exceeds the line limit')
        if self._bytes + len(data) + 1 > self.max_file_bytes:
            raise ValueError('event feed exceeds the file limit')

    def _append(self, data):
        self._check_size(data)
        line = data + b'\n'
        try:
            # Publish one complete newline-terminated record to other readers.
            if self._stream.write(line) != len(line):
                raise OSError('incomplete event feed write')
            self._stream.flush()
        except BaseException:
            # Do not append to a possibly interrupted, partially written record.
            self._failed = True
            raise
        self._bytes += len(line)

    def _check_claims(self, value, generation, stage, roots, session):
        for key, expected in (('native_process_pid', self._instance[0]),
                              ('native_process_created_filetime', self._instance[1]),
                              ('source_build', self._build), ('native_generation', generation),
                              ('native_stage_id', stage)):
            if key in value and (type(value[key]) is not type(expected) or value[key] != expected):
                raise ValueError(f'event or watermark has a different stream binding: {key}')
        if 'native_identity' in value and _roots(value['native_identity']) != roots:
            raise ValueError('event or watermark has a different battle identity')
        if 'native_session' in value:
            claim = value['native_session']
            if (not isinstance(claim, str) or not claim.startswith(self._session_prefix)
                    or len(claim) == len(self._session_prefix) or (session is not None and claim != session)):
                raise ValueError('event or watermark has a different native session')
            return claim
        return session

    def publish(self, mark, events, *, terminal=False):
        """Append a completed frame and only its new records, then flush.

        The first update may instead include earlier frames as a contiguous
        prefix ending at this watermark, or declare a nonzero starting sequence
        without replaying its history. Neither form asserts complete coverage.
        """
        with self._lock:
            self._ensure_writable()
            if (not isinstance(mark, dict) or type(terminal) is not bool or 'events' in mark
                    or any(mark.get(key) for key in ('error', 'stopped', 'retry'))):
                raise ValueError('invalid completed frame watermark')
            through, epoch, active, generation = (mark.get(key) for key in ('sequence', 'epoch', 'active', 'native_generation'))
            if not all(_uint(part) for part in (through, epoch, active, generation)) or active != 0:
                raise ValueError('watermark requires a valid sequence, epoch, generation and no active callbacks')
            clock, fixed = _clock(mark), mark.get('native_fixed_time')
            if (not _uint(mark.get('native_frame'), 0xFFFFFFFF) or mark['native_frame'] != clock[0]
                    or not isinstance(fixed, str) or not _FIXED_TIME.fullmatch(fixed)
                    or int(fixed) / (1 << 32) != clock[1]):
                raise ValueError('watermark does not match the exact native clock')
            roots, stage = _roots(mark.get('native_identity')), mark.get('native_stage_id')
            if not isinstance(stage, str) or not stage or 'native_process_pid' not in mark:
                raise ValueError('watermark is missing its process or stage binding')
            binding = generation, stage, roots
            session = self._check_claims(mark, *binding, self._claimed_session)
            if (not isinstance(events, (list, tuple)) or len(events) > _MAX_BATCH_EVENTS
                    or not all(isinstance(event, dict) for event in events)):
                raise ValueError('event feed batch must contain at most 4096 event objects')
            prior_clock = None
            if self._last is not None:
                previous, previous_epoch, prior_clock, prior_fixed, old_binding = self._last
                if old_binding != binding:
                    raise ValueError('event feed cannot change its battle, stage or generation')
                if (through < previous or epoch < previous_epoch or not _clock_follows(clock, prior_clock)
                        or int(fixed) < prior_fixed or (clock[0] == prior_clock[0] and int(fixed) != prior_fixed)):
                    raise ValueError('event feed watermark went backwards or changed the clock of a completed frame')
                if events and clock[0] == prior_clock[0]:
                    raise ValueError('cannot add events to an already completed frame')
            elif events:
                first = events[0].get('sequence')
                if not _uint(first) or first == 0:
                    raise ValueError('invalid first event sequence')
                previous = first - 1
            else:
                previous = through

            for event in events:
                sequence = event.get('sequence')
                if not _uint(sequence) or sequence != previous + 1:
                    raise ValueError('event feed sequence is not continuous')
                event_clock = _clock(event)
                if (not _clock_follows(clock, event_clock)
                        or (prior_clock is not None and not _clock_follows(event_clock, prior_clock))
                        or (self._last is not None and event_clock != clock)):
                    raise ValueError('event is out of order or does not match the completed frame')
                session = self._check_claims(event, *binding, session)
                previous, prior_clock = sequence, event_clock
            if previous != through:
                raise ValueError('events do not reach the watermark sequence')

            # The reader binds independent session/build evidence before handing
            # the mark to the native host; reserve room without writing any IDs.
            if len(_encode(dict(mark, source_build=self._build, native_session=self._session_prefix + '0' * 64))) > _MAX_MARK_BYTES:
                raise ValueError('watermark exceeds the native host size limit')
            mark_json = _encode(mark, sort_keys=True)
            if self._last is not None and clock[0] == self._last[2][0] and mark_json != self._last_mark:
                raise ValueError('cannot change the watermark of an already completed frame')
            frame = b'{"mark":' + mark_json + b',"events":'
            update = frame + _encode(list(events))
            if terminal:
                update += b',"terminal_mark":' + mark_json
            self._append(update + b'}')
            self._last = through, epoch, clock, int(fixed), binding
            self._last_mark = mark_json
            self._claimed_session = session
            self._heartbeat = frame + b'[]}'
            self._terminal = terminal

    def heartbeat(self):
        """Repeat the last completed watermark, with no new event or terminal."""
        with self._lock:
            self._ensure_writable()
            if self._heartbeat is None:
                raise RuntimeError('cannot send an event feed heartbeat before the first frame')
            self._append(self._heartbeat)
