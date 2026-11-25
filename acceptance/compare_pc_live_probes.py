"""Compare two independently recorded, guarded PC event journals.

This is a file verifier, not a live acquisition tool. Source labels are caller
assertions; even a matching result does not authenticate how the files arose.
Sequence numbers, observer generations and diagnostic pointers are not shared
identities. A complete result requires a zero-frame baseline and independently
checked real terminal fences on both sides.
"""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_probe import contract_for
from event_stream import baseline_barrier, validate_native_event_batch
from native_pc.internal_event_source import _finite_number, _reject_constant, _unique_fields
from native_pc.probe_native_pc import KNOWN_BUILDS


SOURCE_TYPES = ('native-hooks', 'frida', 'controlled', 'offline')
ROOTS = ('controller', 'native', 'logger', 'stats', 'level')
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_DIFFERENCES = 100
# Explicit domain projection: transport-local cursors, hook epochs, traces and
# return addresses must not cause a false mismatch between independent probes.
SEMANTIC_FIELDS = (
    'frame', 'game_time', 'kind', 'source', 'source_character_uid', 'amount',
    'target', 'target_uid', 'target_instance', 'damage_type', 'target_type',
    'real_delta', 'hp_before', 'hp_after', 'overkill', 'effective_damage',
    'killed', 'cancel_reason', 'shield_absorbed', 'nominal_amount',
    'settlement_complete', 'settled_after_nested_kill', 'unresolved_source',
    'enemy_damage_owner', 'mitigation_before', 'mitigation_after',
    'skill_key', 'skill_id', 'skill_name', 'skill_reason', 'skill_method',
    'skill_wait_for_end', 'phase_total_hp', 'phase_count', 'phase_from_birth',
    'enemy_entry_attributes', 'finish_reason', 'enemy_max_hp', 'enemy_regen_ticks',
)
PROOF_FIELDS = ('method', 'evidence_method', 'type', 'target', 'buff',
                'before', 'after', 'incoming', 'direct_healing',
                'nested_settlement', 'settlement_unknown_reason')
# Closed allowlists, not prefix-based filtering: a future domain field must
# participate in equality even before the named schema above is updated.
# Binding fields are independently validated within each stream before they
# are removed. Their observer-local values must not become cross-probe IDs.
TRANSPORT_EVENT_FIELDS = frozenset((
    'sequence', 'native_generation', 'native_session', 'source_build',
    'native_process_pid', 'native_stage_id', 'native_identity', 'native_frame',
    'native_fixed_time', 'epoch', 'hook_epoch',
))
DIAGNOSTIC_EVENT_FIELDS = frozenset((
    'caller_rva', 'return_address', 'returnAddress', 'thread_id', 'threadId',
    'trace_id',
))
DIAGNOSTIC_PROOF_FIELDS = frozenset((
    'caller_rva', 'return_address', 'returnAddress', 'thread_id', 'threadId',
    'trace_id',
))
IDENTITY_FIELDS = ('frame', 'game_time', 'kind', 'source', 'source_character_uid',
                   'target', 'target_uid', 'target_instance', 'damage_type',
                   'target_type', 'skill_key', 'skill_id')
QUALITY_FLAGS = ('damage_unknown', 'engagement_unknown', 'shield_unknown', 'skill_unknown')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def _same(left, right):
    return _json(left) == _json(right)


def _load(data):
    return json.loads(data, object_pairs_hook=_unique_fields,
                      parse_float=_finite_number, parse_constant=_reject_constant)


def _clock(value):
    frame, seconds = value.get('frame'), value.get('game_time')
    if (type(frame) is not int or frame < 0 or type(seconds) not in (int, float)
            or not math.isfinite(seconds) or seconds < 0):
        raise ValueError('invalid logical clock')
    return frame, seconds


def _validate_event_transport(event, mark):
    for key in ('native_session', 'source_build', 'native_process_pid', 'native_stage_id',
                'native_generation', 'native_identity'):
        if key in event and not _same(event[key], mark.get(key)):
            raise ValueError('event transport binding disagrees with its verified stream')
    if 'native_frame' in event and not _same(event['native_frame'], event.get('frame')):
        raise ValueError('event raw frame disagrees with its logical frame')
    if 'native_fixed_time' in event:
        fixed = event['native_fixed_time']
        if (not isinstance(fixed, str) or not fixed.isascii() or not fixed.isdecimal()
                or len(fixed) > 19 or int(fixed) > (1 << 63) - 1
                or int(fixed) / (1 << 32) != event.get('game_time')):
            raise ValueError('event raw fixed time disagrees with its logical clock')


def _read_journal(path):
    records = []
    with path.open('rb') as stream:
        while line := stream.readline(MAX_LINE_BYTES + 1):
            if len(line) > MAX_LINE_BYTES:
                raise ValueError('journal line exceeds 16 MiB')
            record = _load(line.decode('utf-8'))
            if (not isinstance(record, dict) or not isinstance(record.get('kind'), str)
                    or not isinstance(record.get('data'), dict)):
                raise ValueError('invalid product journal envelope')
            records.append(record)
    return records


