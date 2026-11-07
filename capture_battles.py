"""Capture the product lifecycle path for deterministic replay and live QA.

This module observes only. Stage entry/deployment/pause stay with MAA or the
player. A capture does not claim that unknown results or practice flags are zero.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from battle_lifecycle import BattleObservation, BattleRuntime, BattleSessions
from collector import Collector, BattleChanged, DEFAULT_ADB, DEFAULT_SERIAL
from roster import CharacterEntry, RosterSnapshot
from event_capture import CaptureRetry
from snapshot_adapter import AdapterError


def capture(seconds, output, adb=DEFAULT_ADB, serial=DEFAULT_SERIAL):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    runtime = BattleRuntime()
    collector = None
    deadline = time.monotonic() + seconds
    reconnect_at = 0.
    summary = dict(feed=runtime.feed.id, samples=0, archives=0, errors=0,
                   adb=adb, serial=serial)
    # Never silently replace an earlier evidence capture.
    with (output / 'observations.jsonl').open('x', encoding='utf-8') as stream:
        def write(record):
            stream.write(json.dumps(record, ensure_ascii=False, default=sorted) + '\n')
            stream.flush()

        write({'header': summary.copy()})
        try:
            while time.monotonic() < deadline:
                if collector is None:
                    if time.monotonic() < reconnect_at:
                        time.sleep(.2)
                        continue
                    collector = Collector(adb, serial)
                    try:
                        collector.connect(lifecycle=True)
                        write({'connected': dict(
                            pid=collector.core.mc.pid,
                            process_instance=collector.core.mc.process_instance,
                            version=collector.diagnostics.version_actual)})
                        print('lifecycle_ready', flush=True)
                    except Exception as exc:
                        gap = runtime.disconnected(str(exc))
                        summary['errors'] += 1
                        write({'observation': asdict(gap), 'connection': 'disconnected',
                               'error': str(exc), 'phase': 'connect'})
                        collector.close()
                        collector = None
                        reconnect_at = time.monotonic() + 2
                        continue
                try:
                    observation = runtime.poll(collector)
                    write({'observation': asdict(observation)})
                    summary['samples'] += int(observation.kind == 'sample')
                except CaptureRetry as exc:
                    write({'retry': str(exc)})
                except Exception as exc:
                    summary['errors'] += 1
                    # poll already issued the gap, preserve that exact sequence
                    # in replay without recording the exception as a scene exit.
                    gap = BattleObservation(runtime.feed.id, runtime.feed.sequence,
                                            runtime.feed.generation, 'gap',
                                            reason='read_unavailable')
                    write({'observation': asdict(gap), 'error': str(exc)})
                    if isinstance(exc, (BattleChanged, OSError)):
                        gap = runtime.disconnected(str(exc))
                        write({'observation': asdict(gap), 'connection': 'disconnected'})
                        collector.close()
                        collector = None
                        reconnect_at = time.monotonic() + 2
                for archive in runtime.sessions.pop_archives():
                    summary['archives'] += 1
                    write({'archive_ready': archive})
                time.sleep(.2)
        finally:
            if collector is not None:
                collector.close()
            gap = runtime.disconnected('capture_stopped')
            write({'observation': asdict(gap), 'connection': 'disconnected'})
            summary['last_state'] = runtime.sessions.live()
            write({'summary': summary})
            (output / 'summary.json').write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def replay(path):
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    if lines and 'kind' in json.loads(lines[0]):
        return replay_product(lines)
    sessions = None
    pending = []
    samples = archives = 0
    finished = False
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        value = json.loads(line)
        if finished:
            raise ValueError('records after capture summary')
        if 'header' in value:
            if sessions is not None:
                raise ValueError('duplicate capture header')
            sessions = BattleSessions(value['header']['feed'])
        elif 'observation' in value:
            raw = value['observation']
            roster = raw.get('roster')
            if roster is not None:
                raw['roster'] = RosterSnapshot(
                    frame=roster['frame'],
                    entries=tuple(CharacterEntry(**entry) for entry in roster['entries']),
                    present_unique_ids=frozenset(roster['present_unique_ids']),
                    present_known=roster['present_known'])
            sessions.consume(BattleObservation(**raw))
            if 'connection' in value:
                sessions.connection = value['connection']
            samples += int(raw['kind'] == 'sample')
            pending.extend(sessions.pop_archives())
        elif 'archive_ready' in value:
            if (not pending or json.dumps(pending.pop(0), sort_keys=True) !=
                    json.dumps(value['archive_ready'], sort_keys=True)):
                raise ValueError('archive differs from replayed lifecycle/statistics')
            archives += 1
        elif 'summary' in value:
            expected = value['summary']
            if (samples != expected['samples'] or archives != expected['archives'] or
                    json.dumps(sessions.live(), sort_keys=True) !=
                    json.dumps(expected['last_state'], sort_keys=True)):
                raise ValueError('summary differs from replay')
            finished = True
    if sessions is None or pending or not finished:
        raise ValueError('missing header, summary or archive handoff')
    return dict(samples=samples, archives=archives, last_state=sessions.live())


def observation_from_json(raw):
    raw = dict(raw)
    roster = raw.get('roster')
    if roster is not None:
        raw['roster'] = RosterSnapshot(
            frame=roster['frame'], entries=tuple(CharacterEntry(**entry) for entry in roster['entries']),
            present_unique_ids=frozenset(roster['present_unique_ids']), present_known=roster['present_known'])
    return BattleObservation(**raw)


def replay_product(lines):
    """Verify the GUI/CLI journal, including event drains, publications and handoffs."""
    def equal(left, right):
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    sessions = None
    pending = []
    samples = archives = 0
    finished = False
    failures = 0
    for line in lines:
        record = json.loads(line)
        kind, data = record['kind'], record['data']
        if kind == 'lifecycle_header':
            if sessions is not None:
                raise ValueError('duplicate lifecycle header')
            sessions = BattleSessions(data['feed'])
        elif kind == 'lifecycle_observation':
            if sessions is None or finished:
                raise ValueError('observation outside lifecycle journal')
            observation = observation_from_json(data)
            try:
                sessions.consume(observation)
            except (AdapterError, ValueError):
                # The following recorded gap must close the failed read. It
                # cannot publish a value or silently erase an archive signal.
                failures += 1
                continue
            if observation.kind == 'gap':
                failures = 0
                if observation.reason == 'capture_stopped':
                    sessions.connection = 'disconnected'
            samples += int(observation.kind == 'sample')
            pending.extend(sessions.pop_archives())
        elif kind == 'archive_ready':
            if not pending or not equal(pending.pop(0), data):
                raise ValueError('archive differs from replayed lifecycle/events')
            archives += 1
        elif kind == 'published':
            if sessions is None or failures or not equal(sessions.adapter.result(), data['statistics']):
                raise ValueError('published statistics differ from replay')
            if data['session'] != sessions.battle_id:
                raise ValueError('published battle ID differs from lifecycle')
        elif kind == 'lifecycle_summary':
            if sessions is None or not equal(sessions.live(), data):
                raise ValueError('summary differs from replay')
            finished = True
    if sessions is None or pending or not finished or failures:
        raise ValueError('missing lifecycle header, summary or archive handoff')
    return dict(samples=samples, archives=archives, last_state=sessions.live())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--output')
    parser.add_argument('--adb', default=DEFAULT_ADB)
    parser.add_argument('--serial', default=DEFAULT_SERIAL)
    parser.add_argument('--replay')
    args = parser.parse_args()
    if args.replay:
        result = replay(args.replay)
    else:
        if not args.output or args.seconds <= 0:
            parser.error('capture requires --output and positive --seconds')
        result = capture(args.seconds, args.output, args.adb, args.serial)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
