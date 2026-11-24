import sys
from types import SimpleNamespace

import pytest

from connection import VersionStatus
from collection_session import CollectionSession
from event_observer import FridaEventObserver, NativePcFridaObserver, ObserverReadRetry, ObserverStatus


def collector(status):
    return SimpleNamespace(diagnostics=SimpleNamespace(connection_ok=True, version_status=status))


def test_incompatible_version_never_attaches():
    observer = FridaEventObserver(collector(VersionStatus.INCOMPATIBLE), 'unused')
    with pytest.raises(RuntimeError):
        observer.start()
    assert observer.status == ObserverStatus.INCOMPATIBLE


@pytest.mark.parametrize('native_error, expected', [
    ('Process is not using emulation', '请重启设备端 Frida 服务后重连观察者'),
    ('Unexpected realm failure', 'Unexpected realm failure'),
])
def test_emulated_attach_failure_keeps_native_realm_untouched(monkeypatch, native_error, expected):
    class NotSupportedError(Exception):
        pass

    realms = []

    def attach(pid, *, realm):
        realms.append((pid, realm))
        raise NotSupportedError(native_error)

    device = SimpleNamespace(attach=attach)
    manager = SimpleNamespace(add_remote_device=lambda remote: device)
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(
        __version__='17.26.1', get_device_manager=lambda: manager,
        NotSupportedError=NotSupportedError,
    ))
    active = collector(VersionStatus.COMPATIBLE)
    active.core = SimpleNamespace(mc=SimpleNamespace(pid=17561))
    observer = FridaEventObserver(active, '127.0.0.1:27043')
    with pytest.raises((NotSupportedError, RuntimeError), match=expected):
        observer.start()
    assert observer.status == ObserverStatus.FAILED
    assert expected in observer.error
    assert realms == [(17561, 'emulated')]
    assert observer.session is observer.script is None


def test_unload_failure_still_detaches_own_session():
    calls = []

    class Script:
        is_destroyed = False

        def unload(self):
            calls.append('unload')
            raise RuntimeError('script gone')

    class Session:
        is_detached = False

        def detach(self):
            calls.append('detach')

    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    observer.script, observer.session = Script(), Session()
    with pytest.raises(RuntimeError):
        observer.stop()
    assert calls == ['unload', 'detach']
    assert observer.status == ObserverStatus.STOPPED


@pytest.mark.parametrize('already_detached', [True, False])
def test_stop_after_target_exit_is_idempotent(monkeypatch, already_detached):
    class InvalidOperationError(Exception):
        pass

    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(InvalidOperationError=InvalidOperationError))
    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    calls = []

    class Script:
        is_destroyed = already_detached

        def unload(self):
            pytest.fail('destroyed script was unloaded')

        def stop(self):
            calls.append('stop')
            self.is_destroyed = True
            session.is_detached = True
            raise InvalidOperationError('script destroyed during stop')

        @property
        def exports_sync(self):
            return self

    class Session:
        is_detached = already_detached

        def detach(self):
            pytest.fail('detached session was detached again')

    session = Session()
    observer.script, observer.session = Script(), session
    observer.status = ObserverStatus.READY
    observer.stop()
    observer.stop()
    assert calls == ([] if already_detached else ['stop'])
    assert observer.script is observer.session is None
    assert observer.status == ObserverStatus.STOPPED


def test_read_and_ack_timeouts_reuse_the_committed_cursor():
    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    observer.status = ObserverStatus.READY
    observer.request_timeout = 0
    requests = []
    missed = {}

    def response(request, result):
        observer._message({'type': 'send', 'payload': {
            'kind': 'arkdps-event-response', 'id': request['id'], 'result': result,
        }}, None)

    def post(request):
        requests.append(request)
        operation = request['operation']
        if operation not in missed:
            missed[operation] = request
            return
        response(missed[operation], {'error': 'late response'})
        if operation == 'read':
            response(request, {'events': list(range(request['after'] + 1, request['through'] + 1))})
        else:
            response(request, {'acknowledged': request['sequence'], 'error': None})

    observer.script = SimpleNamespace(post=post, is_destroyed=False)
    with pytest.raises(ObserverReadRetry, match='response timed out'):
        observer.read_through(2)
    assert observer.read_through(3)['events'] == [1, 2, 3]
    observer.commit_events(3)
    assert observer.read_through(4)['events'] == [4]
    observer.commit_events(4)
    observer.commit_events(4)
    assert [(r['operation'], r.get('after'), r.get('through'), r.get('sequence')) for r in requests] == [
        ('read', 0, 2, None), ('read', 0, 3, None), ('ack', None, None, 3),
        ('read', 3, 4, None), ('ack', None, None, 4),
    ]
    assert observer.status == ObserverStatus.READY


