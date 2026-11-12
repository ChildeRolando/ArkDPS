"""A temporary, explicitly attached Frida event observer for Android 2.7.91."""
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

from connection import VersionStatus


class ObserverStatus(str, Enum):
    ATTACHING = 'attaching'
    READY = 'ready'
    INCOMPATIBLE = 'incompatible'
    STOPPED = 'stopped'
    FAILED = 'failed'


class ObserverReadRetry(RuntimeError):
    """A live observer did not deliver a response within the request window."""


def _while_live(operation, released):
    if released():
        return
    try:
        operation()
    except Exception as exc:
        if not released():
            raise
        from frida import InvalidOperationError
        if not isinstance(exc, InvalidOperationError):
            raise


class FridaEventObserver:
    def __init__(self, collector, remote):
        self.collector = collector
        self.remote = remote
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.session = None
        self.script = None
        self.ready = Event()
        self.cleanup_errors = {}
        self._request_lock = Lock()
        self._request_id = 0
        self._pending_request = None
        self._committed_sequence = 0
        self._acknowledged_sequence = 0
        self._ack_error = None

    request_timeout = 5.0

    def _wake_request(self):
        with self._request_lock:
            if self._pending_request is not None:
                self._pending_request.done.set()

    def _message(self, message, _data):
        payload = message.get('payload')
        if message.get('type') == 'error':
            self.error = message.get('description', 'observer script error')
            self.status = ObserverStatus.FAILED
            self.ready.set()
            self._wake_request()
        elif isinstance(payload, dict) and payload.get('kind') == 'arkdps-event-response':
            with self._request_lock:
                pending = self._pending_request
                if pending is not None and pending.id == payload.get('id'):
                    pending.response = payload.get('result')
                    pending.done.set()
        elif isinstance(payload, dict) and payload.get('ready'):
            self.ready.set()

    def _detached(self, reason, *_args):
        if self.status != ObserverStatus.STOPPED:
            self.error = f'observer detached: {reason}'
            self.status = ObserverStatus.FAILED
            self.ready.set()
            self._wake_request()

    def start(self):
        if self.status != ObserverStatus.STOPPED or self.session is not None:
            raise RuntimeError('observer is already started')
        diagnostics = self.collector.diagnostics
        if not diagnostics.connection_ok or diagnostics.version_status != VersionStatus.COMPATIBLE:
            self.status = ObserverStatus.INCOMPATIBLE
            raise RuntimeError('a version-checked collector connection is required')
        self.status = ObserverStatus.ATTACHING
        self.error = None
        self.ready.clear()
        self._committed_sequence = self._acknowledged_sequence = 0
        self._ack_error = None
        try:
            import frida
            if frida.__version__ != '17.26.1':
                raise RuntimeError('Frida 17.26.1 is required')
            device = frida.get_device_manager().add_remote_device(self.remote)
            self.session = device.attach(self.collector.core.mc.pid, realm='emulated')
            self.session.on('detached', self._detached)
            source = Path(__file__).with_name('event_probe_android2791.js').read_text(encoding='utf-8')
            self.script = self.session.create_script(source)
            self.script.on('message', self._message)
            self.script.load()
            if not self.ready.wait(20) or self.error:
                raise RuntimeError(self.error or 'observer did not become ready')
            self.status = ObserverStatus.READY
        except Exception as exc:
            self.error = str(exc)
            try:
                self._release()
            finally:
                self.status = ObserverStatus.FAILED
            raise

    def _request(self, operation, **arguments):
        script = self.script
        if self.status != ObserverStatus.READY or script is None:
            raise RuntimeError(self.error or f'observer is {self.status.value}')
        # Frida's synchronous export wrapper has no response deadline. A
        # request ID lets a late answer expire without changing the cursor.
        with self._request_lock:
            if self._pending_request is not None:
                raise RuntimeError('observer request is already in flight')
            self._request_id += 1
            pending = SimpleNamespace(id=self._request_id, done=Event(), response=None)
            self._pending_request = pending
        try:
            try:
                script.post(dict(type='arkdps-event-request', id=pending.id,
                                 operation=operation, **arguments))
            except Exception as exc:
                if self.status != ObserverStatus.READY or script.is_destroyed:
                    raise RuntimeError(self.error or str(exc)) from exc
                raise ObserverReadRetry(f'observer {operation} request failed: {exc}') from exc
            received = pending.done.wait(self.request_timeout)
            if self.status != ObserverStatus.READY or script.is_destroyed:
                raise RuntimeError(self.error or 'observer no longer available')
            if not received:
                raise ObserverReadRetry(f'observer {operation} response timed out')
            if not isinstance(pending.response, dict):
                raise RuntimeError(f'invalid observer {operation} response')
            return pending.response
        finally:
            with self._request_lock:
                self._pending_request = None

    def mark(self):
        return self._request('mark')

    def read_through(self, sequence):
        if self._ack_error:
            raise RuntimeError(self._ack_error)
        return self._request('read', after=self._committed_sequence, through=sequence)

    def commit_events(self, sequence):
        """Release the prefix only after it was accepted by the host."""
        self._committed_sequence = max(sequence, self._committed_sequence)
        if self._committed_sequence <= self._acknowledged_sequence:
            return
        try:
            reply = self._request('ack', sequence=self._committed_sequence)
        except ObserverReadRetry:
            # A lost response can mean the producer already released this
            # prefix. A future acknowledgement is cumulative and idempotent.
            return
        except RuntimeError as exc:
            self._ack_error = str(exc)
            return
        if reply.get('error') or reply.get('acknowledged') != self._committed_sequence:
            self._ack_error = reply.get('error') or 'observer acknowledgement did not match the committed watermark'
        else:
            self._acknowledged_sequence = self._committed_sequence

    def _release(self):
        # Both releases must be attempted. Keep failed handles available for a
        # retry; clearing a Python reference is not evidence of remote release.
        errors = {}
        interrupted = None
        for name, method, released in (('script', 'unload', 'is_destroyed'),
                                       ('session', 'detach', 'is_detached')):
            resource = getattr(self, name)
            if resource is None:
                continue
            try:
                _while_live(getattr(resource, method),
                            lambda: getattr(resource, released))
            except BaseException as exc:
                errors[name] = str(exc)
                if not isinstance(exc, Exception):
                    interrupted = exc
            else:
                setattr(self, name, None)
        self.cleanup_errors = errors
        if interrupted is not None:
            raise interrupted
        if errors:
            raise RuntimeError('; '.join(f'{name}: {error}' for name, error in errors.items()))

    def stop(self):
        try:
            if self.script is not None and self.status == ObserverStatus.READY:
                _while_live(lambda: self.script.exports_sync.stop(), lambda: self.script.is_destroyed)
        finally:
            self.status = ObserverStatus.STOPPED
            self._release()
