"""Derive a fixed Copilot job with a paused window for the first native baseline."""
import argparse
import json
from pathlib import Path


def prepare(source, destination, hold_ms=45000):
    if hold_ms < 1000:
        raise ValueError('baseline hold must be at least one second')
    job = json.loads(source.read_text(encoding='utf-8'))
    if job.get('stage_name') not in ('OF-8', 'OF-2'):
        raise ValueError('only the measured zero-sanity stages are supported')
    pause = {'type': 'Click', 'rect': [1208, 52, 1, 1]}
    job['actions'] = [pause, {'type': 'Output', 'post_delay': hold_ms,
                             'doc': 'Pause for ArkDPS first frame-guarded baseline.'},
                      dict(pause)] + job['actions']
    with destination.open('x', encoding='utf-8') as out:
        json.dump(job, out, ensure_ascii=False, indent=2)
    return job


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--hold-ms', type=int, default=45000)
    args = parser.parse_args()
    prepare(args.source, args.destination, args.hold_ms)
