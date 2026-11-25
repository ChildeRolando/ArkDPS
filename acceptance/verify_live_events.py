"""Reconcile a new product capture's ordered event prefix against its published rows."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace


STATE_ONLY = {'enemy_hp', 'enemy_fake_death', 'enemy_finish'}
PC_MECHANISMS = ('shield', 'elemental', 'nested')
PC_F32_TYPES = {1: 'physical', 2: 'arts', 3: 'true', 4: 'healing', 5: 'elemental'}


def outgoing_counter_event(event):
    """Mirror Statistics' damage-to-friendly/self exclusion, not a value repair.

    Healing still contributes on friendly targets. Raw friendly damage remains
    in the ordered journal and cross-probe comparison, but never supplies the
    game's outgoing damage counter (stats_core.Statistics.event).
    """
    target = event.get('target')
    return not (event.get('kind') == 'damage' and isinstance(target, str)
                and target.startswith(('char_', 'token_')))


def near(left, right):
    if (type(left) not in (int, float) or type(right) not in (int, float)
            or not math.isfinite(left) or not math.isfinite(right)
            or not math.isclose(left, right, rel_tol=1e-7, abs_tol=.01)):
        raise ValueError(f'amount mismatch: {left} != {right}')


def check_pc_f32_counters(events, publications, *, source_domain='all'):
    """PC compatibility entry; caller must first validate registered PC build guards."""
    return check_native_f32_counters(events, publications, source_domain=source_domain, reader_profile='native_pc')


def check_native_f32_counters(events, publications, *, source_domain='all', reader_profile='native_pc'):
    """One pure ordered binary32 model after independent platform source audit.

    Reader profiles select storage evidence and result scope, not arithmetic.
    They do not authenticate processes, source identity, zero-origin counters,
    initial witnesses, terminal fences or the closed-source update instructions.
    The PC CLI still requires its existing fingerprint/batch validation.
    """
    root = Path(__file__).resolve().parents[1]
    if reader_profile == 'native_pc':
        reader = root / 'native_pc' / 'probe_native_pc_rows.py'
        scope = 'selected_guarded_native_pc_published_counter_window'
    elif reader_profile == 'android':
        reader = root / 'collector.py'
        scope = 'independently_audited_android_published_counter_window'
    else:
        raise ValueError('unknown native f32 reader profile; choose native_pc or android')

    def failure(message):
        # Preserve exact PC diagnostics for callers and frozen failure receipts.
        if reader_profile == 'android':
            message = message.replace('PC f32', 'Android f32').replace('PC native', 'Android native')
        return ValueError(message)

    if source_domain not in ('all', 'owned'):
        raise failure('unknown PC f32 source domain; choose all or owned')
    runtime = Path(__file__).resolve().parents[1] / 'event_probe_runtime.js'
    own_contract = "function own(id){return id!==null&&(id.startsWith('char_')||id.startsWith('token_'));}"
    if source_domain == 'owned' and own_contract not in runtime.read_text(encoding='utf-8').splitlines():
        raise failure('shared owned-source contract changed; PC f32 owned domain requires review')
    from source_classification import classify_unmapped_source

    def owned(source):
        return source.startswith(('char_', 'token_'))

    def f32(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise failure('PC f32 counter model requires finite numeric operands')
        try:
            return struct.unpack('<f', struct.pack('<f', value))[0]
        except (OverflowError, struct.error):
            raise failure('PC f32 counter operand is not representable') from None

    baseline = publications[0]['event_watermark']['sequence']
    final = publications[-1]['event_watermark']['sequence']
    previous = baseline
    previous_event_clock = publications[0]['frame'], publications[0]['game_time']
    relevant = []
    for event in events:
        if type(event.get('sequence')) is not int or event['sequence'] != previous + 1:
            raise failure('PC f32 model requires an ordered continuous event prefix')
        previous = event['sequence']
        clock = event.get('frame'), event.get('game_time')
        if (type(clock[0]) is not int or type(clock[1]) not in (int, float)
                or not math.isfinite(clock[1]) or clock[0] < previous_event_clock[0]
                or clock[1] < previous_event_clock[1]):
            raise failure('PC f32 model event clock is missing or out of order')
        previous_event_clock = clock
        if event['kind'] == 'healing':
            if 'damage_type' in event and event['damage_type'] != 4:
                raise failure('PC f32 healing event has a conflicting native type')
            field = 'healing'
        elif event['kind'] == 'elemental_burst' and event.get('source') is not None:
            if type(event.get('damage_type')) is not int or event['damage_type'] != 5:
                raise failure('explicitly sourced PC burst has no elemental native counter type')
            field = 'elemental'
        elif event['kind'] == 'damage' and outgoing_counter_event(event):
            dtype = event.get('damage_type')
            if type(dtype) is not int or dtype not in (1, 2, 3, 5):
                raise failure('PC f32 damage event has no known native type')
            field = PC_F32_TYPES[dtype]
        else:
            continue
        amount = event.get('amount')
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise failure('PC f32 model has an invalid nominal event amount')
        if not isinstance(event.get('source'), str) or not event['source']:
            raise failure('PC f32 counter event has no source identity')
        if source_domain == 'owned' and not owned(event['source']):
            raise failure('PC f32 counter event source is outside the declared owned domain')
        relevant.append((event, field))
    if previous != final:
        raise failure('PC f32 model event prefix does not reach the published watermark')
    by_source = defaultdict(list)
    for event, field in relevant:
        by_source[event['source']].append((event, field))
    seeds, values, cursors, checks = {}, {}, {}, Counter()
    excluded, all_native_sources, validated_rows = {}, set(), 0
    applied = changed_updates = 0
    previous_watermark = baseline
    previous_clock = (-1, -1)
    final_sources = set()
    for publication_index, publication in enumerate(publications):
        watermark = publication.get('event_watermark') or {}
        through = watermark.get('sequence')
        clock = publication.get('frame'), publication.get('game_time')
        if (type(through) is not int or through < previous_watermark
                or type(clock[0]) is not int or type(clock[1]) not in (int, float)
                or not math.isfinite(clock[1])
                or clock[0] < previous_clock[0] or clock[1] < previous_clock[1]):
            raise failure('PC f32 counter publication watermark moved backwards')
        previous_watermark, previous_clock = through, clock
        rows = publication.get('native_rows')
        if not isinstance(rows, list):
            raise failure('PC f32 model requires original native counter rows')
        present, modeled_present = set(), set()
        for row in rows:
            source = row.get('id') if isinstance(row, dict) else None
            if not isinstance(source, str) or not source or source in present:
                raise failure('PC f32 native counter row identity is missing or duplicated')
            present.add(source)
            all_native_sources.add(source)
            for field in PC_F32_TYPES.values():
                value = row.get(field)
                if value is None or f32(value) != value or value < 0:
                    raise failure('PC f32 native counter column is missing or not binary32: ' + field)
            expected_damage = sum(row[field] for field in ('physical', 'arts', 'true', 'elemental'))
            if type(row.get('damage')) not in (int, float) or row['damage'] != expected_damage:
                raise failure('PC native damage total differs from its binary32 typed columns')
            validated_rows += 1
            if source_domain == 'owned':
                if 'raw_output' in row and f32(row['raw_output']) != row['raw_output']:
                    raise failure('PC native raw output column is not binary32')
                if not owned(source):
                    classification = classify_unmapped_source(source)
                    if classification is None:
                        raise failure('PC f32 owned domain cannot exclude an unknown native source: ' + source)
                    point = dict(publication_index=publication_index, sequence=through,
                                 frame=clock[0], game_time=clock[1], native_row=dict(row))
                    if source not in excluded:
                        excluded[source] = dict(classification=classification[0], reason=classification[1],
                            exclusion='proven non-team source outside shared own(char_/token_) counter-event domain',
                            seed=point, final=point, observation_count=0, change_points=[])
                    evidence = excluded[source]
                    if not evidence['change_points'] or evidence['final']['native_row'] != row:
                        evidence['change_points'].append(point)
                    evidence['final'] = point
                    evidence['observation_count'] += 1
                    continue
            modeled_present.add(source)
            if source not in seeds:
                before_seed = [event['sequence'] for event, _field in by_source[source]
                               if event['sequence'] <= through]
                if (source_domain == 'owned' and through > baseline
                        and any(row[field] > 0 for field in PC_F32_TYPES.values()) and not before_seed):
                    raise failure('PC f32 owned positive late seed has no covered source counter event: ' + source)
                values[source] = {field: row[field] for field in PC_F32_TYPES.values()}
                cursors[source] = through
                seeds[source] = dict(sequence=through, frame=clock[0], game_time=clock[1], values=dict(values[source]),
                    unmodeled_prefix=dict(after_sequence=baseline, through_sequence=through,
                        counter_event_sequences=before_seed,
                        reason='first_independently_observed_source_row_is_a_seed_not_a_modelled_zero_origin'))
            else:
                for event, field in by_source[source]:
                    if cursors[source] < event['sequence'] <= through:
                        prior = values[source][field]
                        values[source][field] = f32(prior + f32(event['amount']))
                        changed_updates += values[source][field] != prior
                        applied += 1
                cursors[source] = through
            for field in PC_F32_TYPES.values():
                checks[source] += 1
                if values[source][field] != row[field]:
                    raise failure(f'PC f32 exact counter mismatch: {source}/{field} at sequence {through}: '
                                     f"{values[source][field]} != {row[field]}")
        if source_domain == 'owned' and set(seeds) - modeled_present:
            raise failure('PC f32 owned domain is missing an eligible source row from a publication')
        final_sources = modeled_present
    if not seeds or not applied:
        raise failure('PC f32 model has no observed seeded counter updates')
    if source_domain == 'owned' and not changed_updates:
        raise failure('PC f32 owned model has no changed owned counter update')
    if set(by_source) - set(seeds) or set(seeds) - final_sources:
        raise failure('PC f32 model has a source with no independently observed final counter row')
    for source, evidence in excluded.items():
        evidence['last_observed'] = evidence['final']
        evidence['final_publication_present'] = source in present
        evidence['change_point_count'] = len(evidence['change_points'])
        if source not in present:
            evidence['final'] = None
    return dict(model='binary32_operand_then_binary32_add_in_event_sequence_order',
                scope=scope, reader_profile=reader_profile, source_identity_verified=False,
                independent_input_guard_required=True,
                source_domain=source_domain, modeled_native_sources=sorted(seeds),
                all_native_sources=sorted(all_native_sources), excluded_native_sources=excluded,
                all_native_sources_verified=not excluded,
                verification_domain_meaning='selected_native_sources_at_each_publication_after_their_own_independent_seeds',
                native_counter_from_zero_verified=False,
                full_native_row_counter_model_verified=not excluded,
                all_native_rows_format_validated=True, validated_native_row_count=validated_rows,
                ordered_raw_event_count=len(events), original_event_order_preserved=True,
                source_domain_evidence=dict(shared_runtime_path=str(runtime),
                    shared_runtime_sha256=sha256(runtime.read_bytes()).hexdigest(),
                    owned_source_rule=own_contract,
                    classification_path=str(Path(__file__).resolve().parents[1] / 'source_classification.py'),
                    classification_sha256=sha256((Path(__file__).resolve().parents[1] / 'source_classification.py').read_bytes()).hexdigest()),
                exact=True, exact_typed_checks=sum(checks.values()), checks_by_source=dict(checks),
                applied_event_count=applied, changed_typed_update_count=changed_updates, seeds=seeds, final_typed_values=values,
                baseline_sequence=baseline, final_sequence=final,
                first_clock=dict(frame=publications[0]['frame'], game_time=publications[0]['game_time']),
                final_clock=dict(frame=publications[-1]['frame'], game_time=publications[-1]['game_time']),
                storage_dtype_evidence=dict(reader_path=str(reader), reader_sha256=sha256(reader.read_bytes()).hexdigest(),
                    format='<6f', typed_column_order=list(PC_F32_TYPES.values())),
                closed_source_update_instructions_verified=False,
                algorithm_status='exactly_supported_by_this_observed_window_not_disassembly_verified',
                tolerance_fallback_used=False)


def _explicit_owned_actor_identity(event):
    source, uid = event.get('source'), event.get('source_character_uid')
    if (isinstance(source, str) and source.startswith(('char_', 'token_'))
            and type(uid) is int and 0 < uid <= 0xffffffff):
        return source, uid
    return None


def _complete_elemental_settlement_emitter(event):
    """Recognize the shared producer's two complete elemental emitters.

    settleDamage chooses targetCommit's ApplyModifier scope, else its verified
    output callback (or the original scope). This is not a general callback
    whitelist: a source-null settlement cannot use an output-owner callback.
    All same-hit amount/HP/clock/UID/uniqueness checks remain with the caller.
    """
    if event.get('kind') != 'damage_settlement' or event.get('settlement_complete') is not True:
        return False
    proof = event.get('proof')
    if not isinstance(proof, dict):
        return False
    if proof.get('method') == 'Entity.ApplyModifier':
        return True
    nominal = event.get('nominal_amount')
    return (proof.get('method') == 'Entity.OnAfterOutputDamage'
            and type(event.get('damage_type')) is int and event['damage_type'] == 5
            and _explicit_owned_actor_identity(event) is not None
            and type(nominal) in (int, float) and math.isfinite(nominal) and nominal >= 0
            and type(event.get('killed')) is bool)


def check_pc_owner_counter_supplements(events, publications, ownership):
    """Replay the existing source-null burst supplement, at each watermark.

    Native typed counters and attributed source-null elemental settlement are
    distinct accounts (Stats.event/result and SnapshotAdapter.result). Actual
    HP loss cannot replace a nominal supplement, and one bar cannot be consumed
    by two bursts. This does not claim that a source-null burst had a caster.
    """
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    bursts = [event for event in events if event['kind'] == 'elemental_burst']
    stage = (publications[0].get('battle_metadata') or {}).get('stage_id')
    session = publications[0].get('native_session')
    if bursts and (not isinstance(stage, str) or not stage or not isinstance(session, str)
            or any(pub.get('native_session') != session
                   or (pub.get('battle_metadata') or {}).get('stage_id') != stage for pub in publications)):
        raise ValueError('elemental supplement has no single independently bound stage/session window')
    pairs, used = {}, set()
    for burst in bursts:
        actual = burst.get('real_delta')
        proof = burst.get('proof') or {}
        if (not isinstance(proof, dict) or proof.get('method') != 'Entity.ApplyModifier'
                or type(proof.get('type')) is not int or proof['type'] != 5
                or type(proof.get('target')) is not int or proof['target'] != 0
                or type(burst.get('damage_type')) is not int or burst['damage_type'] != 5
                or type(burst.get('target_type')) is not int or burst['target_type'] != 0
                or type(burst.get('cancel_reason')) is not int or burst['cancel_reason'] != 0
                or not finite(burst.get('amount')) or not finite(burst.get('hp_before'))
                or not finite(burst.get('hp_after')) or not finite(burst.get('shield_absorbed'))
                or type(actual) not in (int, float) or not math.isfinite(actual) or actual > 0
                or not math.isclose(burst['hp_before'] - burst['hp_after'], -actual, rel_tol=1e-7, abs_tol=.01)
                or burst['amount'] + .01 < -actual + burst['shield_absorbed']
                or type(burst.get('killed')) is not bool or burst['killed'] != (burst['hp_after'] == 0)):
            raise ValueError('elemental supplement has no complete raw nominal/actual burst witness')
        candidates = [event for event in events if event['kind'] == 'damage_settlement'
            and event.get('settlement_complete') is True and event.get('damage_type') == 5
            and event.get('target') == burst.get('target') and event.get('target_instance') == burst.get('target_instance')
            and all(event.get(key) == burst.get(key) for key in ('frame', 'game_time', 'source', 'source_character_uid'))
            and event['sequence'] < burst['sequence'] and event.get('amount') == -actual
            and event.get('shield_absorbed') == burst['shield_absorbed']
            and event.get('nominal_amount') == burst['amount']
            and _complete_elemental_settlement_emitter(event)
            and ((event.get('proof') or {}).get('method') != 'Entity.OnAfterOutputDamage'
                 or _explicit_owned_actor_identity(event) == _explicit_owned_actor_identity(burst)
                 and event.get('killed') == burst.get('killed'))
            and isinstance(event.get('target_uid'), str) and event['target_uid'].isascii()
            and event['target_uid'].isdecimal() and 0 < int(event['target_uid']) <= 0xffffffff
            and str(int(event['target_uid'])) == event['target_uid']
            and event['sequence'] not in used]
        if len(candidates) != 1 or candidates[0]['sequence'] in used:
            raise ValueError('elemental supplement has no unique same-hit settlement certificate')
        settlement = candidates[0]
        if 'target_uid' in burst and burst['target_uid'] != settlement['target_uid']:
            raise ValueError('elemental burst UID conflicts with its same-hit settlement')
        used.add(settlement['sequence'])
        pairs[burst['sequence']] = settlement
    bars, supplements, witnesses, consumed = {}, Counter(), [], {}
    cursor = publications[0]['event_watermark']['sequence']
    index, checks = 0, 0
    for publication in publications:
        through = publication['event_watermark']['sequence']
        while index < len(events) and events[index]['sequence'] <= through:
            event = events[index]
            index += 1
            if event['sequence'] <= cursor:
                continue
            kind = event['kind']
            if 'native_stage_id' in event and event['native_stage_id'] != stage:
                raise ValueError('elemental supplement event belongs to another stage')
            if kind in ('enemy_enter', 'enemy_reborn'):
                uid = event.get('target_instance')
                bars = {key: value for key, value in bars.items() if key[0] != uid}
            elif kind == 'damage' and event.get('killed') is True and event.get('settled_after_nested_kill', False) is False:
                bars = {key: value for key, value in bars.items() if key[1] != event.get('target_instance')}
            elif kind == 'elemental_bar':
                uid, instance, source = event.get('target_uid'), event.get('target_instance'), event.get('source')
                proof = event.get('proof') or {}
                if (not isinstance(uid, str) or not uid.isascii() or not uid.isdecimal() or not 0 < int(uid) <= 0xffffffff
                        or str(int(uid)) != uid or not isinstance(instance, str) or not instance
                        or source not in ownership or type(event.get('source_character_uid')) is not int
                        or not 0 < event['source_character_uid'] <= 0xffffffff or not finite(event.get('amount'))
                        or not isinstance(proof, dict) or proof.get('method') != 'Entity.ApplyModifier'
                        or type(proof.get('type')) is not int or proof['type'] != 0
                        or type(proof.get('target')) is not int or proof['target'] != 5):
                    raise ValueError('elemental supplement bar has no verified owner and UID/instance evidence')
                previous = consumed.get((uid, instance))
                if previous is not None and (event['frame'] < previous[0] or event['game_time'] < previous[1]):
                    raise ValueError('elemental bar reuses context from before an already consumed burst')
                bars[uid, instance] = event
            elif kind == 'elemental_burst':
                settlement = pairs[event['sequence']]
                key = settlement['target_uid'], event.get('target_instance')
                source = event.get('source')
                if source is None:
                    bar = bars.get(key)
                    if (bar is None or bar['sequence'] >= settlement['sequence']
                            or bar['frame'] > event['frame'] or bar['game_time'] > event['game_time']):
                        raise ValueError('source-null burst has no preceding unconsumed same-target owned elemental bar')
                    owner = ownership[bar['source']]
                    supplements[owner] += event['amount']
                    witnesses.append(dict(owner=owner, nominal_supplement=event['amount'],
                        actual_hp_damage=-event['real_delta'], target_uid=key[0], target_instance=key[1],
                        bar_sequence=bar['sequence'], settlement_sequence=settlement['sequence'], burst_sequence=event['sequence']))
                elif source not in ownership:
                    raise ValueError('explicitly sourced burst has no declared owner')
                bars.pop(key, None)  # Explicit source bursts consume context but add no supplement.
                consumed[key] = event['frame'], event['game_time']
        cursor = through
        native = {row['id']: row for row in publication['native_rows']}
        for owner, row in publication['statistics']['rows'].items():
            observed = row.get('burst_added_settlement')
            expected = supplements[owner]
            if observed is None and expected == 0:
                observed = 0
            if not finite(observed) or observed != expected:
                raise ValueError('published elemental nominal supplement differs at its actual watermark')
            checks += 1
            if row.get('counter_snapshot_complete') is True:
                sources = row.get('sources')
                if not isinstance(sources, list) or len(set(sources)) != len(sources):
                    raise ValueError('complete owner counter row has no unique native source rows')
                for kind in ('damage', 'healing'):
                    # Adapter.sources also lists roster aliases that never
                    # produced a counter. The exact typed model separately
                    # rejects every actual contributing source without rows.
                    total = sum(native[source][kind] for source in sources if source in native)
                    if kind == 'damage':
                        total += expected
                    if not finite(row.get(kind)) or row[kind] != total:
                        raise ValueError('published owner counter differs from its native counters and verified nominal supplement')
                    checks += 1
    return dict(scope='each_published_native_counter_watermark', exact=True, checks=checks,
                final_nominal_supplements=dict(supplements), source_null_burst_witnesses=witnesses,
                explicit_source_bursts_supplemented=False, future_events_used=False,
                owning_sources={str(path): sha256(path.read_bytes()).hexdigest() for path in (
                    Path(__file__).resolve().parents[1] / 'stats_core.py',
                    Path(__file__).resolve().parents[1] / 'snapshot_adapter.py')})


# Historical PC name remains compatible; both platforms use the same body.
check_native_owner_counter_supplements = check_pc_owner_counter_supplements


def pc_mechanism_evidence(events):
    """Require positive, internally bound raw witnesses, not equal empty sets.

    This is mechanism sample coverage, not proof of live acquisition or of all
    game skills. Sequence values are used only inside their own source stream.
    """
    def number(value, *, nonnegative=False):
        return (type(value) in (int, float) and math.isfinite(value)
                and (not nonnegative or value >= 0))

    def equal(left, right):
        return number(left) and number(right) and math.isclose(left, right, rel_tol=1e-7, abs_tol=.01)

    def clock_equal(left, right):
        return left.get('frame') == right.get('frame') and left.get('game_time') == right.get('game_time')

    def proof_of(event):
        proof = event.get('proof')
        return proof if isinstance(proof, dict) else {}

    def owned_actor(event):
        return (isinstance(event.get('source'), str) and event['source'].startswith(('char_', 'token_'))
                and type(event.get('source_character_uid')) is int and 0 < event['source_character_uid'] <= 0xffffffff)

    def target_equal(left, right):
        return (isinstance(left.get('target_instance'), str) and bool(left['target_instance'])
                and left.get('target_instance') == right.get('target_instance')
                and isinstance(left.get('target'), str) and left['target'].startswith(('enemy_', 'trap_'))
                and left['target'] == right.get('target')
                and ('target_uid' not in left or left['target_uid'] == right.get('target_uid')))

    def actor_equal(left, right):
        return all(left.get(key) == right.get(key) for key in ('source', 'source_character_uid'))

    def settled(event, *, elemental=False):
        return (event.get('kind') == 'damage_settlement' and event.get('settlement_complete') is True
                and isinstance(event.get('target_uid'), str) and event['target_uid'].isascii()
                and event['target_uid'].isdecimal() and 0 < int(event['target_uid']) <= 0xffffffff
                and str(int(event['target_uid'])) == event['target_uid']
                and (_complete_elemental_settlement_emitter(event) if elemental
                     else proof_of(event).get('method') == 'Entity.ApplyModifier')
                and number(event.get('amount'), nonnegative=True)
                and number(event.get('shield_absorbed'), nonnegative=True))

    def hp_evidence(event):
        before, after, real = (event.get(key) for key in ('hp_before', 'hp_after', 'real_delta'))
        return (number(before, nonnegative=True) and number(after, nonnegative=True)
                and number(real) and real <= 0 and equal(before - after, -real)
                and type(event.get('cancel_reason')) is int and event['cancel_reason'] == 0
                and type(event.get('target_type')) is int and event['target_type'] == 0
                and number(event.get('shield_absorbed'), nonnegative=True))

    def same_scope(left, right):
        key = 'native_autochess_scope'
        if key not in left and key not in right:
            return True
        names = ('scope_key', 'phase', 'round', 'collection_mode')
        for event in (left, right):
            stamp = event.get(key)
            if (not isinstance(stamp, dict) or type(stamp.get('protocol_version')) is not int
                    or stamp['protocol_version'] != 1 or stamp.get('status') != 'available'
                    or stamp.get('verified_same_frame') is not True
                    or type(stamp.get('frame')) is not int or stamp['frame'] < 0
                    or type(event.get('frame')) is not int or not number(event.get('game_time'), nonnegative=True)
                    or not number(stamp.get('game_time'), nonnegative=True)
                    or not clock_equal(stamp, event)
                    or not isinstance(stamp.get('scope_key'), str) or not stamp['scope_key']
                    or type(stamp.get('phase')) is not int or stamp['phase'] not in (4, 5, 6)
                    or type(stamp.get('round')) is not int or stamp['round'] < 0
                    or stamp.get('collection_mode') not in ('all', 'local')):
                return False
        return all(left[key][name] == right[key][name] for name in names)

    shield_ok, shield_bad = [], []
    for event in events:
        if event.get('kind') != 'shield_absorbed' or not number(event.get('amount')) or event['amount'] <= 0:
            continue
        proof = proof_of(event)
        before, after, incoming = (proof.get(key) for key in ('before', 'after', 'incoming'))
        valid = (proof.get('buff') in ('shining_s_2', 'catsld_t_1[core]')
                 and all(number(value, nonnegative=True) for value in (before, after, incoming))
                 and equal(before - after, event['amount'])
                 and equal(min(before, incoming), event['amount']))
        (shield_ok if valid else shield_bad).append(event.get('sequence'))

    bars = [event for event in events if event.get('kind') == 'elemental_bar'
            and number(event.get('amount')) and event['amount'] > 0]
    bursts = [event for event in events if event.get('kind') == 'elemental_burst'
              and number(event.get('amount')) and event['amount'] > 0]
    elemental_ok, elemental_bad, burst_settlements = [], [], {}
    verified_burst_pairs = {}
    used_settlements = set()
    last_burst_by_target = {}
    for burst in bursts:
        real = burst.get('real_delta')
        source_null = burst.get('source') is None
        actor_valid = burst.get('source_character_uid') is None if source_null else owned_actor(burst)
        candidates = [event for event in events if settled(event, elemental=True)
                      and event.get('damage_type') == 5 and clock_equal(burst, event)
                      and target_equal(burst, event) and actor_equal(burst, event)
                      and event['sequence'] < burst['sequence']
                      and number(real) and equal(event['amount'], -real)
                      and equal(event['shield_absorbed'], burst.get('shield_absorbed'))
                      and (proof_of(event).get('method') != 'Entity.OnAfterOutputDamage'
                           or _explicit_owned_actor_identity(event) == _explicit_owned_actor_identity(burst)
                           and event.get('nominal_amount') == burst.get('amount')
                           and event.get('killed') == burst.get('killed'))
                      and event['sequence'] not in used_settlements]
        matching_bars = [bar for bar in bars if proof_of(bar).get('method') == 'Entity.ApplyModifier'
                         and type(proof_of(bar).get('type')) is int and proof_of(bar)['type'] == 0
                         and type(proof_of(bar).get('target')) is int and proof_of(bar)['target'] == 5
                         and owned_actor(bar)
                         and bar.get('target_instance') == burst.get('target_instance')
                         and bar['sequence'] > last_burst_by_target.get(burst.get('target_instance'), 0)
                         and bar['sequence'] < burst['sequence']
                         and bar['frame'] <= burst['frame'] and bar['game_time'] <= burst['game_time']
                         and ('target_uid' not in bar or len(candidates) == 1
                              and bar['target_uid'] == candidates[0]['target_uid'])]
        paired = (hp_evidence(burst) and type(burst.get('damage_type')) is int and burst['damage_type'] == 5
                 and actor_valid
                 and proof_of(burst).get('method') == 'Entity.ApplyModifier'
                 and type(proof_of(burst).get('type')) is int and proof_of(burst)['type'] == 5
                 and type(proof_of(burst).get('target')) is int and proof_of(burst)['target'] == 0
                 and burst['amount'] + .01 >= -burst['real_delta'] + burst['shield_absorbed']
                 and burst['real_delta'] < 0 and len(candidates) == 1
                 and candidates[0]['sequence'] not in used_settlements)
        if paired:
            verified_burst_pairs[burst['sequence']] = candidates[0]
            used_settlements.add(candidates[0]['sequence'])
        # The producer's raw kind covers two native type-5 cases. Only a
        # source-null burst needs last-bar attribution; explicitly sourced EP
        # damage is already in that actor's native elemental counter. It does
        # not prove a new bar-triggered burst or consume this classifier's bar.
        valid = paired and (bool(matching_bars) if source_null else True)
        if valid:
            burst_settlements[burst['sequence']] = candidates[0]
            if source_null:
                elemental_ok.append(dict(category='source_null_bar_burst',
                    bar_sequences=[bar['sequence'] for bar in matching_bars],
                    burst_sequence=burst['sequence'], settlement_sequence=candidates[0]['sequence']))
            else:
                elemental_ok.append(dict(category='native_elemental_contribution', source=burst['source'],
                    source_character_uid=burst['source_character_uid'],
                    burst_sequence=burst['sequence'], settlement_sequence=candidates[0]['sequence']))
        else:
            elemental_bad.append(burst['sequence'])
        if source_null:
            last_burst_by_target[burst.get('target_instance')] = burst['sequence']

    nested_ok, nested_bad = [], []
    used_parent_settlements, used_child_bursts = set(), set()
    chain_key = 'nested_elemental_chain'
    parents = [event for event in events if event.get('kind') == 'damage'
               and event.get('settled_after_nested_kill') is True and chain_key not in proof_of(event)]
    for parent in parents:
        proof = proof_of(parent).get('nested_settlement')
        proof = proof if isinstance(proof, dict) else {}
        values = {key: proof.get(key) for key in ('parent_real_delta', 'parent_hp_before', 'parent_hp_after',
                  'child_hp_before', 'child_hp_after', 'child_real_delta', 'parent_entry_hp_before')}
        parent_settlements = [event for event in events if settled(event)
            and event.get('settled_after_nested_kill') is True and event.get('damage_type') == parent.get('damage_type')
            and clock_equal(parent, event) and target_equal(parent, event) and actor_equal(parent, event)
            and event['sequence'] < parent['sequence'] and event['amount'] == 0
            and event['shield_absorbed'] == 0
            and proof_of(event).get('nested_settlement') == proof
            and event.get('target_uid') == proof.get('target_uid')]
        children = [event for event in bursts if event['sequence'] < parent['sequence']
            and clock_equal(parent, event) and target_equal(parent, event)
            and all(number(event.get(key)) and event[key] == values[proof_key] for key, proof_key in (
                ('real_delta', 'child_real_delta'), ('hp_before', 'child_hp_before'), ('hp_after', 'child_hp_after')))
            and event.get('killed') is True and event.get('shield_absorbed') == 0]
        valid = (proof.get('ordering') == 'child_before_parent_zero'
                 and proof_of(parent).get('method') == 'Entity.ApplyModifier' and owned_actor(parent)
                 and type(parent.get('damage_type')) is int and parent['damage_type'] in (1, 2, 3)
                 and type(proof_of(parent).get('type')) is int and proof_of(parent)['type'] == parent['damage_type']
                 and type(proof_of(parent).get('target')) is int and proof_of(parent)['target'] == 0
                 and all(number(value) for value in values.values())
                 and values['parent_real_delta'] == values['parent_hp_before'] == values['parent_hp_after'] == 0
                 and values['child_hp_before'] > 0 and values['child_hp_after'] == 0
                 and equal(values['child_real_delta'], -values['child_hp_before'])
                 and values['parent_entry_hp_before'] == values['child_hp_before']
                 and hp_evidence(parent) and parent.get('real_delta') == 0
                 and parent.get('hp_before') == 0 and parent.get('hp_after') == 0
                 and parent.get('shield_absorbed') == 0 and parent.get('killed') is True
                 and number(parent.get('amount')) and parent['amount'] > 0
                 and len(parent_settlements) == len(children) == 1
                 and children[0]['sequence'] in verified_burst_pairs
                 and verified_burst_pairs[children[0]['sequence']]['target_uid'] == proof.get('target_uid')
                 and parent_settlements[0]['sequence'] not in used_parent_settlements
                 and children[0]['sequence'] not in used_child_bursts
                 and children[0]['sequence'] < parent_settlements[0]['sequence'])
        if valid:
            used_parent_settlements.add(parent_settlements[0]['sequence'])
            used_child_bursts.add(children[0]['sequence'])
            nested_ok.append(dict(parent_sequence=parent['sequence'],
                                  ordering='child_before_parent_zero',
                                  parent_settlement_sequence=parent_settlements[0]['sequence'],
                                  child_burst_sequence=children[0]['sequence'],
                                  child_settlement_sequence=verified_burst_pairs[children[0]['sequence']]['sequence']))
        else:
            nested_bad.append(parent['sequence'])
    # Older producer certificates have seven fields and no ordering tag. The
    # parent was independently committed before the child entered, so its HP
    # endpoints remain independent of the child's final HP. An elemental child
    # still needs its own unique, complete settlement/burst pair.
    legacy_fields = {'target_uid', 'parent_real_delta', 'parent_hp_before', 'parent_hp_after',
                     'child_hp_before', 'child_hp_after', 'child_real_delta'}
    legacy_parents = [event for event in events if event.get('kind') == 'damage'
                      and event.get('settled_after_nested_kill') is not True
                      and proof_of(event).get('nested_settlement')]
    for parent in legacy_parents:
        proof = proof_of(parent)['nested_settlement']
        if not isinstance(proof, dict):
            nested_bad.append(parent['sequence'])
            continue
        numeric = all(number(proof.get(key)) for key in legacy_fields - {'target_uid'})
        parent_settlements = [event for event in events if settled(event)
            and event.get('settled_after_nested_kill') is not True
            and clock_equal(parent, event) and target_equal(parent, event) and actor_equal(parent, event)
            and event.get('damage_type') == parent.get('damage_type')
            and event['sequence'] < parent['sequence']
            and number(proof.get('parent_real_delta')) and event['amount'] == -proof['parent_real_delta']
            and event['shield_absorbed'] == 0 and event.get('killed') is False
            and event.get('target_uid') == proof.get('target_uid')
            and proof_of(event).get('nested_settlement') == proof]
        children = [event for event in events if event.get('kind') in ('damage', 'elemental_burst')
            and event['sequence'] < parent['sequence'] and clock_equal(parent, event) and target_equal(parent, event)
            and event.get('settled_after_nested_kill') is not True and not proof_of(event).get('nested_settlement')
            and all(number(event.get(key)) and event[key] == proof.get(proof_key) for key, proof_key in (
                ('real_delta', 'child_real_delta'), ('hp_before', 'child_hp_before'), ('hp_after', 'child_hp_after')))]
        child = children[0] if len(children) == 1 else {}
        child_settlements = [event for event in events if child and settled(event)
            and clock_equal(child, event) and target_equal(child, event) and actor_equal(child, event)
            and event.get('damage_type') == child.get('damage_type')
            and event['sequence'] < child['sequence']
            and event['target_uid'] == proof.get('target_uid') and event['shield_absorbed'] == 0
            and number(proof.get('child_real_delta')) and event['amount'] == -proof['child_real_delta']]
        child_type = child.get('damage_type')
        child_valid = (hp_evidence(child) and proof_of(child).get('method') == 'Entity.ApplyModifier'
            and type(child_type) is int and type(proof_of(child).get('type')) is int
            and proof_of(child)['type'] == child_type and proof_of(child).get('target') == 0
            and child.get('shield_absorbed') == 0 and number(child.get('amount'))
            and number(child.get('real_delta')) and child['amount'] + .01 >= -child['real_delta']
            and (child['kind'] == 'elemental_burst' and child_type == 5
                 and child['sequence'] in verified_burst_pairs
                 and child.get('killed') is (child['hp_after'] == 0)
                 and (child.get('source') is None or owned_actor(child)
                      and same_scope(parent, child)
                      and same_scope(child, verified_burst_pairs[child['sequence']])
                      and verified_burst_pairs[child['sequence']].get('killed') is child['killed'])
                 or child['kind'] == 'damage' and child_type in (1, 2, 3) and owned_actor(child)
                 and child.get('hp_after', -1) > 0 and child.get('killed') is False))
        valid = (set(proof) == legacy_fields and numeric and owned_actor(parent)
            and proof_of(parent).get('method') == 'Entity.ApplyModifier'
            and type(parent.get('damage_type')) is int and parent['damage_type'] in (1, 2, 3)
            and type(proof_of(parent).get('type')) is int and proof_of(parent)['type'] == parent['damage_type']
            and proof_of(parent).get('target') == 0 and hp_evidence(parent) and parent.get('killed') is False
            and parent.get('shield_absorbed') == 0 and number(parent.get('amount'))
            and proof['parent_real_delta'] < 0 and proof['parent_hp_before'] > proof['parent_hp_after'] > 0
            and proof['child_real_delta'] < 0 and proof['child_hp_before'] == proof['parent_hp_after']
            and proof['child_hp_after'] >= 0 and equal(proof['child_hp_before'] - proof['child_hp_after'], -proof['child_real_delta'])
            and all(parent.get(key) == proof[proof_key] for key, proof_key in (
                ('real_delta', 'parent_real_delta'), ('hp_before', 'parent_hp_before'), ('hp_after', 'parent_hp_after')))
            and parent['amount'] + .01 >= -parent['real_delta']
            and len(parent_settlements) == len(children) == len(child_settlements) == 1 and child_valid
            and (child_type != 5 or child.get('source') is None
                 or verified_burst_pairs[child['sequence']] is child_settlements[0]
                 and same_scope(parent, parent_settlements[0]))
            and child['sequence'] < parent_settlements[0]['sequence']
            and parent_settlements[0]['sequence'] not in used_parent_settlements
            and child['sequence'] not in used_child_bursts)
        if valid:
            used_parent_settlements.add(parent_settlements[0]['sequence'])
            used_child_bursts.add(child['sequence'])
            nested_ok.append(dict(ordering='parent_before_child',
                ordering_evidence='legacy_seven_field_certificate_no_explicit_ordering_tag',
                parent_sequence=parent['sequence'], parent_settlement_sequence=parent_settlements[0]['sequence'],
                child_sequence=child['sequence'], child_kind=child['kind'],
                child_settlement_sequence=child_settlements[0]['sequence']))
        else:
            nested_bad.append(parent['sequence'])

    chain_records = [event for event in events if chain_key in proof_of(event)]
    if chain_records:
        by_sequence = {}
        for event in events:
            sequence = event.get('sequence')
            if type(sequence) is int and sequence > 0:
                by_sequence[sequence] = None if sequence in by_sequence else event
        chain_settlements = [event for event in chain_records if event.get('kind') == 'damage_settlement']
        chain_fields = {'version', 'ordering', 'target_uid', 'parent_real_delta',
                        'parent_hp_before', 'parent_hp_after', 'children'}
        step_fields = {'source', 'settlement_sequence', 'burst_sequence', 'hp_before', 'hp_after', 'real_delta'}

        def chain_witness(parent):
            proof = proof_of(parent)
            chain = proof[chain_key]
            sequence = parent['sequence']
            zero_parent = parent.get('settled_after_nested_kill') is True
            fields = chain_fields | {'parent_entry_hp_before'} if zero_parent else chain_fields
            if (type(sequence) is not int or by_sequence.get(sequence) is not parent
                    or not isinstance(chain, dict) or set(chain) != fields
                    or type(chain['version']) is not int or chain['version'] != 1 or 'nested_settlement' in proof
                    or chain['ordering'] not in ('parent_before_children', 'children_before_parent')
                    or type(chain['children']) is not list or not 1 <= len(chain['children']) <= 16
                    or not owned_actor(parent) or not hp_evidence(parent)
                    or type(parent.get('frame')) is not int or parent['frame'] < 0
                    or not number(parent.get('game_time'), nonnegative=True)
                    or parent['shield_absorbed'] != 0
                    or type(parent.get('damage_type')) is not int or parent['damage_type'] not in (1, 2, 3)
                    or proof.get('method') != 'Entity.ApplyModifier' or type(proof.get('type')) is not int
                    or proof['type'] != parent['damage_type'] or type(proof.get('target')) is not int
                    or proof['target'] != 0 or parent.get('killed') is not (parent['hp_after'] == 0)
                    or not number(parent.get('amount'), nonnegative=True)
                    or any(not number(chain['parent_' + key]) or chain['parent_' + key] != parent[key]
                           for key in ('real_delta', 'hp_before', 'hp_after'))):
                return None
            parent_first = chain['ordering'] == 'parent_before_children'
            if zero_parent:
                entry_hp = chain['parent_entry_hp_before']
                if (parent_first or len(chain['children']) < 2 or parent['amount'] <= 0
                        or parent['real_delta'] != 0 or parent['hp_before'] != 0 or parent['hp_after'] != 0
                        or not number(entry_hp) or entry_hp <= 0):
                    return None
                hp = entry_hp
            elif (parent['real_delta'] >= 0 or parent['amount'] + .01 < -parent['real_delta']
                  or parent_first and parent['killed']):
                return None
            else:
                hp = parent['hp_after'] if parent_first else None
            child_sequences = []
            previous_sequence = 0
            for step in chain['children']:
                if (not isinstance(step, dict) or not step_fields <= set(step) <= step_fields | {'source_character_uid'}
                        or any(not number(step[key]) for key in ('hp_before', 'hp_after', 'real_delta'))
                        or not step['hp_before'] > step['hp_after'] >= 0 or step['real_delta'] >= 0
                        or hp is not None and step['hp_before'] != hp):
                    return None
                start, end = step['settlement_sequence'], step['burst_sequence']
                if (type(start) is not int or type(end) is not int or not previous_sequence < start < end < sequence
                        or end in used_child_bursts):
                    return None
                settlement, burst = by_sequence.get(start), by_sequence.get(end)
                if (settlement is None or burst is None or verified_burst_pairs.get(end) is not settlement
                        or settlement.get('target_uid') != chain['target_uid']
                        or not target_equal(parent, settlement) or not target_equal(parent, burst)
                        or not clock_equal(parent, burst) or not clock_equal(parent, settlement)
                        or not same_scope(parent, burst) or not same_scope(parent, settlement)
                        or not actor_equal(step, burst) or not actor_equal(step, settlement)
                        or (step['source'] is not None and not owned_actor(step))
                        or burst['shield_absorbed'] != 0 or settlement['shield_absorbed'] != 0
                        or any(burst[key] != step[key] for key in ('hp_before', 'hp_after', 'real_delta'))
                        or settlement['amount'] != -step['real_delta']
                        or burst.get('killed') is not (step['hp_after'] == 0)
                        or settlement.get('killed') is not burst['killed']
                        or chain_key in proof_of(settlement) or chain_key in proof_of(burst)
                        or 'nested_settlement' in proof_of(settlement) or 'nested_settlement' in proof_of(burst)):
                    return None
                child_sequences.append(dict(settlement_sequence=start, burst_sequence=end))
                hp, previous_sequence = step['hp_after'], end
            if not parent_first and parent['hp_before'] != hp:
                return None
            chain_json = json.dumps(chain, sort_keys=True)
            candidates = [event for event in chain_settlements if type(event['sequence']) is int
                and previous_sequence < event['sequence'] < sequence
                and event['sequence'] not in used_parent_settlements and by_sequence.get(event['sequence']) is event
                and settled(event) and (event.get('settled_after_nested_kill') is True) == zero_parent
                and type(event.get('damage_type')) is int and event['damage_type'] == parent['damage_type']
                and clock_equal(parent, event) and same_scope(parent, event)
                and target_equal(parent, event) and actor_equal(parent, event) and owned_actor(event)
                and event.get('target_uid') == chain['target_uid'] and event['shield_absorbed'] == 0
                and event['amount'] == -parent['real_delta'] and event.get('killed') is parent['killed']
                and number(event.get('nominal_amount'), nonnegative=True) and event['nominal_amount'] == parent['amount']
                and 'nested_settlement' not in proof_of(event)
                and proof_of(event).get('evidence_method') == 'Unit.OnTakeDamage+Entity.ApplyModifier'
                and json.dumps(proof_of(event)[chain_key], sort_keys=True) == chain_json]
            if len(candidates) != 1:
                return None
            parent_settlement = candidates[0]['sequence']
            witness = dict(ordering=chain['ordering'], parent_sequence=sequence,
                           parent_settlement_sequence=parent_settlement, children=child_sequences)
            if zero_parent:
                # The original Unit callback observed positive HP before the
                # first child. Requiring its raw receipt prevents a truncated
                # chain from choosing a later child's HP as the old endpoint.
                entries = [event for event in events if event.get('kind') == 'hp_damage'
                           and type(event['sequence']) is int and previous_sequence < event['sequence'] < parent_settlement
                           and by_sequence.get(event['sequence']) is event
                           and proof_of(event).get('method') == 'Unit.OnTakeDamage'
                           and type(event.get('damage_type')) is int and event['damage_type'] == parent['damage_type']
                           and type(event.get('target_type')) is int and event['target_type'] == 0
                           and actor_equal(parent, event) and target_equal(parent, event)
                           and clock_equal(parent, event) and same_scope(parent, event)
                           and number(event.get('hp_before')) and event['hp_before'] == entry_hp]
                if len(entries) != 1:
                    return None
                witness['parent_target_sequence'] = entries[0]['sequence']
            return witness

        chain_accepted = set()
        for parent in chain_records:
            if parent.get('kind') != 'damage':
                continue
            witness = chain_witness(parent)
            if witness is None:
                nested_bad.append(parent['sequence'])
                continue
            nested_ok.append(witness)
            parent_settlement = witness['parent_settlement_sequence']
            chain_accepted.update((parent['sequence'], parent_settlement))
            used_parent_settlements.add(parent_settlement)
            used_child_bursts.update(child['burst_sequence'] for child in witness['children'])
        for event in chain_records:
            sequence = event['sequence']
            if sequence not in chain_accepted and sequence not in nested_bad:
                nested_bad.append(sequence)

    result = {name: dict(passed=bool(valid) and not invalid, positive_witnesses=valid,
                       invalid_sequences=invalid,
                       reason=None if valid and not invalid else ('invalid_witness' if invalid else 'no_positive_witness'))
            for name, valid, invalid in (('shield', shield_ok, shield_bad),
                                         ('elemental', elemental_ok, elemental_bad),
                                         ('nested', nested_ok, nested_bad))}
    fresh = [witness for witness in elemental_ok if witness['category'] == 'source_null_bar_burst']
    native = [witness for witness in elemental_ok if witness['category'] == 'native_elemental_contribution']
    result['elemental'].update(source_null_bar_burst_witnesses=fresh,
                               native_elemental_contribution_witnesses=native,
                               explicit_source_ep_does_not_consume_bar=True,
                               passed=bool(fresh) and not elemental_bad)
    if not fresh and native and not elemental_bad:
        result['elemental']['reason'] = 'no_positive_bar_burst_witness'
    return result


def enemy_state_event(event):
    kind = event['kind']
    uid, eid = event.get('target_instance'), event.get('target')
    if (event['source'] is not None or (kind != 'enemy_reborn' and event['amount'] != 0)
            or not isinstance(uid, str) or not uid.isascii() or not uid.isdecimal()
            or len(uid) > 10 or not 0 <= int(uid) <= 0xffffffff or str(int(uid)) != uid
            or not isinstance(eid, str) or not eid):
        raise ValueError('invalid enemy state identity or amount')
    result = {key: event[key] for key in ('sequence', 'kind', 'frame', 'game_time')}
    result.update(eid=eid, instance_uid=uid)
    for key in ('hp_before', 'hp_after'):
        value = event.get(key)
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('invalid enemy state HP')
            result[key] = value
    if kind == 'enemy_hp' and 'hp_after' not in result:
        raise ValueError('enemy HP event has no settlement value')
    maximum = event.get('enemy_max_hp')
    if maximum is not None:
        if kind != 'enemy_hp' or type(maximum) not in (int, float) or not 0 < maximum < math.inf:
            raise ValueError('invalid enemy max HP')
        result['enemy_max_hp'] = maximum
    ticks = event.get('enemy_regen_ticks')
    if ticks is not None:
        before = result.get('hp_before')
        after = result.get('hp_after')
        if (kind != 'enemy_hp' or eid not in ('enemy_1061_zomshd', 'enemy_1061_zomshd_2')
                or type(ticks) is not int or ticks < 2 or before is None or before <= 0
                or maximum is None or after > maximum or after - before != ticks):
            raise ValueError('invalid shieldguard recovery ticks')
        result['enemy_regen_ticks'] = ticks
    reason = event.get('finish_reason')
    if kind == 'enemy_finish':
        if type(reason) is not int or reason == 0 or not -(1 << 31) <= reason < (1 << 31):
            raise ValueError('invalid enemy finish reason')
        result['finish_reason'] = reason
    elif reason is not None:
        raise ValueError('only enemy finish carries a finish reason')
    if kind == 'enemy_enter' and event.get('enemy_entry_attributes'):
        result['enemy_entry_attributes'] = event['enemy_entry_attributes']
    if kind == 'enemy_reborn':
        result.update({key: event[key] for key in ('phase_total_hp', 'phase_count', 'phase_from_birth')})
    return result


def pc_records(records, summary, publications):
    """Read only ordered batches bound to this collector's guarded PC samples."""
    if __package__ in (None, '') and str(Path(__file__).resolve().parents[1]) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from event_stream import baseline_barrier, validate_native_event_batch
    from native_pc.probe_native_pc import KNOWN_BUILDS

    connections = [r['data'] for r in records if r['kind'] == 'connected']
    instance = summary.get('process_instance')
    source_build = summary.get('game_version')
    if (summary.get('platform') != 'native_pc' or type(source_build) is not str or source_build not in KNOWN_BUILDS
            or not isinstance(instance, list) or len(instance) != 2
            or not all(type(value) is int and value > 0 for value in instance)
            or summary.get('pid') != instance[0] or len(connections) != 1):
        raise ValueError('missing native PC capture identity')
    connection = connections[0]
    diagnostics = connection.get('diagnostics') or {}
    details = diagnostics.get('details') or {}
    build = details.get('build') or {}
    files = {entry['file']: entry['sha256'] for entry in build.get('files', [])
             if entry.get('sha256_matches') is True}
    if (connection.get('pid') != instance[0] or connection.get('process_instance') != instance
            or diagnostics.get('platform') != 'native_pc' or diagnostics.get('version_actual') != source_build
            or details.get('fixed_module_bytes_match') is not True or build.get('known_build') is not True
            or files != KNOWN_BUILDS[source_build]):
        raise ValueError('missing matching PC build verification')

    native_session = publications[0].get('native_session')
    if not isinstance(native_session, str) or not native_session.startswith(f'{instance[0]}:{instance[1]}:'):
        raise ValueError('wrong native PC process instance')
    session = publications[0]['session']
    stream = None
    latest = None
    for record in records:
        kind, data = record['kind'], record['data']
        if kind == 'lifecycle_observation' and (data.get('events') or {}).get('drain') is not None:
            if 'terminal_tail_version' in data['events']:
                # Source-only final records are independently checked below;
                # they cannot advance the native cumulative counter window.
                if type(data['events']['terminal_tail_version']) is not int or data['events']['terminal_tail_version'] != 1:
                    raise ValueError('unsupported source-only terminal tail')
                continue
            evidence, sample = data['events'], data['sample']
            metadata = sample.get('battle_metadata') or {}
            if (sample.get('session') != session or sample.get('native_session') != native_session
                    or metadata.get('platform') != 'native_pc' or metadata.get('source_build') != source_build):
                raise ValueError('wrong native PC battle or build')
            seq = baseline_barrier(evidence['before'], sample, evidence['after'])
            batch = evidence['drain']
            if (evidence.get('error') or evidence.get('sequence') != seq or batch.get('error') or batch.get('stopped')
                    # The engagement flag also guards the published observed_damage/healing totals.
                    or any(batch.get(key) is not False for key in
                           ('damage_unknown', 'engagement_unknown', 'shield_unknown', 'skill_unknown'))):
                raise ValueError('missing native PC event coverage')
            validate_native_event_batch(sample, seq, batch, stream=stream)
            if stream is None:
                stream = SimpleNamespace(sequence=seq, baseline_sequence=seq, received={})
            if seq < stream.sequence:
                raise ValueError('native PC watermark moved backwards')
            stream.received.update((e['sequence'], e) for e in batch['events'] if e['sequence'] > stream.sequence)
            stream.sequence = seq
            latest = sample
        elif kind == 'published':
            watermark = data.get('event_watermark') or {}
            if (latest is None or data.get('event_status') not in ('recording', 'finished')
                    or any(data.get(k) != latest[k] for k in ('frame', 'game_time', 'session', 'native_session'))
                    or watermark != dict(frame=latest['frame'], game_time=latest['game_time'], sequence=stream.sequence)
                    or latest['rows'] != data.get('native_rows')):
                raise ValueError('PC publication has no matching guarded event sample')
    return stream.received


