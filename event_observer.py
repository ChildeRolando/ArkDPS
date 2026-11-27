"""Frida event transports sharing bounded reads and commit acknowledgements."""
from enum import Enum
from dataclasses import dataclass
import json
import os
from hashlib import sha256
from threading import Event, Lock
from types import SimpleNamespace

from connection import VersionStatus
from event_probe import (MUMU_CONTRACT, NATIVE_PC_CONTRACT, NativePcBinding, ObserverReadRetry,
                         ready_probe_modes, assemble_probe_source, acquire_native_terminal_tail,
                         request_capture_pause,
                         reset_probe_cursor, commit_probe_prefix, check_round_start_range, read_probe_range)


class ObserverStatus(str, Enum):
    ATTACHING = 'attaching'
    READY = 'ready'
    INCOMPATIBLE = 'incompatible'
    STOPPED = 'stopped'
    FAILED = 'failed'


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


@dataclass(frozen=True)
class _FridaAcceptedAck:
    observer: object
    script: object
    session: object
    lifetime: object
    process_identity: tuple
    sequence: int


class FridaEventObserver:
    contract = MUMU_CONTRACT
    provides_autochess_context = True
    # Frida decodes a fresh event list for every reply. The observer retains no
    # reference to those records after delivering the batch to the collector.
    transfers_event_records = True

    def __init__(self, collector, remote):
        self.collector = collector
        self.remote = remote
        self.status = ObserverStatus.STOPPED
        self.error = None
        self.session = None
        self.script = None
        self.ready = Event()
        self.probe_modes = {}
        self.capture_paused = False
        self._capture_control_revision = self._capture_control_generation = 0
        self._probe_source_manifest = {}
        self.cleanup_errors = {}
        self._request_lock = Lock()
        self._request_id = 0
        self._pending_request = None
        self._accepted_ack_receipt = None
        reset_probe_cursor(self)
        self._autochess_enabled = None

    def set_autochess_setting(self, enabled):
        """Read the worker's latest preference at existing observation boundaries."""
        self._autochess_enabled = enabled

    def _with_autochess_setting(self, source):
        if self._autochess_enabled is None:
            return source
        mode = 'all' if self._autochess_enabled() else 'local'
        return f'globalThis.arkdpsAutoChessMode = "{mode}";\n' + source

    def _mark_options(self):
        return {} if self._autochess_enabled is None else {'autochess_enabled': self._autochess_enabled()}

    request_timeout = 5.0
    terminal_mark_timeout = 0.5

    def _attach_session(self, frida):
        device = frida.get_device_manager().add_remote_device(self.remote)
        try:
            return device.attach(self.collector.core.mc.pid, realm='emulated')
        except frida.NotSupportedError as exc:
            if str(exc).strip().casefold() != 'process is not using emulation':
                raise
            raise RuntimeError(
                'Frida 服务无法加载 ARM64 事件探针（process is not using emulation）。'
                '请重启设备端 Frida 服务后重连观察者。'
            ) from exc

    def _probe_source(self):
        from collector import android_event_layout

        assembled = assemble_probe_source('mumu')
        layout = android_event_layout()
        self._probe_source_manifest = dict(assembled.audit(), android_battle_layout=layout)
        prefix = 'globalThis.arkdpsAndroidBattleLayout = ' + json.dumps(layout, separators=(',', ':')) + ';\n'
        return self._with_autochess_setting(prefix + assembled.source)

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
            self.probe_modes = ready_probe_modes(payload)
            if self._probe_source_manifest:
                self.probe_modes["probe_source"] = dict(self._probe_source_manifest)
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
        self.probe_modes = {}
        self.capture_paused = False
        self._capture_control_revision = self._capture_control_generation = 0
        self._probe_source_manifest = {}
        reset_probe_cursor(self)
        try:
            import frida
            if frida.__version__ != '17.26.1':
                raise RuntimeError('Frida 17.26.1 is required')
            self.session = self._attach_session(frida)
            self.session.on('detached', self._detached)
            source = self._probe_source()
            if os.environ.get('ARKDPS_TRACE_HP_CONTEXT') == '1':
                source = 'globalThis.arkdpsTraceHpContext = true;\n' + source
                if os.environ.get('ARKDPS_TRACE_NESTED_PARENT') == '1':
                    source = 'globalThis.arkdpsTraceNestedParent = true;\n' + source
            if os.environ.get('ARKDPS_COALESCE_ENEMY_HP') == '0':
                source = 'globalThis.arkdpsCoalesceEnemyHp = false;\n' + source
            self._probe_source_manifest['submitted_sha256'] = sha256(source.encode('utf-8')).hexdigest()
            self.probe_modes['probe_source'] = dict(self._probe_source_manifest)
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

    def _request(self, operation, *, timeout=None, **arguments):
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
            received = pending.done.wait(self.request_timeout if timeout is None else timeout)
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
        return self._request('mark', **self._mark_options())

    @property
    def capture_pause_supported(self):
        version = self.probe_modes.get('capture_pause_version')
        return type(version) is int and version == 1

    def set_capture_paused(self, paused, revision):
        return request_capture_pause(self, paused, revision)

    def mark_with_presence(self):
        return self._request('mark', with_presence=True, **self._mark_options())

    def read_terminal_mark(self):
        # The battle already ended. An optional proof must not hold up archiving.
        return self._request('mark', timeout=self.terminal_mark_timeout, **self._mark_options())

    def read_through(self, sequence):
        return self._read_event_range(self._committed_sequence, sequence)

    def read_baseline(self, sequence):
        return self._read_event_range(sequence, sequence)

    def read_round_start_range(self, after, through):
        check_round_start_range(self, after)
        return self._read_event_range(after, through)

    def _read_event_range(self, after, through):
        return read_probe_range(self, after, through)

    def _accepted_ack_state(self):
        """Actual attached source identity, with no event or sampling reads."""
        script, session = self.script, self.session
        if (self.status != ObserverStatus.READY or script is None or session is None
                or self._pending_request is not None or self._ack_error is not None):
            return None
        try:
            if script.is_destroyed is not False or session.is_detached is not False:
                return None
            process = getattr(self.collector, 'process', None)
            if process is not None:
                instance = getattr(process, 'process_instance', None)
                if (type(instance) not in (tuple, list) or len(instance) != 2
                        or any(type(value) is not int or value <= 0 for value in instance)
                        or getattr(process, 'pid', None) != instance[0]
                        or process.process_alive() is not True):
                    return None
                identity = ('native_pc', *instance)
            else:
                pid = self.collector.core.mc.pid
                if type(pid) is not int or pid <= 0:
                    return None
                identity = ('mumu', pid)
        except Exception:
            return None
        return script, session, self._cursor_lifetime, identity

    def accepted_ack_receipt(self, sequence):
        """Issue one capability only for a committed-but-unconfirmed ACK.

        The shared script's EventStore sequence never resets within the same
        script/session lifetime. This grants no new read, baseline or ingestion.
        """
        if (type(sequence) is not int or not self._acknowledged_sequence < sequence <= self._committed_sequence):
            return None
        state = self._accepted_ack_state()
        if state is None:
            return None
        receipt = _FridaAcceptedAck(self, *state, sequence)
        self._accepted_ack_receipt = receipt
        return receipt

    def retry_accepted_ack(self, sequence, receipt):
        if (type(sequence) is not int or not isinstance(receipt, _FridaAcceptedAck)
                or receipt is not self._accepted_ack_receipt or receipt.observer is not self
                or receipt.sequence != sequence or sequence > self._committed_sequence):
            return None
        state = self._accepted_ack_state()
        if (state is None or state[0] is not receipt.script or state[1] is not receipt.session
                or state[2] is not receipt.lifetime or state[3] != receipt.process_identity):
            return None
        return self.commit_events(sequence)

    def commit_events(self, sequence):
        """Advance the host cursor and report whether the producer confirmed it."""
        return commit_probe_prefix(self, sequence,
            mismatch='observer acknowledgement did not match the committed watermark')

    def _release(self):
        self._accepted_ack_receipt = None
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


