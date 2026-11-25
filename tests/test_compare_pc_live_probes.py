from copy import deepcopy
import json

import pytest

from acceptance.compare_pc_live_probes import compare, main, read_capture, semantic_event
from event_probe import NATIVE_PC_CONTRACT
from native_pc.probe_native_pc import BUILD_ID, KNOWN_FILES


ROOTS = dict(controller='0x1000', native='0x2000', logger='0x3000', stats='0x4000', level='0x5000')
SESSION = '10:1234:1:4096:16384:20480'
STAGE = 'Obt/Main/level_main_01-07'


def event(amount=12, **kwargs):
    return dict(frame=2, game_time=2, kind='damage', source='char_a', source_character_uid=1,
                target='enemy_a', target_uid='42', target_instance='0x6000', damage_type=1,
                amount=amount, hp_before=50, hp_after=50-amount, real_delta=-amount,
                proof=dict(method='Entity.ApplyModifier', type=1, target=0), **kwargs)


def capture(directory, events=None, *, offset=0, terminal=True, first_frame=0, last_frame=4,
            session=SESSION, stage=STAGE, build=BUILD_ID, unknown=False):
    directory.mkdir()
    events = deepcopy([event(), event(15)] if events is None else events)
    for index, record in enumerate(events, offset + 1):
        record['sequence'] = index
    common = dict(native_session=session, source_build=build, native_identity=ROOTS,
                  native_process_pid=10, native_stage_id=stage, native_generation=8,
                  damage_settlement_scope=1, damage_unknown=unknown, engagement_unknown=False,
                  shield_unknown=False, skill_unknown=False, quality_contract_version=1,
                  probe_contract=NATIVE_PC_CONTRACT.describe())

    def sample(frame):
        return dict(frame=frame, game_time=frame, session='product-battle-1', native_session=session,
                    rows=[], battle_metadata=dict(platform='native_pc', source_build=build, stage_id=stage))

    def mark(frame, sequence):
        return dict(common, frame=frame, game_time=frame, native_frame=frame,
                    native_fixed_time=str(frame << 32), sequence=sequence, epoch=sequence, active=0)

    def observation(frame, sequence, emitted):
        current = sample(frame)
        fence = mark(frame, sequence)
        return [dict(kind='lifecycle_observation', data=dict(kind='sample', sample=current,
                    events=dict(before=fence, after=fence, sequence=sequence,
                                drain=dict(common, native_frame=frame,
                                           native_fixed_time=str(frame << 32), events=emitted)))),
                dict(kind='published', data=dict(current, native_rows=[], event_status='recording',
                    event_watermark=dict(frame=frame, game_time=frame, sequence=sequence)))]

    details = dict(fixed_module_bytes_match=True, build=dict(known_build=True, files=[
        dict(file=name, sha256=digest, sha256_matches=True) for name, digest in KNOWN_FILES.items()]))
    records = [dict(kind='connected', data=dict(pid=10, process_instance=[10, 1234],
                    diagnostics=dict(platform='native_pc', version_actual=build, details=details))),
               dict(kind='observer_ready', data=dict(probe_source=dict(provider='controlled-test')))]
    records.extend(observation(first_frame, offset, []))
    records.extend(observation(last_frame, offset + len(events), events))
    if terminal:
        final = mark(last_frame, offset + len(events))
        boundary = dict(ROOTS, **{key: final[key] for key in
                        ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope')})
        records.append(dict(kind='lifecycle_observation', data=dict(kind='end', events=dict(terminal_boundary=boundary))))
    summary = dict(platform='native_pc', game_version=build, process_instance=[10, 1234], pid=10,
                   head='controlled-source', error=None)
    (directory / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)) + '\n', encoding='utf-8')
    return records


def compare_controlled(left, right):
    return compare(left, right, left_source='controlled', right_source='controlled')


def write_records(directory, records):
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)) + '\n', encoding='utf-8')


def add_initial_prefix(records, prefix_events=()):
    observation = next(r['data'] for r in records if r['kind'] == 'lifecycle_observation'
                       and (r['data'].get('events') or {}).get('drain') is not None)
    mark = observation['events']['after']
    boundary = dict(version=1, source='native-pc-producer', method='BattleController.set_state', phase='leave',
                    state=2, hooks_ready=True, native_identity=deepcopy(ROOTS), native_stage_id=STAGE,
                    native_generation=8, native_process_pid=10, native_frame=0, native_fixed_time='0',
                    frame=0, game_time=0, sequence=0, epoch=0, active=0, damage_settlement_scope=1)
    for key in ('before', 'after'):
        observation['events'][key] = dict(observation['events'][key], initial_boundary=deepcopy(boundary))
    mark = observation['events']['after']
    drain = deepcopy(observation['events']['drain'])
    drain['events'] = [dict(deepcopy(event), sequence=index) for index, event in enumerate(prefix_events, 1)]
    data = dict(initial_boundary=deepcopy(boundary), mark=deepcopy(mark), drain=drain,
                read_after=0, read_through=mark['sequence'], committed_sequence=0,
                probe_contract=NATIVE_PC_CONTRACT.describe())
    index = next(index for index, record in enumerate(records) if record['data'] is observation)
    records.insert(index, dict(kind='source_initial_prefix', data=data))
    return data