def check_pc_families(accepted, rows, ownership, deltas):
    """Reconcile a window with attributed hits and directly observed skill endings."""
    from stats_core import NATIVE_DAMAGE_TYPES, Statistics

    for family in ('damage', 'healing'):
        if not any(delta[family]['native_delta'] > 0 for delta in deltas.values()):
            raise ValueError(f'no positive native PC {family} delta to reconcile')
    if not any(e['kind'] == 'shield_absorbed' and e['amount'] > 0 for e in accepted):
        raise ValueError('no positive PC shield absorption')
    hp, shield, absorbed_by_source = Counter(), Counter(), Counter()
    active, durations, windows = {}, Counter(), Counter()
    for event in accepted:
        source, kind = event['source'], event['kind']
        if kind == 'shield_absorbed':
            absorbed_by_source[source] += event['amount']
        if kind in ('damage', 'healing') and event['amount'] > 0 and (
                source not in deltas or event['sequence'] <= deltas[source]['start_sequence']):
            raise ValueError('PC event has no preceding native counter baseline')
        if kind not in ('damage_settlement', 'skill_start', 'skill_end'):
            continue
        owner = ownership.get(source)
        if not owner:
            raise ValueError('unattributed PC settlement or skill')
        if kind == 'damage_settlement':
            absorption = event.get('shield_absorbed')
            if (event.get('settlement_complete') is not True
                    or not Statistics._enemy_identity(event.get('target'), event.get('target_uid'))
                    or not event['target'].startswith(('enemy_', 'trap_'))
                    or type(event.get('damage_type')) is not int or event['damage_type'] not in NATIVE_DAMAGE_TYPES
                    or type(absorption) not in (int, float)
                    or not 0 <= absorption < math.inf):
                raise ValueError('incomplete PC damage settlement')
            hp[owner] += event['amount']
            shield[owner] += absorption
            continue
        method = (event.get('proof') or {}).get('method')
        identity = (source, event.get('skill_id') or event.get('skill_key'))
        if not identity[1]:
            raise ValueError('missing PC skill identity')
        if kind == 'skill_start':
            if method not in ('Character.OnSkillStart', 'Character.SwitchToSkillState',
                              'Character.OnSkillCastSucceed', 'Character.OnToggleSkillStart',
                              'Character.OnSkillRetriggered'):
                raise ValueError('unverified PC skill activation')
            active.setdefault(identity, event['game_time'])
        elif method in ('Character.OnSkillFinish', 'Character.SwitchOutFromSkillState'):
            if identity not in active:
                raise ValueError('PC skill ending has no matching activation in this window')
            durations[owner] += event['game_time'] - active.pop(identity)
            windows[owner] += 1
    if sum(hp.values()) <= 0 or active or sum(durations.values()) <= 0:
        raise ValueError('missing HP settlement or directly observed closed PC skill window')
    for owner, row in rows.items():
        ledger = row.get('settled_damage') or {}
        if ledger.get('complete') is not True or row.get('skill_coverage_complete') is not True:
            raise ValueError('incomplete published PC settlement or skill window')
        near(ledger['known_hp_damage'], hp[owner])
        near(ledger['known_shield_damage'], shield[owner])
        near(row['skill_active_seconds'], durations[owner])
        if row['skill_window_count'] != windows[owner]:
            raise ValueError('PC skill window count differs from paired events')
    return {'scope': 'single_capture_journal_window', 'settled_hp': dict(hp), 'settled_enemy_shield': dict(shield),
            'absorbed_by_source': dict(absorbed_by_source),
            'paired_skill_windows': dict(windows), 'skill_seconds': dict(durations)}


