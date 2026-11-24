"""Verify recorded PC Frida event batches through the standalone C++ host."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from event_probe import NATIVE_PC_CONTRACT
from native_pc.internal_event_host.build import DEFAULT_OUTPUT
from native_pc.internal_event_source import InternalEventProbe, _finite_number, _reject_constant, _unique_fields


_MAX_LINE_BYTES = 16 * 1024 * 1024
_ROOTS = ('controller', 'native', 'logger', 'stats', 'level')
_ROOT = re.compile(r'0x[1-9a-f][0-9a-f]{0,15}')
_SCOPE = ('native_generation', 'native_process_pid', 'native_stage_id', 'native_identity',
          'native_session', 'source_build')
_MARK_ONLY = {'frame', 'game_time', 'sequence', 'epoch', 'active', 'active_hooks', 'terminal_boundary'}
_READ_ONLY = {'events', 'probe_contract', 'retry', 'hp_context', 'hp_context_dropped',
              'scoped_trace', 'scoped_trace_count', 'scoped_trace_dropped',
              'nested_parent_trace_count', 'nested_parent_trace_dropped',
              'baseline_discarded_skill_events', 'baseline_discarded_skill_events_complete',
              'baseline_discarded_skill_events_error'}
_DIAGNOSTICS = {'mitigation_diagnostics'}


def _lines(path):
    with Path(path).open('rb') as stream:
        number = 0
        while line := stream.readline(_MAX_LINE_BYTES + 1):
            number += 1
            if len(line) > _MAX_LINE_BYTES or not line.endswith(b'\n'):
                raise ValueError(f'line {number}: expected a complete record of at most 16 MiB')
            try:
                record = json.loads(line.decode('utf-8'), object_pairs_hook=_unique_fields,
                                    parse_float=_finite_number, parse_constant=_reject_constant)
            except (ValueError, RecursionError) as exc:
                raise ValueError(f'line {number}: invalid UTF-8 JSON record') from exc
            if not isinstance(record, dict) or not isinstance(record.get('kind'), str):
                raise ValueError(f'line {number}: invalid journal envelope')
            if record['kind'] == 'lifecycle_observation':
                yield number, record.get('data')


def _same(left, right):
    # Unlike Python's default equality, this distinguishes true from 1.
    return (json.dumps(left, sort_keys=True, allow_nan=False)
            == json.dumps(right, sort_keys=True, allow_nan=False))


def _nonnegative(value):
    return type(value) is int and value >= 0


def _journal_position(observation):
    feed, generation, sequence = (observation.get(key) for key in ('feed', 'generation', 'sequence'))
    if not isinstance(feed, str) or not feed or not _nonnegative(generation) or not _nonnegative(sequence):
        raise ValueError('recorded event observation has no lifecycle position')
    return (feed, generation), sequence


def _matches_clock(mark, frame, seconds):
    fixed = mark.get('native_fixed_time')
    return (_same(frame, mark.get('frame')) and _same(frame, mark.get('native_frame'))
            and type(mark.get('game_time')) in (int, float) and mark['game_time'] == seconds
            and isinstance(fixed, str) and fixed.isascii() and fixed.isdecimal()
            and len(fixed) <= 19 and int(fixed) <= (1 << 63) - 1 and int(fixed) / (1 << 32) == seconds)


def _batch(observation, evidence):
    sample = observation.get('sample')
    before, mark, drain = (evidence.get(key) for key in ('before', 'after', 'drain'))
    if not all(isinstance(part, dict) for part in (sample, before, mark, drain)):
        raise ValueError('missing sample, watermarks or bounded read')
    metadata = sample.get('battle_metadata')
    profile = NATIVE_PC_CONTRACT.describe()
    if (not isinstance(metadata, dict) or metadata.get('platform') != 'native_pc'
            or evidence.get('probe_contract') != profile or drain.get('probe_contract') != profile):
        raise ValueError('not a recorded native PC Frida batch')
    if any(part.get(key) for part in (before, mark, drain) for key in ('error', 'stopped', 'retry')):
        raise ValueError('recorded event source is unavailable')
    NATIVE_PC_CONTRACT.validate_binding(sample, before, mark, drain)

    through = evidence.get('sequence')
    if (not _nonnegative(through) or not _same(mark.get('sequence'), through)
            or not _same(mark.get('active'), 0) or not _same(before.get('active'), 0)
            or not _nonnegative(before.get('sequence')) or before['sequence'] > through
            or not _nonnegative(mark.get('epoch')) or not _nonnegative(before.get('epoch'))
            or before['epoch'] > mark['epoch']
            or (before['sequence'] < through and before['epoch'] == mark['epoch'])
            or not _same(before.get('damage_settlement_scope'), mark.get('damage_settlement_scope'))):
        raise ValueError('invalid recorded event watermark')
    frame, seconds = sample.get('frame'), sample.get('game_time')
    if (not _nonnegative(frame) or type(seconds) not in (int, float) or seconds < 0
            or not all(_matches_clock(part, frame, seconds) for part in (before, mark))):
        raise ValueError('recorded watermark does not match the sample clock')
    if (not isinstance(mark.get('native_identity'), dict) or set(mark['native_identity']) != set(_ROOTS)
            or any(not isinstance(value, str) or not _ROOT.fullmatch(value)
                   for value in mark['native_identity'].values())
            or not _nonnegative(mark.get('native_generation')) or not _nonnegative(mark.get('native_process_pid'))
            or not mark['native_process_pid'] or not isinstance(mark.get('native_stage_id'), str)
            or not mark['native_stage_id'] or metadata.get('stage_id') != mark['native_stage_id']
            or any(not _same(mark[key], part.get(key)) for key in _SCOPE for part in (before, drain))):
        raise ValueError('recorded event scope changed or disagrees with the sample')

    # Replay the original closing mark, never overwrite it with a later read.
    # Require both to agree on shared evidence; RPC-only diagnostics are not replayed.
    mark_evidence = {key: value for key, value in mark.items() if key not in _MARK_ONLY | _DIAGNOSTICS}
    read_evidence = {key: value for key, value in drain.items() if key not in _READ_ONLY | _DIAGNOSTICS}
    if not _same(mark_evidence, read_evidence):
        raise ValueError('recorded watermark and bounded read disagree on shared evidence')
    NATIVE_PC_CONTRACT.normalize_quality(mark)
    events = drain.get('events')
    if (not isinstance(events, list) or any(not isinstance(event, dict)
            or not _nonnegative(event.get('sequence')) or not event['sequence']
            or not isinstance(event.get('kind'), str) or not event['kind'] for event in events)):
        raise ValueError('invalid recorded event list')
    return mark, events


def _terminal(mark, boundary):
    """Keep a terminal barrier only at the original, fully checked watermark."""
    if (not isinstance(boundary, dict)
            or not _nonnegative(mark.get('damage_settlement_scope'))
            or any(not _same(boundary.get(key), mark['native_identity'][key]) for key in _ROOTS)
            or any(not _same(boundary.get(key), mark.get(key))
                   for key in ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope'))
            or any(key in boundary and not _same(boundary[key], mark[key]) for key in _SCOPE)):
        raise ValueError('recorded terminal proof does not match its watermark')
    # InternalEventProbe wraps the stored proof itself; an unbound nested
    # Frida boundary must not stand in for the checked host terminal.
    return {key: value for key, value in mark.items() if key != 'terminal_boundary'}


def replay_probe_batches(path, host_path=DEFAULT_OUTPUT):
    """Return a transport-only receipt; never attach, reconstruct frames or replay stats."""
    probe = InternalEventProbe(Path(host_path))
    batches = repeated = 0
    cursor = first_sequence = initial_cursor = None
    mark = None
    journal_position = None
    kinds = Counter()
    terminal_verified = closed = False
    probe.start()
    try:
        for number, observation in _lines(path):
            try:
                if not isinstance(observation, dict) or not isinstance(observation.get('kind'), str):
                    raise ValueError('invalid lifecycle observation')
                kind = observation['kind']
                evidence = observation.get('events')
                if evidence is None:
                    if mark is not None and kind in ('gap', 'end', 'terminal', 'scope_unavailable'):
                        closed = True
                    continue
                if not isinstance(evidence, dict):
                    raise ValueError('invalid recorded event envelope')
                if 'drain' not in evidence:
                    if mark is not None and kind == 'end' and 'terminal_boundary' in evidence:
                        end_scope, end_sequence = _journal_position(observation)
                        if closed:
                            raise ValueError('recorded terminal proof follows a coverage gap')
                        if end_scope != journal_position[0] or end_sequence <= journal_position[1]:
                            raise ValueError('recorded terminal proof belongs to a different lifecycle position')
                        terminal = _terminal(mark, evidence['terminal_boundary'])
                        if (not probe.advance(mark, [], terminal_mark=terminal)
                                or not _same(probe.read_terminal_mark(), {'terminal_boundary': terminal})):
                            raise ValueError('native host did not preserve the terminal mark')
                        terminal_verified = True
                    if mark is not None:
                        closed = True
                    continue
                if closed or terminal_verified or kind not in ('sample', 'terminal'):
                    raise ValueError('expected a single contiguous recorded event segment')
                next_mark, events = _batch(observation, evidence)
                scope, position = _journal_position(observation)
                if journal_position is not None and (scope != journal_position[0] or position <= journal_position[1]):
                    raise ValueError('expected one lifecycle generation with increasing observations')
                if mark is not None and any(not _same(next_mark[key], mark[key]) for key in _SCOPE):
                    raise ValueError('expected a single recorded battle and build')
                through = next_mark['sequence']
                if cursor is None:
                    cursor = initial_cursor = events[0]['sequence'] - 1 if events else through
                boundary = next_mark.get('terminal_boundary')
                terminal = _terminal(next_mark, boundary) if boundary is not None else None
                if not probe.advance(next_mark, events, terminal_mark=terminal):
                    raise ValueError('native host rejected the recorded batch')
                # The host verifies any replays against the retained queue.
                # Its bounded read must return exactly the new tail, once.
                new = list({event['sequence']: event for event in events if event['sequence'] > cursor}.values())
                if not _same(probe.mark(), next_mark) or not _same(
                        probe.read_through(through), dict(next_mark, events=new)):
                    raise ValueError('native host changed the recorded watermark or event payload')
                if terminal is not None and not _same(probe.read_terminal_mark(), {'terminal_boundary': terminal}):
                    raise ValueError('native host did not preserve the terminal mark')
                if not probe.commit_events(through):
                    raise ValueError('native host did not acknowledge the recorded batch')
                if new and first_sequence is None:
                    first_sequence = new[0]['sequence']
                kinds.update(event['kind'] for event in new)
                repeated += len(events) - len(new)
                batches += 1
                cursor, mark = through, next_mark
                journal_position = scope, position
                terminal_verified = terminal is not None
            except (ValueError, RuntimeError) as exc:
                raise ValueError(f'line {number}: {exc}') from exc
    finally:
        probe.stop()
    if not batches:
        raise ValueError('no recorded native PC Frida event batches')
    return dict(batches=batches, events=sum(kinds.values()), events_by_kind=dict(kinds),
                first_sequence=first_sequence, last_sequence=cursor, unverified_through=initial_cursor,
                replayed_records=repeated, terminal_verified=terminal_verified, game_connected=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('journal', type=Path, help='Single battle segment of a product debug JSONL journal')
    parser.add_argument('--host', type=Path, default=DEFAULT_OUTPUT, help='Trusted standalone event host executable')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(replay_probe_batches(args.journal, args.host), sort_keys=True))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f'{parser.prog}: {exc}\n')


if __name__ == '__main__':
    main()
