"""Bounded event transport for a standalone, self-owned test host."""

from concurrent.futures import Future, TimeoutError as FutureTimeout
import json
from pathlib import Path
from queue import SimpleQueue
import subprocess
from threading import Lock, Thread

from event_observer import ObserverStatus
from event_probe import NATIVE_PC_CONTRACT


class _TransportError(RuntimeError):
    pass


def _reject_constant(value):
    raise ValueError(f'non-JSON numeric constant {value}')


class InternalEventProbe:
    """Expose the test host's retained event stream through the EventProbe API."""

    contract = NATIVE_PC_CONTRACT
    transfers_event_records = True
    request_timeout = 5.0
    terminal_mark_timeout = 0.5
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
        self._io_thread = None
        self._commands = None
        self._request_id = 0
        self._lock = Lock()

    def start(self):
        with self._lock:
            if self.status != ObserverStatus.STOPPED or self._process is not None:
                raise RuntimeError('internal event probe is already started')
            self.status = ObserverStatus.ATTACHING
            self.error = None
            self.probe_modes = {}
            self._request_id = 0
            try:
                self._process = subprocess.Popen(
                    [str(self.host)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                self._commands = SimpleQueue()
                io_thread = Thread(
                    target=self._run_io, args=(self._process, self._commands),
                    name='ArkDPS internal event host I/O', daemon=True,
                )
                io_thread.start()
                self._io_thread = io_thread
                hello = self._exchange('hello', dict)
                if type(hello.get('protocol')) is not int or hello['protocol'] != 1:
                    raise _TransportError('unsupported internal event host protocol')
            except BaseException as exc:
                self._fail(exc)
                raise
            self.probe_modes = {'probe_source': {'provider': 'internal-test-host', 'protocol': 1}}
            self.status = ObserverStatus.READY

    def _run_io(self, process, commands):
        while (command := commands.get()) is not None:
            request, reply = command
            try:
                process.stdin.write(request)
                process.stdin.flush()
                line = process.stdout.readline(self.max_response_bytes + 2)
            except (OSError, ValueError) as exc:
                reply.set_exception(exc)
                return
            reply.set_result(line)

    def _exchange(self, operation, result_type, *, timeout=None, **arguments):
        self._request_id += 1
        request_id = self._request_id
        request = json.dumps(
            dict(id=request_id, op=operation, **arguments),
            ensure_ascii=False, allow_nan=False, separators=(',', ':'),
        ).encode('utf-8') + b'\n'
        if len(request) > self.max_request_bytes + 1:
            raise ValueError('internal event host request exceeds the frame limit')

        reply = Future()
        self._commands.put((request, reply))
        try:
            line = reply.result(timeout=self.request_timeout if timeout is None else timeout)
        except FutureTimeout as exc:
            raise _TransportError(f'internal event host {operation} timed out') from exc
        except (OSError, ValueError) as exc:
            raise _TransportError(f'internal event host {operation} I/O failed: {exc}') from exc
        if not line:
            raise _TransportError(f'internal event host closed its output during {operation}')
        if len(line) > self.max_response_bytes + 1 or not line.endswith(b'\n'):
            raise _TransportError('invalid internal event host frame length or terminator')
        try:
            response = json.loads(line.decode('utf-8'), parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            raise _TransportError('invalid internal event host JSON response') from exc
        if (not isinstance(response, dict) or type(response.get('id')) is not int
                or response['id'] != request_id):
            raise _TransportError('internal event host response does not match the request ID')
        if set(response) == {'id', 'error'} and isinstance(response['error'], str):
            raise RuntimeError(f'internal event host {operation} rejected the request: {response["error"]}')
        if set(response) != {'id', 'result'} or type(response['result']) is not result_type:
            raise _TransportError(f'invalid internal event host {operation} result')
        return response['result']

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

    def commit_events(self, sequence):
        return self._request('commit', bool, sequence=sequence)

    def stop(self):
        with self._lock:
            graceful = False
            try:
                if self.status == ObserverStatus.READY:
                    graceful = self._exchange('stop', bool, timeout=self.shutdown_timeout)
                    if not graceful:
                        self.error = 'internal event host did not confirm shutdown'
            except RuntimeError as exc:
                self.error = str(exc)
            finally:
                self.status = ObserverStatus.STOPPED
                self._release(graceful=graceful)

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
        try:
            if graceful:
                try:
                    process.wait(timeout=self.shutdown_timeout)
                except subprocess.TimeoutExpired:
                    self.error = 'internal event host did not exit after confirming shutdown'
            if process.poll() is None:
                process.kill()
            process.wait(timeout=self.shutdown_timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.cleanup_errors = {'process': str(exc)}
            raise RuntimeError('could not reap the internal event host') from exc

        if self._io_thread is not None:
            self._commands.put(None)
            self._io_thread.join(timeout=self.shutdown_timeout)
            if self._io_thread.is_alive():
                self.cleanup_errors = {'io': 'I/O thread did not stop after host exit'}
                raise RuntimeError(self.cleanup_errors['io'])
            self._io_thread = None

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
        self._process = self._commands = None