def _verify_terminal(boundary, mark, sample, contract):
    # Production Frida candidates carry native roots but acquire their session
    # binding on the enclosing mark. Do not require runtime-local generations
    # to equal another observer's generations, or invent a missing candidate.
    terminal_roots = boundary.get('native_identity', boundary) if isinstance(boundary, dict) else {}
    if (not isinstance(boundary, dict)
            or any(terminal_roots.get(key) != mark['native_identity'][key] for key in ROOTS)
            or any(key in boundary and boundary[key] != mark['native_identity'][key] for key in ROOTS)
            or any(not _same(boundary.get(key), mark.get(key))
                   for key in ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope'))
            or any(key in boundary and not _same(boundary[key], mark.get(key))
                   for key in ('native_session', 'source_build', 'native_stage_id', 'native_identity',
                               'native_generation', 'native_process_pid', 'native_frame', 'native_fixed_time'))):
        raise ValueError('terminal fence differs from the verified prefix')
    if ('native_process_created_filetime' in boundary
            and boundary['native_process_created_filetime'] != int(sample['native_session'].split(':', 2)[1])):
        raise ValueError('terminal fence belongs to another process birth')
    bound = dict(mark, **boundary)
    if not contract.terminal_identity_matches(sample, bound):
        raise ValueError('terminal fence belongs to another native battle')
    baseline_barrier(mark, sample, bound, contract=contract)


def _verify_terminal_response(evidence, mark, sample, contract):
    raw = evidence.get('terminal_raw_mark')
    if raw is None:
        return  # Older journals stored the unbound source candidate directly.
    if (not isinstance(raw, dict) or any(raw.get(key) for key in ('error', 'fatal', 'stopped'))
            or type(raw.get('active')) is not int or raw['active'] != 0 or raw.get('active_hooks') != []):
        raise ValueError('raw terminal response is not a successful quiescent source fence')
    _verify_terminal(raw.get('terminal_boundary'), mark, sample, contract)
    for key in ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope',
                'native_session', 'source_build', 'native_stage_id', 'native_identity',
                'native_generation', 'native_process_pid', 'native_frame', 'native_fixed_time'):
        if key in raw and not _same(raw[key], mark.get(key)):
            raise ValueError('outer raw terminal response conflicts with the verified prefix')
    if ('native_process_created_filetime' in raw
            and raw['native_process_created_filetime'] != int(sample['native_session'].split(':', 2)[1])):
        raise ValueError('raw terminal response belongs to another process birth')


def _verify_terminal_tail(data, anchor, sample, publication, contract):
    evidence = data['events']
    before, after, batch = (evidence.get(key) for key in ('before', 'after', 'drain'))
    # display_sample deliberately projects state from Stats.ended; pending
    # finalization is published as 2 even though the guarded raw sample is 3.
    # The projection is not an independent new native state witness.
    published_final = isinstance(publication, dict) and (publication.get('state') == 3 or (
        publication.get('state') == 2 and publication.get('lifecycle_state') == 'finalizing'
        and publication.get('event_status') == 'recording'))
    if (anchor is None or sample is None or publication is None or data.get('kind') != 'end'
            or type(data.get('generation')) is not int or data['generation'] < 0
            or sample.get('state') != 3 or not published_final
            or not all(isinstance(part, dict) for part in (before, after, batch))
            or not _same(before, after) or not _same(after, evidence.get('terminal_boundary'))
            or type(evidence.get('read_after')) is not int or evidence['read_after'] != anchor['sequence']
            or type(evidence.get('sequence')) is not int or evidence['sequence'] != after.get('sequence')
            or evidence.get('error')):
        raise ValueError('terminal tail has no independently published state3 anchor or exact closing fence')
    if data.get('sample') is not None and not _same(data['sample'], sample):
        raise ValueError('terminal tail introduces a new or rewritten native snapshot')
    for key in ('frame', 'game_time', 'native_frame', 'native_fixed_time', 'native_identity',
                'native_generation', 'native_process_pid', 'native_stage_id', 'native_session',
                'source_build', 'native_autochess_context', 'damage_settlement_scope'):
        if (key in anchor) != (key in after) or key in anchor and not _same(after[key], anchor[key]):
            raise ValueError('terminal tail changed its independently verified clock or native scope')
    if (type(after.get('epoch')) is not int or after['epoch'] < anchor['epoch']
            or type(after.get('sequence')) is not int or after['sequence'] < anchor['sequence']
            or after['sequence'] > anchor['sequence'] and after['epoch'] <= anchor['epoch']):
        raise ValueError('terminal tail is not a forward bounded event continuation')
    diagnostic = evidence.get('terminal_diagnostic') or {}
    if (diagnostic.get('status') != 'verified' or diagnostic.get('reason') != 'guarded_clock_terminal_tail'
            or diagnostic.get('terminal_only') is not True
            or not _same(diagnostic.get('anchor_sequence'), anchor['sequence'])
            or not _same(diagnostic.get('anchor_epoch'), anchor['epoch'])
            or not _same(evidence.get('probe_contract'), contract.describe())):
        raise ValueError('terminal tail does not retain its exact independent anchor contract')
    raw = evidence.get('terminal_raw_mark')
    if (not isinstance(raw, dict) or type(raw.get('terminal_read_version')) is not int
            or raw['terminal_read_version'] != 1
            or any(not _same(raw.get(key), after.get(key)) for key in ('sequence', 'epoch', 'active'))
            or type(batch.get('terminal_read_version')) is not int or batch['terminal_read_version'] != 1
            or batch.get('terminal_only') is not True or batch.get('retry') is not False
            or batch.get('active_hooks') != [] or type(batch.get('active')) is not int or batch['active'] != 0
            or type(batch.get('after')) is not int or batch['after'] != anchor['sequence']
            or type(batch.get('through')) is not int or batch['through'] != after['sequence']
            or any(not _same(batch.get(key), after.get(key)) for key in ('sequence', 'epoch', 'active'))
            or not _same(batch.get('terminal_boundary'), after)):
        raise ValueError('terminal tail RPC is not the exact supported quiescent read range')
    _verify_terminal(after, after, sample, contract)
    _verify_terminal_response(evidence, after, sample, contract)
    if _quality_problems(raw, contract) or _quality_problems(batch, contract):
        raise ValueError('terminal tail has unknown event coverage')
    return dict(version=1, generation=data['generation'], read_after=anchor['sequence'],
                final_sequence=after['sequence'], anchor_epoch=anchor['epoch'], final_epoch=after['epoch'],
                event_count=after['sequence'] - anchor['sequence'],
                native_counter_advanced=False, counter_snapshot_reused=False,
                source_advanced=(after['sequence'] != anchor['sequence'] or after['epoch'] != anchor['epoch']),
                raw_fence_verified=True, terminal_ack_confirmed=False)


