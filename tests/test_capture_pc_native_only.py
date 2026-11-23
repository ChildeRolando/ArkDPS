from types import SimpleNamespace

import pytest

from acceptance.capture_pc_native_only import BlockFridaImports, audit_native_wire, module_inventory


@pytest.mark.parametrize('name', ['frida', 'frida.core', 'native_pc.frida_runtime'])
def test_native_only_guard_rejects_instrumentation_dependency(name):
    with pytest.raises(RuntimeError, match='forbids Frida'):
        BlockFridaImports().find_spec(name)


def test_native_only_guard_does_not_replace_unrelated_imports():
    assert BlockFridaImports().find_spec('native_pc.native_hook_runtime') is None
    assert BlockFridaImports().find_spec('json') is None


def test_exited_process_does_not_certify_module_absence():
    result = module_inventory(SimpleNamespace(process_instance=(1, 2), process_alive=lambda: False))
    assert result['complete'] is False and result['reason'] == 'process_exited'


def test_wire_evidence_records_only_ack_and_stop_whitelisted_reply_fields():
    records, calls = [], []

    def request(operation, **arguments):
        calls.append((operation, arguments))
        return dict(acknowledged=12, active=0, native_quiet=True, error=None,
                    token='must-not-be-recorded', source='private-source')

    pipe = SimpleNamespace(request=request)
    for _ in range(2):
        audit_native_wire(pipe, lambda kind, data: records.append((kind, data)))
    pipe.request('initialize', token='must-not-be-recorded')
    pipe.request('mark')
    reply = pipe.request('ack', sequence=12)
    pipe.request('stop')
    assert reply['token'] == 'must-not-be-recorded'  # Return the actual reply unchanged.
    assert len(calls) == 4 and len(records) == 2
    assert records[0] == ('native_only_wire_evidence', dict(operation='ack', requested_sequence=12,
        outcome='reply', error_present=False, acknowledged=12, active=0, native_quiet=True))
    assert 'token' not in repr(records) and 'private-source' not in repr(records)


def test_wire_evidence_does_not_treat_boolean_sequences_as_integers():
    records = []
    pipe = SimpleNamespace(request=lambda *_a, **_k: dict(acknowledged=True, active=False, error='redacted'))
    audit_native_wire(pipe, lambda _kind, data: records.append(data))
    pipe.request('ack', sequence=True)
    assert records == [dict(operation='ack', outcome='reply', error_present=True)]


def test_wire_failure_keeps_error_type_not_arbitrary_exception_text():
    records = []

    def request(*_args, **_kwargs):
        raise TimeoutError('do-not-log-this-authentication-text')

    pipe = SimpleNamespace(request=request)
    audit_native_wire(pipe, lambda _kind, data: records.append(data))
    with pytest.raises(TimeoutError):
        pipe.request('ack', sequence=8)
    assert records == [dict(operation='ack', requested_sequence=8, outcome='error', error_type='TimeoutError')]
