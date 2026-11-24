"""Public event-probe contract and compatibility boundary; no statistics writes."""
from dataclasses import dataclass
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from math import isfinite
import re
import struct
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class AssembledProbeSource:
    """Host-owned source bytes and provenance, before any Frida transport."""
    source: str
    adapter_file: str
    runtime_file: str
    adapter_sha256: str
    runtime_sha256: str
    adapter_source_sha256: str
    runtime_source_sha256: str
    assembled_sha256: str
    context_file: str
    context_sha256: str
    context_source_sha256: str

    def audit(self):
        return dict(assembly_version=2, adapter_file=self.adapter_file,
                    runtime_file=self.runtime_file, adapter_sha256=self.adapter_sha256,
                    runtime_sha256=self.runtime_sha256,
                    adapter_source_sha256=self.adapter_source_sha256,
                    runtime_source_sha256=self.runtime_source_sha256,
                    assembled_sha256=self.assembled_sha256, context_file=self.context_file,
                    context_sha256=self.context_sha256,
                    context_source_sha256=self.context_source_sha256)


def assemble_probe_source(platform, *, directory=None, read_file=None):
    """Compose the fixed adapter/runtime boundary; never rewrite source tokens.

    A caller may supply its already-verified immutable file snapshot. Both
    compilation and provenance then use that snapshot, not a second disk read.
    """
    names = {'mumu': 'event_probe_android2791.js', 'native_pc': 'event_probe_native_pc.js'}
    if platform not in names:
        raise ValueError('unsupported event probe source platform')
    directory = Path(__file__).resolve().parent if directory is None else Path(directory)
    adapter_name, runtime_name = names[platform], 'event_probe_runtime.js'
    adapter_path, runtime_path = directory / adapter_name, directory / runtime_name
    def read(path):
        raw = path.read_bytes() if read_file is None else read_file(path)
        if type(raw) is not bytes:
            raise TypeError('event probe source reader must return immutable bytes')
        return raw

    adapter_bytes, runtime_bytes = read(adapter_path), read(runtime_path)
    # Hash and compile the same single read, even if files change concurrently.
    normalize = lambda raw: raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    adapter, runtime = normalize(adapter_bytes), normalize(runtime_bytes)
    if not adapter.startswith('// ArkDPS event probe adapter v1; loaded with event_probe_runtime.js.\n'):
        raise ValueError('unsupported event probe adapter source format')
    context_name = 'event_probe_autochess.js'
    context_bytes = read(directory / context_name)
    context = normalize(context_bytes)
    source = '\n'.join((context, adapter, runtime))
    digest = lambda text: sha256(text.encode('utf-8')).hexdigest()
    return AssembledProbeSource(source, adapter_name, runtime_name,
                                sha256(adapter_bytes).hexdigest(), sha256(runtime_bytes).hexdigest(),
                                digest(adapter), digest(runtime), digest(source),
                                context_name, sha256(context_bytes).hexdigest(), digest(context))


class ObserverReadRetry(RuntimeError):
    """An available probe needs another guarded read without advancing a cursor."""


class CallbackBusyMark(ObserverReadRetry):
    """A decoded live mark found an in-flight callback before it could commit."""


class TerminalReadRetry(ObserverReadRetry):
    """A decoded, available final source needs quiet/OnDestroy completion.

    Transport timeouts remain ordinary ObserverReadRetry and are not admitted
    to the bounded terminal-source retry loop.
    """


class StreamGap(ValueError):
    """The stream can no longer prove continuous event coverage."""


class BarrierBusy(StreamGap):
    """Valid producer marks surround a changing frame; retry without draining."""


class EventProbe(Protocol):
    contract: 'ProbeContract'
    status: Any
    error: str | None
    probe_modes: dict
    cleanup_errors: dict

    def start(self): ...
    def mark(self) -> dict: ...
    def read_terminal_mark(self) -> dict: ...
    def read_through(self, sequence: int) -> dict: ...
    def commit_events(self, sequence: int) -> bool | None: ...
    def stop(self): ...


def event_processing_payload(record):
    """Remove transport evidence while retaining the untouched replay record.

    Native AutoChess scope is checked by the acquisition/attribution boundary;
    it is not a Statistics event argument and cannot supply an event amount.
    """
    payload = dict(record)
    proof = payload.pop('proof', None)
    payload.pop('native_autochess_scope', None)
    if (payload.get('kind') in ('skill_start', 'skill_end')
            and isinstance(proof, dict) and isinstance(proof.get('method'), str)):
        payload['skill_method'] = proof['method']
    return payload