def _verify_terminal_archive(archive, info, anchor, final, sample):
    metadata = archive.get('metadata') or {}
    if (type(archive.get('generation')) is not int or archive['generation'] != info['generation']
            or archive.get('battle_id') != sample.get('session')
            or (archive.get('last_sample') or {}).get('native_session') != sample.get('native_session')
            or not _same(metadata.get('native_counter_watermark'), anchor)
            or not _same(metadata.get('event_source_final_watermark'), final)
            or metadata.get('terminal_pending') is not False or metadata.get('event_source_prefix_complete') is not True):
        raise ValueError('terminal archive does not bind the same independent counter and final event watermarks')
    expected_status = 'unknown' if info['source_advanced'] else 'verified_at_final_watermark'
    if metadata.get('final_counter_status') != expected_status:
        raise ValueError('terminal archive claims an unobserved final native counter snapshot')
    if info['source_advanced']:
        statistics = archive.get('statistics') or {}
        rows = statistics.get('rows')
        if (statistics.get('totals_complete') is not False or not isinstance(rows, dict)
                or any(not isinstance(row, dict) or row.get('counter_snapshot_complete') is not False for row in rows.values())):
            raise ValueError('terminal archive did not invalidate unobserved final cumulative counters')
    status = metadata.get('terminal_ack_status')
    if status not in ('confirmed', 'pending'):
        raise ValueError('terminal archive has no explicit final ACK ticket status')
    if status == 'confirmed' and (archive.get('event_status') != 'finished' or archive.get('event_error')):
        raise ValueError('confirmed terminal ACK has no successful finished archive')
    if status == 'pending' and archive.get('event_status') != 'gap':
        raise ValueError('unconfirmed terminal ACK must not claim finished capture')
    info['terminal_ack_confirmed'] = status == 'confirmed'


def _quality_problems(payload, contract):
    quality = contract.normalize_quality(payload)
    problems = [key for key in QUALITY_FLAGS if quality[key] is not False]
    # Unknown settlement/mitigation/enemy-state coverage must not disappear
    # merely because it was added after the original four-family contract.
    problems.extend(key for key, value in payload.items()
                    if key.endswith('_unknown') and value is not False)
    problems.extend(key for key, value in payload.items()
                    if (key == 'unknown_sources' or key.endswith('_unknown_sources')) and value)
    return sorted(set(problems))


def initial_prefix_evidence(records, sample, mark, first_record_index, contract):
    """Validate a raw read(0,N) made before this exact accepted baseline.

    A zero-clock source witness certifies event inception, not native counter
    values. No snapshot or Statistics baseline is rewritten by this function.
    Other failed baseline attempts are retained in the diagnostic count only.
    """
    candidates = [(index, record['data']) for index, record in enumerate(records)
                  if record['kind'] == 'source_initial_prefix']
    selected = [data for index, data in candidates
                if index < first_record_index and _same(data.get('mark'), mark)]
    evidence = dict(verified=False, candidate_count=len(candidates),
                    matching_candidate_count=len(selected),
                    ignored_candidate_count=len(candidates) - len(selected),
                    native_cumulative_values_certified=False)
    if not selected:
        return [], dict(evidence, reason='no_audited_prefix_for_first_accepted_mark')
    if any(not _same(data, selected[0]) for data in selected[1:]):
        raise ValueError('conflicting initial prefix audits for the accepted baseline')
    data = selected[0]
    boundary = data.get('initial_boundary')
    if boundary is None and mark.get('initial_boundary') is None:
        return [], dict(evidence, reason='no_source_initial_boundary')
    if not isinstance(boundary, dict) or not _same(boundary, mark.get('initial_boundary')):
        raise ValueError('initial boundary differs from its accepted producer mark')
    expected = dict(version=1, source='native-pc-producer', method='BattleController.set_state',
                    phase='leave', state=2, hooks_ready=True, native_frame=0,
                    native_fixed_time='0', frame=0, game_time=0, sequence=0, active=0)
    if any(not _same(boundary.get(key), value) for key, value in expected.items()):
        raise ValueError('initial boundary is not a fully installed zero-clock source inception')
    if (('active_hooks' in boundary and boundary['active_hooks'] != [])
            or any(key.endswith('_unknown') and value is not False for key, value in boundary.items())
            or any((key == 'unknown_sources' or key.endswith('_unknown_sources')) and value
                   for key, value in boundary.items())
            or any(key.endswith('_ready') and value is not True for key, value in boundary.items())):
        raise ValueError('initial boundary declares active callbacks or unknown event coverage')
    if (type(boundary.get('epoch')) is not int or not 0 <= boundary['epoch'] <= mark['epoch']
            or type(boundary.get('native_generation')) is not int or boundary['native_generation'] < 0
            or type(boundary.get('damage_settlement_scope')) is not int
            or boundary['damage_settlement_scope'] < 0
            or any(boundary.get(key) for key in ('error', 'fatal', 'stopped', 'retry'))):
        raise ValueError('invalid initial boundary epoch, generation or settlement scope')
    binding_fields = ('native_identity', 'native_stage_id', 'native_generation',
                      'native_process_pid', 'damage_settlement_scope')
    if (not isinstance(boundary.get('native_identity'), dict)
            or set(boundary['native_identity']) != set(ROOTS)
            or any(not _same(boundary.get(key), mark.get(key)) for key in binding_fields)
            or any(key in boundary and not _same(boundary[key], mark.get(key))
                   for key in ('source_build', 'native_session'))):
        raise ValueError('initial boundary belongs to another native battle or generation')
    if ('native_process_created_filetime' in boundary
            and boundary['native_process_created_filetime'] != int(sample['native_session'].split(':', 2)[1])):
        raise ValueError('initial boundary belongs to another process birth')
    if (type(data.get('read_after')) is not int or data['read_after'] != 0
            or type(data.get('read_through')) is not int or data['read_through'] != mark['sequence']
            or type(data.get('committed_sequence')) is not int or data['committed_sequence'] != 0
            or not _same(data.get('probe_contract'), contract.describe())):
        raise ValueError('initial prefix has no fresh read(0,N) contract evidence')
    batch = data.get('drain')
    if not isinstance(batch, dict) or any(batch.get(key) for key in ('error', 'stopped', 'retry', 'overflow')):
        raise ValueError('initial prefix read failed or overflowed')
    if _quality_problems(mark, contract) or _quality_problems(batch, contract):
        raise ValueError('initial prefix has unknown event-family coverage')
    # validate_batch checks native session/build and every logical event clock;
    # the raw read clock can be later but must still be bound to this battle.
    for key in binding_fields:
        if not _same(batch.get(key), mark.get(key)):
            raise ValueError('initial prefix read belongs to another native battle or generation')
    fixed, frame = batch.get('native_fixed_time'), batch.get('native_frame')
    if (type(frame) is not int or not isinstance(fixed, str) or not fixed.isascii()
            or not fixed.isdecimal() or len(fixed) > 19 or int(fixed) > (1 << 63) - 1
            or frame < sample['frame'] or int(fixed) / (1 << 32) < sample['game_time']
            or frame == sample['frame'] and int(fixed) / (1 << 32) != sample['game_time']):
        raise ValueError('initial prefix read clock conflicts with its accepted watermark')
    stream = SimpleNamespace(sequence=0, baseline_sequence=0, received={})
    contract.validate_batch(sample, mark['sequence'], batch, stream=stream)
    events = []
    previous = (0, 0)
    for event in batch['events']:
        if event['sequence'] <= 0:
            raise ValueError('initial prefix contains an event at or before source inception')
        _validate_event_transport(event, mark)
        clock = _clock(event)
        if clock[0] < previous[0] or clock[1] < previous[1]:
            raise ValueError('initial prefix event clocks are out of order')
        previous = clock
        if event['sequence'] in stream.received:
            if event != stream.received[event['sequence']]:
                raise ValueError('initial prefix has a conflicting replay')
            continue
        if not isinstance(event.get('kind'), str) or not event['kind']:
            raise ValueError('initial prefix event has no kind')
        stream.received[event['sequence']] = event
        events.append(event)
    return events, dict(evidence, verified=True, reason=None, boundary=boundary,
                        through_sequence=mark['sequence'], event_count=len(events))


