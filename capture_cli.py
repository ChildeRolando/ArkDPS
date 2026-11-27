"""Foreground capture through the same collection session used by the GUI."""
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from collection_session import CollectionSession
from battle_history import BattleHistory
from event_capture import CaptureRetry
from log_context import LogContext
from native_pc.event_source import PcEventSource
from native_pc.internal_entry import portable_manifest


def _connect_pc_after_bootstrap(session, bootstrap, *, timeout, cancelled, audit=None):
    """Share one wait budget across readiness and initial module/access retries.

    A zero timeout permits one immediate readiness/connection check, without
    retrying. An already running synchronous connect is not preempted.
    """
    from collector import SampleUnavailable, UnsupportedVersion
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout < 0):
        raise ValueError('PC connection timeout must be finite and nonnegative')

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise InterruptedError('collection cancelled before PC connection')

    check_cancelled()
    deadline = time.monotonic() + timeout
    lease = bootstrap.wait_ready(max(0, deadline - time.monotonic()), cancelled=cancelled)
    check_cancelled()
    if timeout > 0 and time.monotonic() >= deadline:
        raise TimeoutError('PC connection readiness exhausted the initialization wait budget')
    permission_deadline = min(deadline, time.monotonic() + 5)
    module_api_deadline = None
    attempts = 0
    while True:
        check_cancelled()
        if timeout > 0 and time.monotonic() >= deadline:
            raise TimeoutError('PC connection initialization wait budget expired')
        try:
            attempts += 1
            session.connect()
        except UnsupportedVersion:
            raise
        except SampleUnavailable:
            check_cancelled()
            diagnostic = session.collector.diagnostics
            details = diagnostic.details
            module_initializing = (diagnostic.code == 'native_unavailable'
                and details.get('reason') in ('module_not_unique', 'process_not_unique'))
            access_initializing = (diagnostic.code == 'native_permission_denied'
                                   and time.monotonic() < permission_deadline)
            module_api_initializing = (diagnostic.code == 'native_unavailable'
                and details.get('reason') == 'windows_api_failed'
                and details.get('api') in ('EnumProcessModulesEx', 'GetModuleFileNameExW', 'GetModuleInformation')
                and type(details.get('win32_error')) is int and details['win32_error'] in (6, 24, 299))
            if module_api_initializing and details['win32_error'] == 6:
                # ERROR_INVALID_HANDLE is not generally a module-list race.
                # The owning bootstrap must independently confirm the exact
                # lease birth and path is still alive; no guessed PID fallback.
                alive = getattr(bootstrap, '_alive', None)
                valid_lease = (isinstance(lease, dict) and type(lease.get('pid')) is int and lease['pid'] > 0
                    and type(lease.get('created')) is int and lease['created'] > 0)
                try:
                    module_api_initializing = valid_lease and callable(alive) and alive(lease) is True
                except Exception:
                    module_api_initializing = False
            if module_api_initializing:
                if module_api_deadline is None:
                    module_api_deadline = min(deadline, time.monotonic() + 5)
                module_api_initializing = time.monotonic() < module_api_deadline
            if (not (module_initializing or access_initializing or module_api_initializing)
                    or session.collector.process is not None or time.monotonic() >= deadline):
                raise
            retry_deadline = (min(deadline, permission_deadline) if access_initializing else
                              module_api_deadline if module_api_initializing else deadline)
            if audit is not None:
                audit('native_pc_connect_retry', dict(attempt=attempts, code=diagnostic.code,
                    reason=details.get('reason'), api=details.get('api'), win32_error=details.get('win32_error'),
                    remaining_seconds=max(0, retry_deadline - time.monotonic()),
                    process_instance=[lease['pid'], lease['created']] if isinstance(lease, dict)
                        and type(lease.get('pid')) is int and type(lease.get('created')) is int else None))
            time.sleep(min(.1, max(0, retry_deadline - time.monotonic())))
            check_cancelled()
            if time.monotonic() >= retry_deadline:
                raise
        else:
            check_cancelled()
            return


def json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        # The JSON encoder walks nested values itself. A recursive asdict
        # copy repeats the entire event batch before any bytes can be written.
        return {field.name: getattr(value, field.name) for field in fields(value)}
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