SECOND_SESSION = '10:1234:second-battle'


def multi_capture(directory):
    directory.mkdir()
    first = capture(directory / 'first')
    add_initial_prefix(first)
    second = capture(directory / 'second', offset=2, session=SECOND_SESSION)
    records = []
    for generation, source in enumerate((first, second), 1):
        for record in source:
            if generation == 2 and record['kind'] in ('connected', 'observer_ready'):
                continue
            if record['kind'] == 'lifecycle_observation':
                record['data']['generation'] = generation
                if generation == 2:
                    evidence = record['data'].get('events') or {}
                    for key in ('before', 'after', 'drain'):
                        if isinstance(evidence.get(key), dict):
                            evidence[key].update(native_generation=9, damage_settlement_scope=2)
                    if evidence.get('terminal_boundary'):
                        evidence['terminal_boundary']['damage_settlement_scope'] = 2
            if record['kind'] == 'published':
                record['data']['statistics'] = dict(rows=dict(char_a=dict(sources=['char_a'],
                    observed_damage=0 if record['data']['frame'] == 0 else 27, observed_healing=0,
                    shield_absorbed=0, counter_snapshot_complete=False)))
            records.append(record)
    summary = json.loads((directory / 'second' / 'summary.json').read_text(encoding='utf-8'))
    summary.update(samples=4, last_sample=next(r['data'] for r in reversed(records) if r['kind'] == 'published'))
    (directory / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    write_records(directory, records)
    return records


def add_terminal_tail(records, events=None, *, ack='confirmed'):
    records[:] = [record for record in records if not (record['kind'] == 'lifecycle_observation'
                  and record['data'].get('kind') == 'end')]
    observations = [r['data'] for r in records if r['kind'] == 'lifecycle_observation']
    for observation in observations:
        observation['generation'] = 1
    last = observations[-1]
    last['sample']['state'] = 3
    publication = next(record['data'] for record in reversed(records) if record['kind'] == 'published')
    publication['state'] = 3
    anchor = deepcopy(last['events']['after'])
    tail = [dict(event(), frame=anchor['frame'], game_time=anchor['game_time'], sequence=anchor['sequence'] + 1)] \
        if events is None else deepcopy(events)
    final = dict(anchor, **ROOTS, sequence=anchor['sequence'] + len(tail), epoch=anchor['epoch'] + len(tail))
    # Real bound destroy fences contain source identity, not a forged snapshot.
    final.pop('initial_boundary', None)
    drain = dict(anchor, events=tail, sequence=final['sequence'], epoch=final['epoch'],
                 terminal_read_version=1, terminal_only=True, retry=False, active_hooks=[],
                 after=anchor['sequence'], through=final['sequence'], terminal_boundary=deepcopy(final))
    raw = dict(sequence=final['sequence'], epoch=final['epoch'], active=0, active_hooks=[], retry=True,
               terminal_read_version=1, terminal_boundary=deepcopy(final),
               damage_unknown=False, engagement_unknown=False, shield_unknown=False, skill_unknown=False)
    evidence = dict(terminal_tail_version=1, read_after=anchor['sequence'], sequence=final['sequence'],
                    before=deepcopy(final), after=deepcopy(final), drain=drain, terminal_boundary=deepcopy(final),
                    terminal_raw_mark=raw, probe_contract=NATIVE_PC_CONTRACT.describe(),
                    terminal_diagnostic=dict(status='verified', reason='guarded_clock_terminal_tail',
                        terminal_only=True, anchor_sequence=anchor['sequence'], anchor_epoch=anchor['epoch']))
    records.append(dict(kind='lifecycle_observation', data=dict(kind='end', generation=1, sample=None, events=evidence)))
    archive = dict(battle_id=last['sample']['session'], generation=1, last_sample=deepcopy(last['sample']),
                   event_status='finished' if ack == 'confirmed' else 'gap', event_error=None if ack == 'confirmed' else 'ACK pending',
                   metadata=dict(native_counter_watermark=anchor, event_source_final_watermark=deepcopy(final),
                       terminal_pending=False, event_source_prefix_complete=True, terminal_ack_status=ack,
                       final_counter_status='unknown' if tail else 'verified_at_final_watermark'),
                   statistics=dict(totals_complete=False, rows=dict(char_a=dict(counter_snapshot_complete=False))))
    records.append(dict(kind='archive_ready', data=archive))
    return evidence, archive


def test_independent_sequences_do_not_define_cross_probe_identity(tmp_path):
    capture(tmp_path / 'left')
    capture(tmp_path / 'right', offset=100)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['comparison_equal'] is True
    assert result['status'] == 'partial'
    assert result['complete_battle_comparison'] is False
    assert result['partial_reasons']['right'] == ['late_or_nonzero_baseline']
    assert result['source_authenticity_verified'] is result['game_connected_by_verifier'] is False


def test_full_zero_baseline_and_real_terminal_compare_without_live_claim(tmp_path):
    capture(tmp_path / 'left')
    capture(tmp_path / 'right')
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'matched_complete_battle'
    assert result['complete_battle_comparison'] is True
    assert result['compared_event_counts'] == dict(left=2, right=2)
    assert result['evidence_class'] == 'controlled_or_offline'
    assert result['live_acceptance_passed'] is False
    assert result['sources']['left']['asserted_source_type'] == 'controlled'
    assert result['window']['nonterminal_tail_frames_excluded'] is False
    assert len(result['sources']['left']['journal_sha256']) == 64


@pytest.mark.parametrize('side', ['left', 'right'])
def test_missing_event_and_extra_duplicate_keep_multiplicity(tmp_path, side):
    capture(tmp_path / 'left', [event(), event()] if side == 'right' else [event()])
    capture(tmp_path / 'right', [event(), event()] if side == 'left' else [event()])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'mismatch'
    assert result[f'{side}_only_count'] == 0
    other = 'right' if side == 'left' else 'left'
    assert result[f'{other}_only_count'] == 1
    assert result[f'{other}_only'][0]['count'] == 1


def test_same_frame_event_order_mismatch_cannot_be_hidden_by_sets(tmp_path):
    capture(tmp_path / 'left', [event(12), event(15)])
    capture(tmp_path / 'right', [event(15), event(12)])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['order_mismatch'] is True
    assert result['status'] == 'mismatch'
    assert result['left_only_count'] == result['right_only_count'] == 0


@pytest.mark.parametrize('field,value', [('amount', 11), ('real_delta', -11), ('shield_absorbed', 4),
                                        ('target_uid', '43'), ('source_character_uid', 2)])
def test_amount_settlement_shield_and_identity_differences_are_reported(tmp_path, field, value):
    changed = event()
    changed[field] = value
    capture(tmp_path / 'left', [event()])
    capture(tmp_path / 'right', [changed])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['comparison_equal'] is False
    assert result['left_only_count'] == result['right_only_count'] == 1
    if field not in ('target_uid', 'source_character_uid'):
        assert field in result['changed_events'][0]['differing_fields']


@pytest.mark.parametrize('change', [dict(session='10:1234:other-battle'), dict(stage='other-stage'),
                                    dict(build='win-x64:other-build')])
def test_different_native_battle_stage_or_build_never_compares(tmp_path, change):
    capture(tmp_path / 'left')
    capture(tmp_path / 'right', **change)
    with pytest.raises(ValueError, match='not the same'):
        compare_controlled(tmp_path / 'left', tmp_path / 'right')


def test_nonterminal_tail_is_excluded_and_result_stays_partial(tmp_path):
    tail = dict(event(), frame=4, game_time=4)
    capture(tmp_path / 'left', [event(), tail], terminal=False)
    capture(tmp_path / 'right', [event()], terminal=False)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['comparison_equal'] is True
    assert result['status'] == 'partial'
    assert result['window']['last_frame'] == 3
    assert result['compared_event_counts'] == dict(left=1, right=1)


def test_unknown_coverage_cannot_become_full_pass(tmp_path):
    capture(tmp_path / 'left', unknown=True)
    capture(tmp_path / 'right')
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'partial'
    assert 'unknown_event_family_coverage' in result['partial_reasons']['left']


def test_empty_window_is_not_a_matching_combat_sample(tmp_path):
    capture(tmp_path / 'left', [])
    capture(tmp_path / 'right', [])
    with pytest.raises(ValueError, match='no events'):
        compare_controlled(tmp_path / 'left', tmp_path / 'right')


@pytest.mark.parametrize('fault', ['sequence_gap', 'watermark', 'root', 'publication', 'terminal'])
def test_invalid_prefix_evidence_cannot_be_used_for_cross_probe_comparison(tmp_path, fault):
    directory = tmp_path / 'left'
    records = capture(directory)
    observations = [r['data'] for r in records if r['kind'] == 'lifecycle_observation']
    last = observations[1]
    if fault == 'sequence_gap':
        last['events']['drain']['events'][-1]['sequence'] = 3
    elif fault == 'watermark':
        last['events']['before'] = dict(last['events']['before'], epoch=999)
    elif fault == 'root':
        last['events']['drain']['native_identity'] = dict(ROOTS, native='0x2222')
    elif fault == 'terminal':
        observations[-1]['events']['terminal_boundary']['sequence'] += 1
    else:
        publications = [r['data'] for r in records if r['kind'] == 'published']
        publications[-1]['event_watermark']['sequence'] += 1
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    with pytest.raises(ValueError):
        read_capture(directory, 'controlled')


def test_live_label_requires_known_build_fingerprint_and_is_still_an_assertion(tmp_path):
    capture(tmp_path / 'left', build='win-x64:synthetic')
    with pytest.raises(ValueError, match='registered PC fingerprints'):
        read_capture(tmp_path / 'left', 'native-hooks')
    capture(tmp_path / 'right')
    source = read_capture(tmp_path / 'right', 'frida')
    assert source.manifest['source_authenticity_verified'] is False


def test_cli_writes_new_receipt_but_never_overwrites(tmp_path):
    capture(tmp_path / 'left')
    capture(tmp_path / 'right')
    output = tmp_path / 'comparison.json'
    args = [str(tmp_path / 'left'), str(tmp_path / 'right'), '--left-source', 'controlled',
            '--right-source', 'controlled', '--output', str(output)]
    assert main(args) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['source_authenticity_verified'] is False
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 1


@pytest.mark.parametrize('fault', ['missing', 'changed_child_hp', 'future_field'])
def test_equal_amounts_do_not_hide_different_nested_parent_child_certificates(tmp_path, fault):
    left = event()
    left['proof']['nested_settlement'] = dict(target_uid='42', parent_real_delta=-12,
        parent_hp_before=50, parent_hp_after=38, child_hp_before=38, child_hp_after=0, child_real_delta=-38)
    right = deepcopy(left)
    if fault == 'missing':
        right['proof'].pop('nested_settlement')
    elif fault == 'changed_child_hp':
        right['proof']['nested_settlement']['child_hp_after'] = 1
    else:
        right['proof']['nested_settlement']['future_child_actual_hp'] = 2
    capture(tmp_path / 'left', [left])
    capture(tmp_path / 'right', [right])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'mismatch'
    assert 'proof' in result['changed_events'][0]['differing_fields']


def chain_event(offset=0):
    result = event(sequence=offset + 5)
    result['proof']['nested_elemental_chain'] = dict(version=1, ordering='parent_before_children',
        target_uid='42', parent_real_delta=-12, parent_hp_before=50, parent_hp_after=38,
        children=[dict(source=None, settlement_sequence=offset + 1, burst_sequence=offset + 2,
                       hp_before=38, hp_after=30, real_delta=-8),
                  dict(source='char_b', source_character_uid=2,
                       settlement_sequence=offset + 3, burst_sequence=offset + 4,
                       hp_before=30, hp_after=20, real_delta=-10)])
    return result


def test_nested_chain_cursor_offsets_compare_without_mutating_original_replay_records():
    left, right = chain_event(), chain_event(100)
    original = deepcopy((left, right))
    assert semantic_event(left) == semantic_event(right)
    assert (left, right) == original
    refs = semantic_event(left)['proof']['nested_elemental_chain']['children']
    assert [step['settlement_sequence'] for step in refs] == [-4, -2]
    assert [step['burst_sequence'] for step in refs] == [-3, -1]


@pytest.mark.parametrize('field,value', [('source', 'char_c'), ('hp_after', 21),
    ('real_delta', -9), ('settlement_sequence', 2), ('future_proof_field', 'different')])
def test_chain_cursor_normalization_retains_source_hp_and_relative_reference_differences(field, value):
    left, right = chain_event(), chain_event(100)
    step = right['proof']['nested_elemental_chain']['children'][0]
    step[field] = value + 100 if field == 'settlement_sequence' else value
    if field == 'settlement_sequence':
        with pytest.raises(ValueError, match='sequence references'):
            semantic_event(right)
    else:
        assert semantic_event(left) != semantic_event(right)


@pytest.mark.parametrize('value', [True, None, 0, 5, 9])
def test_chain_references_must_identify_earlier_ordered_records(value):
    record = chain_event()
    record['proof']['nested_elemental_chain']['children'][0]['burst_sequence'] = value
    with pytest.raises(ValueError, match='sequence references'):
        semantic_event(record)


@pytest.mark.parametrize('location', ['event', 'proof'])
def test_new_domain_fields_are_compared_instead_of_silently_projected_away(tmp_path, location):
    left, right = event(), event()
    if location == 'event':
        left['future_actual_absorption'] = dict(amount=10)
        right['future_actual_absorption'] = dict(amount=11)
    else:
        left['proof']['future_actual_absorption'] = dict(amount=10)
        right['proof']['future_actual_absorption'] = dict(amount=11)
    capture(tmp_path / 'left', [left])
    capture(tmp_path / 'right', [right])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'mismatch'
    assert 'future_actual_absorption' in result[f'additional_compared_{location}_fields']


def test_bound_transport_fields_and_known_diagnostics_do_not_become_cross_probe_identity(tmp_path):
    left, right = event(), event()
    for record in (left, right):
        record.update(native_session=SESSION, source_build=BUILD_ID, native_identity=ROOTS,
                      native_process_pid=10, native_stage_id=STAGE, native_generation=8,
                      native_frame=record['frame'], native_fixed_time=str(record['game_time'] << 32))
    left['proof']['caller_rva'], right['proof']['caller_rva'] = '0x123', '0x456'
    left['thread_id'], right['thread_id'] = 100, 200
    capture(tmp_path / 'left', [left])
    capture(tmp_path / 'right', [right])
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'matched_complete_battle'
    assert result['unprojected_proof_fields']['left'] == {'caller_rva': 1}
    assert not result['additional_compared_event_fields']


def test_per_event_transport_binding_is_checked_before_being_ignored(tmp_path):
    record = event()
    record['native_session'] = 'wrong-battle'
    capture(tmp_path / 'left', [record])
    with pytest.raises(ValueError, match='transport binding'):
        read_capture(tmp_path / 'left', 'controlled')


def test_later_bounded_read_clock_does_not_replace_original_guarded_watermark(tmp_path):
    directory = tmp_path / 'left'
    records = capture(directory, terminal=False)
    observation = [r['data'] for r in records if r['kind'] == 'lifecycle_observation'][1]
    observation['events']['drain'].update(native_frame=5, native_fixed_time=str(5 << 32))
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    checked = read_capture(directory, 'controlled')
    assert checked.last_mark['frame'] == 4
    assert checked.last_mark['game_time'] == 4
    assert len(checked.events) == 2
    assert checked.partial_reasons == ['no_verified_terminal_fence']


@pytest.mark.parametrize('frame,seconds', [(3, 3), (5, 3), (4, 5)])
def test_read_clock_backwards_or_same_frame_different_time_is_not_a_valid_late_rpc(tmp_path, frame, seconds):
    directory = tmp_path / 'left'
    records = capture(directory, terminal=False)
    observation = [r['data'] for r in records if r['kind'] == 'lifecycle_observation'][1]
    observation['events']['drain'].update(native_frame=frame, native_fixed_time=str(seconds << 32))
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    with pytest.raises(ValueError, match='bounded read clock'):
        read_capture(directory, 'controlled')


def test_later_read_clock_never_admits_events_beyond_original_through(tmp_path):
    directory = tmp_path / 'left'
    records = capture(directory, terminal=False)
    observation = [r['data'] for r in records if r['kind'] == 'lifecycle_observation'][1]
    batch = observation['events']['drain']
    batch.update(native_frame=5, native_fixed_time=str(5 << 32))
    batch['events'][-1].update(frame=5, game_time=5)
    (directory / 'capture.jsonl').write_text('\n'.join(map(json.dumps, records)), encoding='utf-8')
    with pytest.raises(ValueError, match='bounded watermark'):
        read_capture(directory, 'controlled')


def test_source_initial_certificate_recovers_raw_prefix_but_never_fabricates_zero_snapshot(tmp_path):
    early = dict(event(7), frame=0, game_time=0)
    for side, frame in (('left', 1), ('right', 2)):
        directory = tmp_path / side
        records = capture(directory, offset=1, first_frame=frame)
        add_initial_prefix(records, [early])
        write_records(directory, records)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['comparison_equal'] is True
    assert result['event_prefix_from_initial_boundary_complete'] is True
    assert result['native_cumulative_from_zero_complete'] is False
    assert result['complete_battle_comparison'] is False and result['status'] == 'partial'
    assert result['window']['first_frame'] == 0 and result['compared_event_counts'] == dict(left=3, right=3)
    assert read_capture(tmp_path / 'left', 'controlled').first_mark['frame'] == 1
    assert result['source_initial_prefix']['left']['native_cumulative_values_certified'] is False


def test_initial_same_frame_event_multiplicity_is_not_lost_or_excluded(tmp_path):
    early = dict(event(7), frame=0, game_time=0)
    for side, prefix in (('left', [early]), ('right', [early, early])):
        directory = tmp_path / side
        records = capture(directory, offset=len(prefix), first_frame=2)
        add_initial_prefix(records, prefix)
        write_records(directory, records)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['status'] == 'mismatch' and result['right_only_count'] == 1


@pytest.mark.parametrize('fault', ['missing_hook', 'wrong_birth', 'wrong_root', 'wrong_scope', 'wrong_generation',
                                  'nonzero_source', 'read_not_zero', 'already_committed', 'missing_sequence',
                                  'unknown', 'overflow', 'future_event', 'source_clock', 'epoch',
                                  'initial_active_hooks', 'initial_unknown', 'initial_unknown_sources'])
def test_invalid_initial_source_certificate_or_prefix_cannot_certify_inception(tmp_path, fault):
    directory = tmp_path / 'left'
    records = capture(directory, offset=1, first_frame=2)
    audit = add_initial_prefix(records, [dict(event(), frame=1, game_time=1)])
    boundary = audit['initial_boundary']
    if fault == 'missing_hook':
        boundary['hooks_ready'] = False
    elif fault == 'wrong_birth':
        boundary['native_process_created_filetime'] = 999
    elif fault == 'wrong_root':
        boundary['native_identity']['stats'] = '0x9990'
    elif fault == 'wrong_scope':
        boundary['damage_settlement_scope'] = 2
    elif fault == 'wrong_generation':
        boundary['native_generation'] = 9
    elif fault == 'nonzero_source':
        boundary['sequence'] = 1
    elif fault == 'read_not_zero':
        audit['read_after'] = 1
    elif fault == 'already_committed':
        audit['committed_sequence'] = 1
    elif fault == 'missing_sequence':
        audit['drain']['events'].clear()
    elif fault == 'unknown':
        audit['drain']['damage_settlement_unknown'] = True
    elif fault == 'overflow':
        audit['drain']['error'] = 'event buffer overflow'
    elif fault == 'future_event':
        audit['drain']['events'][0].update(frame=3, game_time=3)
    elif fault == 'source_clock':
        boundary['native_fixed_time'] = '1'
    elif fault == 'epoch':
        boundary['epoch'] = audit['mark']['epoch'] + 1
    elif fault == 'initial_active_hooks':
        boundary['active_hooks'] = ['Entity.ApplyModifier']
    elif fault == 'initial_unknown':
        boundary['damage_unknown'] = True
    else:
        boundary['unknown_sources'] = ['char_bad']
    # Keep the audit's original-source payload exactly attached to the mark;
    # failures must come from its proof, not merely a copied object mismatch.
    for mark in (audit['mark'], *[r['data']['events'][key] for r in records
            if r['kind'] == 'lifecycle_observation' and (r['data'].get('events') or {}).get('drain') is not None
            for key in ('before', 'after')][:2]):
        mark['initial_boundary'] = deepcopy(boundary)
    write_records(directory, records)
    with pytest.raises(ValueError):
        read_capture(directory, 'controlled')


def test_only_prefix_audit_matching_actual_first_accepted_mark_is_used(tmp_path):
    directory = tmp_path / 'left'
    records = capture(directory, first_frame=1)
    audit = add_initial_prefix(records)
    rejected_attempt = deepcopy(audit)
    rejected_attempt['mark']['epoch'] += 1
    rejected_attempt['initial_boundary']['hooks_ready'] = False
    records.insert(2, dict(kind='source_initial_prefix', data=rejected_attempt))
    write_records(directory, records)
    checked = read_capture(directory, 'controlled')
    assert checked.initial_prefix_evidence['verified'] is True
    assert checked.initial_prefix_evidence['ignored_candidate_count'] == 1


def test_post_baseline_prefix_audit_cannot_retroactively_certify_the_baseline(tmp_path):
    directory = tmp_path / 'left'
    records = capture(directory, first_frame=1)
    add_initial_prefix(records)
    audit = records.pop(2)
    records.append(audit)
    write_records(directory, records)
    checked = read_capture(directory, 'controlled')
    assert checked.initial_prefix_evidence['verified'] is False


def test_zero_host_sample_alone_is_not_an_initial_source_certificate_or_zero_counter_proof(tmp_path):
    capture(tmp_path / 'left')
    capture(tmp_path / 'right')
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['complete_battle_comparison'] is True  # preserved legacy gate
    assert result['event_prefix_from_initial_boundary_complete'] is False
    assert result['native_cumulative_from_zero_complete'] is False  # empty rows != known zero


@pytest.mark.parametrize('fault', [None, 'nonzero_row', 'nonzero_global', 'unknown_roster'])
def test_cumulative_zero_requires_independently_observed_native_zero_counter_values(tmp_path, fault):
    for side in ('left', 'right'):
        directory = tmp_path / side
        records = capture(directory)
        first = next(r['data'] for r in records if r['kind'] == 'lifecycle_observation')
        first['sample'].update(global_total=0, global_heal=0, state=2, presence_duration_supported=True)
        first['roster'] = dict(frame=0, entries=[], present_known=True, present_unique_ids=[])
        if side == 'right' and fault:
            if fault == 'nonzero_row':
                first['sample']['rows'] = [dict(id='char_a', damage=1, healing=0)]
                next(r['data'] for r in records if r['kind'] == 'published').update(
                    rows=first['sample']['rows'], native_rows=first['sample']['rows'])
            elif fault == 'nonzero_global':
                first['sample']['global_total'] = 1
            else:
                first['roster']['present_known'] = False
        write_records(directory, records)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['native_cumulative_from_zero_complete'] is (fault is None)
    assert result['complete_battle_comparison'] is True


def test_true_raw_terminal_allows_only_postclosure_bookkeeping_not_preclosure_gap(tmp_path):
    for side in ('left', 'right'):
        directory = tmp_path / side
        records = capture(directory)
        add_initial_prefix(records)
        records.append(dict(kind='lifecycle_observation', data=dict(kind='gap', reason='capture_stopped', events={})))
        records.append(dict(kind='lifecycle_observation', data=dict(kind='end', reason='capture_stopped', events={})))
        if side == 'right':
            records.insert(-3, dict(kind='lifecycle_observation', data=dict(kind='gap', reason='transport_loss', events={})))
        write_records(directory, records)
    left = read_capture(tmp_path / 'left', 'controlled')
    right = read_capture(tmp_path / 'right', 'controlled')
    assert left.partial_reasons == []
    assert right.partial_reasons == ['unverified_lifecycle_boundary: gap']
    assert compare_controlled(tmp_path / 'left', tmp_path / 'right')['event_prefix_from_initial_boundary_complete'] is False


@pytest.mark.parametrize('fault', ['outer_active', 'raw_sequence', 'raw_roots', 'new_drain'])
def test_raw_terminal_cannot_hide_a_conflict_or_allow_a_later_event_drain(tmp_path, fault):
    directory = tmp_path / 'left'
    records = capture(directory)
    last = records[-1]['data']['events']
    last['terminal_raw_mark'] = dict(active=0, active_hooks=[], terminal_boundary=deepcopy(last['terminal_boundary']))
    if fault == 'outer_active':
        last['terminal_raw_mark']['active'] = 1
    elif fault == 'raw_sequence':
        last['terminal_raw_mark']['terminal_boundary']['sequence'] += 1
    elif fault == 'raw_roots':
        last['terminal_raw_mark']['terminal_boundary']['logger'] = '0x8000'
    else:
        records.append(deepcopy(next(r for r in records if r['kind'] == 'lifecycle_observation')))
    write_records(directory, records)
    with pytest.raises(ValueError):
        read_capture(directory, 'controlled')


@pytest.mark.parametrize('fault', ['typed_positive', 'row_incomplete', 'row_unavailable', 'row_no_id',
                                  'duplicate_id', 'future_counter', 'events_only', 'snapshot_unknown'])
def test_zero_cumulative_baseline_cannot_ignore_explicit_nonzero_or_unavailable_native_rows(fault):
    from acceptance.compare_pc_live_probes import zero_cumulative_baseline
    sample = dict(frame=0, game_time=0, rows=[dict(id='char_a', damage=0, healing=0)])
    if fault == 'typed_positive':
        sample['rows'][0]['physical'] = 1
    elif fault == 'row_incomplete':
        sample['rows'][0]['counter_snapshot_complete'] = False
    elif fault == 'row_unavailable':
        sample['rows'][0]['availability'] = 'unknown'
    elif fault == 'row_no_id':
        sample['rows'][0].pop('id')
    elif fault == 'duplicate_id':
        sample['rows'].append(dict(sample['rows'][0]))
    elif fault == 'future_counter':
        sample['rows'][0]['future_cumulative_damage'] = 1
    elif fault == 'events_only':
        sample['native_counter_scope'] = 'events_only'
    else:
        sample['native_snapshot_witnesses'] = dict(actor_rows=dict(status='unavailable'))
    assert zero_cumulative_baseline(sample)['verified'] is False


def test_multi_battle_journal_requires_explicit_selector_and_retains_original_provenance(tmp_path):
    from hashlib import sha256
    directory = tmp_path / 'capture'
    records = multi_capture(directory)
    original = (directory / 'capture.jsonl').read_bytes()
    with pytest.raises(ValueError, match='explicit native session'):
        read_capture(directory, 'controlled')
    first = read_capture(directory, 'controlled', native_session=SESSION)
    second = read_capture(directory, 'controlled', native_session=SECOND_SESSION)
    assert first.terminal is True and first.initial_prefix_evidence['verified'] is True
    assert first.partial_reasons == []
    assert second.first_mark['sequence'] == 2 and second.initial_prefix_evidence['verified'] is False
    assert second.partial_reasons == ['late_or_nonzero_baseline']
    manifest = first.manifest['selection']
    assert manifest['view'] == 'derived_in_memory_native_session_selection'
    assert manifest['native_session'] == SESSION and manifest['lifecycle_generation'] == 1
    assert manifest['original_journal_sha256'] == sha256(original).hexdigest()
    assert manifest['original_record_count'] == len(records)
    assert manifest['selected_record_count'] + manifest['dropped_record_count'] == len(records)
    assert manifest['original_summary_samples'] == 4 and manifest['selected_publication_count'] == 2
    assert manifest['original_summary_last_native_session'] == SECOND_SESSION
    assert all(records[index - 1]['kind'] != 'published' or records[index - 1]['data']['native_session'] == SESSION
               for index in manifest['selected_original_line_indices'])
    assert (directory / 'capture.jsonl').read_bytes() == original


@pytest.mark.parametrize('fault', ['wrong_generation', 'another_sample_same_generation', 'mixed_raw_fence',
                                  'regressed_generation', 'wrong_publication', 'unbound_sample'])
def test_session_selection_cannot_hide_conflicting_generation_sample_or_terminal_evidence(tmp_path, fault):
    directory = tmp_path / 'capture'
    records = multi_capture(directory)
    observations = [r['data'] for r in records if r['kind'] == 'lifecycle_observation']
    if fault == 'wrong_generation':
        observations[1]['generation'] = 2
    elif fault == 'another_sample_same_generation':
        observations[1]['sample']['native_session'] = SECOND_SESSION
    elif fault == 'mixed_raw_fence':
        observations[2]['events']['terminal_boundary']['native_session'] = SECOND_SESSION
    elif fault == 'regressed_generation':
        observations[-1]['generation'] = 0
    elif fault == 'wrong_publication':
        next(r['data'] for r in records if r['kind'] == 'published')['native_session'] = SECOND_SESSION
    else:
        observations[1]['sample'].pop('native_session')
    write_records(directory, records)
    with pytest.raises(ValueError):
        read_capture(directory, 'controlled', native_session=SESSION)


def test_same_selected_battle_compares_but_second_baseline_n_is_not_promoted_to_source_zero(tmp_path):
    for side in ('left', 'right'):
        multi_capture(tmp_path / side)
    first = compare(tmp_path / 'left', tmp_path / 'right', left_source='controlled', right_source='controlled',
                    native_session=SESSION)
    assert first['comparison_equal'] is first['event_prefix_from_initial_boundary_complete'] is True
    second = compare(tmp_path / 'left', tmp_path / 'right', left_source='controlled', right_source='controlled',
                     native_session=SECOND_SESSION)
    assert second['comparison_equal'] is True and second['complete_battle_comparison'] is False
    assert second['event_prefix_from_initial_boundary_complete'] is second['native_cumulative_from_zero_complete'] is False
    assert second['sources']['left']['selection']['lifecycle_generation'] == 2


def test_real_terminal_tail_completes_source_events_but_never_replays_cached_native_counters(tmp_path):
    for side in ('left', 'right'):
        directory = tmp_path / side
        records = capture(directory, terminal=False)
        add_initial_prefix(records)
        add_terminal_tail(records)
        write_records(directory, records)
    result = compare_controlled(tmp_path / 'left', tmp_path / 'right')
    assert result['comparison_equal'] is result['event_prefix_from_initial_boundary_complete'] is True
    assert result['compared_event_counts'] == dict(left=3, right=3)
    assert result['native_counter_watermarks']['left']['sequence'] == 2
    assert result['event_source_final_watermarks']['left']['sequence'] == 3
    assert result['native_cumulative_from_zero_complete'] is result['complete_battle_comparison'] is False
    assert result['terminal_tail_evidence']['left']['terminal_ack_confirmed'] is True
    assert result['window']['nonterminal_tail_frames_excluded'] is False


def test_unconfirmed_terminal_ack_is_visible_but_never_complete(tmp_path):
    directory = tmp_path / 'capture'
    records = capture(directory, terminal=False)
    add_initial_prefix(records)
    add_terminal_tail(records, ack='pending')
    write_records(directory, records)
    parsed = read_capture(directory, 'controlled')
    assert parsed.terminal is True and parsed.terminal_tail_evidence['terminal_ack_confirmed'] is False
    assert 'terminal_ack_not_confirmed' in parsed.partial_reasons


def test_terminal_pending_display_state2_is_not_mistaken_for_a_missing_raw_state3_anchor(tmp_path):
    directory = tmp_path / 'capture'
    records = capture(directory, terminal=False)
    add_initial_prefix(records)
    add_terminal_tail(records, events=[])
    publication = next(record['data'] for record in reversed(records) if record['kind'] == 'published')
    publication.update(state=2, lifecycle_state='finalizing', event_status='recording')
    write_records(directory, records)
    parsed = read_capture(directory, 'controlled')
    assert parsed.terminal is True and parsed.terminal_tail_evidence['event_count'] == 0
    assert parsed.partial_reasons == []
    # Neither publication nor independent raw sample is rewritten by verifier.
    assert publication['state'] == 2
    assert records[-2]['data']['sample'] is None


@pytest.mark.parametrize('fault', ['state3_missing', 'new_snapshot', 'read_after', 'before_differs', 'sequence_gap',
                                  'wrong_clock', 'wrong_roots', 'wrong_generation', 'wrong_scope', 'wrong_birth',
                                  'active_callback', 'read_version', 'read_range', 'unknown_coverage',
                                  'raw_final_differs', 'anchor_epoch', 'ack_before_journal', 'fake_counters_complete',
                                  'fake_final_counter_status', 'archive_wrong_final', 'missing_ack_archive'])
def test_terminal_tail_must_be_real_bounded_same_anchor_and_confirmed_after_journal(tmp_path, fault):
    directory = tmp_path / 'capture'
    records = capture(directory, terminal=False)
    add_initial_prefix(records)
    tail, archive = add_terminal_tail(records)
    if fault == 'state3_missing':
        next(r['data'] for r in reversed(records) if r['kind'] == 'published')['state'] = 2
    elif fault == 'new_snapshot':
        records[-2]['data']['sample'] = dict(archive['last_sample'], rows=[dict(id='invented')])
    elif fault == 'read_after':
        tail['read_after'] = 0
    elif fault == 'before_differs':
        tail['before']['epoch'] += 1
    elif fault == 'sequence_gap':
        tail['drain']['events'][0]['sequence'] += 1
    elif fault in ('wrong_clock', 'wrong_roots', 'wrong_generation', 'wrong_scope', 'wrong_birth'):
        key, value = {'wrong_clock': ('native_fixed_time', '0'), 'wrong_roots': ('native_identity', dict(ROOTS, stats='0x9990')),
                      'wrong_generation': ('native_generation', 9), 'wrong_scope': ('damage_settlement_scope', 2),
                      'wrong_birth': ('native_process_created_filetime', 999)}[fault]
        for part in (tail['before'], tail['after'], tail['terminal_boundary'], tail['drain']['terminal_boundary']):
            part[key] = value
    elif fault == 'active_callback':
        tail['drain']['active_hooks'] = ['Entity.ApplyModifier']
    elif fault == 'read_version':
        tail['drain']['terminal_read_version'] = 2
    elif fault == 'read_range':
        tail['drain']['after'] = 0
    elif fault == 'unknown_coverage':
        tail['drain']['damage_unknown'] = True
    elif fault == 'raw_final_differs':
        tail['terminal_raw_mark']['terminal_boundary']['sequence'] += 1
    elif fault == 'anchor_epoch':
        tail['terminal_diagnostic']['anchor_epoch'] += 1
    elif fault == 'ack_before_journal':
        records[-2:] = records[-1:] + records[-2:-1]
    elif fault == 'fake_counters_complete':
        archive['statistics']['totals_complete'] = True
    elif fault == 'fake_final_counter_status':
        archive['metadata']['final_counter_status'] = 'verified_at_final_watermark'
    elif fault == 'archive_wrong_final':
        archive['metadata']['event_source_final_watermark']['sequence'] += 1
    else:
        records.pop()
    write_records(directory, records)
    if fault in ('ack_before_journal', 'missing_ack_archive'):
        assert 'terminal_ack_not_confirmed' in read_capture(directory, 'controlled').partial_reasons
    else:
        with pytest.raises(ValueError):
            read_capture(directory, 'controlled')