def zero_cumulative_baseline(sample, roster=None):
    """Describe an actual zero-clock counter baseline, never a source witness."""
    zero = lambda value: type(value) in (int, float) and math.isfinite(value) and value == 0
    rows = sample.get('rows')
    reasons = []
    if type(sample.get('frame')) is not int or sample['frame'] != 0 or not zero(sample.get('game_time')):
        reasons.append('host_sample_is_not_zero_clock')
    counters = ('damage', 'healing', 'physical', 'arts', 'true', 'elemental', 'raw_output')
    row_metadata = ('id', 'counter_snapshot_complete', 'counter_available', 'available', 'availability', 'status')
    if (not isinstance(rows, list) or any(not isinstance(row, dict)
            or not isinstance(row.get('id'), str) or not row['id']
            or not zero(row.get('damage')) or not zero(row.get('healing'))
            or any(key in row and not zero(row[key]) for key in counters)
            or any(key not in counters + row_metadata for key in row)
            or any(key in row and row[key] is not True
                   for key in ('counter_snapshot_complete', 'counter_available', 'available'))
            or any(key in row and row[key] not in ('available', 'ready', 'complete')
                   for key in ('availability', 'status')) for row in rows)
            or len({row.get('id') for row in rows if isinstance(row, dict)}) != len(rows)):
        reasons.append('native_counter_rows_not_verified_zero')
    for field in ('global_total', 'global_heal', 'sum_damage'):
        if field in sample and not zero(sample[field]):
            reasons.append(field + '_not_verified_zero')
    witnesses = sample.get('native_snapshot_witnesses')
    if (sample.get('native_counter_scope') == 'events_only'
            or 'counter_snapshot_complete' in sample and sample['counter_snapshot_complete'] is not True
            or any(key in sample and sample[key] != 'available'
                   for key in ('actor_rows_status', 'rows_status', 'roster_status'))
            or witnesses is not None and (not isinstance(witnesses, dict)
                or any(not isinstance(value, dict) or value.get('status') != 'available'
                       for value in witnesses.values()))):
        reasons.append('native_counters_explicitly_unavailable')
    if rows == []:
        # Match SnapshotAdapter's native initial-presence evidence, tightened
        # to a true zero-clock sample. Empty rows by themselves prove nothing.
        if (not all(zero(sample.get(field)) for field in ('global_total', 'global_heal'))
                or sample.get('presence_duration_supported') is not True or sample.get('state') != 2
                or not isinstance(roster, dict) or roster.get('frame') != sample.get('frame')
                or roster.get('present_known') is not True or roster.get('present_unique_ids') != []
                or not isinstance(roster.get('entries'), list)):
            reasons.append('empty_rows_have_no_verified_initial_empty_roster')
    return dict(verified=not reasons, reasons=reasons,
                scope='actual_host_zero_clock_native_counter_baseline',
                source_initial_boundary_is_not_counter_evidence=True)