def run_capture(args, *, session_factory=CollectionSession, native_pc_observer_factory=None, state_dir=None,
                before_session_close=None, after_session_close=None, native_pc_bootstrap=None,
                baseline_poll_seconds=.1):
    """Capture, optionally coordinating native patch ownership with a trusted caller.

    before_session_close(session) may block until another observer is safely
    detached. False or an exception defers *both* session and startup-owner
    cleanup; the caller must retain them and keep the process alive as needed.
    after_session_close(session, succeeded) reports the actual close outcome.
    These hooks never manufacture a successful cleanup or event boundary.
    native_pc_bootstrap is a borrowed, already-started PC startup owner. Its
    readiness gate and bounded cold-start retries are reused, but this capture
    neither starts nor closes it; its caller retains that responsibility.
    """
    root = Path(__file__).resolve().parent
    if (type(baseline_poll_seconds) not in (int, float) or not math.isfinite(baseline_poll_seconds)
            or not .005 <= baseline_poll_seconds <= 1):
        raise ValueError('baseline poll interval must be finite and between 0.005 and 1 seconds')
    state_dir = Path(state_dir) if state_dir is not None else root / 'runtime'
    frozen = getattr(sys, 'frozen', False)
    manifest = portable_manifest(root)
    if manifest is not None:
        head = manifest.get('head')
        if not isinstance(head, str) or not re.fullmatch(r'[a-f0-9]{40}', head):
            raise RuntimeError('invalid portable candidate source identity')
    elif frozen:
        from release_identity import load_manifest
        head = load_manifest(root, verify=True)['head']
    else:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if args.expect_head and head != args.expect_head:
        raise RuntimeError(f'candidate mismatch: expected {args.expect_head}, got {head}')
    if args.expect_head and manifest is None and not frozen:
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
    platform = getattr(args, 'platform', 'mumu')
    events_enabled = args.frida_remote is None if platform == 'native_pc' else bool(args.frida_remote)
    framework_root = getattr(args, 'arkframework_root', None)
    pc_backend = getattr(args, 'native_pc_event_backend', None) or 'frida'
    pc_feed = getattr(args, 'native_pc_event_feed', None)
    log_context = LogContext(state_dir, root=root, build_head=head, connection_override={
        'platform': platform, 'adb_path': args.adb, 'serial': args.serial,
        'native_game_root': getattr(args, 'native_game_root', None), 'auto_detect': False,
    }, layout_override={'event_observation': events_enabled})
    metadata = {'head': head, 'stage': args.stage, 'frida_remote': args.frida_remote,
                'adb': args.adb, 'serial': args.serial, 'started_at': time.time(),
                'capture_seconds_after_baseline': args.capture_seconds,
                'baseline_poll_seconds': baseline_poll_seconds, 'steady_poll_seconds': .1,
                'require_events': args.require_events,
                'log_header': log_context.header('foreground_capture', debug_mode=True)['data']}
    if platform == 'native_pc':
        metadata.update(platform=platform, native_game_root=args.native_game_root,
                        frida_remote=None, event_observation=events_enabled, native_pc_event_backend=pc_backend)
        if framework_root is not None:
            metadata['arkframework_root'] = str(framework_root)
    if args.maa_job:
        job = args.maa_job.read_bytes()
        metadata['maa_job_sha256'] = hashlib.sha256(job).hexdigest()
        (dest / 'maa-job.json').write_bytes(job)
    counts = {'samples': 0, 'retries': 0}
    last_sample = None
    first_frame = None
    progressed = False
    failure = None
    with (dest / 'capture.jsonl').open('x', encoding='utf-8') as journal:
        def audit(kind, data):
            if kind == 'connected':
                environment = log_context.update_environment(data['diagnostics'])
                metadata.update(pid=data['pid'], process_instance=data.get('process_instance'),
                                game_version=data['diagnostics'].get('version_actual'),
                                log_header={**metadata['log_header'], 'environment': environment})
                data = {**data, 'environment': environment}
            record = {'kind': kind, 'wall': time.time(), 'data': data}
            journal.write(json.dumps(record, ensure_ascii=False, default=json_value, allow_nan=False) + '\n')
            journal.flush()

        audit('metadata', metadata)
        session_options = dict(frida_remote=args.frida_remote, audit=audit,
                               event_queue_memory_mib=getattr(args, 'event_queue_memory_mib', 1024))
        regeneration = None
        pc_source = PcEventSource(state_dir, observer_factory=native_pc_observer_factory, collector_root=root)
        if platform == 'native_pc':
            from native_pc_collector import NativePcCollector
            collector = NativePcCollector(args.native_game_root,
                                          collect_actions=getattr(args, 'collect_native_actions', True))
            collector.allow_event_identity_sampling = framework_root is None
            session_options.update(collector=collector, frida_remote=args.frida_remote)
            if framework_root is not None:
                from native_pc.arkframework_replay import RegenerationRecorder
                regeneration = RegenerationRecorder(Path(framework_root) / 'battle_timeline.json',
                                                     dest / 'arkframework')
        session = None

        def poll_replay(*, final=False):
            if regeneration is not None:
                regeneration.poll_reporting(time.monotonic(), audit, force=final)

        try:
            poll_replay()
            bootstrap_error = None

            def startup_failed(exc):
                nonlocal bootstrap_error
                bootstrap_error = exc
                audit('native_pc_bootstrap_error', {'reason': str(exc)})
                if args.require_events:
                    raise exc

            if platform == 'native_pc':
                provider = pc_source.select(enabled=events_enabled, game_root=args.native_game_root,
                                            backend=pc_backend, feed_path=pc_feed, on_start_error=startup_failed)
                if provider is not None:
                    session_options['observer_factory'] = provider
            session = session_factory(args.adb, args.serial, **session_options)
            if isinstance(session, CollectionSession):
                session.history = BattleHistory(state_dir / 'history')
                session.enable_lifecycle()
            pc_runtime = pc_source.runtime or (native_pc_bootstrap if platform == 'native_pc' else None)
            if pc_runtime is not None and bootstrap_error is None:
                if pc_source.runtime is None:
                    print('WAITING_PC_INIT 复用已就绪的 PC 启动组件，正在确认模块和读取权限；无需再次重启游戏。', flush=True)
                else:
                    print('WAITING_GAME 已准备 PC 事件采集，请启动游戏；已在运行时请先正常重启游戏。', flush=True)
                try:
                    _connect_pc_after_bootstrap(session, pc_runtime, timeout=args.wait_battle_seconds,
                        cancelled=(lambda: args.stop_file.exists()) if args.stop_file else None, audit=audit)
                except Exception as exc:
                    if (args.require_events or isinstance(exc, InterruptedError)
                            or not pc_runtime.is_failed):
                        raise
                    audit('native_pc_bootstrap_error', {'reason': str(exc)})
                    session.connect()
                audit('native_pc_bootstrap', pc_runtime.describe())
            else:
                session.connect()
            if isinstance(session, CollectionSession):
                session.wait_for_game_init(wait_seconds=args.wait_battle_seconds,
                    cancelled=(lambda: args.stop_file.exists()) if args.stop_file else None)
            if args.require_events and session.event_status != 'ready':
                raise RuntimeError(session.event_error or 'event coverage is unavailable')
            # Connection may have fallen back after an optional observer failure.
            # Signal readiness only after that decision and cleanup have completed.
            write_json(dest / 'READY.json', {**metadata,
                                           'status': 'observer_ready' if session.event_status == 'ready' else 'connected',
                                           'event_status': session.event_status,
                                           'event_error': session.event_error})
            print('READY', dest / 'READY.json', flush=True)
            session.locate(wait_seconds=args.wait_battle_seconds,
                           cancelled=(lambda: args.stop_file.exists()) if args.stop_file else None)
            baseline_deadline = time.monotonic() + args.baseline_timeout
            end = None
            while end is None or time.monotonic() < end:
                if args.stop_file and args.stop_file.exists():
                    break
                poll_replay()
                try:
                    last_sample = session.read()
                    counts['samples'] += 1
                    if end is None:
                        end = time.monotonic() + args.capture_seconds
                        first_frame = (last_sample.get('session'), last_sample['frame'])
                        print('BASELINE', last_sample['frame'], last_sample['game_time'], flush=True)
                    progressed |= (last_sample.get('session'), last_sample['frame']) != first_frame
                    if args.require_events and last_sample['event_status'] not in ('recording', 'finished'):
                        raise RuntimeError(last_sample.get('event_error') or 'event coverage is unavailable')
                except CaptureRetry as exc:
                    counts['retries'] += 1
                    audit('retry', {'reason': str(exc)})
                    if end is None and time.monotonic() >= baseline_deadline:
                        raise RuntimeError('no reliable baseline before timeout') from exc
                time.sleep(baseline_poll_seconds if end is None else .1)
            if not counts['samples']:
                raise RuntimeError('capture stopped without a reliable baseline')
            if args.require_progress and not progressed:
                raise RuntimeError('capture contains no observed battle progression')
        except BaseException as exc:
            failure = f'{type(exc).__name__}: {exc}'
            failure_data = {'reason': failure}
            if platform == 'native_pc' and session is not None:
                failure_data['diagnostics'] = session.collector.diagnostics.as_dict()
            audit('failure', failure_data)
        finally:
            resources = [('session', session), ('PC source', pc_source)]
            close_allowed = True
            session_close_succeeded = session is None
            if before_session_close is not None:
                try:
                    close_allowed = before_session_close(session) is not False
                except BaseException as exc:
                    close_allowed = False
                    failure = failure or f'cleanup gate failed: {exc}'
                if not close_allowed:
                    failure = failure or 'cleanup deferred by the observer ownership gate'
                    audit('cleanup_deferred', {'reason': 'observer ownership gate',
                                              'resources_retained': True})
            if close_allowed:
                for name, resource in resources:
                    if resource is None:
                        continue
                    for _ in range(2):
                        try:
                            resource.close()
                        except Exception as exc:
                            failure = failure or f'{name} cleanup failed: {exc}'
                            audit('cleanup_error', {'resource': name, 'reason': str(exc)})
                        else:
                            if name == 'session':
                                session_close_succeeded = True
                            break
            if after_session_close is not None:
                try:
                    after_session_close(session, close_allowed and session_close_succeeded)
                except BaseException as exc:
                    failure = failure or f'cleanup outcome callback failed: {exc}'
                    audit('cleanup_error', {'resource': 'cleanup outcome callback', 'reason': str(exc)})
            poll_replay(final=True)
            final = session.capture.adapter.result() if session is not None and session.capture is not None else None
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
