"""Measure starting HP per life from a single read-only enemy lifecycle trace."""

import argparse
from collections import defaultdict
import json
from math import isfinite
from pathlib import Path


def health(sample, field):
    value = sample.get(field)
    return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
            and isfinite(value) and value >= 0 else None)


def summarize(records, *, eid, stage=None):
    enemies = defaultdict(list)
    errors = []
    for record in records:
        if record.get('kind') == 'error':
            errors.append(record.get('error', record.get('message', 'probe error')))
        if (record.get('kind') == 'callback' and record.get('eid') == eid
                and (stage is None or record.get('stage') == stage)):
            enemies[record['uid']].append(record)

    rows = []
    for uid, events in enemies.items():
        phases = []
        saw_birth = False
        saw_fake_death = False
        finish_frame = None
        incomplete = False
        for event in sorted(events, key=lambda item: item['sequence']):
            after = event.get('after') or {}
            before = event.get('before') or {}
            method = event['method']
            hp = health(after, 'hp')
            if method == 'Enemy.DoFakeDeath':
                saw_fake_death = True
                continue
            if method == 'Enemy.OnFinish' and hp == 0 and (
                    after.get('finish_reason') in (2, 8) or before.get('finish_reason') in (2, 8)):
                finish_frame = event['frame']
            initial = method == 'Enemy.Born' and not phases
            reborn = method == 'Enemy.DoReborn'
            if initial or reborn:
                if hp is None or hp <= 0 or (reborn and not saw_fake_death):
                    incomplete = True
                    continue
                phases.append({
                    'frame': event['frame'], 'game_time': event['game_time'],
                    'starting_hp': hp, 'max_hp': health(after, 'max_hp'),
                })
                saw_birth |= initial
                saw_fake_death = False
                finish_frame = None
        rows.append({
            'uid': uid, 'eid': eid, 'phases': phases, 'finish_frame': finish_frame,
            'observed_total_hp': sum(item['starting_hp'] for item in phases),
            'complete': (saw_birth and finish_frame is not None
                         and not (saw_fake_death or incomplete or errors)),
        })
    return {'enemies': rows, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--eid', default='enemy_1506_patrt')
    parser.add_argument('--stage')
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--expect-phases', type=int)
    args = parser.parse_args()
    if args.expect_phases is not None and args.expect_phases < 1:
        parser.error('--expect-phases must be positive')
    with args.trace.open(encoding='utf-8-sig') as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    report = summarize(records, eid=args.eid, stage=args.stage)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_complete:
        sequences = [record.get('sequence') for record in records if record.get('kind') == 'callback']
        if (not records or records[0].get('kind') != 'ready'
                or records[-1].get('kind') != 'stop' or records[-1].get('errors') != 0
                or records[-1].get('callbacks') != len(sequences)
                or sequences != list(range(1, len(sequences) + 1))
                or not report['enemies'] or not all(enemy['complete'] for enemy in report['enemies'])):
            parser.exit(1, 'Phase trace does not cover birth through final defeat.\n')
    if args.expect_phases is not None and (not report['enemies'] or any(
            len(enemy['phases']) != args.expect_phases for enemy in report['enemies'])):
        parser.exit(1, 'Unexpected number of recorded phases.\n')


if __name__ == '__main__':
    main()