def select_native_session(records, summary, native_session):
    """Make an indexed in-memory view; never rewrite the source journal.

    Sessionless end/gap records belong to their validated lifecycle generation,
    not whichever publication happened to be last in the global summary.
    """
    if native_session is None:
        return records, summary, None
    if not isinstance(native_session, str) or not native_session:
        raise ValueError('native session selector must be a nonempty exact identity')
    anchors, generations = [], set()
    last_generation = -1
    generation_sessions = {}
    current_generation = None
    for index, record in enumerate(records):
        kind, data = record['kind'], record['data']
        if kind == 'lifecycle_observation':
            generation = data.get('generation')
            if type(generation) is not int or generation < 0 or generation < last_generation:
                raise ValueError('session selection requires ordered validated lifecycle generations')
            last_generation = current_generation = generation
            sample = data.get('sample')
            if sample is not None:
                if not isinstance(sample, dict) or not isinstance(sample.get('native_session'), str):
                    raise ValueError('session selection has an unbound lifecycle sample')
                session = sample['native_session']
                previous = generation_sessions.setdefault(generation, session)
                if previous != session:
                    raise ValueError('lifecycle generation contains different native battles')
                if session == native_session:
                    generations.add(generation)
                    if (data.get('events') or {}).get('drain') is not None:
                        anchors.append(index)
        elif kind == 'published':
            session = data.get('native_session')
            if (current_generation in generation_sessions
                    and session != generation_sessions[current_generation]):
                raise ValueError('publication conflicts with its lifecycle generation session')
    if len(generations) != 1 or not anchors:
        raise ValueError('selected native session has no single guarded lifecycle generation')
    generation = next(iter(generations))
    selected, indices, omitted = [], [], Counter()
    for index, record in enumerate(records):
        kind, data = record['kind'], record['data']
        keep = True
        if kind == 'lifecycle_observation':
            keep = data['generation'] == generation
            if keep:
                evidence = data.get('events') or {}
                for key in ('before', 'after', 'drain', 'terminal_boundary', 'terminal_raw_mark', 'terminal_raw_candidate'):
                    payload = evidence.get(key)
                    if isinstance(payload, dict) and 'native_session' in payload and payload['native_session'] != native_session:
                        raise ValueError('selected generation carries another battle event or terminal fence')
        elif kind == 'published':
            keep = data.get('native_session') == native_session
        elif kind == 'source_initial_prefix':
            keep = (data.get('mark') or {}).get('native_session') == native_session
        elif kind == 'archive_ready':
            keep = data.get('generation') == generation
            if keep and (data.get('last_sample') or {}).get('native_session') != native_session:
                raise ValueError('selected generation archive belongs to another native battle')
        elif kind == 'drain':
            if 'native_session' not in data:
                raise ValueError('cannot select a battle from an unbound standalone drain')
            keep = data['native_session'] == native_session
        elif isinstance(data.get('native_session'), str):
            keep = data['native_session'] == native_session
        if keep:
            selected.append(record)
            indices.append(index + 1)
        else:
            omitted[kind] += 1
    publications = [record['data'] for record in selected if record['kind'] == 'published']
    if not publications:
        raise ValueError('selected native session has no guarded publication')
    selection = dict(view='derived_in_memory_native_session_selection', native_session=native_session,
                     lifecycle_generation=generation, original_record_count=len(records),
                     selected_record_count=len(selected), dropped_record_count=len(records) - len(selected),
                     dropped_record_kinds=dict(omitted), selected_original_line_indices=indices,
                     selected_guarded_sample_line_indices=[index + 1 for index in anchors],
                     original_summary_samples=summary.get('samples'),
                     original_summary_last_native_session=(summary.get('last_sample') or {}).get('native_session'),
                     selected_publication_count=len(publications),
                     original_files_modified=False)
    derived_summary = dict(summary, samples=len(publications), last_sample=publications[-1],
                           final_statistics=publications[-1].get('statistics'))
    if 'first_sample' in summary:
        derived_summary['first_sample'] = publications[0]
    if 'native_session' in summary:
        derived_summary['native_session'] = native_session
    if 'session' in summary:
        derived_summary['session'] = publications[-1]['session']
    return selected, derived_summary, selection


@dataclass
class Capture:
    manifest: dict
    identity: dict
    events: list
    first_mark: dict
    last_mark: dict
    terminal: bool
    partial_reasons: list
    initial_prefix_events: list
    initial_prefix_evidence: dict
    native_cumulative_baseline: dict
    native_counter_mark: dict
    terminal_tail_evidence: dict | None


