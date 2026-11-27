from copy import deepcopy

import pytest

from metrics import (sanitize_native_async_capture, sanitize_native_queue_failure,
                     sanitize_native_queue_memory)
from test_native_async_performance_logging import raw_async


def memory_status():
    mib = 1024**2
    return dict(version=1, enabled=True, requested_budget_mib=1024, budget_bytes=1024*mib,
        maximum_storage_bytes=1013*mib, prepared_storage_bytes=103*mib,
        fixed_capture_working_bytes=62*mib, slot_capacity=16*2048+56*65536,
        arena_capacity=928*mib, prepared_slot_capacity=16*2048+4*65536,
        prepared_arena_capacity=96*mib, maximum_banks=56, prepared_banks=4,
        free_banks=4, trimmed=3, prepare_failures=0)


def queue_failure():
    return dict(version=1, failure_code=4, ticket=str(2**64-1), lane=3,
        attempted_bytes=4096,
        private_queue=dict(slot_capacity=2048, arena_capacity=2*1024**2,
                           used_slots=2048, used_bytes=819200),
        spill=dict(configured_banks=56, prepared_banks=56, free_banks=0,
            owned_banks=55, foreign_owned_banks=1, busy_banks=1, unprepared_banks=0,
            slot_rejections=55, byte_rejections=0, oversize_rejections=0))


def test_accounting_preserves_budget_prepared_and_non_queue_working_storage():
    value = memory_status()
    assert sanitize_native_queue_memory(value) == value
    raw = raw_async()
    raw['event_queue_memory'] = value
    raw['queue_failure'] = queue_failure()
    cleaned = sanitize_native_async_capture(raw, identity=(123, 99))
    assert cleaned['available'] is True
    assert cleaned['event_queue_memory'] == value
    assert cleaned['queue_failure']['ticket'] == str(2**64-1)
    assert sanitize_native_async_capture(cleaned, identity=(123, 99)) == cleaned


@pytest.mark.parametrize('change', [
    dict(version=True), dict(enabled=1), dict(requested_budget_mib=True),
    dict(requested_budget_mib=511), dict(requested_budget_mib=2049),
    dict(budget_bytes=1024), dict(maximum_storage_bytes=1025*1024**2),
    dict(prepared_storage_bytes=10), dict(prepared_banks=57), dict(free_banks=5),
    dict(slot_capacity=2**63), dict(prepared_slot_capacity=2**63),
])
def test_invalid_or_over_budget_accounting_is_unavailable(change):
    value = dict(memory_status(), **change)
    assert sanitize_native_queue_memory(value) is None


def test_queue_fault_keeps_uint64_precision_and_strips_unapproved_context():
    value = queue_failure()
    private = deepcopy(value)
    private['token'] = 'private token'
    private['spill']['address'] = '0x1234'
    private['private_queue']['path'] = 'C:/private'
    assert sanitize_native_queue_failure(private) == value


@pytest.mark.parametrize('change', [
    dict(failure_code=6), dict(lane=True), dict(lane=16), dict(ticket=2**64-1),
    dict(ticket=str(2**64)), dict(ticket='01'), dict(ticket='-1'),
    dict(attempted_bytes=0), dict(attempted_bytes=2**63),
    dict(private_queue=dict(slot_capacity=1, arena_capacity=1, used_slots=2, used_bytes=0)),
    dict(spill=dict(queue_failure()['spill'], free_banks=57)),
])
def test_only_bounded_overflow_snapshot_is_retained(change):
    assert sanitize_native_queue_failure(dict(queue_failure(), **change)) is None
