from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from metrics import sanitize_native_publication_failure


def diagnostic():
    return dict(version=1, failure_code=9, reason_code=19, origin=1, sampling_stage=0,
        store_state_available=True, acknowledged=3, read_through=4, store_sequence=7,
        store_event_count=4, store_retained_bytes=1024, published_through=6,
        marks_count=2, metadata_bytes=200, max_events=100000, max_bytes=64*1024**2, max_marks=256)


def test_real_owned_store_and_journal_failure_branches():
    node=shutil.which('node')
    if node is None:pytest.skip('Node required')
    file=Path(__file__).with_name('native_publication_failure_diagnostic_fixture.js')
    result=subprocess.run([node,str(file)],capture_output=True,text=True,check=True,timeout=20)
    report=json.loads(result.stdout)
    assert len(report['cases'])==7
    assert report['live_reads']==report['live_calls']==0


def test_failure_numeric_fields_strip_private_payloads():
    d=diagnostic();raw=dict(d,error='private',path='C:/private',player={'uid':'secret'})
    assert sanitize_native_publication_failure(raw)==d


@pytest.mark.parametrize('change',[
    dict(version=True),dict(failure_code=4),dict(reason_code=23),dict(reason_code=True),
    dict(origin=0),dict(origin=5),dict(sampling_stage=8),dict(store_state_available=1),
    dict(acknowledged=5),dict(store_event_count=5),dict(metadata_bytes=2**53),
    dict(max_bytes=-1),dict(store_state_available=False),dict(read_through='4'),
])
def test_invalid_failure_diagnostic_is_unavailable(change):
    assert sanitize_native_publication_failure(dict(diagnostic(),**change)) is None


def test_existing_error_envelope_transports_diagnostic_and_keeps_poison():
    from test_event_queue_memory import error_pipe
    pipe=error_pipe(dict(error='native capture failed: code=9',publication_failure=dict(diagnostic(),path='secret')))
    with pytest.raises(RuntimeError) as caught:pipe.request('mark')
    assert caught.value.publication_failure==diagnostic()
    assert pipe._poisoned


def test_success_envelope_cannot_smuggle_failure_metadata():
    from test_event_queue_memory import error_pipe
    pipe=error_pipe(dict(result={},publication_failure=diagnostic()))
    with pytest.raises(RuntimeError,match='envelope mismatch'):pipe.request('mark')
    assert pipe._poisoned


@pytest.mark.parametrize('detailed',[False,True])
def test_same_finite_diagnostic_survives_normal_and_debug_journals(tmp_path,detailed):
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,debug_mode=lambda:detailed,
            header=lambda *a,**k:dict(kind='log_header',data={})) as journal:
        journal.write('native_hook_request_failed',dict(op='mark',error_type='RuntimeError',
            native_failure_code=9,publication_failure=dict(diagnostic(),raw_error='private')))
    rows=[json.loads(s) for s in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['data']['publication_failure']==diagnostic()
    assert 'private' not in json.dumps(rows)


def test_probe_audit_uses_finite_error_attribute():
    from event_observer import ObserverStatus
    from native_pc.native_hook_source import NativeHookEventProbe
    from test_event_queue_memory import error_pipe
    probe=NativeHookEventProbe(SimpleNamespace(process=SimpleNamespace(process_alive=lambda:True)))
    probe.status=ObserverStatus.READY
    probe._pipe=error_pipe(dict(error='native capture failed: code=9',publication_failure=diagnostic()))
    rows=[];probe.set_request_timing_audit(lambda k,d:rows.append((k,d)))
    with pytest.raises(RuntimeError):probe._raw_request('mark')
    assert rows[-1][1]['publication_failure']==diagnostic()
    assert rows[-1][1]['native_failure_code']==9
