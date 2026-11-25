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
from desktop_audit import CAPTURE_NAME, DesktopAuditReader
from roster import CharacterEntry, RosterSnapshot
from event_capture import CaptureRetry
from log_context import LogContext
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

        context = LogContext(Path(__file__).resolve().parent / 'runtime',
            connection_override=dict(platform='mumu', adb_path=adb, serial=serial, auto_detect=False),
            layout_override=dict(event_observation=False))
        header = context.header('lifecycle_capture', debug_mode=True)
        write({'header': {**summary, 'log_header': header['data']}})
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
                            version=collector.diagnostics.version_actual,
                            environment=context.update_environment(vars(collector.diagnostics)))})
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


_OBSERVED_HEALING_DERIVED_METRICS = frozenset({
    'observed_healing_total', 'observed_healing_complete', 'observed_recent_healing',
    'observed_recent_healing_window', 'observed_recent_healing_complete',
})
_OPTIONAL_DERIVED_METRICS = _OBSERVED_HEALING_DERIVED_METRICS | frozenset({
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
_INITIAL_SKILL_REASON = '部分命中的采集起点技能状态未确认'
_OLD_MITIGATION_REASON = '部分命中缺少同一次结算的公式前后值或归属'
_MATCHED_MITIGATION_REASON = '已记录命中的公式前后值均已配对，整局事件覆盖未确认'


def _same_json(actual, recorded):
    return json.dumps(actual, sort_keys=True) == json.dumps(recorded, sort_keys=True)


def _recorded_settlement(lane, recorded):
    if not isinstance(lane, dict) or not isinstance(recorded, dict):
        return lane
    out = dict(lane)
    if 'skill_initial_unknown_reason_codes' not in recorded:
        out.pop('skill_initial_unknown_reason_codes', None)
        initial_hits = lane.get('skill_initial_unknown_hits')
        if (lane.get('skill_split_reason') == _INITIAL_SKILL_REASON
                and recorded.get('skill_split_reason') is None
                and lane.get('skill_split_complete') is False
                and type(initial_hits) is int and initial_hits > 0
                and recorded.get('skill_initial_unknown_hits') == initial_hits):
            if 'skill_split_reason' in recorded:
                out['skill_split_reason'] = None
            else:
                out.pop('skill_split_reason')
    hits = lane.get('observed_hits')
    matched = lane.get('mitigation_matched_hits')
    if (lane.get('mitigation_reason') == _MATCHED_MITIGATION_REASON
            and recorded.get('mitigation_reason') == _OLD_MITIGATION_REASON
            and type(hits) is int and hits > 0 and type(matched) is int and matched == hits
            and lane.get('mitigation_complete') is False and lane.get('complete') is False
            and 'mitigation_unverified_share' in lane and lane['mitigation_unverified_share'] is None):
        out['mitigation_reason'] = _OLD_MITIGATION_REASON
    return out


def _recorded_metrics(row, recorded, *, optional=True):
    if not isinstance(row, dict) or not isinstance(recorded, dict):
        return row
    # Both ordinary and scoped old journals predate these derived values.
    # A new journal that supplies any of them still verifies it exactly.
    optional_metrics = _OPTIONAL_DERIVED_METRICS if optional else _OBSERVED_HEALING_DERIVED_METRICS
    out = {key: value for key, value in row.items()
           if key not in optional_metrics or key in recorded}
    if 'settled_damage' in row:
        out['settled_damage'] = _recorded_settlement(row['settled_damage'], recorded.get('settled_damage'))
    if 'boss_damage' in row:
        out['boss_damage'] = _recorded_metrics(row['boss_damage'], recorded.get('boss_damage'), optional=optional)
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


def _recorded_player(player, recorded):
    if not isinstance(player, dict) or not isinstance(recorded, dict):
        return player
    out = dict(player)
    if 'skill_initialization' not in recorded:
        out.pop('skill_initialization', None)
    stats, old_stats = player.get('statistics'), recorded.get('statistics')
    if isinstance(stats, dict) and isinstance(old_stats, dict):
        rows, old_rows = stats.get('rows'), old_stats.get('rows')
        if isinstance(rows, dict) and isinstance(old_rows, dict):
            out['statistics'] = dict(stats, rows={owner: _recorded_metrics(row, old_rows.get(owner), optional=False)
                                                 for owner, row in rows.items()})
    actors, old_actors = player.get('actor_ledgers'), recorded.get('actor_ledgers')
    if isinstance(actors, list) and isinstance(old_actors, list) and len(actors) == len(old_actors):
        out['actor_ledgers'] = [
            dict(actor, ledger=_recorded_settlement(actor['ledger'], old_actor.get('ledger')))
            if isinstance(actor, dict) and 'ledger' in actor and isinstance(old_actor, dict) else actor
            for actor, old_actor in zip(actors, old_actors)
        ]
    return out


def _recorded_multiplayer_scope(scope, recorded):
    if not isinstance(scope, dict) or not isinstance(recorded, dict):
        return scope
    players, old_players = scope.get('players'), recorded.get('players')
    if isinstance(players, list) and isinstance(old_players, list) and len(players) == len(old_players):
        return dict(scope, players=[_recorded_player(player, old_player)
                                    for player, old_player in zip(players, old_players)])
    return scope


def _recorded_multiplayer(scope, recorded):
    out = _recorded_multiplayer_scope(scope, recorded)
    if not isinstance(scope, dict) or not isinstance(recorded, dict):
        return out
    previous, old_previous = scope.get('previous_rounds'), recorded.get('previous_rounds')
    if isinstance(previous, list) and isinstance(old_previous, list) and len(previous) == len(old_previous):
        out = dict(out, previous_rounds=[_recorded_multiplayer_scope(prior, old_prior)
                                        for prior, old_prior in zip(previous, old_previous)])
    return out


def _same_lifecycle_state(actual, recorded):
    if 'multiplayer' in actual:
        actual = dict(actual, multiplayer=_recorded_multiplayer(actual['multiplayer'], recorded.get('multiplayer')))
    sample, old_sample = actual.get('last_sample'), recorded.get('last_sample')
    if isinstance(sample, dict) and isinstance(old_sample, dict) and 'multiplayer' in sample:
        actual = dict(actual, last_sample=dict(sample, multiplayer=_recorded_multiplayer(
            sample['multiplayer'], old_sample.get('multiplayer'))))
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
    path = Path(path)
    match = CAPTURE_NAME.fullmatch(path.name)
    if match and match['part'] is not None and path.is_file():
        return _replay_segments(path, match['run'])
    with path.open(encoding='utf-8') as stream:
        first = stream.readline()
        lines = chain((first,), stream) if first else ()
        if first and 'kind' in json.loads(first):
            return replay_product(lines)
        return _replay_snapshots(lines)


def _replay_segments(path, run):
    segments = []
    for candidate in path.parent.glob(f'{run}-part-*.jsonl'):
        match = CAPTURE_NAME.fullmatch(candidate.name)
        if match and candidate.is_file():
            segments.append((int(match['part']), candidate))
    segments.sort()

    def lines():
        for _, segment in segments:
            with segment.open(encoding='utf-8') as stream:
                yield from stream

    if all(number == expected for expected, (number, _) in enumerate(segments, start=1)):
        return replay_product(lines())

    # Each segment starts its own enemy delta prefix. A retained tail can be
    # inspected even after the original lifecycle baseline has been removed.
    last_publication = None
    records = samples = 0
    reader = DesktopAuditReader()
    for line in lines():
        record = reader.restore(json.loads(line))
        records += 1
        if record['kind'] == 'lifecycle_observation':
            samples += record['data']['kind'] == 'sample'
        elif record['kind'] == 'published':
            last_publication = record['data']
    return dict(coverage='retained_only', segments=[number for number, _ in segments],
                records=records, samples=samples, last_publication=last_publication)


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
            try:
                sessions.consume(observation_from_json(raw))
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
            present_unique_ids=frozenset(roster['present_unique_ids']),
            present_known=roster['present_known'], local_player_side=roster.get('local_player_side'))
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
        if ((kind == 'log_header' and data.get('debug_mode') is False)
                or (kind == 'debug_mode' and data.get('enabled') is False)):
            raise ValueError('详细采样未覆盖日志全程，无法完整回放。请在复现前开启调试模式。')
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
