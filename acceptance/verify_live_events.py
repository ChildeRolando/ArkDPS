"""Reconcile a new product capture's ordered event prefix against its published rows."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace


STATE_ONLY = {'enemy_hp', 'enemy_fake_death', 'enemy_finish'}


def near(left, right):
    if (type(left) not in (int, float) or type(right) not in (int, float)
            or not math.isfinite(left) or not math.isfinite(right)
            or not math.isclose(left, right, rel_tol=1e-7, abs_tol=.01)):
        raise ValueError(f'amount mismatch: {left} != {right}')


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
    from native_pc.probe_native_pc import KNOWN_FILES
    from native_pc_collector import BUILD_ID

    connections = [r['data'] for r in records if r['kind'] == 'connected']
    instance = summary.get('process_instance')
    if (summary.get('platform') != 'native_pc' or summary.get('game_version') != BUILD_ID
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
            or diagnostics.get('platform') != 'native_pc' or diagnostics.get('version_actual') != BUILD_ID
            or details.get('fixed_module_bytes_match') is not True or build.get('known_build') is not True
            or files != KNOWN_FILES):
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
            evidence, sample = data['events'], data['sample']
            metadata = sample.get('battle_metadata') or {}
            if (sample.get('session') != session or sample.get('native_session') != native_session
                    or metadata.get('platform') != 'native_pc' or metadata.get('source_build') != BUILD_ID):
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


def verify(directory, *, source_owners=(), require_partial=False, require_zero=False,
           allow_incomplete=False, pc_settlements=False):
    if pc_settlements and allow_incomplete:
        raise ValueError('PC settlement reconciliation requires a continuous capture')
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
    if pc_settlements and any(not isinstance(sample.get('event_watermark'), dict) for sample in publications):
        raise ValueError('PC sample has no combat-event watermark')
    complete = not failed_run
    if first['session'] != last['session'] or not complete and not allow_incomplete:
        raise ValueError('capture does not have one reliable event session')
    start_sequence = first['event_watermark']['sequence']
    end_sequence = last['event_watermark']['sequence']
    by_sequence = pc_records(records, summary, publications) if pc_settlements else captured_records(records)
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
        if pc_settlements and kind == 'damage_settlement':
            continue
        if kind in ('hp_damage', 'elemental_bar', 'elemental_burst', 'skill_start', 'skill_end'):
            continue
        if source not in ownership or kind not in ('damage', 'healing', 'shield_absorbed'):
            raise ValueError('invalid or unowned event')
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
    result = {'head': summary['head'], 'session': last['session'],
            'complete': bool(complete), 'capture_error': summary.get('error'),
            'coverage_start': first['game_time'],
            'coverage_end': last['event_watermark'].get('game_time', last['game_time']),
            'baseline_sequence': start_sequence, 'final_sequence': end_sequence,
            'events': len(accepted), 'partial_break_sequences': partial,
            'event_counts': dict(Counter(event['kind'] for event in accepted)),
            'native_counter_deltas': counter_deltas,
            'zero_absorption_sequences': zeros,
            'shield_totals': {owner: totals[owner, 'shield_absorbed'] for owner in rows}}
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
    parser.add_argument('--allow-incomplete-prefix', action='store_true',
                        help='diagnose a frozen reliable prefix; never report it as complete acceptance')
    args = parser.parse_args()
    result = verify(args.directory, source_owners=args.source_owner,
                    require_partial=args.require_partial, require_zero=args.require_zero,
                    allow_incomplete=args.allow_incomplete_prefix, pc_settlements=args.pc_settlements)
    if args.output:
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
