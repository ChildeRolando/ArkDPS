"""Public event-probe contract and compatibility boundary; no statistics writes."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from math import isfinite
import re
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


def assemble_probe_source(platform, *, directory=None):
    """Compose the fixed adapter/runtime boundary; never rewrite source tokens."""
    names = {'mumu': 'event_probe_android2791.js', 'native_pc': 'event_probe_native_pc.js'}
    if platform not in names:
        raise ValueError('unsupported event probe source platform')
    directory = Path(__file__).resolve().parent if directory is None else Path(directory)
    adapter_name, runtime_name = names[platform], 'event_probe_runtime.js'
    adapter_path, runtime_path = directory / adapter_name, directory / runtime_name
    adapter_bytes, runtime_bytes = adapter_path.read_bytes(), runtime_path.read_bytes()
    # Hash and compile the same single read, even if files change concurrently.
    normalize = lambda raw: raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    adapter, runtime = normalize(adapter_bytes), normalize(runtime_bytes)
    if not adapter.startswith('// ArkDPS event probe adapter v1; loaded with event_probe_runtime.js.\n'):
        raise ValueError('unsupported event probe adapter source format')
    context_name = 'event_probe_autochess.js'
    context_bytes = (directory / context_name).read_bytes()
    context = normalize(context_bytes)
    source = '\n'.join((context, adapter, runtime))
    digest = lambda text: sha256(text.encode('utf-8')).hexdigest()
    return AssembledProbeSource(source, adapter_name, runtime_name,
                                sha256(adapter_bytes).hexdigest(), sha256(runtime_bytes).hexdigest(),
                                digest(adapter), digest(runtime), digest(source),
                                context_name, sha256(context_bytes).hexdigest(), digest(context))


class ObserverReadRetry(RuntimeError):
    """An available probe needs another guarded read without advancing a cursor."""


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
    payload = {key: value for key, value in record.items()
               if key not in ('proof', 'native_autochess_scope')}
    proof = record.get('proof')
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


def android_autochess_event_window(sample, before, after, *, anchor=None):
    """Prove an event-only watermark around an independent Android snapshot.

    Each mark attests its own frame. The counter snapshot attests the battle
    roots and an intervening clock, but contributes no cumulative amounts.
    """
    from event_stream import baseline_barrier

    metadata = sample.get('battle_metadata') or {}
    if (sample.get('state') != 2 or metadata.get('platform') != 'android'
            or 'AUTOCHESS' not in str(metadata.get('stage_id', '')).upper()):
        raise BarrierBusy('an Android AutoChess event window requires a playing battle')
    contexts = []
    for mark in (before, after):
        context = android_autochess_mark_context(sample, mark)
        if context is None:
            raise BarrierBusy('Android AutoChess event mark failed independent binding')
        check = dict(sample, frame=context['frame'], game_time=context['game_time'],
                     autochess_context=context, native_counter_scope='events_only')
        baseline_barrier(mark, check, mark, contract=MUMU_AUTOCHESS_CONTRACT)
        contexts.append(context)
    opening, closing = contexts
    clock_fields = ('frame', 'game_time')
    if ({k: v for k, v in opening.items() if k not in clock_fields}
            != {k: v for k, v in closing.items() if k not in clock_fields}):
        raise BarrierBusy('Android AutoChess identity changed across the event window')

    original = sample if anchor is None else anchor
    frame, game_time = original.get('frame'), original.get('game_time')
    guard = sample.get('guard')
    if (type(frame) is not int or type(game_time) not in (int, float) or not isfinite(game_time)
            or not isinstance(guard, dict) or guard.get('complete') is not True
            or not guard.get('start') == guard.get('end') == frame
            or not opening['frame'] <= frame <= closing['frame']
            or not opening['game_time'] <= game_time <= closing['game_time']
            or any(context['frame'] == frame and context['game_time'] != game_time
                   for context in contexts)):
        raise BarrierBusy('Android AutoChess counter clock is outside the event window')
    settlement_scope = before.get('damage_settlement_scope')
    if (after['sequence'] < before['sequence'] or after['epoch'] < before['epoch']
            or (after['sequence'] > before['sequence'] and after['epoch'] == before['epoch'])
            or type(settlement_scope) is not int or settlement_scope < 0
            or type(after.get('damage_settlement_scope')) is not int
            or after['damage_settlement_scope'] != settlement_scope):
        raise BarrierBusy('Android AutoChess event window regressed or changed settlement scope')
    return closing, after['sequence']


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


def autochess_scope_detail(evidence):
    """Expose failed proof separately from fields that qualify a sample."""
    for name in ('after', 'before'):
        mark = (evidence or {}).get(name) or {}
        context = mark.get('native_autochess_context') or {}
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
MUMU_AUTOCHESS_CONTRACT = MuMuAutoChessProbeContract(
    'mumu-autochess-frida-v1', True, True, True, False, True)
_CONTRACTS = {c.name: c for c in (LEGACY_CONTRACT, MUMU_CONTRACT, NATIVE_PC_CONTRACT, MUMU_AUTOCHESS_CONTRACT)}


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


class NativePcBinding:
    """Map raw Windows evidence to the collector's verified battle identity."""
    def __init__(self, collector):
        self.collector = collector
        self.generation_sessions, self.session_generations = {}, {}

    def reset(self):
        self.generation_sessions.clear()
        self.session_generations.clear()

    def bind(self, operation, result):
        if operation not in ('mark', 'read') or result.get('error') or result.get('stopped'):
            return result
        if result.get('retry'):
            raise ObserverReadRetry('官服 PC Frida 战局或时钟正在变化，请重试当前采集')
        generation = result.get('native_generation')
        if type(generation) is not int or generation < 0:
            raise RuntimeError('官服 PC Frida 缺少事件世代')
        frame = result.get('native_frame', result.get('frame'))
        fixed_time = result.get('native_fixed_time')
        if (not isinstance(fixed_time, str) or not fixed_time.isdecimal()
                or len(fixed_time) > 20 or int(fixed_time) > 0x7FFFFFFFFFFFFFFF):
            raise RuntimeError('官服 PC Frida 缺少精确逻辑时钟')
        game_time = int(fixed_time) / (1 << 32)
        if operation == 'mark' and (result.get('frame') != frame or result.get('game_time') != game_time):
            raise RuntimeError('官服 PC Frida 标记与原始逻辑时钟不一致')
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
            raise RuntimeError('官服 PC Frida 事件世代与已确认战局不一致')
        previous_generation = self.session_generations.get(session)
        if previous_generation is not None and previous_generation != generation:
            raise RuntimeError('官服 PC 原生战局未能区分已观察到的新事件世代，当前事件覆盖已中断')
        self.generation_sessions[generation] = session
        self.session_generations[session] = generation
        return dict(result, **binding)