def captured_records(records):
    by_sequence = {}
    for record in records:
        if record['kind'] == 'lifecycle_observation':
            evidence = record['data'].get('events') or {}
            drain = evidence.get('drain', {})
        elif record['kind'] == 'drain':
            drain = record['data']
        else:
            continue
        for event in drain.get('events', []):
            sequence = event['sequence']
            if sequence in by_sequence and by_sequence[sequence] != event:
                raise ValueError('conflicting replay in raw events')
            by_sequence[sequence] = event
    return by_sequence


def pc_source_completeness(directory, records, summary, *, native_session=None):
    """Report source fences separately from the published counter-window check."""
    from acceptance.compare_pc_live_probes import ROOTS, read_capture
    observations = [record['data'] for record in records if record['kind'] == 'lifecycle_observation'
                    and (record['data'].get('events') or {}).get('drain') is not None]
    has_prefix_audit = any(record['kind'] == 'source_initial_prefix' for record in records)
    has_tail = any(record['kind'] == 'lifecycle_observation' and
                   'terminal_tail_version' in (record['data'].get('events') or {}) for record in records)
    roots = ((observations[0].get('events') or {}).get('after') or {}).get('native_identity') if observations else None
    if summary.get('platform') != 'native_pc' or not (has_prefix_audit or has_tail) and (
            not isinstance(roots, dict) or set(roots) != set(ROOTS)):
        return dict(event_prefix_from_initial_boundary_complete=False,
                    native_cumulative_from_zero_complete=False,
                    raw_terminal_verified=False, source_authenticity_verified=False,
                    partial_reasons=['no_full_native_source_boundary_evidence'])
    capture = read_capture(directory, 'offline', native_session=native_session)
    return dict(event_prefix_from_initial_boundary_complete=(
                    capture.initial_prefix_evidence['verified'] and capture.terminal
                    and not [reason for reason in capture.partial_reasons if reason not in (
                        'late_or_nonzero_baseline', 'terminal_tail_has_no_new_native_counter_snapshot')]),
                native_cumulative_from_zero_complete=(
                    capture.native_cumulative_baseline['verified'] and not capture.partial_reasons),
                source_initial_prefix=capture.initial_prefix_evidence,
                native_cumulative_baseline=capture.native_cumulative_baseline,
                raw_terminal_verified=capture.terminal, source_authenticity_verified=False,
                host_first_mark=capture.first_mark, source_final_mark=capture.last_mark,
                raw_initial_event_count=len(capture.initial_prefix_events),
                native_counter_watermark=capture.native_counter_mark,
                event_source_final_watermark=capture.last_mark,
                terminal_tail_evidence=capture.terminal_tail_evidence,
                source_verified_event_count=len(capture.initial_prefix_events) + len(capture.events),
                source_event_mechanism_coverage=pc_mechanism_evidence(capture.initial_prefix_events + capture.events),
                source_manifest=capture.manifest,
                partial_reasons=capture.partial_reasons)