def _autochess_scope_key(payload):
    """Check the root part against the mark already bound to the native sample."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get('native_autochess_context')
    roots = payload.get('native_identity')
    if not isinstance(raw, dict) or not isinstance(roots, dict):
        return None
    try:
        values = []
        for name in ('controller', 'native', 'logger', 'stats', 'level'):
            value = roots[name]
            if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{1,16}', value):
                return None
            number = int(value, 16)
            if not 0x10000 <= number <= 0x7FFFFFFFFFFF or number % 8:
                return None
            values.append(hex(number))
        prefix = ':'.join(values) + ':autochess:'
        key = raw.get('scope_key')
        if not isinstance(key, str) or not key.startswith(prefix):
            return None
        epoch = key[len(prefix):]
        if not re.fullmatch(r'0|[1-9][0-9]{0,15}', epoch) or int(epoch) > 9007199254740991:
            return None
        return key
    except (KeyError, TypeError, ValueError):
        return None


def native_identity_sampling_mark(observer, mark):
    """Require a host-admitted capability and a bound opening mark."""
    if (getattr(observer, 'native_autochess_identity_sampling', False) is not True
            or not isinstance(mark, dict) or _autochess_scope_key(mark) is None):
        return None
    try:
        if not isinstance(contract_for(observer), NativePcProbeContract):
            return None
    except StreamGap:
        return None
    context = mark['native_autochess_context']
    if (type(context.get('protocol_version')) is int and context['protocol_version'] == 1
            and context.get('status') == 'available' and context.get('verified_same_frame') is True
            and context.get('phase') in (4, 5, 6)
            and context.get('frame') == mark.get('frame') and context.get('game_time') == mark.get('game_time')):
        return mark
    return None


def _android_autochess_scope_key(sample, payload):
    """Bind the producer's Android roots to the collector's guarded snapshot."""
    scope = _autochess_scope_key(payload)
    metadata = sample.get('battle_metadata') or {}
    if scope is None or payload.get('native_stage_id') != metadata.get('stage_id'):
        return None
    context = payload['native_autochess_context']
    fixed = payload.get('native_fixed_time')
    if (not isinstance(fixed, str) or not fixed.isascii() or not fixed.isdecimal()
            or len(fixed) > 20 or int(fixed) > 0x7FFFFFFFFFFFFFFF
            or type(payload.get('native_frame')) is not int or payload['native_frame'] < 0
            or payload['native_frame'] != context.get('frame')
            or int(fixed) / (1 << 32) != context.get('game_time')):
        return None
    try:
        controller, native = sample['battle_identity']
        session = sample.get('native_session', sample.get('session'))
        pid, session_controller, stats, level = map(int, session.rsplit(':', 4)[-4:])
        roots = payload['native_identity']
        if (pid > 0 and type(payload.get('native_process_pid')) is int
                and payload['native_process_pid'] == pid and session_controller == controller and
                all(type(expected) is int and expected > 0 and int(roots[key], 16) == expected
                    for key, expected in (('controller', controller), ('native', native),
                                          ('logger', sample.get('observer_logger')),
                                          ('stats', stats), ('level', level)))):
            return scope
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return None


def android_autochess_mark_context(sample, mark):
    """Normalize a mark at its own clock against the Android collector roots."""
    from autochess_context import normalize_native_autochess

    if not isinstance(mark, dict) or mark.get('retry'):
        return None
    scope = _android_autochess_scope_key(sample, mark)
    if scope is None:
        return None
    context = normalize_native_autochess(mark.get('native_autochess_context'),
                expected_frame=mark.get('frame'), expected_game_time=mark.get('game_time'),
                expected_scope_key=scope)
    if context.get('status') in ('available', 'partial') and context.get('match_key'):
        return context
    return None


def _native_pc_autochess_scope_key(sample, mark):
    """Parse only same-read PC binding facts; do not reinterpret a game window."""
    witness = sample.get('native_window_guard')
    metadata = sample.get('battle_metadata') or {}
    anchor = sample.get('autochess_snapshot_anchor', sample)
    names = ('controller', 'native', 'logger', 'stats', 'level')
    try:
        if (not isinstance(witness, dict) or type(witness.get('version')) is not int or witness['version'] != 1
                or witness.get('source') != 'native_pc_guarded_read'
                or not NativePcBinding._same(witness['before'], witness['after'])):
            return None
        birth = witness['process_instance_before']
        if (not isinstance(birth, list) or len(birth) != 2
                or any(type(value) is not int or value <= 0 for value in birth)
                or not NativePcBinding._same(witness['process_instance_after'], birth)):
            return None
        stamp = witness['before']
        roots = stamp['native_identity']
        if (not isinstance(roots, dict) or set(roots) != set(names)
                or any(not isinstance(roots[key], str) or re.fullmatch(r'0x[0-9a-fA-F]{1,16}', roots[key]) is None
                       or not 0x10000 <= int(roots[key], 16) <= 0x7FFFFFFFFFFF or int(roots[key], 16) % 8
                       for key in names)):
            return None
        serial, fixed = witness['battle_serial'], stamp['native_fixed_time']
        if (type(serial) is not int or not 0 < serial <= 9007199254740991
                or type(stamp['frame']) is not int or stamp['frame'] < 0
                or not isinstance(fixed, str) or re.fullmatch(r'[0-9]{1,20}', fixed) is None
                or int(fixed) > 0x7FFFFFFFFFFFFFFF or type(anchor.get('game_time')) not in (int, float)
                or not isfinite(anchor['game_time']) or not 0 <= anchor['game_time'] <= 864000
                or stamp['frame'] != anchor.get('frame') or int(fixed) / (1 << 32) != anchor['game_time']):
            return None
        digest = sha256(struct.pack('<6Q', *[int(roots[key], 16) for key in names], serial)).hexdigest()
        session = f'{birth[0]}:{birth[1]}:{digest}'
        if (sample.get('native_session', sample.get('session')) != session
                or tuple(sample.get('battle_identity', ())) != (digest,)
                or not isinstance(metadata.get('source_build'), str) or not metadata['source_build']
                or witness['source_build'] != metadata['source_build']
                or stamp['stage_id'] != metadata.get('stage_id')
                or mark.get('native_session') != session or mark.get('source_build') != metadata['source_build']
                or mark.get('native_identity') != roots or mark.get('native_stage_id') != stamp['stage_id']
                or type(mark.get('native_process_pid')) is not int or mark['native_process_pid'] != birth[0]
                or type(mark.get('native_generation')) is not int or not 0 <= mark['native_generation'] <= 9007199254740991
                or mark.get('active_hooks') != []):
            return None
        if ('native_process_created_filetime' in mark
                and (type(mark['native_process_created_filetime']) is not int
                     or mark['native_process_created_filetime'] != birth[1])):
            return None
        raw_time = mark.get('native_fixed_time')
        if (type(mark.get('native_frame')) is not int or mark['native_frame'] != mark.get('frame')
                or not isinstance(raw_time, str) or re.fullmatch(r'[0-9]{1,20}', raw_time) is None
                or int(raw_time) > 0x7FFFFFFFFFFFFFFF or type(mark.get('game_time')) not in (int, float)
                or not isfinite(mark['game_time']) or int(raw_time) / (1 << 32) != mark['game_time']):
            return None
        return _autochess_scope_key(mark)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def autochess_event_mark_context(sample, mark):
    """Normalize a raw platform binding at its own clock, without changing it."""
    from autochess_context import normalize_native_autochess
    if not isinstance(mark, dict) or mark.get('retry') or mark.get('error') or mark.get('fatal') or mark.get('stopped'):
        return None
    platform = (sample.get('battle_metadata') or {}).get('platform')
    if platform == 'android':
        return android_autochess_mark_context(sample, mark)
    if platform != 'native_pc':
        return None
    scope = _native_pc_autochess_scope_key(sample, mark)
    if scope is None:
        return None
    context = normalize_native_autochess(mark.get('native_autochess_context'),
        expected_frame=mark.get('frame'), expected_game_time=mark.get('game_time'), expected_scope_key=scope)
    if context.get('status') in ('available', 'partial'):
        return context
    return None


def verified_autochess_event_window(sample, before, after, *, anchor=None):
    """Prove an event-only watermark around one independent guarded snapshot.

    Each mark attests its own frame. The counter snapshot attests the battle
    roots and an intervening clock, but contributes no cumulative amounts.
    """
    from event_stream import baseline_barrier

    metadata = sample.get('battle_metadata') or {}
    platform = metadata.get('platform')
    if (sample.get('state') != 2 or platform not in ('android', 'native_pc')
            or 'AUTOCHESS' not in str(metadata.get('stage_id', '')).upper()):
        raise BarrierBusy('an AutoChess event window requires a playing battle')
    contexts = []
    for mark in (before, after):
        context = autochess_event_mark_context(sample, mark)
        if context is None:
            raise BarrierBusy('AutoChess event mark failed independent binding')
        check = dict(sample, frame=context['frame'], game_time=context['game_time'],
                     autochess_context=context, native_counter_scope='events_only')
        baseline_barrier(mark, check, mark, contract=(MUMU_AUTOCHESS_CONTRACT if platform == 'android' else NATIVE_PC_CONTRACT))
        contexts.append(context)
    opening, closing = contexts
    clock_fields = ('frame', 'game_time')
    if ({k: v for k, v in opening.items() if k not in clock_fields}
            != {k: v for k, v in closing.items() if k not in clock_fields}):
        raise BarrierBusy('AutoChess identity changed across the event window')

    original = sample if anchor is None else anchor
    frame, game_time = original.get('frame'), original.get('game_time')
    guard = sample.get('guard')
    if platform == 'native_pc':
        raw_guard = sample.get('native_window_guard') or {}
        start, end = raw_guard.get('before') or {}, raw_guard.get('after') or {}
        guard = dict(start=start.get('frame'), end=end.get('frame'), complete=start == end)
    if (type(frame) is not int or type(game_time) not in (int, float) or not isfinite(game_time)
            or not isinstance(guard, dict) or guard.get('complete') is not True
            or not guard.get('start') == guard.get('end') == frame
            or not opening['frame'] <= frame <= closing['frame']
            or not opening['game_time'] <= game_time <= closing['game_time']
            or any(context['frame'] == frame and context['game_time'] != game_time
                   for context in contexts)):
        raise BarrierBusy('AutoChess counter clock is outside the event window')
    settlement_scope = before.get('damage_settlement_scope')
    if (after['sequence'] < before['sequence'] or after['epoch'] < before['epoch']
            or (after['sequence'] > before['sequence'] and after['epoch'] == before['epoch'])
            or type(settlement_scope) is not int or settlement_scope < 0
            or type(after.get('damage_settlement_scope')) is not int
            or after['damage_settlement_scope'] != settlement_scope
            or (platform == 'native_pc' and before['native_generation'] != after['native_generation'])):
        raise BarrierBusy('AutoChess event window regressed or changed settlement scope')
    return closing, after['sequence']


def android_autochess_event_window(sample, before, after, *, anchor=None):
    """Compatibility entry: Android retains the same queue/window rules."""
    if (sample.get('battle_metadata') or {}).get('platform') != 'android':
        raise BarrierBusy('an Android AutoChess event window requires a playing battle')
    return verified_autochess_event_window(sample, before, after, anchor=anchor)


def bind_native_autochess_pair(sample, before, after):
    """Attach identity evidence only after the ordinary paired barrier accepted.

    Older recordings remain on their existing contract. New native envelopes
    must prove one complete identity frame; research pages are never merged.
    """
    metadata = sample.get('battle_metadata') or {}
    platform = metadata.get('platform')
    if (platform not in ('native_pc', 'android')
            or 'AUTOCHESS' not in str(metadata.get('stage_id', '')).upper()
            or not any('native_autochess_context' in mark for mark in (before, after))):
        return
    from autochess_context import normalize_native_autochess
    contexts = [normalize_native_autochess(mark.get('native_autochess_context'),
                    expected_frame=sample['frame'], expected_game_time=sample['game_time'],
                    expected_scope_key=(_android_autochess_scope_key(sample, mark) if platform == 'android'
                                        else _autochess_scope_key(mark)))
                for mark in (before, after)]
    known = [item.get('status') in ('available', 'partial')
             and (platform != 'android' or item.get('match_key') is not None) for item in contexts]
    controlled_scope = sample.get('controlled_autochess_scope')
    if controlled_scope is not None and any(
            not accepted or context.get('identity') != controlled_scope
            for accepted, context in zip(known, contexts)):
        raise BarrierBusy('controlled AutoChess event scope differs from the independent snapshot')
    if any(known) and (not all(known) or contexts[0] != contexts[1]):
        raise BarrierBusy('AutoChess identity changed across the snapshot')
    if platform == 'android' and not all(known):
        return  # Unproved Android observations keep their legacy contract.
    sample['autochess_context'] = contexts[0]
    # These CID-keyed counters cannot be split into per-player cumulative
    # amounts, even when the native damage rows themselves are nonempty.
    sample['native_counter_scope'] = 'events_only'


def native_autochess_context_bound(sample):
    """Whether this accepted frame carries independently bound AutoChess identity."""
    context = sample.get('autochess_context') or {}
    metadata = sample.get('battle_metadata') or {}
    identity = context.get('identity') or {}
    return bool(sample.get('native_counter_scope') == 'events_only'
                and (metadata.get('platform') == 'native_pc'
                     or metadata.get('platform') == 'android' and isinstance(context.get('match_key'), str))
                and 'AUTOCHESS' in str(metadata.get('stage_id', '')).upper()
                and context.get('status') in ('available', 'partial')
                and context.get('verified_same_frame') is True
                and context.get('frame') == sample.get('frame')
                and context.get('game_time') == sample.get('game_time')
                and isinstance(identity.get('scope_key'), str) and identity['scope_key']
                and identity.get('phase') == context.get('phase')
                and identity.get('round') == context.get('round'))


def native_autochess_event_baseline(sample):
    """Whether this accepted PC frame supplies a combat identity baseline."""
    return (native_autochess_context_bound(sample)
            and sample['autochess_context']['phase'] in (4, 5, 6))


def autochess_root_failure(mark):
    """Preserve the failed native guard when a mark asks the host to retry."""
    diagnostic = mark.get('autochess_binding_diagnostic')
    if not isinstance(diagnostic, dict) or diagnostic.get('status') != 'unavailable':
        return None
    guard, reason = diagnostic.get('guard'), diagnostic.get('reason')
    if not isinstance(guard, str) or not re.fullmatch(r'[a-z_]{1,64}', guard):
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return f'AutoChess {guard}: {reason.strip().splitlines()[0]}'[:200]


def autochess_scope_detail(evidence):
    """Expose failed proof separately from fields that qualify a sample."""
    for name in ('after', 'before'):
        mark = (evidence or {}).get(name) or {}
        context = mark.get('native_autochess_context') or {}
        failure = autochess_root_failure(mark)
        if failure:
            return failure
        diagnostic = mark.get('autochess_binding_diagnostic') or {}
        for report in (context, diagnostic):
            reason = report.get('reason') if isinstance(report, dict) else None
            if isinstance(reason, str) and reason.strip():
                return reason.strip().splitlines()[0][:200]
    return '阶段证据尚未通过采集快照归属校验'


def ready_probe_modes(payload):
    """Retain installation diagnostics, without certifying observed hit coverage."""
    booleans = ('coalesce_enemy_hp', 'trace_hp_context', 'source_character_uid_ready', 'skill_ready',
                'mitigation_ready', 'mitigation_no_event_ready', 'mitigation_factory_ready',
                'enemy_entry_ready', 'enemy_phase_ready', 'enemy_state_ready', 'enemy_max_hp_ready',
                'skill_cast_state_ready')
    errors = ('source_character_uid_error', 'skill_finish_error', 'mitigation_error',
              'mitigation_no_event_error', 'mitigation_factory_error', 'terminal_error',
              'enemy_entry_error', 'enemy_phase_error', 'enemy_state_error', 'enemy_max_hp_error',
              'stage_object_finish_error', 'skill_cast_state_error')
    result = {key: payload[key] for key in booleans if type(payload.get(key)) is bool}
    if type(payload.get('terminal_read_version')) is int and payload['terminal_read_version'] == 1:
        result['terminal_read_version'] = 1
    result.update({key: payload[key] for key in errors if key in payload
                   and (payload[key] is None or isinstance(payload[key], str))})
    raw = payload.get('mitigation_diagnostics')
    if isinstance(raw, dict):
        numeric = ('formula_scopes', 'formula_candidates', 'formula_missing_pre', 'apply_missing_candidate',
                   'apply_ambiguous_candidate', 'apply_value_mismatch', 'apply_changed_at_leave',
                   'emitted', 'emitted_after_adjustment', 'factory_candidates', 'factory_ambiguous',
                   'factory_emitted', 'unpaired_damage')
        diagnostics = {key: raw[key] for key in numeric if type(raw.get(key)) in (int, float)
                       and 0 <= raw[key] <= 9007199254740991 and isfinite(raw[key])}
        diagnostics.update({key: raw[key] for key in ('no_event_ready', 'factory_ready')
                            if type(raw.get(key)) is bool})
        diagnostics.update({key: raw[key] for key in ('no_event_error', 'factory_error')
                            if key in raw and (raw[key] is None or isinstance(raw[key], str))})
        result['mitigation_diagnostics'] = diagnostics
    return result


@dataclass(frozen=True)
class ProbeContract:
    name: str
    raw_replay: bool = False
    bounded_reads_required: bool = False
    ack_mark_required: bool = False
    strict_quality: bool = False
    strict_batches: bool = False

    def describe(self):
        return {'name': self.name}

    def validate_binding(self, sample, *payloads):
        pass

    def validate_batch(self, sample, through, batch, *, stream=None):
        if not self.strict_batches:
            return
        self.validate_binding(sample, batch)
        if batch.get('error') or batch.get('stopped'):
            return
        events = batch.get('events')
        if not isinstance(events, (list, tuple)):
            raise StreamGap('invalid bounded event batch')
        after = stream.sequence if stream is not None else through
        baseline = stream.baseline_sequence if stream is not None else through
        verified, last_record = after, None
        for event in events:
            if not isinstance(event, dict):
                raise StreamGap('invalid bounded event record')
            number, frame, time = event.get('sequence'), event.get('frame'), event.get('game_time')
            if (type(number) is not int or not 0 <= number <= through
                    or type(frame) is not int or not 0 <= frame <= sample['frame']
                    or type(time) not in (int, float) or not 0 <= time <= sample['game_time']
                    or (frame == sample['frame'] and time != sample['game_time'])):
                raise StreamGap('event record does not match the bounded watermark')
            if last_record is not None:
                previous = last_record['sequence']
                if number < previous or (number == previous and event != last_record):
                    raise StreamGap('event batch is out of order or has a conflicting replay')
            if baseline < number <= after and event != stream.received.get(number):
                raise StreamGap('event replay conflicts with the accepted record')
            if number > verified:
                if number != verified + 1:
                    raise StreamGap('event sequence gap')
                verified = number
            last_record = event
        if verified != through:
            raise StreamGap('event sequence does not reach the bounded watermark')

    def normalize_quality(self, payload):
        """Normalize declarations only; event records and measured values are untouched."""
        result = dict(payload)
        declared = 'quality_contract_version' in payload
        supported = type(payload.get('quality_contract_version')) is int and payload['quality_contract_version'] == 1
        if declared and not supported:
            raise StreamGap('unsupported event probe quality contract')
        strict = self.strict_quality or declared
        flags = ('damage_unknown', 'engagement_unknown', 'shield_unknown', 'skill_unknown', 'mitigation_unknown')
        for key in flags:
            result[key] = (payload.get(key) is not False if strict else bool(payload.get(key)))
        result['mitigation_ready'] = payload.get('mitigation_ready') is True if strict else payload.get('mitigation_ready') is not False
        return result

    def ack_scope(self, sample, generation):
        metadata = sample.get('battle_metadata') or {}
        return generation, sample.get('native_session', sample.get('session')), metadata.get('source_build')

    def validate_ack_mark(self, sample, mark, sequence):
        self.validate_binding(sample, mark)
        return type(mark.get('sequence')) is int and mark['sequence'] >= sequence

    def terminal_identity_matches(self, sample, boundary):
        try:
            controller, native = sample['battle_identity']
            session_controller, stats, level = map(int, sample['native_session'].rsplit(':', 3)[-3:])
            if session_controller != controller:
                return False
            return (all(expected > 0 and int(boundary[key], 16) == expected for key, expected in
                        (('controller', controller), ('native', native), ('stats', stats), ('level', level)))
                    and int(boundary['logger'], 16) > 0)
        except (KeyError, ValueError, TypeError):
            return False


@dataclass(frozen=True)
class NativePcProbeContract(ProbeContract):
    def validate_binding(self, sample, *payloads):
        metadata = sample.get('battle_metadata') if isinstance(sample, dict) else None
        session = sample.get('native_session', sample.get('session'))
        build = metadata.get('source_build') if isinstance(metadata, dict) else None
        if not isinstance(session, str) or not session or not isinstance(build, str) or not build:
            raise StreamGap('missing native PC battle or build identity')
        if any(not isinstance(payload, dict) or payload.get('native_session') != session
               or payload.get('source_build') != build for payload in payloads):
            raise StreamGap('observer data belongs to a different or unverified native PC battle or build')
        _validate_autochess_scope(sample, payloads, _autochess_scope_key)

    def terminal_identity_matches(self, sample, boundary):
        # Native marks/boundaries must instead carry the checked session/build.
        self.validate_binding(sample, boundary)
        return True


@dataclass(frozen=True)
class NativePcLiveProbeContract(NativePcProbeContract):
    def validate_binding(self, sample, *payloads):
        super().validate_binding(sample, *payloads)
        stage = sample['battle_metadata'].get('stage_id')
        if not isinstance(stage, str) or not stage or any(
                payload.get('native_stage_id') != stage for payload in payloads):
            raise StreamGap('cooperative PC event data belongs to a different or unverified stage')


def _validate_autochess_scope(sample, payloads, scope_key):
    context = sample.get('autochess_context') or {}
    identity = context.get('identity') if context.get('status') in ('available', 'partial') else None
    if identity:
        for payload in payloads:
            raw = (payload.get('native_autochess_context') or {}) if isinstance(payload, dict) else {}
            if (not isinstance(raw, dict) or raw.get('protocol_version') != 1 or raw.get('status') != 'available'
                    or raw.get('verified_same_frame') is not True
                    or scope_key(payload) != identity['scope_key']
                    or raw.get('phase') != identity['phase'] or raw.get('round') != identity['round']
                    or raw.get('match_key') != context.get('match_key')
                    or raw.get('collection_mode') != context.get('collection_mode')):
                raise BarrierBusy('AutoChess identity, phase or round changed before the bounded read')


@dataclass(frozen=True)
class MuMuAutoChessProbeContract(ProbeContract):
    def validate_binding(self, sample, *payloads):
        if not native_autochess_context_bound(sample):
            raise StreamGap('missing bound MuMu AutoChess identity')
        _validate_autochess_scope(sample, payloads, lambda payload: _android_autochess_scope_key(sample, payload))


LEGACY_CONTRACT = ProbeContract('legacy-events')
# Existing MuMu queue/baseline/replay/ack semantics remain unchanged. Current
# probes require explicit quality, independently of those source-proof choices.
MUMU_CONTRACT = ProbeContract('frida-events-v1', strict_quality=True)
NATIVE_PC_CONTRACT = NativePcProbeContract('native-pc-frida-v1', True, True, True, True, True)
NATIVE_PC_LIVE_CONTRACT = NativePcLiveProbeContract('native-pc-live-v1', True, True, True, True, True)
MUMU_AUTOCHESS_CONTRACT = MuMuAutoChessProbeContract(
    'mumu-autochess-frida-v1', True, True, True, False, True)
_CONTRACTS = {c.name: c for c in (LEGACY_CONTRACT, MUMU_CONTRACT, NATIVE_PC_CONTRACT,
                                  NATIVE_PC_LIVE_CONTRACT, MUMU_AUTOCHESS_CONTRACT)}


def _validate_contract(contract):
    flags = (contract.raw_replay, contract.strict_batches, contract.bounded_reads_required,
             contract.ack_mark_required, contract.strict_quality)
    if any(type(value) is not bool for value in flags):
        raise ValueError('event probe contract capabilities must be booleans')
    # The base validator compares a replay against EventStream.received. Raw
    # replay storage and strict batch comparison must therefore be selected
    # together: neither silent skipping nor dict-vs-normalized-tuple conflicts.
    if contract.raw_replay != contract.strict_batches:
        raise ValueError('raw replay and strict batches must be enabled together')


def register_contract(contract):
    """Register a host-owned provider profile before acquisition/replay starts.

    New providers can instead reuse a public profile. Wire payloads cannot
    register profiles or replace a built-in profile with weaker validation.
    """
    if not isinstance(contract, ProbeContract) or not isinstance(contract.name, str) or not contract.name:
        raise ValueError('a named ProbeContract is required')
    _validate_contract(contract)
    previous = _CONTRACTS.get(contract.name)
    if previous is not None and previous is not contract:
        raise ValueError('event probe contract name is already registered')
    _CONTRACTS[contract.name] = contract
    return contract


def contract_for(probe=None, sample=None, descriptor=None):
    """Only this boundary knows old source labels; payloads cannot weaken PC proof."""
    contract = getattr(probe, 'contract', None)
    if isinstance(contract, ProbeContract) and _CONTRACTS.get(contract.name) is not contract:
        raise StreamGap('unregistered host event probe contract')
    if not isinstance(contract, ProbeContract):
        if descriptor is not None:
            if not isinstance(descriptor, dict) or set(descriptor) != {'name'} or descriptor.get('name') not in _CONTRACTS:
                raise StreamGap('unsupported event probe contract')
            contract = _CONTRACTS[descriptor['name']]
        else:
            contract = LEGACY_CONTRACT
    try:
        _validate_contract(contract)
    except ValueError as exc:
        raise StreamGap(str(exc)) from exc
    metadata = sample.get('battle_metadata') if isinstance(sample, dict) else None
    if isinstance(metadata, dict) and metadata.get('platform') == 'native_pc':
        # Includes old recordings and test providers without an explicit host
        # contract. No producer field can downgrade the established PC checks.
        if isinstance(contract, NativePcProbeContract):
            if not all((contract.raw_replay, contract.bounded_reads_required, contract.ack_mark_required,
                        contract.strict_quality, contract.strict_batches)):
                raise StreamGap('native PC probe contract cannot weaken verified proof')
            return contract
        return NATIVE_PC_CONTRACT
    if (isinstance(metadata, dict) and metadata.get('platform') == 'android'
            and native_autochess_context_bound(sample)):
        return MUMU_AUTOCHESS_CONTRACT
    return contract


def acquire_native_terminal_tail(observer, raw_request, after, *, deadline=None):
    """Read one replayable final prefix under two identical real destroy fences."""
    observer.last_terminal_response = None
    observer.last_terminal_batch = None
    options = observer._mark_options()
    def request(operation, **arguments):
        timeout = observer.terminal_mark_timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TerminalReadRetry('terminal tail read admission budget expired')
            timeout = min(timeout, remaining)
        return raw_request(operation, timeout=timeout, **arguments)
    raw = request('mark', **options)
    observer.last_terminal_response = deepcopy(raw)
    opening = observer.binding.bind_terminal_tail_mark(raw, after)
    through = opening['terminal_boundary']['sequence']
    raw_batch = request('terminal_read', after=after, through=through)
    observer.last_terminal_batch = deepcopy(raw_batch)
    batch = observer.binding.bind_terminal_tail_batch(raw_batch, opening, after)
    raw = request('mark', **options)
    observer.last_terminal_response = deepcopy(raw)
    closing = observer.binding.bind_terminal_tail_mark(raw, after)
    if not observer.binding._same(opening['terminal_boundary'], closing['terminal_boundary']):
        raise TerminalReadRetry('terminal tail destroy fence changed across the read')
    boundary = closing['terminal_boundary']
    return dict(terminal_tail_version=1, sequence=through, read_after=after,
                before=opening['terminal_boundary'], after=boundary, drain=batch,
                terminal_boundary=boundary, terminal_raw_mark=deepcopy(raw),
                terminal_diagnostic=closing['terminal_binding'])


class NativePcBinding:
    """Map raw Windows evidence to the collector's verified battle identity."""
    def __init__(self, collector):
        self.collector = collector
        self.generation_sessions, self.session_generations = {}, {}
        self._terminal_cache = None
        self.terminal_diagnostic = {'status': 'missing', 'reason': 'no_guarded_watermark'}

    def reset(self):
        self.generation_sessions.clear()
        self.session_generations.clear()
        self._terminal_cache = None
        self.terminal_diagnostic = {'status': 'missing', 'reason': 'binding_reset'}

    @staticmethod
    def _same(left, right):
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(NativePcBinding._same(left[key], right[key]) for key in left)
        if isinstance(left, (list, tuple)):
            return len(left) == len(right) and all(NativePcBinding._same(a, b) for a, b in zip(left, right))
        return left == right

    _terminal_fields = ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope',
                        'native_frame', 'native_fixed_time', 'native_generation', 'native_process_pid',
                        'native_identity', 'native_stage_id', 'native_session', 'source_build')
    _roots = ('controller', 'native', 'logger', 'stats', 'level')

    def _terminal_mark_valid(self, mark):
        if (not isinstance(mark, dict) or mark.get('error') or mark.get('fatal') or mark.get('stopped')
                or mark.get('retry') or any(type(mark.get(key)) is not int or mark[key] < 0
                    for key in ('frame', 'sequence', 'epoch', 'active', 'native_frame',
                                'native_generation', 'native_process_pid', 'damage_settlement_scope'))
                or mark['active'] != 0 or mark.get('active_hooks') != [] or mark['native_process_pid'] <= 0
                or mark['native_frame'] != mark['frame']):
            raise RuntimeError('terminal watermark is not a complete quiescent mark')
        roots = mark.get('native_identity')
        if (not isinstance(roots, dict) or set(roots) != set(self._roots)
                or any(not isinstance(roots[key], str) or re.fullmatch(r'0x[0-9a-fA-F]{1,16}', roots[key]) is None
                       or not 0x10000 <= int(roots[key], 16) <= 0x7FFFFFFFFFFF or int(roots[key], 16) % 8
                       for key in self._roots)):
            raise RuntimeError('terminal watermark has no verified five-root identity')
        fixed = mark.get('native_fixed_time')
        if (not isinstance(fixed, str) or re.fullmatch(r'[0-9]{1,20}', fixed) is None
                or int(fixed) > 0x7FFFFFFFFFFFFFFF or type(mark.get('game_time')) not in (int, float)
                or not isfinite(mark['game_time']) or int(fixed) / (1 << 32) != mark['game_time']
                or any(not isinstance(mark.get(key), str) or not mark[key]
                       for key in ('native_stage_id', 'native_session', 'source_build'))):
            raise RuntimeError('terminal watermark has no exact clock or bound scope')

    def remember_verified_mark(self, sample, mark):
        """Called only after guarded lifecycle ingestion, never by predictive bind()."""
        self._terminal_cache = None
        try:
            self._terminal_mark_valid(mark)
            from event_stream import baseline_barrier
            baseline_barrier(mark, sample, mark, contract=NATIVE_PC_CONTRACT)
            process = self.collector.process
            birth = process.process_instance
            if (not isinstance(birth, (tuple, list)) or len(birth) != 2
                    or any(type(value) is not int or value <= 0 for value in birth)
                    or process.pid != birth[0] or mark['native_process_pid'] != birth[0]
                    or not process.process_alive()
                    or self.collector.diagnostics.version_actual != mark['source_build']
                    or (sample.get('battle_metadata') or {}).get('stage_id') != mark['native_stage_id']):
                raise RuntimeError('terminal cache process, stage or build is not independently bound')
            cached = {key: deepcopy(mark[key]) for key in self._terminal_fields}
            if 'native_autochess_context' in mark:
                cached['native_autochess_context'] = deepcopy(mark['native_autochess_context'])
            self._terminal_cache = (tuple(birth), cached)
            self.terminal_diagnostic = {'status': 'cached', 'reason': 'guarded_ingested_watermark'}
            return True
        except Exception as error:
            self.terminal_diagnostic = {'status': 'cache_rejected', 'error_type': type(error).__name__}
            return False  # Missing optional terminal proof cannot invent or invalidate ordinary samples.

    def _bound_terminal(self, boundary, mark):
        if not isinstance(boundary, dict):
            raise RuntimeError('raw terminal boundary is not an object')
        roots = boundary.get('native_identity')
        if roots is None:
            roots = {key: boundary.get(key) for key in self._roots}
        if not self._same(roots, mark['native_identity']):
            raise RuntimeError('raw terminal boundary five roots conflict with the verified watermark')
        for key in self._roots:
            if key in boundary and not self._same(boundary[key], mark['native_identity'][key]):
                raise RuntimeError('raw terminal boundary flat root conflicts with its nested identity')
        for key in ('frame', 'game_time', 'sequence', 'epoch', 'active', 'damage_settlement_scope'):
            if key not in boundary or not self._same(boundary[key], mark[key]):
                raise RuntimeError('raw terminal boundary clock, sequence, epoch or scope conflicts with the verified watermark')
        result = deepcopy(boundary)
        for key in (*self._terminal_fields, 'native_autochess_context'):
            if key not in mark:
                if key in boundary:
                    raise RuntimeError('raw terminal boundary introduces an unverified scope')
                continue
            if key in boundary and not self._same(boundary[key], mark[key]):
                raise RuntimeError('raw terminal boundary binding conflicts with the verified watermark')
            result.setdefault(key, deepcopy(mark[key]))
        for key in self._roots:
            result.setdefault(key, mark['native_identity'][key])
        return result

    def bind_terminal_mark(self, result):
        """Bind only a real raw fence at an already ingested watermark.

        A destroyed singleton may legitimately make normal mark() retry. That
        retry remains a retry: only its nested fence gains a cached host binding.
        """
        if not isinstance(result, dict):
            raise RuntimeError('invalid terminal-only response')
        if result.get('error') or result.get('fatal') or result.get('stopped'):
            raise RuntimeError('terminal source is failed or stopped')
        if (type(result.get('active')) is not int or result['active'] != 0
                or result.get('active_hooks') != []):
            raise ObserverReadRetry('terminal-only response has no explicit quiescent outer watermark')
        if not isinstance(result.get('terminal_boundary'), dict):
            if result.get('retry'):
                raise ObserverReadRetry('terminal-only retry has no raw boundary')
            return result
        if self._terminal_cache is None:
            raise RuntimeError('raw terminal boundary has no independently ingested watermark binding')
        birth, mark = self._terminal_cache
        process = self.collector.process
        if (process is None or tuple(process.process_instance) != birth or process.pid != birth[0]
                or not process.process_alive() or self.collector.diagnostics.version_actual != mark['source_build']):
            self._terminal_cache = None
            raise RuntimeError('terminal process birth or build changed')
        for key in (*self._terminal_fields, 'native_autochess_context'):
            if key in result and (key not in mark or not self._same(result[key], mark[key])):
                raise RuntimeError('outer terminal response conflicts with the cached clock or scope')
        if ('native_process_created_filetime' in result
                and not self._same(result['native_process_created_filetime'], birth[1])):
            raise RuntimeError('outer terminal response belongs to another process birth')
        boundary = self._bound_terminal(result['terminal_boundary'], mark)
        if ('native_process_created_filetime' in boundary
                and not self._same(boundary['native_process_created_filetime'], birth[1])):
            raise RuntimeError('raw terminal boundary belongs to another process birth')
        self.terminal_diagnostic = {'status': 'verified', 'reason': 'previous_guarded_watermark',
                                    'terminal_only': True}
        return dict(result, terminal_boundary=boundary, terminal_binding=dict(self.terminal_diagnostic))

    def bind_terminal_tail_mark(self, result, after):
        """Authorize a *read*, not sampling success, at a later real destroy fence.

        Only sequence/epoch may advance from an independently ingested mark.
        No singleton is re-named and the trusted counter watermark is unchanged.
        """
        if (not isinstance(result, dict) or result.get('error') or result.get('fatal')
                or result.get('stopped')):
            raise RuntimeError('terminal tail source is failed or stopped')
        if (type(result.get('active')) is not int or result['active'] < 0
                or not isinstance(result.get('active_hooks'), list)):
            raise RuntimeError('terminal tail has no explicit outer quiescence proof')
        if result['active'] != 0 or result['active_hooks'] != []:
            raise TerminalReadRetry('terminal tail requires explicit outer quiescence')
        rejection = result.get('terminal_read_rejection')
        if rejection is not None and rejection not in ('producer_not_quiet', 'terminal_boundary_unavailable',
                                                       'terminal_fence_changed'):
            raise RuntimeError('terminal tail producer rejected its retained prefix')
        if type(result.get('terminal_read_version')) is not int or result['terminal_read_version'] != 1:
            raise RuntimeError('terminal tail is not supported by this producer')
        if self._terminal_cache is None:
            raise RuntimeError('terminal tail has no independently ingested watermark binding')
        birth, cached = self._terminal_cache
        process = self.collector.process
        if (process is None or tuple(process.process_instance) != birth or process.pid != birth[0]
                or not process.process_alive() or self.collector.diagnostics.version_actual != cached['source_build']):
            self._terminal_cache = None
            raise RuntimeError('terminal tail process birth or build changed')
        candidate = result.get('terminal_boundary')
        if not isinstance(candidate, dict):
            raise TerminalReadRetry('terminal tail has no real destroy boundary')
        sequence, epoch = candidate.get('sequence'), candidate.get('epoch')
        if (type(after) is not int or type(sequence) is not int or type(epoch) is not int
                or not cached['sequence'] <= after <= sequence or epoch < cached['epoch']
                or (sequence > cached['sequence'] and epoch <= cached['epoch'])
                or type(result.get('sequence')) is not int or result['sequence'] != sequence
                or type(result.get('epoch')) is not int or result['epoch'] != epoch):
            raise RuntimeError('terminal tail sequence or epoch is not a bounded continuation')
        final = dict(cached, sequence=sequence, epoch=epoch)
        for key in (*self._terminal_fields, 'native_autochess_context'):
            if key in result and (key not in final or not self._same(result[key], final[key])):
                raise RuntimeError('outer terminal tail response conflicts with the cached clock or scope')
        boundary = self._bound_terminal(candidate, final)
        for item in (result, boundary):
            if ('native_process_created_filetime' in item
                    and not self._same(item['native_process_created_filetime'], birth[1])):
                raise RuntimeError('terminal tail belongs to another process birth')
        diagnostic = dict(status='verified', reason='guarded_clock_terminal_tail', terminal_only=True,
                          anchor_sequence=cached['sequence'], anchor_epoch=cached['epoch'])
        return dict(result, terminal_boundary=boundary, terminal_binding=diagnostic)

    def bind_terminal_tail_batch(self, result, opening, after):
        bound = self.bind_terminal_tail_mark(result, after)
        boundary = bound['terminal_boundary']
        if (not self._same(boundary, opening['terminal_boundary']) or result.get('retry')
                or result.get('terminal_only') is not True
                or type(result.get('after')) is not int or result['after'] != after
                or type(result.get('through')) is not int or result['through'] != boundary['sequence']
                or not isinstance(result.get('events'), list)):
            raise RuntimeError('terminal tail read changed its bounded final fence')
        for key in (*self._terminal_fields, 'native_autochess_context'):
            if key in boundary:
                bound.setdefault(key, deepcopy(boundary[key]))
        return bound

    def bind(self, operation, result):
        if operation not in ('mark', 'read') or result.get('error') or result.get('stopped'):
            return result
        if result.get('retry'):
            detail = autochess_root_failure(result)
            if not detail and type(result.get('active')) is int and result['active'] > 0:
                detail = ('采样开始时有未完成的战斗回调' if result.get('mark_retry_reason') == 'callback_pending_at_start'
                          else '采样结束时仍有未完成的战斗回调')
                if operation == 'mark':
                    raise CallbackBusyMark(f'官服 PC 事件来源 {operation} · {detail}')
            if not detail and operation == 'mark' and type(result.get('native_frame')) is int:
                detail = '独立战局时钟与采样标记未能对齐'
            raise ObserverReadRetry(f'官服 PC 事件来源 {operation} · {detail}' if detail else
                                    f'官服 PC 事件来源 {operation} 战局或时钟正在变化，请重试当前采集')
        generation = result.get('native_generation')
        if type(generation) is not int or generation < 0:
            raise RuntimeError('官服 PC 事件来源 缺少事件世代')
        frame = result.get('native_frame', result.get('frame'))
        fixed_time = result.get('native_fixed_time')
        if (not isinstance(fixed_time, str) or not fixed_time.isdecimal()
                or len(fixed_time) > 20 or int(fixed_time) > 0x7FFFFFFFFFFFFFFF):
            raise RuntimeError('官服 PC 事件来源 缺少精确逻辑时钟')
        game_time = int(fixed_time) / (1 << 32)
        if operation == 'mark' and (result.get('frame') != frame or result.get('game_time') != game_time):
            raise RuntimeError('官服 PC 事件来源 标记与原始逻辑时钟不一致')
        from collector import ProcessChanged, SampleUnavailable, UnsupportedVersion
        try:
            binding = self.collector.observer_binding(
                frame=frame, game_time=game_time, native_identity=result.get('native_identity'),
                native_stage_id=result.get('native_stage_id'),
                native_process_pid=result.get('native_process_pid'), native_fixed_time=fixed_time)
        except (ProcessChanged, UnsupportedVersion):
            raise
        except SampleUnavailable as exc:
            raise ObserverReadRetry(str(exc)) from exc
        session = binding['native_session']
        previous = self.generation_sessions.get(generation)
        if previous is not None and previous != session:
            raise RuntimeError('官服 PC 事件来源 事件世代与已确认战局不一致')
        previous_generation = self.session_generations.get(session)
        if previous_generation is not None and previous_generation != generation:
            raise RuntimeError('官服 PC 原生战局未能区分已观察到的新事件世代，当前事件覆盖已中断')
        self.generation_sessions[generation] = session
        self.session_generations[session] = generation
        bound = dict(result, **binding)
        if operation == 'mark' and isinstance(result.get('terminal_boundary'), dict):
            self._terminal_mark_valid(bound)
            bound['terminal_boundary'] = self._bound_terminal(result['terminal_boundary'], bound)
        return bound