class NativePcFridaObserver(FridaEventObserver):
    """Use the local Windows device and independently bind every PC prefix."""

    contract = NATIVE_PC_CONTRACT

    def __init__(self, collector, remote=None):
        if remote is not None:
            raise ValueError('PC Frida 使用本机进程，frida_remote 必须为 None')
        super().__init__(collector, remote)
        self.binding = NativePcBinding(collector)

    def _message(self, message, data):
        payload = message.get('payload')
        if isinstance(payload, dict) and payload.get('ready') and payload.get('arch') != 'x64':
            message = {'type': 'error', 'description': 'PC Frida 探针必须运行在 Windows x64 进程'}
        super()._message(message, data)

    def _attach_session(self, frida):
        process = self.collector.process
        if not process.process_alive():
            raise RuntimeError('PC 游戏进程已退出，请重新连接')
        instance = process.process_instance
        if (not isinstance(instance, tuple) or len(instance) != 2
                or instance[0] != process.pid or type(instance[1]) is not int or instance[1] <= 0):
            raise RuntimeError('PC 缺少已验证的进程创建时间')
        try:
            return frida.get_local_device().attach(process.pid, realm='native')
        except frida.PermissionDeniedError as exc:
            raise RuntimeError('Windows 拒绝 Frida 访问PC 游戏进程，请以管理员权限启动 ArkDPS') from exc
        except frida.ProcessNotRespondingError as exc:
            raise RuntimeError('Frida 本机附加未完成，代理会话未建立；PC 事件探针尚未执行：' + str(exc)) from exc

    def _probe_source(self):
        if not self.collector.process.process_alive():
            raise RuntimeError('PC 游戏进程已退出，请重新连接')
        self.binding.reset()
        assembled = assemble_probe_source('native_pc')
        self._probe_source_manifest = assembled.audit()
        return self._with_autochess_setting(assembled.source)

    def _request(self, operation, *, timeout=None, **arguments):
        result = super()._request(operation, timeout=timeout, **arguments)
        return self.binding.bind(operation, result)

    def remember_verified_mark(self, sample, mark):
        return self.binding.remember_verified_mark(sample, mark)

    def read_terminal_mark(self):
        from copy import deepcopy
        self.last_terminal_response = None
        raw = super()._request('mark', timeout=self.terminal_mark_timeout, **self._mark_options())
        self.last_terminal_response = deepcopy(raw)
        return self.binding.bind_terminal_mark(raw)

    def read_terminal_tail(self, after, *, deadline=None):
        return acquire_native_terminal_tail(self, super()._request, after, deadline=deadline)

    def stop(self):
        self.binding.reset()
        self.last_terminal_response = None
        return super().stop()
