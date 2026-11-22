"""Credentials, in-memory ownership and public waiting state commit together."""
import json
from pathlib import Path
from threading import Event, Thread

import pytest

from test_native_pc_frida_runtime import add_new, arm, environment, wait_for


def prepared_owner(environment, entry):
    first = arm(environment)
    pid = add_new(environment, first)
    lease = first.wait_ready(2)
    external = None
    if entry == 'moved_dead_lease':
        first.close()
        copied = environment.gadget.parent / 'external-original'
        copied.mkdir()
        external = copied / environment.gadget.with_suffix('.config').name
        external.write_bytes(first.config_path.read_bytes())
        changed = dict(lease, gadget_path=str(copied / environment.gadget.name))
        first.lease_path.write_text(json.dumps(changed), encoding='utf-8')
        runtime = environment.make()
    else:
        runtime = first
    return runtime, pid, external


@pytest.mark.parametrize('entry', ['moved_dead_lease', 'owned_process_exit'])
@pytest.mark.parametrize('deletion', ['config', 'lease'])
def test_waiting_snapshot_cannot_escape_an_inflight_credential_deletion(
        environment, monkeypatch, entry, deletion):
    runtime, pid, external = prepared_owner(environment, entry)
    entered, release, reader_entered, reader_done = Event(), Event(), Event(), Event()
    unlink = Path.unlink
    target = runtime.config_path if deletion == 'config' else runtime.lease_path

    def blocked_unlink(path, *args, **kwargs):
        if path == target:
            entered.set()
            assert release.wait(2), 'test did not release the controlled disk operation'
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', blocked_unlink)
    environment.api.rows[pid].alive = False
    if entry == 'moved_dead_lease':
        runtime.start()
    snapshots = []

    def read_snapshot():
        reader_entered.set()
        snapshots.append((runtime.describe(), runtime.config_path.exists(), runtime.lease_path.exists()))
        reader_done.set()

    reader = None
    try:
        assert entered.wait(2), 'watcher did not reach credential deletion'
        # Deterministically expose the old bug: before this fix describe() could
        # acquire this lock and return WAITING while unlink was still blocked.
        acquired = runtime._condition.acquire(blocking=False)
        stale = None
        if acquired:
            try:
                stale = runtime.describe()
            finally:
                runtime._condition.release()
        assert not acquired, stale
        reader = Thread(target=read_snapshot)
        reader.start()
        assert reader_entered.wait(2)
    finally:
        release.set()
        if reader is not None:
            reader.join(2)
    assert reader_done.is_set() and reader is not None and not reader.is_alive()
    state, config_exists, lease_exists = snapshots[0]
    assert state['status'] == 'waiting_for_process' and state['lease'] is None
    assert not config_exists and not lease_exists
    assert len(environment.loaders.loads) == 1
    if external is not None:
        assert external.exists()


@pytest.mark.parametrize('deletion', ['config', 'lease'])
@pytest.mark.parametrize('failure_count', [1, 2])
def test_deletion_failure_is_explicit_and_retains_ownership_for_retry(
        environment, monkeypatch, deletion, failure_count):
    runtime, pid, _ = prepared_owner(environment, 'owned_process_exit')
    previous = runtime.describe()
    unlink = Path.unlink
    target = runtime.config_path if deletion == 'config' else runtime.lease_path
    failed = []
    failed_poll = Event()
    failed_snapshots = []
    step = runtime._step

    def observe_failed_poll():
        was_failed = runtime.status.value == 'failed'
        result = step()
        if was_failed:
            failed_snapshots.append(runtime.describe())
            failed_poll.set()
        return result

    def fail_once(path, *args, **kwargs):
        if path == target and len(failed) < failure_count:
            failed.append(path)
            raise PermissionError('controlled owned credential unlink failure')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    monkeypatch.setattr(runtime, '_step', observe_failed_poll)
    environment.api.rows[pid].alive = False
    state = wait_for(runtime, lambda value: value['status'] == 'failed')
    assert state['code'] == 'credential_cleanup_failed'
    assert state['lease'] == previous['lease'] and state['expected_resident'] is False
    assert runtime.lease_path.exists()
    assert runtime.config_path.exists() is (deletion == 'config')
    assert failed_poll.wait(2), 'watcher did not poll the explicit-failure state'
    assert failed_snapshots[0]['status'] == 'failed'
    assert failed_snapshots[0]['lease'] == previous['lease']
    assert runtime.lease_path.exists()
    assert runtime.config_path.exists() is (deletion == 'config')
    if failure_count == 2:
        failed_poll.clear()
        failed_snapshots.clear()
        assert runtime.retry()
        state = wait_for(runtime, lambda value: value['status'] == 'failed')
        assert failed_poll.wait(2), 'watcher did not retain the second failed cleanup'
        assert failed_snapshots[0]['status'] == 'failed'
        assert failed_snapshots[0]['lease'] == previous['lease']
        assert runtime.lease_path.exists()
        assert runtime.config_path.exists() is (deletion == 'config')
        assert len(failed) == 2
    assert runtime.retry()
    state = wait_for(runtime, lambda value: value['status'] == 'waiting_for_process')
    assert state['lease'] is None and state['armed_filetime'] == previous['armed_filetime']
    assert not runtime.config_path.exists() and not runtime.lease_path.exists()
    assert len(environment.loaders.loads) == 1
