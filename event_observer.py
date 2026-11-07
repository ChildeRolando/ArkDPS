"""A temporary, explicitly attached Frida event observer for Android 2.7.91."""
from enum import Enum
from pathlib import Path
from threading import Event

from connection import VersionStatus


class ObserverStatus(str, Enum):
    ATTACHING = 'attaching'
    READY = 'ready'
    INCOMPATIBLE = 'incompatible'
    STOPPED = 'stopped'
    FAILED = 'failed'


class FridaEventObserver:
    def __init__(self, collector, remote):
        self.collector = collector
        self.remote = remote
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.session = None
        self.script = None
        self.ready = Event()

    def _message(self, message, _data):
        if message.get('type') == 'error':
            self.error = message.get('description', 'observer script error')
            self.status = ObserverStatus.FAILED
            self.ready.set()
        elif message.get('payload', {}).get('ready'):
            self.ready.set()

    def _detached(self, reason, *_args):
        if self.status != ObserverStatus.STOPPED:
            self.error = f'observer detached: {reason}'
            self.status = ObserverStatus.FAILED
            self.ready.set()

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

    def mark(self):
        if self.status != ObserverStatus.READY:
            raise RuntimeError(self.error or f'observer is {self.status.value}')
        return self.script.exports_sync.mark()

    def drain(self):
        if self.status != ObserverStatus.READY:
            raise RuntimeError(self.error or f'observer is {self.status.value}')
        return self.script.exports_sync.drain()

    def _release(self):
        try:
            if self.script is not None:
                try:
                    self.script.unload()
                finally:
                    self.script = None
        finally:
            if self.session is not None:
                try:
                    self.session.detach()
                finally:
                    self.session = None

    def stop(self):
        try:
            if self.script is not None and self.status == ObserverStatus.READY:
                self.script.exports_sync.stop()
        finally:
            self.status = ObserverStatus.STOPPED
            self._release()