def read_capture(directory, source_type, *, native_session=None):
    """Require a continuous guarded prefix, while retaining explicit quality gaps."""
    if source_type not in SOURCE_TYPES:
        raise ValueError('unsupported source type')
    directory = Path(directory).resolve()
    summary_path, journal_path = directory / 'summary.json', directory / 'capture.jsonl'
    summary = _load(summary_path.read_text(encoding='utf-8'))
    records = _read_journal(journal_path)
    if native_session is None and len({r['data'].get('native_session') for r in records
            if r['kind'] == 'published' and r['data'].get('native_session')}) > 1:
        raise ValueError('multiple native battles require an explicit native session selector')
    records, summary, selection = select_native_session(records, summary, native_session)
    if not isinstance(summary, dict) or summary.get('platform') != 'native_pc':
        raise ValueError('missing native PC capture identity')
    instance, build = summary.get('process_instance'), summary.get('game_version')
    if (not isinstance(instance, list) or len(instance) != 2
            or any(type(part) is not int or part <= 0 for part in instance)
            or summary.get('pid') != instance[0] or not isinstance(build, str) or not build):
        raise ValueError('missing native PC process/build identity')
    connections = [r['data'] for r in records if r['kind'] == 'connected']
    if len(connections) != 1:
        raise ValueError('expected one independently connected capture')
    connection = connections[0]
    diagnostics = connection.get('diagnostics') or {}
    details = diagnostics.get('details') or {}
    if (connection.get('pid') != instance[0] or connection.get('process_instance') != instance
            or diagnostics.get('platform') != 'native_pc' or diagnostics.get('version_actual') != build
            or details.get('fixed_module_bytes_match') is not True):
        raise ValueError('missing guarded PC connection/build verification')
    build_check = details.get('build') or {}
    files = {entry.get('file'): entry.get('sha256') for entry in build_check.get('files', [])
             if isinstance(entry, dict) and entry.get('sha256_matches') is True}
    if source_type in ('native-hooks', 'frida') and (
            build not in KNOWN_BUILDS or build_check.get('known_build') is not True
            or files != KNOWN_BUILDS[build]):
        raise ValueError('claimed game source has no matching registered PC fingerprints')

    identity = None
    local_binding = None
    stream = None
    first_mark = last_mark = latest_sample = latest_contract = None
    first_sample = first_contract = first_record_index = first_roster = None
    publications = 0
    terminal = False
    native_counter_mark = latest_publication = tail_info = None
    reasons = []
    if summary.get('error'):
        reasons.append('capture_error: ' + str(summary['error']))
    for record_index, record in enumerate(records):
        kind, data = record['kind'], record['data']
        if kind == 'lifecycle_observation':
            evidence = data.get('events') or {}
            if not isinstance(evidence, dict):
                raise ValueError('invalid lifecycle event envelope')
            batch = evidence.get('drain')
            if batch is None:
                boundary = evidence.get('terminal_boundary')
                if boundary is not None:
                    if last_mark is None:
                        raise ValueError('terminal fence precedes its verified prefix')
                    _verify_terminal(boundary, last_mark, latest_sample, latest_contract)
                    _verify_terminal_response(evidence, last_mark, latest_sample, latest_contract)
                    terminal = True
                elif not terminal and first_mark is not None and data.get('kind') in ('gap', 'scope_unavailable', 'end'):
                    reasons.append('unverified_lifecycle_boundary: ' + data['kind'])
                continue
            if terminal:
                raise ValueError('event sample follows a terminal fence')
            is_tail = 'terminal_tail_version' in evidence
            if is_tail:
                if type(evidence['terminal_tail_version']) is not int or evidence['terminal_tail_version'] != 1:
                    raise ValueError('unsupported terminal tail protocol')
                tail_info = _verify_terminal_tail(data, last_mark, latest_sample, latest_publication, latest_contract)
                sample = latest_sample
            else:
                sample = data.get('sample')
            before, after = evidence.get('before'), evidence.get('after')
            if not all(isinstance(part, dict) for part in (sample, before, after, batch)):
                raise ValueError('missing guarded sample, watermarks or bounded read')
            metadata = sample.get('battle_metadata') or {}
            stage = metadata.get('stage_id')
            native_session = sample.get('native_session')
            if (metadata.get('platform') != 'native_pc' or metadata.get('source_build') != build
                    or not isinstance(stage, str) or not stage or not isinstance(native_session, str)
                    or not native_session.startswith(f'{instance[0]}:{instance[1]}:')):
                raise ValueError('missing guarded battle/stage identity')
            roots = after.get('native_identity')
            if (not isinstance(roots, dict) or set(roots) != set(ROOTS)
                    or any(not isinstance(roots[key], str) or not roots[key].startswith('0x')
                           or not 0 < int(roots[key], 16) < (1 << 64) for key in ROOTS)):
                raise ValueError('missing native battle roots')
            current_identity = dict(process_instance=instance, source_build=build,
                                    native_session=native_session, stage_id=stage, native_identity=roots)
            if identity is None:
                identity = current_identity
            elif not _same(identity, current_identity):
                raise ValueError('capture changes native battle, stage or build')
            current_local_binding = {key: after.get(key) for key in ('native_generation', 'damage_settlement_scope')}
            if any(type(value) is not int or value < 0 for value in current_local_binding.values()):
                raise ValueError('capture has no native generation or settlement scope')
            if local_binding is None:
                local_binding = current_local_binding
            elif current_local_binding != local_binding:
                raise ValueError('capture changes local native generation or settlement scope')
            for part in (before, after, batch):
                fixed = part.get('native_fixed_time')
                if (part.get('native_process_pid') != instance[0] or part.get('native_stage_id') != stage
                        or not _same(part.get('native_identity'), roots)
                        or any(part.get(key) != value for key, value in local_binding.items())
                        or type(part.get('native_frame')) is not int
                        or not isinstance(fixed, str) or not fixed.isascii() or not fixed.isdecimal()
                        or len(fixed) > 19 or int(fixed) > (1 << 63) - 1):
                    raise ValueError('event evidence disagrees with native battle roots or stage')
                if part is batch:
                    # A bounded read is a later RPC. Its independently bound
                    # live clock may have advanced after the closing mark;
                    # validate_batch still limits every event to the original
                    # sampled frame/time and exact through cursor. Never move
                    # the sample fence forward to the RPC's current clock.
                    seconds = int(fixed) / (1 << 32)
                    if (part['native_frame'] < sample['frame'] or seconds < sample['game_time']
                            or part['native_frame'] == sample['frame'] and seconds != sample['game_time']):
                        raise ValueError('bounded read clock precedes or conflicts with its sampled watermark')
                elif part['native_frame'] != sample.get('frame') or int(fixed) / (1 << 32) != sample.get('game_time'):
                    raise ValueError('producer mark clock disagrees with its guarded sample')
            contract = contract_for(sample=sample, descriptor=batch.get('probe_contract'))
            seq = baseline_barrier(before, sample, after, contract=contract)
            if evidence.get('error') or evidence.get('sequence') != seq or any(
                    batch.get(key) for key in ('error', 'stopped', 'retry')):
                raise ValueError('unverified event prefix')
            clock = _clock(after)
            if last_mark is not None and (clock[0] < last_mark['frame']
                    or clock[1] < last_mark['game_time']
                    or clock[0] == last_mark['frame'] and clock[1] != last_mark['game_time']):
                raise ValueError('logical clock moved backwards or changed within a frame')
            validate_native_event_batch(sample, seq, batch, stream=stream)
            if _quality_problems(batch, contract) or not is_tail and _quality_problems(after, contract):
                reasons.append('unknown_event_family_coverage')
            if stream is None:
                stream = SimpleNamespace(sequence=seq, baseline_sequence=seq, received={})
                first_mark = dict(after)
                first_sample, first_contract, first_record_index = sample, contract, record_index
                first_roster = data.get('roster')
            elif seq < stream.sequence:
                raise ValueError('event watermark moved backwards')
            for event in batch['events']:
                _validate_event_transport(event, after)
                if event['sequence'] > stream.sequence:
                    if not isinstance(event.get('kind'), str) or not event['kind']:
                        raise ValueError('event has no kind')
                    _clock(event)
                    if event['kind'] == 'damage_settlement' and event.get('settlement_complete') is not True:
                        reasons.append('incomplete_settlement_event')
                    if stream.received:
                        previous = next(reversed(stream.received.values()))
                        if event['frame'] < previous['frame'] or event['game_time'] < previous['game_time']:
                            raise ValueError('event clocks are out of order')
                    stream.received[event['sequence']] = event
            stream.sequence = seq
            last_mark, latest_sample, latest_contract = dict(after), sample, contract
            if not is_tail:
                native_counter_mark = dict(after)
            else:
                terminal = True
                if tail_info['source_advanced']:
                    reasons.append('terminal_tail_has_no_new_native_counter_snapshot')
            boundary = after.get('terminal_boundary')
            if boundary is not None:
                _verify_terminal(boundary, after, sample, contract)
                terminal = True
        elif kind == 'published':
            if tail_info is not None:
                raise ValueError('source-only terminal tail cannot publish cached rows as a new native snapshot')
            if last_mark is None:
                raise ValueError('publication precedes a guarded event prefix')
            watermark = dict(frame=last_mark['frame'], game_time=last_mark['game_time'], sequence=stream.sequence)
            if (data.get('event_status') not in ('recording', 'finished')
                    or data.get('event_watermark') != watermark
                    or any(data.get(key) != latest_sample.get(key)
                           for key in ('frame', 'game_time', 'session', 'native_session'))
                    or data.get('native_rows') != latest_sample.get('rows')):
                raise ValueError('publication has no matching guarded event prefix')
            publications += 1
            latest_publication = data
        elif kind == 'archive_ready' and tail_info is not None:
            if data.get('generation') == tail_info['generation']:
                _verify_terminal_archive(data, tail_info, native_counter_mark, last_mark, latest_sample)
    if (not publications or first_mark is None or last_mark is None
            or first_mark['frame'] >= last_mark['frame']):
        raise ValueError('no observed guarded battle progression')
    if first_mark['frame'] != 0 or first_mark['sequence'] != 0 or first_mark['game_time'] != 0:
        reasons.append('late_or_nonzero_baseline')
    if not terminal:
        reasons.append('no_verified_terminal_fence')
    if tail_info is not None and not tail_info['terminal_ack_confirmed']:
        reasons.append('terminal_ack_not_confirmed')
    prefix_events, prefix_evidence = initial_prefix_evidence(
        records, first_sample, first_mark, first_record_index, first_contract)
    if prefix_evidence['verified']:
        prefix_by_sequence = {event['sequence']: event for event in prefix_events}
        for record in records:
            drain = (record['data'].get('events') or {}).get('drain') if record['kind'] == 'lifecycle_observation' else None
            for event in (drain or {}).get('events', []):
                if event['sequence'] <= first_mark['sequence'] and event != prefix_by_sequence.get(event['sequence']):
                    raise ValueError('baseline replay conflicts with the audited initial prefix')
        if any(event.get('kind') == 'damage_settlement' and event.get('settlement_complete') is not True
               for event in prefix_events):
            reasons.append('incomplete_settlement_event')
    source = [r['data'].get('probe_source') for r in records if r['kind'] == 'observer_ready']
    manifest = dict(directory=str(directory), asserted_source_type=source_type,
                    observed_probe_sources=source, head=summary.get('head'),
                    summary_sha256=sha256(summary_path.read_bytes()).hexdigest(),
                    journal_sha256=sha256(journal_path.read_bytes()).hexdigest(),
                    source_authenticity_verified=False)
    if selection is not None:
        manifest['selection'] = dict(selection, original_summary_sha256=manifest['summary_sha256'],
                                     original_journal_sha256=manifest['journal_sha256'])
    return Capture(manifest, identity, list(stream.received.values()), first_mark,
                   last_mark, terminal, list(dict.fromkeys(reasons)), prefix_events, prefix_evidence,
                   zero_cumulative_baseline(first_sample, first_roster), native_counter_mark, tail_info)