def test_terminal_mark_uses_short_deadline_without_changing_normal_requests(monkeypatch):
    observer = FridaEventObserver(collector(VersionStatus.COMPATIBLE), 'unused')
    requests = []

    def request(operation, **arguments):
        requests.append((operation, arguments))
        return {'terminal_boundary': {'sequence': 1}}

    monkeypatch.setattr(observer, '_request', request)
    assert observer.read_terminal_mark() == {'terminal_boundary': {'sequence': 1}}
    observer.mark()
    assert requests == [('mark', {'timeout': .5}), ('mark', {})]


def test_native_baseline_reads_only_the_watermark_before_releasing_the_prefix(monkeypatch):
    observer = NativePcFridaObserver(collector(VersionStatus.COMPATIBLE))
    requests = []

    def request(operation, **arguments):
        requests.append((operation, arguments))
        if operation == 'read':
            return {'events': list(range(arguments['after'] + 1, arguments['through'] + 1))}
        return {'acknowledged': arguments['sequence']}

    monkeypatch.setattr(observer, '_request', request)
    assert observer.read_baseline(50)['events'] == []
    assert requests == [('read', {'after': 50, 'through': 50})]
    observer.commit_events(50)
    assert observer.read_through(51)['events'] == [51]
    assert requests[1:] == [('ack', {'sequence': 50}), ('read', {'after': 50, 'through': 51})]


def native_collector():
    value = collector(VersionStatus.COMPATIBLE)
    value.process = SimpleNamespace(pid=123, process_instance=(123, 456), process_alive=lambda: True)
    return value


@pytest.mark.parametrize('platform', ('native_pc', 'mumu'))
def test_observer_reads_scope_preference_at_start_and_existing_marks(monkeypatch, platform):
    value = native_collector()
    value.diagnostics.platform = platform
    observer = NativePcFridaObserver(value) if platform == 'native_pc' else FridaEventObserver(value, 'fixture')
    calls, sources = [], []
    choices = [False]
    monkeypatch.setattr(observer, 'start', lambda: sources.append(observer._probe_source()))
    if platform == 'native_pc':
        monkeypatch.setattr(observer.binding, 'bind', lambda _op, result: result)
        # This case checks preference transport, not terminal proof admission.
        monkeypatch.setattr(observer.binding, 'bind_terminal_mark', lambda result: result)

    def request(self, operation, *, timeout=None, **arguments):
        calls.append((operation, timeout, arguments))
        return {}

    monkeypatch.setattr(FridaEventObserver, '_request', request)
    session = CollectionSession(None, None, collector=value, observer_factory=lambda *_: observer,
                                frida_remote=None, autochess_enabled=lambda: choices[0])
    assert session._attach_observer()
    assert sources[0].startswith('globalThis.arkdpsAutoChessMode = "local";\n')
    observer.mark()
    choices[0] = True
    observer.mark()
    observer.read_terminal_mark()
    observer.read_through(42)
    assert calls == [
        ('mark', None, {'autochess_enabled': False}),
        ('mark', None, {'autochess_enabled': True}),
        ('mark', observer.terminal_mark_timeout, {'autochess_enabled': True}),
        ('read', None, {'after': 0, 'through': 42}),
    ]


def test_native_pc_uses_local_native_device_without_android_endpoint(monkeypatch):
    calls = []
    attached = SimpleNamespace()
    device = SimpleNamespace(attach=lambda pid, **kwargs: calls.append((pid, kwargs)) or attached)
    frida = SimpleNamespace(get_local_device=lambda: device)
    observer = NativePcFridaObserver(native_collector())
    assert observer._attach_session(frida) is attached
    assert calls == [(123, {'realm': 'native'})]
    with pytest.raises(ValueError, match='frida_remote'):
        NativePcFridaObserver(observer.collector, '127.0.0.1:27043')


def test_native_pc_start_loads_pc_probe_and_reuses_full_cleanup(monkeypatch):
    calls = []
    class Script:
        is_destroyed = False

        def on(self, event, callback):
            assert event == 'message'
            self.notify = callback

        def load(self):
            self.notify({'type': 'send', 'payload': {'ready': True, 'arch': 'x64'}}, None)

        def stop(self):
            calls.append('stop')

        @property
        def exports_sync(self):
            return self

        def unload(self):
            self.is_destroyed = True
            calls.append('unload')

    class Session:
        is_detached = False

        def on(self, event, callback):
            assert event == 'detached'

        def create_script(self, source):
            assert "Process.platform!=='windows'" in source and 'GameAssembly.dll' in source
            return Script()

        def detach(self):
            self.is_detached = True
            calls.append('detach')

    frida = SimpleNamespace(__version__='17.26.1', get_local_device=lambda: SimpleNamespace(
        attach=lambda pid, **kwargs: Session()))
    monkeypatch.setitem(sys.modules, 'frida', frida)
    observer = NativePcFridaObserver(native_collector())
    observer.start()
    assert observer.status == ObserverStatus.READY
    observer.stop()
    assert calls == ['stop', 'unload', 'detach']
    assert observer.script is observer.session is None


