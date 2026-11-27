"""Actual-engine concurrent owned-memory supply, never a game process.

Keeps unsuccessful high-load results. Exit status distinguishes a failed target
from a broken harness; neither is converted into successful acceptance.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from production_hp_actual_engine_bundle import engine_bundle, ROOT


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--count', type=int, required=True)
    p.add_argument('--rate', type=int, required=True)
    p.add_argument('--pause-ms', type=int, default=0)
    p.add_argument('--pause-at-ms', type=int, default=1000)
    p.add_argument('--pub-every', type=int, default=1000)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    digest = lambda b: hashlib.sha256(b).hexdigest()
    build = json.loads((a.binary.parent/'build-receipt.json').read_text())
    assert build['binary_sha256'] == digest(a.binary.read_bytes())
    for name, value in build['source_sha256'].items():
        assert digest((ROOT/name).read_bytes()) == value, ('compiled source drift', name)
    script = a.output/'paced.js'
    source = engine_bundle(a.count, True, pub_every=a.pub_every, paced={
        'rate': a.rate, 'pause_ms': a.pause_ms, 'pause_at_ms': a.pause_at_ms})
    script.write_text(source, encoding='utf-8')
    run = subprocess.run([str(a.binary.resolve()), str(script.resolve())], cwd=ROOT,
                         capture_output=True, text=True, timeout=70)
    (a.output/'stdout.json').write_text(run.stdout, encoding='utf-8')
    (a.output/'stderr.log').write_text(run.stderr, encoding='utf-8')
    assert run.returncode == 0, (run.returncode, run.stderr)
    result = json.loads(run.stdout)
    report = result['report']
    events = json.loads(result.pop('event_json'))
    assert report['pin_mismatches'] == 0
    assert report['active_after'] == 0
    assert result['scope_fields'] == 64 and result['publication_fields'] == 1604
    assert 0 <= report['published_ticket'] <= report['closed_ticket'] <= report['processed_ticket'] <= report['raw_reserved']
    complete = report['produced_setters'] == a.count and report['failure_code'] == 0
    assert not report['failure_code'] or report['failure_code'] == 4, report
    if report['failure_code'] == 0:
        assert report['batch_issued'] == report['batch_finalized']
    else:
        # Queue exhaustion can interrupt the currently issued item. It is a
        # failed target, and its unfinalized tail must stay unacknowledged.
        assert report['batch_finalized'] <= report['batch_issued'] <= report['batch_finalized']+1
        assert report['processed_ticket'] < report['raw_reserved']
        assert report['published_ticket'] == 0
    if complete:
        assert report['raw_reserved'] == report['raw_produced_edges'] == 2*a.count
        assert report['processed_ticket'] == report['closed_ticket'] == report['published_ticket'] == 2*a.count
        assert report['native_hp_io_issued'] == 2*a.count
        assert result['source_active'] == 0 and result['source_error'] is None and result['unknown'] is False
        assert events[0]['hp_before'] == 100000 and events[-1]['hp_after'] == 100000+a.count
    supplied_rate = report['produced_setters']*1000/report['produce_ms']
    duration_target = a.count/a.rate*1000
    # OS sleep jitter is visible as max_pacing_lateness_ns. A slow source never
    # counts as a successful requested-rate test, even if consumption finishes.
    target = complete and report['produce_ms'] <= duration_target*1.02+20
    if a.pause_ms:
        target = target and report['pause_observed'] and 0 <= report['catchup_ms'] <= 2500
    receipt = {'scope': 'paced owned producer / actual engine capture-queue-prepare-finish-drain / shared production JS',
               'excluded': ['game', 'detours', 'assembly ingress', 'IPC', 'FPS', 'per-fact p99 publication acceptance'],
               'build_identity': build, 'bundle_sha256': digest(source.encode()),
               'event_sha256': digest(json.dumps(events, separators=(',', ':'), ensure_ascii=False).encode()),
               'event_count': len(events), 'supplied_rate': supplied_rate,
               'requested_target_pass': bool(target), 'complete': bool(complete), 'result': result}
    (a.output/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'target_pass': bool(target), 'complete': bool(complete),
                      'produced': report['produced_setters'], 'requested': a.count,
                      'supplied_rate': supplied_rate, 'code': report['failure_code'],
                      'peak_lag_edges': report['peak_lag_edges'], 'catchup_ms': report['catchup_ms'],
                      'receipt': str(a.output/'receipt.json')}, ensure_ascii=False))
    return 0 if target else 2


if __name__ == '__main__':
    raise SystemExit(main())