def semantic_event(event):
    result = {key: value for key, value in event.items()
              if key not in TRANSPORT_EVENT_FIELDS | DIAGNOSTIC_EVENT_FIELDS and key != 'proof'}
    proof = event.get('proof') or {}
    if not isinstance(proof, dict):
        raise ValueError('event proof must be an object')
    # Includes the complete nested parent/child HP certificate and any future
    # proof fields. Equal amounts alone cannot substitute for equal evidence.
    projected = {key: value for key, value in proof.items() if key not in DIAGNOSTIC_PROOF_FIELDS}
    if projected:
        result['proof'] = projected
    return result


def compare_captures(left, right):
    from acceptance.verify_live_events import pc_mechanism_evidence
    if not _same(left.identity, right.identity):
        raise ValueError('captures are not the same process instance, native battle, stage and build')
    # A sample proves a prefix, not that another callback cannot execute later
    # in the same logical frame. Exclude both initial frames and open tail frames.
    from_zero = all(c.first_mark['frame'] == c.first_mark['sequence'] == c.first_mark['game_time'] == 0
                    for c in (left, right))
    from_source_initial = all(c.initial_prefix_evidence['verified'] for c in (left, right))
    start = 0 if from_zero or from_source_initial else max(left.first_mark['frame'], right.first_mark['frame']) + 1
    end = min(c.last_mark['frame'] if c.terminal else c.last_mark['frame'] - 1 for c in (left, right))
    if start > end:
        raise ValueError('no common closed-frame comparison window')
    raw_streams = [[event for event in capture.initial_prefix_events + capture.events if start <= event['frame'] <= end]
                  for capture in (left, right)]
    streams = [[semantic_event(event) for event in events] for events in raw_streams]
    if not any(streams):
        raise ValueError('no events in the common closed-frame window')
    keys = [[_json(event) for event in stream] for stream in streams]
    counts = list(map(Counter, keys))
    only = [counts[0] - counts[1], counts[1] - counts[0]]
    ordered_equal = keys[0] == keys[1]
    order_mismatch = not ordered_equal and not any(only)
    left_by_identity, right_by_identity = defaultdict(list), defaultdict(list)
    for events, target in zip(streams, (left_by_identity, right_by_identity)):
        for event in events:
            target[_json({key: event[key] for key in IDENTITY_FIELDS if key in event})].append(event)
    changed = []
    for identity in left_by_identity.keys() & right_by_identity.keys():
        for first, second in zip(left_by_identity[identity], right_by_identity[identity]):
            if first != second and len(changed) < MAX_DIFFERENCES:
                changed.append(dict(left=first, right=second,
                                    differing_fields=sorted(key for key in first.keys() | second.keys()
                                                            if not _same(first.get(key), second.get(key)))))
    reasons = {side: capture.partial_reasons for side, capture in (('left', left), ('right', right))}
    complete = not any(reasons.values()) and (
        _clock(left.last_mark) == _clock(right.last_mark))
    source_prefix_complete = from_source_initial and all(
        capture.terminal and not [reason for reason in capture.partial_reasons
                                 if reason not in ('late_or_nonzero_baseline', 'terminal_tail_has_no_new_native_counter_snapshot')]
        for capture in (left, right)) and (
        _clock(left.last_mark) == _clock(right.last_mark))
    if not complete and not any(reasons.values()):
        reasons['shared'] = ['terminal_clocks_differ']
    mechanisms = {name: pc_mechanism_evidence(events)
                  for name, events in zip(('left', 'right'), raw_streams)}
    mechanism_equal = {name: ordered_equal and all(side[name]['passed'] for side in mechanisms.values())
                       for name in ('shield', 'elemental', 'nested')}
    status = 'mismatch' if not ordered_equal else ('matched_complete_battle' if complete else 'partial')
    return dict(format='arkdps-pc-dual-probe-comparison', version=1, status=status,
                comparison_equal=ordered_equal, complete_battle_comparison=complete,
                event_prefix_from_initial_boundary_complete=source_prefix_complete,
                native_cumulative_from_zero_complete=complete and from_zero and all(
                    c.native_cumulative_baseline['verified'] for c in (left, right)),
                native_cumulative_baselines={side: capture.native_cumulative_baseline
                                            for side, capture in (('left', left), ('right', right))},
                source_initial_prefix={side: capture.initial_prefix_evidence
                                       for side, capture in (('left', left), ('right', right))},
                native_counter_watermarks={side: capture.native_counter_mark for side, capture in (('left', left), ('right', right))},
                event_source_final_watermarks={side: capture.last_mark for side, capture in (('left', left), ('right', right))},
                terminal_tail_evidence={side: capture.terminal_tail_evidence for side, capture in (('left', left), ('right', right))},
                pc_mechanism_coverage=mechanisms, pc_mechanism_comparison_passed=mechanism_equal,
                all_required_mechanisms_with_complete_event_prefix=(
                    source_prefix_complete and all(mechanism_equal.values())),
                source_authenticity_verified=False, game_connected_by_verifier=False,
                live_acceptance_passed=False,
                evidence_class=('caller_asserted_live' if all(c.manifest['asserted_source_type'] in
                    ('native-hooks', 'frida') for c in (left, right)) else 'controlled_or_offline'),
                limitation='File comparison only; source labels and live provenance are not authenticated.',
                identity=left.identity, sources=dict(left=left.manifest, right=right.manifest),
                window=dict(first_frame=start, last_frame=end,
                            initial_frames_excluded=not (from_zero or from_source_initial),
                            source_initial_boundary_used=from_source_initial,
                            nonterminal_tail_frames_excluded=any(not c.terminal for c in (left, right))), partial_reasons=reasons,
                compared_event_counts=dict(left=len(streams[0]), right=len(streams[1])),
                event_kinds={name: dict(Counter(event['kind'] for event in events))
                             for name, events in zip(('left', 'right'), streams)},
                left_only_count=sum(only[0].values()), right_only_count=sum(only[1].values()),
                left_only=[dict(event=_load(key), count=count) for key, count in list(only[0].items())[:MAX_DIFFERENCES]],
                right_only=[dict(event=_load(key), count=count) for key, count in list(only[1].items())[:MAX_DIFFERENCES]],
                order_mismatch=order_mismatch, changed_events=changed,
                difference_details_limit=MAX_DIFFERENCES,
                unprojected_fields={name: dict(Counter(key for event in events for key in event
                    if key in TRANSPORT_EVENT_FIELDS | DIAGNOSTIC_EVENT_FIELDS))
                    for name, events in zip(('left', 'right'), raw_streams)},
                unprojected_proof_fields={name: dict(Counter(key for event in events
                    for key in (event.get('proof') or {}) if key in DIAGNOSTIC_PROOF_FIELDS))
                    for name, events in zip(('left', 'right'), raw_streams)},
                additional_compared_event_fields=sorted({key for events in raw_streams for event in events
                    for key in event if key not in SEMANTIC_FIELDS
                    and key not in TRANSPORT_EVENT_FIELDS | DIAGNOSTIC_EVENT_FIELDS and key != 'proof'}),
                additional_compared_proof_fields=sorted({key for events in raw_streams for event in events
                    for key in (event.get('proof') or {}) if key not in PROOF_FIELDS
                    and key not in DIAGNOSTIC_PROOF_FIELDS}),
                ignored_transport_fields=sorted(TRANSPORT_EVENT_FIELDS),
                ignored_diagnostic_event_fields=sorted(DIAGNOSTIC_EVENT_FIELDS),
                ignored_diagnostic_proof_fields=sorted(DIAGNOSTIC_PROOF_FIELDS),
                compared_semantic_fields=list(SEMANTIC_FIELDS), compared_proof_fields=list(PROOF_FIELDS))


def compare(left, right, *, left_source, right_source, native_session=None):
    return compare_captures(read_capture(left, left_source, native_session=native_session),
                            read_capture(right, right_source, native_session=native_session))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path, help='First product capture directory')
    parser.add_argument('right', type=Path, help='Second product capture directory')
    parser.add_argument('--left-source', required=True, choices=SOURCE_TYPES)
    parser.add_argument('--right-source', required=True, choices=SOURCE_TYPES)
    parser.add_argument('--native-session', help='Exact native session in a multi-battle journal; creates only an indexed in-memory view')
    parser.add_argument('--output', required=True, type=Path, help='New JSON receipt; existing files are not overwritten')
    args = parser.parse_args(argv)
    try:
        result = compare(args.left, args.right, left_source=args.left_source, right_source=args.right_source,
                         native_session=args.native_session)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
        print(_json(dict(status=result['status'], comparison_equal=result['comparison_equal'], output=str(args.output.resolve()))))
        return 0 if result['status'] == 'matched_complete_battle' else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f'{parser.prog}: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