def verify(directory, *, source_owners=(), require_partial=False, require_zero=False,
           allow_incomplete=False, pc_settlements=False, pc_mechanisms=(), native_session=None,
           pc_f32_counters=False, pc_f32_source_domain=None):
    if pc_f32_source_domain is not None and pc_f32_source_domain not in ('all', 'owned'):
        raise ValueError('unknown PC f32 source domain; choose all or owned')
    if pc_f32_source_domain is not None and not pc_f32_counters:
        raise ValueError('PC f32 source domain requires --pc-f32-counters')
    required_mechanisms = (tuple(item.strip() for item in pc_mechanisms.split(',')) if pc_mechanisms else ()) \
        if isinstance(pc_mechanisms, str) else tuple(pc_mechanisms)
    if any(item not in PC_MECHANISMS for item in required_mechanisms):
        raise ValueError('unknown PC mechanism; choose shield,elemental,nested')
    if (pc_settlements or required_mechanisms or pc_f32_counters) and allow_incomplete:
        raise ValueError('PC settlement reconciliation requires a continuous capture')
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('error') and not allow_incomplete:
        raise ValueError('capture reports a failure: ' + summary['error'])
    records = [json.loads(line) for line in (directory / 'capture.jsonl').read_text(encoding='utf-8').splitlines()]
    selection = None
    if native_session is not None:
        from acceptance.compare_pc_live_probes import select_native_session
        records, summary, selection = select_native_session(records, summary, native_session)
        selection = dict(selection,
            original_summary_sha256=sha256((directory / 'summary.json').read_bytes()).hexdigest(),
            original_journal_sha256=sha256((directory / 'capture.jsonl').read_bytes()).hexdigest())
    elif summary.get('platform') == 'native_pc' and len({record['data'].get('native_session') for record in records
            if record['kind'] == 'published' and record['data'].get('native_session')}) > 1:
        raise ValueError('multiple native battles require an explicit native session selector')
    publications = [record['data'] for record in records if record['kind'] == 'published']
    failed_run = bool(summary.get('error')) or not publications or publications[-1]['event_status'] not in ('recording', 'finished')
    if failed_run and allow_incomplete:
        publications = [sample for sample in publications if sample['event_status'] in ('recording', 'finished')]
    if not publications or publications[0]['frame'] == publications[-1]['frame']:
        raise ValueError('no observed battle progression')
    first, last = publications[0], publications[-1]
    if (pc_settlements or required_mechanisms or pc_f32_counters) and any(not isinstance(sample.get('event_watermark'), dict) for sample in publications):
        raise ValueError('PC sample has no combat-event watermark')
    complete = not failed_run
    if first['session'] != last['session'] or not complete and not allow_incomplete:
        raise ValueError('capture does not have one reliable event session')
    start_sequence = first['event_watermark']['sequence']
    end_sequence = last['event_watermark']['sequence']
    by_sequence = pc_records(records, summary, publications) if pc_settlements or required_mechanisms or pc_f32_counters else captured_records(records)
    if pc_f32_counters:
        from acceptance.compare_pc_live_probes import _quality_problems
        from event_probe import contract_for
        for record in records:
            if record['kind'] != 'lifecycle_observation':
                continue
            evidence = record['data'].get('events') or {}
            batch = evidence.get('drain')
            if batch is None:
                continue
            if 'terminal_tail_version' in evidence:
                continue
            contract = contract_for(sample=record['data'].get('sample'), descriptor=batch.get('probe_contract'))
            if any(_quality_problems(payload, contract) for payload in
                   (evidence.get('before') or {}, evidence.get('after') or {}, batch)):
                raise ValueError('PC f32 model requires known event-family coverage')
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
    enemy_entries = {}
    enemy_phases = {}
    published_enemy_state = last['statistics'].get('enemy_state')
    enemy_state_events = []
    partial, zeros = [], []
    sources_with_absorption = set()
    excluded_friendly_damage = []
    previous_time = first['game_time']
    previous_frame = first['frame']
    for event in accepted:
        source, kind, amount = event['source'], event['kind'], event['amount']
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount < 0:
            raise ValueError('invalid event amount')
        if not previous_time <= event['game_time'] <= last['game_time']:
            raise ValueError('event time is outside the ordered coverage')
        previous_time = event['game_time']
        if not previous_frame <= event['frame'] <= last['frame']:
            raise ValueError('event frame is outside ordered coverage')
        previous_frame = event['frame']
        if kind in STATE_ONLY or (published_enemy_state is not None
                                 and kind in ('enemy_enter', 'enemy_reborn')):
            enemy_state_events.append(enemy_state_event(event))
        if kind in STATE_ONLY:
            continue
        if kind == 'enemy_enter':
            uid, eid = event.get('target_instance'), event.get('target')
            if (source is not None or amount != 0 or not isinstance(uid, str)
                    or not uid.isascii() or not uid.isdecimal() or len(uid) > 10
                    or not 0 <= int(uid) <= 0xffffffff or str(int(uid)) != uid
                    or not isinstance(eid, str) or not eid):
                raise ValueError('invalid enemy entry')
            if uid in enemy_entries:
                raise ValueError('enemy entry was emitted more than once: ' + uid)
            entry = dict(
                instance_uid=uid, eid=eid, frame=event['frame'], game_time=event['game_time'])
            if 'enemy_entry_attributes' in event:
                attributes = event['enemy_entry_attributes']
                if (not isinstance(attributes, dict) or not attributes
                        or any(name not in ('max_hp', 'atk', 'def', 'res', 'move_speed', 'attack_speed')
                               or type(value) not in (int, float) or not -1e10 < value < 1e10
                               or name == 'max_hp' and value <= 0
                               for name, value in attributes.items())):
                    raise ValueError('invalid enemy entry attributes')
                entry['enemy_entry_attributes'] = attributes
            enemy_entries[uid] = entry
            continue
        if kind == 'enemy_reborn':
            uid, eid = event.get('target_instance'), event.get('target')
            before, after, total = (event.get(key) for key in (
                'hp_before', 'hp_after', 'phase_total_hp'))
            count, from_birth = event.get('phase_count'), event.get('phase_from_birth')
            if (source is not None or not isinstance(uid, str)
                    or not uid.isascii() or not uid.isdecimal() or len(uid) > 10
                    or not 0 <= int(uid) <= 0xffffffff or str(int(uid)) != uid
                    or not isinstance(eid, str) or not eid
                    or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                               and math.isfinite(value) and value >= 0 for value in (before, after, total))
                    or amount <= 0 or total < amount
                    or type(count) is not int or count < 1 or type(from_birth) is not bool
                    or from_birth and (count < 2 or total <= amount)):
                raise ValueError('invalid enemy phase')
            near(after, amount)
            entry = enemy_entries.get(uid)
            previous = enemy_phases.get(uid)
            if ((entry is not None and entry['eid'] != eid)
                    or (previous is not None and (previous['eid'] != eid
                        or count != previous['phase_count'] + 1
                        or from_birth != previous['phase_from_birth']))):
                raise ValueError('enemy phase progression conflicts')
            if previous is not None:
                near(previous['phase_total_hp'] + amount, total)
            enemy_phases[uid] = dict(
                eid=eid, frame=event['frame'], game_time=event['game_time'],
                phase_total_hp=total, phase_count=count, phase_from_birth=from_birth)
            continue
        if kind in ('damage_settlement', 'hp_damage', 'elemental_bar', 'elemental_burst', 'skill_start', 'skill_end'):
            continue
        if source not in ownership or kind not in ('damage', 'healing', 'shield_absorbed'):
            raise ValueError('invalid or unowned event')
        if not outgoing_counter_event(event):
            excluded_friendly_damage.append(event)
            continue
        totals[ownership[source], kind] += amount
        if kind == 'shield_absorbed':
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
    enemy_entry = last['statistics'].get('enemy_entry')
    if (enemy_entries or enemy_entry is not None) and (
            not isinstance(enemy_entry, dict)
            or enemy_entry.get('start_frame') != first['frame']
            or enemy_entry.get('events') != list(enemy_entries.values())):
        raise ValueError('published enemy entries differ from ordered events')
    published_phases = last['statistics'].get('enemy_phases')
    if (enemy_phases or published_phases is not None) and (
            not isinstance(published_phases, dict)
            or published_phases.get('instances') != enemy_phases):
        raise ValueError('published enemy phases differ from ordered events')
    if (enemy_state_events or published_enemy_state is not None) and (
            not isinstance(published_enemy_state, dict)
            or type(published_enemy_state.get('complete')) is not bool
            or published_enemy_state.get('start_frame') != first['frame']
            or published_enemy_state.get('events') != enemy_state_events):
        raise ValueError('published enemy state differs from ordered events')
    native = {row['id']: row for row in last.get('native_rows', last['rows'])}
    references = {}
    for publication in publications:
        for raw in publication.get('native_rows', publication['rows']):
            references.setdefault(raw['id'], (raw, publication['event_watermark']['sequence'],
                                               publication['game_time']))
    counter_deltas = {}
    f32_evidence = check_pc_f32_counters(accepted, publications,
        source_domain=pc_f32_source_domain or 'all') if pc_f32_counters else None
    supplement_evidence = check_pc_owner_counter_supplements(accepted, publications, ownership) if pc_f32_counters else None
    for source, (baseline, sequence, game_time) in references.items():
        if source not in ownership or source not in native:
            continue
        check = {'start': game_time, 'end': last['game_time'], 'start_sequence': sequence}
        for kind in ('damage', 'healing'):
            delta = native[source][kind] - baseline[kind]
            observed = sum(event['amount'] for event in accepted if event['source'] == source
                           and (event['kind'] == kind or pc_f32_counters and kind == 'damage'
                                and event['kind'] == 'elemental_burst' and event['source'] is not None)
                           and event['sequence'] > sequence
                           and outgoing_counter_event(event))
            if not pc_f32_counters:
                near(delta, observed)
            check[kind] = {'native_delta': delta, 'observed_events': observed}
        counter_deltas[source] = check
    for owner, row in rows.items():
        for kind, field in (('damage', 'observed_damage'), ('healing', 'observed_healing'),
                            ('shield_absorbed', 'shield_absorbed')):
            near(totals[owner, kind], row[field])
        if row['counter_snapshot_complete'] and not pc_f32_counters:
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
    result = {'head': summary['head'], 'session': last['session'],
            'complete': bool(complete), 'capture_error': summary.get('error'),
            'verification_scope': 'single_capture_published_counter_window',
            'pc_settlement_mechanisms_verified': pc_settlements,
            'coverage_start': first['game_time'],
            'coverage_end': last['event_watermark'].get('game_time', last['game_time']),
            'baseline_sequence': start_sequence, 'final_sequence': end_sequence,
            'events': len(accepted), 'partial_break_sequences': partial,
            'event_counts': dict(Counter(event['kind'] for event in accepted)),
            'excluded_friendly_damage': dict(count=len(excluded_friendly_damage),
                amount=sum(event['amount'] for event in excluded_friendly_damage),
                sequences=[event['sequence'] for event in excluded_friendly_damage]),
            'native_counter_deltas': counter_deltas,
            'native_counter_arithmetic': 'pc_binary32_ordered_exact' if pc_f32_counters else 'nominal_f64_sum_with_fixed_tolerance',
            'pc_f32_counters_verified': bool(f32_evidence),
            'zero_absorption_sequences': zeros,
            'shield_totals': {owner: totals[owner, 'shield_absorbed'] for owner in rows}}
    if f32_evidence is not None:
        result['pc_f32_counter_evidence'] = f32_evidence
        result['pc_owner_counter_supplement_evidence'] = supplement_evidence
        result['pc_f32_source_domain'] = f32_evidence['source_domain']
        result['full_native_row_counter_model_verified'] = f32_evidence['all_native_sources_verified']
        result['complete_scope'] = 'published_capture_window_continuity_not_all_source_counter_acceptance'
    if summary.get('platform') == 'native_pc':
        source_evidence = pc_source_completeness(directory, records, summary, native_session=native_session)
        result['native_pc_source_evidence'] = source_evidence
        for field in ('event_prefix_from_initial_boundary_complete', 'native_cumulative_from_zero_complete'):
            result[field] = source_evidence[field]
    if selection is not None:
        result['source_selection_manifest'] = selection
    mechanism_evidence = pc_mechanism_evidence(accepted)
    result['pc_mechanism_coverage'] = mechanism_evidence
    result['required_pc_mechanisms'] = list(required_mechanisms)
    result['required_pc_mechanisms_passed'] = bool(required_mechanisms) and all(
        mechanism_evidence[item]['passed'] for item in required_mechanisms)
    for item in required_mechanisms:
        if not mechanism_evidence[item]['passed']:
            raise ValueError('PC mechanism not verified: ' + item + ' (' + mechanism_evidence[item]['reason'] + ')')
    if enemy_entry is not None:
        result['enemy_entry_count'] = len(enemy_entries)
        result['enemy_entry_complete'] = enemy_entry.get('complete') is True
    if published_phases is not None:
        result['enemy_phases'] = published_phases
    if published_enemy_state is not None:
        result['enemy_state_complete'] = published_enemy_state['complete']
        result['enemy_state_events'] = len(enemy_state_events)
    if pc_settlements:
        result['native_pc_settlements'] = check_pc_families(accepted, rows, ownership, counter_deltas)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source-owner', action='append', default=[])
    parser.add_argument('--require-partial', action='store_true')
    parser.add_argument('--require-zero', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--pc-settlements', action='store_true',
                        help='reconcile a single native PC window with positive hits, healing, shield absorption and closed skills')
    parser.add_argument('--pc-mechanisms', default='',
                        help='require positive raw mechanism witnesses: comma-separated shield,elemental,nested')
    parser.add_argument('--native-session', help='Select one exact native battle using validated lifecycle generation; never rewrites raw evidence')
    parser.add_argument('--pc-f32-counters', action='store_true',
                        help='explicit exact ordered binary32 native PC counter model; never a tolerance fallback')
    parser.add_argument('--pc-f32-source-domain', choices=('all', 'owned'),
                        help='requires --pc-f32-counters; all is the default, owned models every char_/token_ row and reports proven non-team exclusions')
    parser.add_argument('--allow-incomplete-prefix', action='store_true',
                        help='diagnose a frozen reliable prefix; never report it as complete acceptance')
    args = parser.parse_args()
    result = verify(args.directory, source_owners=args.source_owner,
                    require_partial=args.require_partial, require_zero=args.require_zero,
                    allow_incomplete=args.allow_incomplete_prefix, pc_settlements=args.pc_settlements,
                    pc_mechanisms=args.pc_mechanisms, native_session=args.native_session,
                    pc_f32_counters=args.pc_f32_counters, pc_f32_source_domain=args.pc_f32_source_domain)
    if args.output:
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
