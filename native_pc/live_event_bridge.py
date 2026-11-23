"""Publish completed frames supplied by a cooperating telemetry source on stdin."""

import argparse
import json
import sys

from native_pc.internal_event_source import _finite_number, _reject_constant, _unique_fields
from native_pc.live_event_producer import CooperativeEventWriter
from native_pc.live_event_source import LiveNativeEventProbe


def _publish(stream, writer):
    limit = LiveNativeEventProbe.max_line_bytes
    while True:
        line = stream.readline(limit + 2)
        if not line:
            raise ValueError('upstream closed before publishing a terminal frame')
        if len(line) > limit + 1 or not line.endswith(b'\n'):
            raise ValueError('upstream frame must be a bounded newline-terminated record')
        try:
            row = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_fields,
                             parse_float=_finite_number, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            raise ValueError('upstream sent invalid UTF-8 JSON') from exc
        if isinstance(row, dict) and set(row) == {'heartbeat'} and row['heartbeat'] is True:
            writer.heartbeat()
            continue
        if (not isinstance(row, dict) or not {'mark', 'events'} <= row.keys() <= {'mark', 'events', 'terminal'}
                or type(row.get('terminal', False)) is not bool or not isinstance(row['events'], list)):
            raise ValueError('expected a completed frame or explicit heartbeat from upstream')
        terminal = row.get('terminal', False)
        writer.publish(row['mark'], row['events'], terminal=terminal)
        if terminal:
            return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New per-battle JSONL file (must not exist)')
    parser.add_argument('--producer', required=True, help='Name of upstream telemetry producer')
    parser.add_argument('--producer-version', required=True, type=int)
    parser.add_argument('--pid', required=True, type=int, help='PID reported by the telemetry source')
    parser.add_argument('--creation-filetime', required=True, type=int, help='Process creation FILETIME from upstream')
    parser.add_argument('--source-build', required=True, help='Source build claimed by upstream')
    options = parser.parse_args(argv)
    try:
        with CooperativeEventWriter(
            options.output, producer=options.producer, producer_version=options.producer_version,
            process_instance=(options.pid, options.creation_filetime), source_build=options.source_build,
        ) as writer:
            _publish(sys.stdin.buffer, writer)
    except (OSError, ValueError, RuntimeError, RecursionError) as exc:
        print(f'Cooperative PC event bridge: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
