"""Record a bounded sequence of native PC battle samples as research evidence."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time


POLL_SECONDS = 0.2
MAX_SECONDS = 300
HEARTBEAT_SECONDS = 1.0


def _enemy_hp(sample):
    if sample.get('enemies_status') != 'available':
        return {}
    return {row['instance_uid']: row['hp'] for row in sample['enemies']['entities']
            if row['instance_uid'] > 0 and row['inited'] and row['borned']
            and row['native_object_present'] and not row['is_disappeared']
            and row['hp'] is not None and row['hp'] >= 0}


def record_samples(poll, seconds, emit):
    started = time.monotonic()
    deadline = started + seconds
    counts = Counter()
    last_status = None
    previous = None
    last_heartbeat = 0.0
    stable_samples = 0
    frame_progress_observed = False
    damage_change_observed = False
    healing_change_observed = False
    actor_rows_observed = False
    actor_damage_change_observed = False
    enemy_hp_observed = False
    enemy_hp_change_observed = False
    interrupted = False
    try:
        while True:
            report = poll()
            observed = time.monotonic()
            elapsed = round(observed - started, 6)
            status = report['status']
            counts[status] += 1
            if status == 'sample_available':
                sample = report['sample']
                if sample != previous:
                    stable_samples += 1
                    rows = sample.get('actor_rows') or []
                    enemy_hp = _enemy_hp(sample)
                    actor_rows_observed |= bool(rows)
                    enemy_hp_observed |= bool(enemy_hp)
                    if previous is not None and previous['battle_serial'] == sample['battle_serial']:
                        if (previous['frame'] < sample['frame']
                                and previous['game_time'] < sample['game_time']):
                            frame_progress_observed = True
                            previous_hp = _enemy_hp(previous)
                            enemy_hp_change_observed |= any(
                                previous_hp[uid] != hp for uid, hp in enemy_hp.items() if uid in previous_hp)
                        if previous['frame'] <= sample['frame'] and previous['game_time'] <= sample['game_time']:
                            damage_change_observed |= previous['total_damage'] != sample['total_damage']
                            healing_change_observed |= previous['total_heal'] != sample['total_heal']
                            previous_damage = {row['id']: row['damage']
                                               for row in previous.get('actor_rows') or []}
                            actor_damage_change_observed |= any(
                                row['id'] in previous_damage and previous_damage[row['id']] < row['damage']
                                for row in rows)
                    emit({'kind': 'observation', 'elapsed_seconds': elapsed, **report})
                    last_heartbeat = observed
                    previous = sample
                elif observed - last_heartbeat >= HEARTBEAT_SECONDS:
                    emit({'kind': 'heartbeat', 'elapsed_seconds': elapsed,
                          'battle_serial': sample['battle_serial'], 'frame': sample['frame'],
                          'game_time': sample['game_time'], 'battle_state': sample['battle_state'],
                          'paused': sample.get('paused'), 'speed_level': sample.get('speed_level')})
                    last_heartbeat = observed
            else:
                if status != last_status:
                    emit({'kind': 'observation', 'elapsed_seconds': elapsed, **report})
                previous = None
            last_status = status
            remaining = deadline - observed
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
            'enemy_hp_observed': enemy_hp_observed,
            'enemy_hp_change_observed': enemy_hp_change_observed,
            'status_counts': dict(counts), 'interrupted': interrupted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new JSON Lines evidence file')
    parser.add_argument('--seconds', type=int, default=120,
                        help=f'maximum observation time, 1 to {MAX_SECONDS} seconds')
    parser.add_argument('--witnesses', action='store_true',
                        help='also record managed unit membership and retained manual actions')
    parser.add_argument('--enemies', action='store_true',
                        help='also investigate current enemy membership and HP in the same logical frame')
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= MAX_SECONDS:
        parser.error(f'--seconds must be between 1 and {MAX_SECONDS}')

    from probe_native_pc import ApiFailure
    from probe_native_pc_battle import NativePcBattleProbe, ResearchFailure
    from probe_native_pc_rows import capture_character_rows
    from probe_native_pc_roster import capture_roster

    captures = [('actor_rows', capture_character_rows), ('roster', capture_roster)]
    if args.witnesses:
        from probe_native_pc_actions import capture_actions
        from probe_native_pc_presence import capture_presence

        captures.extend((('actions', capture_actions), ('presence', capture_presence)))
    if args.enemies:
        from native_pc.probe_native_pc_enemies import capture_enemies

        captures.append(('enemies', capture_enemies))

    def read_optional_fields(read, context):
        fields = {}
        for key, capture in captures:
            try:
                fields.update(capture(read, context))
            except (ApiFailure, ResearchFailure):
                fields.update({key: None, f'{key}_status': 'unavailable',
                               f'{key}_reason': 'optional_read_failed'})
        return fields

    with args.output.open('x', encoding='utf-8') as stream:
        def emit(record):
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()

        start = {'kind': 'start', 'source': 'native_pc_read_only', 'research_only': True,
                 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'duration_seconds': args.seconds, 'witnesses': args.witnesses, 'enemies': args.enemies}
        emit(start)
        try:
            with NativePcBattleProbe(args.game_root) as probe:
                emit({'kind': 'build_verified', 'build': probe.build, 'fixed_module_bytes_match': True})
                print(json.dumps({'status': 'recording', 'duration_seconds': args.seconds}), flush=True)
                summary = record_samples(lambda: probe.sample(extra_reader=read_optional_fields),
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
