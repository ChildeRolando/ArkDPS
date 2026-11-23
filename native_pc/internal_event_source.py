"""Bounded event transport for a standalone, self-owned test host."""

from concurrent.futures import Future, TimeoutError as FutureTimeout
import json
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue, SimpleQueue
import subprocess
from threading import Lock, Thread
from time import monotonic

from event_observer import ObserverStatus
from event_probe import NATIVE_PC_CONTRACT


PROTOCOL_VERSION = 1
SOURCE_PROVIDER = 'internal-test-host'
SOURCE_VERSION = 1


class _TransportError(RuntimeError):
    pass


class _ReplyTimeout(_TransportError):
    pass


class _RequestRejected(RuntimeError):
    pass


def _reject_constant(value):
    raise ValueError(f'non-JSON numeric constant {value}')


def _finite_number(value):
    number = float(value)
    if not isfinite(number):
        raise ValueError('non-finite JSON number')
    return number


def _unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate JSON field')
    return result


class InternalEventProbe:
    """Expose the test host's retained event stream through the EventProbe API."""

    contract = NATIVE_PC_CONTRACT
    transfers_event_records = True
    request_timeout = 5.0
    terminal_mark_timeout = 0.5
    acknowledgement_timeout = 0.5
    shutdown_timeout = 1.0
    max_request_bytes = 8 * 1024 * 1024
    # A read reply also carries the current mark and the JSON envelope.
    max_response_bytes = 9 * 1024 * 1024

    def __init__(self, host: Path):
        self.host = Path(host).expanduser().resolve()
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.probe_modes = {}
        self.cleanup_errors = {}
        self._process = None
        self._threads = []
        self._commands = None
        self._responses = None
        self._duplicate_response = None
        self._request_id = 0
        self._lock = Lock()

    def start(self):
        with self._lock:
            if self.status != ObserverStatus.STOPPED or self._process is not None:
                raise RuntimeError('internal event probe is already started')
            self.status = ObserverStatus.ATTACHING
            self.error = None
            self.probe_modes = {}
            self._duplicate_response = None
            self._request_id = 0
            try:
                self._process = subprocess.Popen(
                    [str(self.host)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                self._commands = SimpleQueue()
                self._responses = Queue(maxsize=3)
                for name, target, pipe, queue in (
                    ('write', self._write_requests, self._process.stdin, self._commands),
                    ('read', self._read_responses, self._process.stdout, self._responses),
                ):
                    thread = Thread(target=target, args=(pipe, queue), daemon=True,
                                    name=f'ArkDPS internal event host {name}')
                    thread.start()
                    self._threads.append(thread)
                hello = self._exchange('hello', dict)
                source = dict(provider=SOURCE_PROVIDER, protocol=PROTOCOL_VERSION, source_version=SOURCE_VERSION)
                if (hello != source or type(hello.get('protocol')) is not int
                        or type(hello.get('source_version')) is not int):
                    raise _TransportError('unsupported internal event host identity or version')
            except BaseException as exc:
                self._fail(exc)
                raise
            self.probe_modes = {'probe_source': source}
            self.status = ObserverStatus.READY

    @staticmethod
    def _write_requests(pipe, commands):
        while (command := commands.get()) is not None:
            request, written = command
            try:
                pipe.write(request)
                pipe.flush()
            except (OSError, ValueError) as exc:
                written.set_exception(exc)
                return
            written.set_result(None)

    def _read_responses(self, pipe, responses):
        while True:
            try:
                line = pipe.readline(self.max_response_bytes + 2)
            except (OSError, ValueError) as exc:
                line = exc
            try:
                responses.put_nowait(line)
            except Full:
                # A serial connection can have only one outstanding reply and
                # one duplicate. Stop reading an unsolicited stream.
                return
            if not isinstance(line, bytes) or not line.endswith(b'\n'):
                return

    def _exchange(self, operation, result_type, *, timeout=None, repeat=False, **arguments):
        self._request_id += 1
        request_id = self._request_id
        request = json.dumps(
            dict(version=PROTOCOL_VERSION, id=request_id, op=operation, **arguments),
            ensure_ascii=False, allow_nan=False, separators=(',', ':'),
        ).encode('utf-8') + b'\n'
        if len(request) > self.max_request_bytes + 1:
            raise ValueError('internal event host request exceeds the frame limit')
        timeout = self.request_timeout if timeout is None else timeout
        retried = False
        try:
            line = self._transact(operation, request, timeout)
        except _ReplyTimeout:
            if not repeat:
                raise
            # Only replay an already-written acknowledgement. The host uses
            # the same request ID to return its cached outcome.
            retried = True
            line = self._transact(operation, request, timeout)
        try:
            response = json.loads(line.decode('utf-8'), parse_constant=_reject_constant,
                                  parse_float=_finite_number, object_pairs_hook=_unique_fields)
        except (ValueError, RecursionError) as exc:
            raise _TransportError('invalid internal event host JSON response') from exc
        if not isinstance(response, dict) or type(response.get('version')) is not int:
            raise _TransportError('invalid internal event host response envelope')
        if response['version'] != PROTOCOL_VERSION:
            raise _TransportError('unsupported internal event host response version')
        if type(response.get('id')) is not int or response['id'] != request_id:
            raise _TransportError('internal event host response does not match the request ID')
        self._duplicate_response = line if retried else None
        if set(response) == {'version', 'id', 'error'} and isinstance(response['error'], str):
            raise _RequestRejected(f'internal event host {operation} rejected the request: {response["error"]}')
        if set(response) != {'version', 'id', 'result'} or type(response['result']) is not result_type:
            raise _TransportError(f'invalid internal event host {operation} result')
        return response['result']

    def _transact(self, operation, request, timeout):
        deadline = monotonic() + timeout
        written = Future()
        self._commands.put((request, written))
        try:
            written.result(timeout=timeout)
        except FutureTimeout as exc:
            raise _TransportError(f'internal event host {operation} timed out during write') from exc
        except (OSError, ValueError) as exc:
            raise _TransportError(f'internal event host {operation} I/O failed: {exc}') from exc
        line = self._next_response(operation, deadline)
        if line == self._duplicate_response:
            self._duplicate_response = None
            line = self._next_response(operation, deadline)
        return line

    def _next_response(self, operation, deadline):
        try:
            line = self._responses.get(timeout=max(0, deadline - monotonic()))
        except Empty as exc:
            raise _ReplyTimeout(f'internal event host {operation} timed out') from exc
        if isinstance(line, Exception):
            raise _TransportError(f'internal event host {operation} I/O failed: {line}') from line
        if not line:
            raise _TransportError(f'internal event host closed its output during {operation}')
        if len(line) > self.max_response_bytes + 1 or not line.endswith(b'\n'):
            raise _TransportError('invalid internal event host frame length or terminator')
        return line

    def _request(self, operation, result_type, **arguments):
        with self._lock:
            if self.status != ObserverStatus.READY:
                raise RuntimeError(self.error or f'internal event probe is {self.status.value}')
            try:
                return self._exchange(operation, result_type, **arguments)
            except _TransportError as exc:
                self._fail(exc)
                raise

    def advance(self, mark, events, terminal_mark=None):
        return self._request('advance', bool, mark=mark, events=events, terminal_mark=terminal_mark)

    def mark(self):
        return self._request('mark', dict)

    def read_terminal_mark(self):
        result = self._request('terminal', dict, timeout=self.terminal_mark_timeout)
        return {'terminal_boundary': result} if result and 'terminal_boundary' not in result else result

    def read_through(self, sequence):
        return self._request('read', dict, sequence=sequence)

    def read_baseline(self, sequence):
        return self._request('read', dict, sequence=sequence, baseline=True)

    def commit_events(self, sequence):
        with self._lock:
            if self.status != ObserverStatus.READY:
                return False
            try:
                result = self._exchange('commit', dict, sequence=sequence, repeat=True,
                                        timeout=self.acknowledgement_timeout)
                acknowledged = result.get('acknowledged')
                if result == {'acknowledged': None}:
                    return False
                if set(result) != {'acknowledged'} or type(acknowledged) is not int or acknowledged != sequence:
                    raise _TransportError('internal event host acknowledgement does not match its watermark')
            except _RequestRejected:
                return False
            except _TransportError as exc:
                # The consumer already accepted this batch. A subsequent mark
                # reports the failed source without rolling back native data.
                self._fail(exc)
                return False
            return True

    def stop(self):
        with self._lock:
            graceful = False
            failure = None
            try:
                if self.status == ObserverStatus.READY:
                    graceful = self._exchange('stop', bool, timeout=self.shutdown_timeout)
                    if not graceful:
                        failure = RuntimeError('internal event host did not confirm shutdown')
            except RuntimeError as exc:
                failure = exc
            finally:
                self.status = ObserverStatus.STOPPED
                self._release(graceful=graceful)
            if failure is not None:
                self.error = str(failure)
                raise failure

    def _fail(self, exc):
        self.status = ObserverStatus.FAILED
        self.error = str(exc)
        try:
            self._release()
        except RuntimeError:
            # Keep cleanup ownership and diagnostics for a later stop retry.
            pass

    def _release(self, *, graceful=False):
        process = self._process
        if process is None:
            return
        graceful_error = None
        try:
            if graceful:
                try:
                    exit_code = process.wait(timeout=self.shutdown_timeout)
                    if exit_code:
                        graceful_error = f'internal event host exited with code {exit_code} after confirming shutdown'
                except subprocess.TimeoutExpired:
                    graceful_error = 'internal event host did not exit after confirming shutdown'
            if process.poll() is None:
                process.kill()
            process.wait(timeout=self.shutdown_timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.cleanup_errors = {'process': str(exc)}
            raise RuntimeError('could not reap the internal event host') from exc

        if self._threads:
            self._commands.put(None)
            deadline = monotonic() + self.shutdown_timeout
            for thread in self._threads:
                thread.join(timeout=max(0, deadline - monotonic()))
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if self._threads:
                self.cleanup_errors = {'io': 'I/O threads did not stop after host exit'}
                raise RuntimeError(self.cleanup_errors['io'])

        errors = {}
        for name in ('stdin', 'stdout'):
            pipe = getattr(process, name)
            try:
                pipe.close()
            except OSError as exc:
                if not pipe.closed:
                    errors[name] = str(exc)
        self.cleanup_errors = errors
        if errors:
            raise RuntimeError('could not close internal event host pipes')
        self._process = self._commands = self._responses = None
        if graceful_error is not None:
            self.error = graceful_error
            raise RuntimeError(graceful_error)
