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


def _stage_enemy_references(sample):
    if sample.get('stage_enemy_refs_status') != 'available' or not sample.get('stage_id'):
        return None
    reference = sample['stage_enemy_refs']
    if reference['scope'] != 'current_stage_enemy_db_refs':
        return None
    entries = reference['entries']
    if not entries or reference['collection_count'] != len(entries):
        return None
    return tuple((row['enemy_key'], row['variant_level'], row['use_db'], row['has_overwrite'])
                 for row in entries)


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
    stage_enemy_refs_samples = 0
    stage_refs_by_battle = {}
    game_flow_samples = 0
    idle_flow_changes = 0
    last_idle_fields = None
    practice_selection_observations = 0
    practice_flag_values = set()
    unresolved_finish_transition_observed = False
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
                    refs = _stage_enemy_references(sample)
                    if refs is not None:
                        stage_enemy_refs_samples += 1
                        key = (sample['battle_serial'], sample['stage_id'])
                        frames, signatures = stage_refs_by_battle.setdefault(key, (set(), set()))
                        frames.add(sample['frame'])
                        signatures.add(refs)
                    if sample.get('game_flow_status') == 'available':
                        game_flow_samples += 1
                        flow = sample['game_flow']
                        stage = (sample.get('stage_id') or '').rsplit('/', 1)[-1].casefold()
                        unresolved_finish_transition_observed |= (
                            sample['battle_state'] == 2 and sample.get('battle_result') == 0
                            and flow['transiting'] and stage.startswith('level_')
                            and flow['source_scene'].casefold() == stage
                            and flow['target_scene'].casefold() == 'battle_finish')
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
                idle_fields = (report.get('game_flow_status'), report.get('game_flow_reason'), report.get('game_flow'),
                               report.get('practice_selection_status'), report.get('practice_selection_reason'),
                               report.get('practice_selection'))
                if status != last_status or (status == 'waiting_for_battle' and idle_fields != last_idle_fields):
                    emit({'kind': 'observation', 'elapsed_seconds': elapsed, **report})
                    if status == 'waiting_for_battle':
                        if report.get('game_flow_status') == 'available':
                            idle_flow_changes += 1
                        if report.get('practice_selection_status') == 'available':
                            practice_selection_observations += 1
                            practice_flag_values.add(report['practice_selection']['practice'])
                last_idle_fields = idle_fields
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
            'stage_enemy_refs_samples': stage_enemy_refs_samples,
            'stage_enemy_refs_stable': any(len(frames) >= 2 and len(signatures) == 1
                                           for frames, signatures in stage_refs_by_battle.values()),
            'game_flow_samples': game_flow_samples,
            'idle_game_flow_observations': idle_flow_changes,
            'practice_selection_observations': practice_selection_observations,
            'practice_selection_values': sorted(practice_flag_values),
            'unresolved_finish_transition_observed': unresolved_finish_transition_observed,
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
    parser.add_argument('--stage-enemies', action='store_true',
                        help='also investigate current-stage enemy keys, variant levels and override presence')
    parser.add_argument('--game-flow', action='store_true',
                        help='also record guarded scene transitions without classifying the battle outcome')
    parser.add_argument('--practice-selection', action='store_true',
                        help='also record the top squad page selection before entering a battle')
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= MAX_SECONDS:
        parser.error(f'--seconds must be between 1 and {MAX_SECONDS}')

    from probe_native_pc import ApiFailure
    from probe_native_pc_battle import NativePcBattleProbe, ResearchFailure
    from probe_native_pc_rows import capture_character_rows
    from probe_native_pc_roster import capture_roster

    captures = [('actor_rows', capture_character_rows), ('roster', capture_roster)]
    idle_captures = []
    if args.witnesses:
        from probe_native_pc_actions import capture_actions
        from probe_native_pc_presence import capture_presence

        captures.extend((('actions', capture_actions), ('presence', capture_presence)))
    if args.enemies:
        from native_pc.probe_native_pc_enemies import capture_enemies

        captures.append(('enemies', capture_enemies))
    if args.stage_enemies:
        from native_pc.probe_native_pc_stage_enemies import capture_stage_enemy_refs

        captures.append(('stage_enemy_refs', capture_stage_enemy_refs))
    if args.game_flow:
        from native_pc.probe_native_pc_flow import capture_game_flow

        captures.append(('game_flow', capture_game_flow))
        idle_captures.append(('game_flow', capture_game_flow))
    if args.practice_selection:
        from native_pc.probe_native_pc_practice import capture_practice_selection

        idle_captures.append(('practice_selection', capture_practice_selection))

    next_stage_read = float('-inf')

    def read_fields(read, context, requests):
        nonlocal next_stage_read
        fields = {}
        for key, capture in requests:
            if key == 'stage_enemy_refs':
                now = time.monotonic()
                if now < next_stage_read:
                    continue
                next_stage_read = now + HEARTBEAT_SECONDS
            try:
                fields.update(capture(read, context))
            except (ApiFailure, ResearchFailure):
                fields.update({key: None, f'{key}_status': 'unavailable',
                               f'{key}_reason': 'optional_read_failed'})
        return fields

    def read_optional_fields(read, context):
        return read_fields(read, context, captures)

    def read_idle_fields(read, context):
        return read_fields(read, context, idle_captures)

    with args.output.open('x', encoding='utf-8') as stream:
        def emit(record):
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()

        start = {'kind': 'start', 'source': 'native_pc_read_only', 'research_only': True,
                 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'duration_seconds': args.seconds, 'witnesses': args.witnesses, 'enemies': args.enemies,
                 'stage_enemies': args.stage_enemies, 'game_flow': args.game_flow,
                 'practice_selection': args.practice_selection}
        emit(start)
        try:
            with NativePcBattleProbe(args.game_root) as probe:
                emit({'kind': 'build_verified', 'build': probe.build, 'fixed_module_bytes_match': True})
                print(json.dumps({'status': 'recording', 'duration_seconds': args.seconds}), flush=True)
                idle_reader = read_idle_fields if idle_captures else None
                summary = record_samples(lambda: probe.sample(read_optional_fields, idle_reader=idle_reader),
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
        if args.stage_enemies or args.game_flow or args.practice_selection:
            completed = ((not args.stage_enemies or summary['stage_enemy_refs_stable'])
                         and (not args.game_flow or summary['game_flow_samples'] > 0)
                         and (not args.practice_selection or summary['practice_selection_observations'] > 0))
            return 0 if completed else 2
        return 0 if summary['frame_progress_observed'] and summary['damage_change_observed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
