"""Foreground capture through the same collection session used by the GUI."""
from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from collection_session import CollectionSession
from event_capture import CaptureRetry


def json_value(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'unsupported evidence value: {type(value).__name__}')


def write_json(path, value):
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2, default=json_value, allow_nan=False)


def run_capture(args, *, session_factory=CollectionSession):
    root = Path(__file__).resolve().parent
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if args.expect_head and head != args.expect_head:
        raise RuntimeError(f'candidate mismatch: expected {args.expect_head}, got {head}')
    if args.expect_head:
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                        cwd=root, text=True).strip()
        if dirty:
            raise RuntimeError('candidate worktree has uncommitted changes')
    if args.stop_file and args.stop_file.exists():
        raise RuntimeError('stop file already exists; choose a fresh run path')
    dest = args.output_dir or root / 'runtime' / f'capture-{time.time_ns()}'
    dest.mkdir(parents=True, exist_ok=True)
    if any((dest / name).exists() for name in ('capture.jsonl', 'READY.json', 'summary.json',
                                             'maa-job.json', 'maa-callbacks.jsonl')):
        raise RuntimeError('capture evidence already exists; choose a fresh output directory')
    metadata = {'head': head, 'stage': args.stage, 'frida_remote': args.frida_remote,
                'adb': args.adb, 'serial': args.serial, 'started_at': time.time(),
                'capture_seconds_after_baseline': args.capture_seconds}
    if args.maa_job:
        job = args.maa_job.read_bytes()
        metadata['maa_job_sha256'] = hashlib.sha256(job).hexdigest()
        (dest / 'maa-job.json').write_bytes(job)
    counts = {'samples': 0, 'retries': 0}
    last_sample = None
    first_frame = None
    failure = None
    with (dest / 'capture.jsonl').open('x', encoding='utf-8') as journal:
        def audit(kind, data):
            if kind == 'connected':
                metadata.update(pid=data['pid'], process_instance=data.get('process_instance'),
                                game_version=data['diagnostics'].get('version_actual'))
            record = {'kind': kind, 'wall': time.time(), 'data': data}
            journal.write(json.dumps(record, ensure_ascii=False, default=json_value, allow_nan=False) + '\n')
            journal.flush()
            if kind == 'observer_ready' or kind == 'connected' and not args.frida_remote:
                write_json(dest / 'READY.json', {**metadata, 'status': kind})
                print('READY', dest / 'READY.json', flush=True)

        session = session_factory(args.adb, args.serial, frida_remote=args.frida_remote, audit=audit)
        audit('metadata', metadata)
        try:
            session.connect()
            session.locate(wait_seconds=args.wait_battle_seconds,
                           cancelled=(lambda: args.stop_file.exists()) if args.stop_file else None)
            baseline_deadline = time.monotonic() + args.baseline_timeout
            end = None
            while end is None or time.monotonic() < end:
                if args.stop_file and args.stop_file.exists():
                    break
                try:
                    last_sample = session.read()
                    counts['samples'] += 1
                    if end is None:
                        end = time.monotonic() + args.capture_seconds
                        first_frame = last_sample['frame']
                        print('BASELINE', last_sample['frame'], last_sample['game_time'], flush=True)
                    if args.frida_remote and last_sample['event_status'] not in ('recording', 'finished'):
                        raise RuntimeError(last_sample.get('event_error') or 'event coverage is unavailable')
                except CaptureRetry as exc:
                    counts['retries'] += 1
                    audit('retry', {'reason': str(exc)})
                    if end is None and time.monotonic() >= baseline_deadline:
                        raise RuntimeError('no reliable baseline before timeout') from exc
                time.sleep(.1)
            if not counts['samples']:
                raise RuntimeError('capture stopped without a reliable baseline')
            if args.require_progress and last_sample['frame'] == first_frame:
                raise RuntimeError('capture contains no observed battle progression')
        except BaseException as exc:
            failure = f'{type(exc).__name__}: {exc}'
            audit('failure', {'reason': failure})
        finally:
            try:
                session.close()
            except Exception as exc:
                failure = failure or f'cleanup failed: {exc}'
                audit('cleanup_error', {'reason': str(exc)})
            final = session.capture.adapter.result() if session.capture is not None else None
            summary = {**metadata, **counts, 'ended_at': time.time(), 'error': failure,
                       'last_sample': last_sample, 'final_statistics': final}
            if args.maa_log and args.maa_log.exists():
                if args.maa_log.resolve() != (dest / 'maa-callbacks.jsonl').resolve():
                    shutil.copyfile(args.maa_log, dest / 'maa-callbacks.jsonl')
                summary['maa_callbacks_sha256'] = hashlib.sha256((dest / 'maa-callbacks.jsonl').read_bytes()).hexdigest()
            write_json(dest / 'summary.json', summary)
    print('EVIDENCE', dest, flush=True)
    if failure:
        raise RuntimeError(failure)
    return summary
