"""Record a bounded sequence of native PC battle samples as research evidence."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time


POLL_SECONDS = 0.2


def record_samples(poll, seconds, emit):
    deadline = time.monotonic() + seconds
    counts = Counter()
    last_status = None
    previous = None
    stable_samples = 0
    frame_progress_observed = False
    damage_change_observed = False
    healing_change_observed = False
    actor_rows_observed = False
    actor_damage_change_observed = False
    interrupted = False
    try:
        while True:
            report = poll()
            status = report['status']
            counts[status] += 1
            if status == 'sample_available':
                sample = report['sample']
                if sample != previous:
                    stable_samples += 1
                    rows = sample.get('actor_rows') or []
                    actor_rows_observed |= bool(rows)
                    if previous is not None and previous['battle_serial'] == sample['battle_serial']:
                        if (previous['frame'] < sample['frame']
                                and previous['game_time'] < sample['game_time']):
                            frame_progress_observed = True
                        if previous['frame'] <= sample['frame'] and previous['game_time'] <= sample['game_time']:
                            damage_change_observed |= previous['total_damage'] != sample['total_damage']
                            healing_change_observed |= previous['total_heal'] != sample['total_heal']
                            previous_damage = {row['id']: row['damage']
                                               for row in previous.get('actor_rows') or []}
                            actor_damage_change_observed |= any(
                                row['id'] in previous_damage and previous_damage[row['id']] < row['damage']
                                for row in rows)
                    emit({'kind': 'observation', **report})
                    previous = sample
            else:
                if status != last_status:
                    emit({'kind': 'observation', **report})
                previous = None
            last_status = status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, POLL_SECONDS))
    except KeyboardInterrupt:
        interrupted = True
    return {'kind': 'summary', 'stable_samples': stable_samples,
            'frame_progress_observed': frame_progress_observed,
            'damage_change_observed': damage_change_observed,
            'healing_change_observed': healing_change_observed,
            'actor_rows_observed': actor_rows_observed,
            'actor_damage_change_observed': actor_damage_change_observed,
            'status_counts': dict(counts), 'interrupted': interrupted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new JSON Lines evidence file')
    parser.add_argument('--seconds', type=int, default=120, help='maximum observation time, 1 to 180 seconds')
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 180:
        parser.error('--seconds must be between 1 and 180')

    from probe_native_pc import ApiFailure
    from probe_native_pc_battle import NativePcBattleProbe, ResearchFailure
    from probe_native_pc_rows import capture_character_rows

    def read_optional_rows(read, context):
        try:
            return capture_character_rows(read, context)
        except (ApiFailure, ResearchFailure):
            # Keep the bounded global sample when an optional character counter cannot be read.
            return {'actor_rows_status': 'unavailable', 'actor_rows_reason': 'counter_read_failed',
                    'actor_rows': None}

    with args.output.open('x', encoding='utf-8') as stream:
        def emit(record):
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()

        start = {'kind': 'start', 'source': 'native_pc_read_only', 'research_only': True,
                 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'duration_seconds': args.seconds}
        emit(start)
        try:
            with NativePcBattleProbe(args.game_root) as probe:
                emit({'kind': 'build_verified', 'build': probe.build, 'fixed_module_bytes_match': True})
                print(json.dumps({'status': 'recording', 'duration_seconds': args.seconds}), flush=True)
                summary = record_samples(lambda: probe.sample(extra_reader=read_optional_rows),
                                         args.seconds, emit)
        except ResearchFailure as error:
            report = {'kind': 'failure', 'status': error.status, 'reason': error.reason}
            if error.diagnostic is not None:
                report['error'] = error.diagnostic
            emit(report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return 2
        emit(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary['frame_progress_observed'] and summary['damage_change_observed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
