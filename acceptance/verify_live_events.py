"""Reconcile a new product capture's ordered event prefix against its published rows."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path


def near(left, right):
    if not math.isclose(left, right, rel_tol=1e-7, abs_tol=.01):
        raise ValueError(f'amount mismatch: {left} != {right}')


def verify(directory, *, source_owners=(), require_partial=False, require_zero=False,
           allow_incomplete=False):
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('error') and not allow_incomplete:
        raise ValueError('capture reports a failure: ' + summary['error'])
    records = [json.loads(line) for line in (directory / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    publications = [record['data'] for record in records if record['kind'] == 'published']
    failed_run = bool(summary.get('error')) or not publications or publications[-1]['event_status'] not in ('recording', 'finished')
    if failed_run and allow_incomplete:
        publications = [sample for sample in publications if sample['event_status'] in ('recording', 'finished')]
    if not publications or publications[0]['frame'] == publications[-1]['frame']:
        raise ValueError('no observed battle progression')
    first, last = publications[0], publications[-1]
    complete = not failed_run
    if first['session'] != last['session'] or not complete and not allow_incomplete:
        raise ValueError('capture does not have one reliable event session')
    start_sequence = first['event_watermark']['sequence']
    end_sequence = last['event_watermark']['sequence']
    by_sequence = {}
    for record in records:
        if record['kind'] != 'drain':
            continue
        for event in record['data']['events']:
            sequence = event['sequence']
            if sequence in by_sequence and by_sequence[sequence] != event:
                raise ValueError('conflicting replay in raw events')
            by_sequence[sequence] = event
    accepted = [by_sequence[sequence] for sequence in range(start_sequence + 1, end_sequence + 1)
                if sequence in by_sequence]
    if len(accepted) != end_sequence - start_sequence:
        raise ValueError('raw events are missing from the delivered prefix')
    rows = last['statistics']['rows']
    ownership = {}
    for owner, row in rows.items():
        for source in row['sources']:
            if source in ownership and ownership[source] != owner:
                raise ValueError('ambiguous source ownership')
            ownership[source] = owner
    totals = defaultdict(float)
    partial, zeros = [], []
    sources_with_absorption = set()
    previous_time = first['game_time']
    previous_frame = first['frame']
    for event in accepted:
        source, amount = event['source'], event['amount']
        if (source not in ownership or not math.isfinite(amount) or amount < 0
                or event['kind'] not in ('damage', 'healing', 'shield_absorbed')):
            raise ValueError('invalid or unowned event')
        if not previous_time <= event['game_time'] <= last['game_time']:
            raise ValueError('event time is outside the ordered coverage')
        previous_time = event['game_time']
        if not previous_frame <= event['frame'] <= last['frame']:
            raise ValueError('event frame is outside ordered coverage')
        previous_frame = event['frame']
        totals[ownership[source], event['kind']] += amount
        if event['kind'] == 'shield_absorbed':
            proof = event['proof']
            if proof['buff'] not in ('shining_s_2', 'catsld_t_1[core]'):
                raise ValueError('unverified shield mechanism')
            near(proof['before'] - proof['after'], amount)
            near(min(proof['before'], proof['incoming']), amount)
            if amount > 0:
                sources_with_absorption.add(source)
            if amount > 0 and abs(proof['after']) < .001 and proof['incoming'] > amount + .001:
                partial.append(event['sequence'])
            if amount == 0:
                zeros.append(event['sequence'])
    native = {row['id']: row for row in last['rows']}
    references = {}
    for publication in publications:
        for raw in publication['rows']:
            references.setdefault(raw['id'], (raw, publication['event_watermark']['sequence'],
                                               publication['game_time']))
    counter_deltas = {}
    for source, (baseline, sequence, game_time) in references.items():
        if source not in ownership or source not in native:
            continue
        check = {'start': game_time, 'end': last['game_time'], 'start_sequence': sequence}
        for kind in ('damage', 'healing'):
            delta = native[source][kind] - baseline[kind]
            observed = sum(event['amount'] for event in accepted if event['source'] == source
                           and event['kind'] == kind and event['sequence'] > sequence)
            near(delta, observed)
            check[kind] = {'native_delta': delta, 'observed_events': observed}
        counter_deltas[source] = check
    for owner, row in rows.items():
        for kind, field in (('damage', 'observed_damage'), ('healing', 'observed_healing'),
                            ('shield_absorbed', 'shield_absorbed')):
            near(totals[owner, kind], row[field])
        if row['counter_snapshot_complete']:
            for kind in ('damage', 'healing'):
                near(sum(native[source][kind] for source in row['sources'] if source in native), row[kind])
    for item in source_owners:
        source, owner = item.split('=', 1)
        if ownership.get(source) != owner or source not in sources_with_absorption:
            raise ValueError('required shield source/owner was not observed: ' + item)
    if require_partial and not partial:
        raise ValueError('no partial shield break was observed')
    if require_zero and not zeros:
        raise ValueError('no zero-absorption callback was observed')
    return {'head': summary['head'], 'session': last['session'],
            'complete': bool(complete), 'capture_error': summary.get('error'),
            'coverage_start': first['game_time'],
            'coverage_end': last['event_watermark'].get('game_time', last['game_time']),
            'baseline_sequence': start_sequence, 'final_sequence': end_sequence,
            'events': len(accepted), 'partial_break_sequences': partial,
            'event_counts': dict(Counter(event['kind'] for event in accepted)),
            'native_counter_deltas': counter_deltas,
            'zero_absorption_sequences': zeros,
            'shield_totals': {owner: totals[owner, 'shield_absorbed'] for owner in rows}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source-owner', action='append', default=[])
    parser.add_argument('--require-partial', action='store_true')
    parser.add_argument('--require-zero', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-incomplete-prefix', action='store_true',
                        help='diagnose a frozen reliable prefix; never report it as complete acceptance')
    args = parser.parse_args()
    result = verify(args.directory, source_owners=args.source_owner,
                    require_partial=args.require_partial, require_zero=args.require_zero,
                    allow_incomplete=args.allow_incomplete_prefix)
    if args.output:
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