@pytest.mark.parametrize('hp,nested,enabled', [('1', '1', True), ('1', '0', False), ('0', '1', False)])
def test_native_provider_diagnostic_prefixes_are_symmetric_and_nested_requires_hp(monkeypatch, hp, nested, enabled):
    from native_pc.native_hook_source import _native_source
    from pathlib import Path
    monkeypatch.setenv('ARKDPS_TRACE_HP_CONTEXT', hp)
    monkeypatch.setenv('ARKDPS_TRACE_NESTED_PARENT', nested)
    monkeypatch.setenv('ARKDPS_COALESCE_ENEMY_HP', '0')
    sources = []
    observer = NativePcFridaObserver(native_collector())

    class Script:
        def on(self, _event, notify):
            self.notify = notify

        def load(self):
            self.notify({'type': 'send', 'payload': {'ready': True, 'arch': 'x64'}}, None)

    script = Script()
    session = SimpleNamespace(on=lambda *_: None,
        create_script=lambda source: (sources.append(source) or script))
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace(__version__='17.26.1'))
    monkeypatch.setattr(observer, '_attach_session', lambda _: session)
    observer.start()
    assert sources == [_native_source(Path(__file__).resolve().parents[1])[1]]
    assert ('globalThis.arkdpsTraceNestedParent = true;\n' in sources[0]) is enabled


def test_native_pc_permission_failure_names_admin_requirement():
    class PermissionDeniedError(Exception):
        pass

    def attach(*_args, **_kwargs):
        raise PermissionDeniedError('access denied')

    frida = SimpleNamespace(PermissionDeniedError=PermissionDeniedError,
                            get_local_device=lambda: SimpleNamespace(attach=attach))
    observer = NativePcFridaObserver(native_collector())
    with pytest.raises(RuntimeError, match='管理员权限'):
        observer._attach_session(frida)


def test_native_pc_loader_timeout_is_distinct_from_permission_or_layout_failure():
    class PermissionDeniedError(Exception):
        pass

    class ProcessNotRespondingError(Exception):
        pass

    def attach(*_args, **_kwargs):
        raise ProcessNotRespondingError('agent loader failed')

    frida = SimpleNamespace(PermissionDeniedError=PermissionDeniedError,
                            ProcessNotRespondingError=ProcessNotRespondingError,
                            get_local_device=lambda: SimpleNamespace(attach=attach))
    observer = NativePcFridaObserver(native_collector())
    with pytest.raises(RuntimeError, match='代理会话未建立.*尚未执行.*agent loader failed'):
        observer._attach_session(frida)


def test_native_pc_rechecks_owned_process_before_attach():
    value = native_collector()
    value.process.process_alive = lambda: False
    with pytest.raises(RuntimeError, match='已退出'):
        NativePcFridaObserver(value)._attach_session(SimpleNamespace())


def test_native_pc_mark_binds_raw_clock_and_rejects_generation_reassignment(monkeypatch):
    value = native_collector()
    binding = {'native_session': '123:456:first-battle', 'source_build': 'checked-build'}
    seen = []
    value.observer_binding = lambda **kwargs: seen.append(kwargs) or dict(binding)
    reply = dict(native_process_pid=123, native_generation=1, native_identity={'controller': '0x10000'},
                 native_stage_id='stage', native_frame=30, native_fixed_time=str(1 << 32),
                 frame=30, game_time=1.0, sequence=4, epoch=8, active=0)
    monkeypatch.setattr(FridaEventObserver, '_request', lambda *args, **kwargs: dict(reply))
    observer = NativePcFridaObserver(value)
    result = observer.mark()
    assert result['native_session'] == binding['native_session']
    assert seen[-1] == dict(frame=30, game_time=1.0, native_identity=reply['native_identity'],
                            native_stage_id='stage', native_process_pid=123, native_fixed_time=str(1 << 32))
    reply['native_generation'] = 2
    with pytest.raises(RuntimeError, match='新事件世代'):
        observer.mark()
    binding['native_session'] = '123:456:second-battle'
    assert observer.mark()['native_session'] == binding['native_session']
    binding['native_session'] = '123:456:third-battle'
    with pytest.raises(RuntimeError, match='已确认战局'):
        observer.read_through(4)


def test_native_pc_stale_pair_is_retryable_and_unstable_mark_is_retained(monkeypatch):
    from collector import SampleUnavailable

    value = native_collector()
    def pending(**kwargs):
        raise SampleUnavailable('new lifecycle pending')
    value.observer_binding = pending
    reply = dict(native_process_pid=123, native_generation=1, native_frame=0,
                 native_fixed_time='0', frame=0, game_time=0.0)
    monkeypatch.setattr(FridaEventObserver, '_request', lambda *args, **kwargs: dict(reply))
    observer = NativePcFridaObserver(value)
    with pytest.raises(ObserverReadRetry, match='lifecycle pending'):
        observer.mark()
    reply.clear()
    reply.update(retry=True, error=None)
    with pytest.raises(ObserverReadRetry, match='正在变化'):
        observer.mark()
