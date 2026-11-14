"""Capture the product lifecycle path for deterministic replay and live QA.

This module observes only. Stage entry/deployment/pause stay with MAA or the
player. A capture does not claim that unknown results or practice flags are zero.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from itertools import chain
import json
from pathlib import Path
import time

from battle_lifecycle import BattleObservation, BattleRuntime, BattleSessions, MappingRetry
from collector import Collector, BattleChanged, DEFAULT_ADB, DEFAULT_SERIAL
from desktop_audit import DesktopAuditReader
from roster import CharacterEntry, RosterSnapshot
from event_capture import CaptureRetry
from snapshot_adapter import AdapterError, MappingUnavailable


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
                    retry = {'retry': str(exc)}
                    if isinstance(exc, MappingRetry):
                        # The attempted new generation can archive its predecessor.
                        retry['observation'] = asdict(exc.observation)
                    write(retry)
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


_OPTIONAL_DERIVED_METRICS = frozenset({
    'mitigation_rate', 'mitigation_before', 'mitigation_after', 'mitigation_unknown_reason',
    'observed_mitigation_rate', 'observed_mitigation_before', 'observed_mitigation_after',
    'mitigation_matched_hits',
    'observed_skill_damage', 'observed_non_skill_damage',
    'observed_skill_damage_effective', 'observed_non_skill_damage_effective',
    'partial_damage',
})
_TERMINOLOGY_REASON_FIELDS = {
    'mitigation_unknown_reason', 'overkill_unknown_reason', 'skill_split_reason',
}
_LEGACY_REASON_TEXT = {
    '事件观测未建立': '事件观察未建立',
    '未观测到弹药技能的实际结束': '未观察到弹药技能的实际结束',
    '未观测到技能效果的实际结束': '未观察到技能效果的实际结束',
}


def _same_json(actual, recorded):
    return json.dumps(actual, sort_keys=True) == json.dumps(recorded, sort_keys=True)


def _recorded_metrics(row, recorded):
    out = {key: value for key, value in row.items()
           if key not in _OPTIONAL_DERIVED_METRICS or key in recorded}
    if isinstance(out.get('boss_damage'), dict) and isinstance(recorded.get('boss_damage'), dict):
        out['boss_damage'] = _recorded_metrics(out['boss_damage'], recorded['boss_damage'])
    return out


def _same_statistics(actual, recorded):
    if isinstance(actual, dict) and isinstance(recorded, dict):
        rows, recorded_rows = actual.get('rows'), recorded.get('rows')
        if isinstance(rows, dict) and isinstance(recorded_rows, dict):
            # Older journals can omit derived metrics. Check every value the
            # writer recorded, including optional fields when present.
            actual = dict(actual, rows={
                owner: _recorded_metrics(row, recorded_rows.get(owner) or {})
                for owner, row in rows.items()
            })
            recorded = dict(recorded, rows={
                owner: {
                    key: _LEGACY_REASON_TEXT.get(value, value)
                    if key in _TERMINOLOGY_REASON_FIELDS and isinstance(value, str)
                    else value
                    for key, value in row.items()
                }
                for owner, row in recorded_rows.items()
            })
    return _same_json(actual, recorded)


def _same_lifecycle_state(actual, recorded):
    if 'statistics' in actual and 'statistics' in recorded:
        return (_same_statistics(actual['statistics'], recorded['statistics'])
                and _same_json({key: value for key, value in actual.items() if key != 'statistics'},
                               {key: value for key, value in recorded.items() if key != 'statistics'}))
    return _same_json(actual, recorded)


def same_archive(actual, recorded):
    # Pre-history journals did not contain snapshot interval history. Keep
    # their existing evidence replayable; new journals verify the field too.
    if 'snapshot_intervals' not in recorded:
        actual = {key: value for key, value in actual.items() if key != 'snapshot_intervals'}
    return _same_lifecycle_state(actual, recorded)


def replay(path):
    with Path(path).open(encoding='utf-8') as stream:
        first = stream.readline()
        lines = chain((first,), stream) if first else ()
        if first and 'kind' in json.loads(first):
            return replay_product(lines)
        return _replay_snapshots(lines)


def _replay_snapshots(lines):
    sessions = None
    pending = []
    samples = archives = 0
    finished = False
    for line in lines:
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
            try:
                sessions.consume(BattleObservation(**raw))
            except MappingUnavailable:
                if 'retry' not in value:
                    raise
            else:
                samples += int(raw['kind'] == 'sample')
            if 'connection' in value:
                sessions.connection = value['connection']
            pending.extend(sessions.pop_archives())
        elif 'archive_ready' in value:
            if not pending or not same_archive(pending.pop(0), value['archive_ready']):
                raise ValueError('archive differs from replayed lifecycle/statistics')
            archives += 1
        elif 'summary' in value:
            expected = value['summary']
            if (samples != expected['samples'] or archives != expected['archives'] or
                    not _same_lifecycle_state(sessions.live(), expected['last_state'])):
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
    reader = DesktopAuditReader()
    sessions = None
    pending = []
    samples = archives = 0
    finished = False
    failures = 0
    for line in lines:
        record = reader.restore(json.loads(line))
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
            except MappingUnavailable:
                pending.extend(sessions.pop_archives())
                continue
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
            if not pending or not same_archive(pending.pop(0), data):
                raise ValueError('archive differs from replayed lifecycle/events')
            archives += 1
        elif kind == 'published':
            if sessions is None or failures or not _same_statistics(sessions.adapter.result(), data['statistics']):
                raise ValueError('published statistics differ from replay')
            if data['session'] != sessions.battle_id:
                raise ValueError('published battle ID differs from lifecycle')
        elif kind == 'lifecycle_summary':
            if sessions is None or not _same_lifecycle_state(sessions.live(), data):
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
