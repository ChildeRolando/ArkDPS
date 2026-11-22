"""Frida event transports sharing bounded reads and commit acknowledgements."""
from enum import Enum
import os
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
        self.probe_modes = {}
        self.cleanup_errors = {}
        self._request_lock = Lock()
        self._request_id = 0
        self._pending_request = None
        self._committed_sequence = 0
        self._acknowledged_sequence = 0
        self._ack_error = None

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
        return Path(__file__).with_name('event_probe_android2791.js').read_text(encoding='utf-8')

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
            self.probe_modes = {
                key: payload[key] for key in ('coalesce_enemy_hp', 'trace_hp_context',
                                              'source_character_uid_ready')
                if type(payload.get(key)) is bool
            }
            if isinstance(payload.get('source_character_uid_error'), str):
                self.probe_modes['source_character_uid_error'] = payload['source_character_uid_error']
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
        self._committed_sequence = self._acknowledged_sequence = 0
        self._ack_error = None
        try:
            import frida
            if frida.__version__ != '17.26.1':
                raise RuntimeError('Frida 17.26.1 is required')
            self.session = self._attach_session(frida)
            self.session.on('detached', self._detached)
            source = self._probe_source()
            if os.environ.get('ARKDPS_TRACE_HP_CONTEXT') == '1':
                source = 'globalThis.arkdpsTraceHpContext = true;\n' + source
            if os.environ.get('ARKDPS_COALESCE_ENEMY_HP') == '1':
                source = 'globalThis.arkdpsCoalesceEnemyHp = true;\n' + source
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
        return self._request('mark')

    def read_terminal_mark(self):
        # The battle already ended. An optional proof must not hold up archiving.
        return self._request('mark', timeout=self.terminal_mark_timeout)

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


class NativePcFridaObserver(FridaEventObserver):
    """Use the local Windows device and independently bind every PC prefix."""

    def __init__(self, collector, remote=None):
        if remote is not None:
            raise ValueError('官服 PC Frida 使用本机进程，frida_remote 必须为 None')
        super().__init__(collector, remote)
        self._generation_sessions = {}
        self._session_generations = {}

    def _message(self, message, data):
        payload = message.get('payload')
        if isinstance(payload, dict) and payload.get('ready') and payload.get('arch') != 'x64':
            message = {'type': 'error', 'description': '官服 PC Frida 探针必须运行在 Windows x64 进程'}
        super()._message(message, data)

    def _attach_session(self, frida):
        process = self.collector.process
        if not process.process_alive():
            raise RuntimeError('官服 PC 游戏进程已退出，请重新连接')
        instance = process.process_instance
        if (not isinstance(instance, tuple) or len(instance) != 2
                or instance[0] != process.pid or type(instance[1]) is not int or instance[1] <= 0):
            raise RuntimeError('官服 PC 缺少已验证的进程创建时间')
        try:
            return frida.get_local_device().attach(process.pid, realm='native')
        except frida.PermissionDeniedError as exc:
            raise RuntimeError('Windows 拒绝 Frida 访问官服 PC 游戏进程，请以管理员权限启动 ArkDPS') from exc
        except frida.ProcessNotRespondingError as exc:
            raise RuntimeError('Frida 本机附加未完成，代理会话未建立；PC 事件探针尚未执行：' + str(exc)) from exc

    def _probe_source(self):
        if not self.collector.process.process_alive():
            raise RuntimeError('官服 PC 游戏进程已退出，请重新连接')
        self._generation_sessions.clear()
        self._session_generations.clear()
        return Path(__file__).with_name('event_probe_native_pc.js').read_text(encoding='utf-8')

    def _request(self, operation, *, timeout=None, **arguments):
        result = super()._request(operation, timeout=timeout, **arguments)
        if operation not in ('mark', 'read') or result.get('error') or result.get('stopped'):
            return result
        if result.get('retry'):
            raise ObserverReadRetry('官服 PC Frida 战局或时钟正在变化，请重试当前采集')
        generation = result.get('native_generation')
        if type(generation) is not int or generation < 0:
            raise RuntimeError('官服 PC Frida 缺少事件世代')
        frame = result.get('native_frame', result.get('frame'))
        fixed_time = result.get('native_fixed_time')
        if (not isinstance(fixed_time, str) or not fixed_time.isdecimal()
                or len(fixed_time) > 20 or int(fixed_time) > 0x7FFFFFFFFFFFFFFF):
            raise RuntimeError('官服 PC Frida 缺少精确逻辑时钟')
        game_time = int(fixed_time) / (1 << 32)
        if operation == 'mark' and (result.get('frame') != frame or result.get('game_time') != game_time):
            raise RuntimeError('官服 PC Frida 标记与原始逻辑时钟不一致')
        from collector import ProcessChanged, SampleUnavailable, UnsupportedVersion
        try:
            binding = self.collector.observer_binding(
                frame=frame, game_time=game_time, native_identity=result.get('native_identity'),
                native_stage_id=result.get('native_stage_id'),
                native_process_pid=result.get('native_process_pid'), native_fixed_time=fixed_time)
        except (ProcessChanged, UnsupportedVersion):
            raise
        except SampleUnavailable as exc:
            raise ObserverReadRetry(str(exc)) from exc
        session = binding['native_session']
        previous = self._generation_sessions.get(generation)
        if previous is not None and previous != session:
            raise RuntimeError('官服 PC Frida 事件世代与已确认战局不一致')
        previous_generation = self._session_generations.get(session)
        if previous_generation is not None and previous_generation != generation:
            raise RuntimeError('官服 PC 原生战局未能区分已观察到的新事件世代，当前事件覆盖已中断')
        self._generation_sessions[generation] = session
        self._session_generations[session] = generation
        return dict(result, **binding)
