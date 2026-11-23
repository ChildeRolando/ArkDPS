"""Drive real capture and history from a controlled, device-free event host."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from battle_history import BattleHistory
from capture_cli import json_value
from collection_session import CollectionSession, shutdown_report
from event_capture import CaptureRetry
from roster import CharacterEntry, RosterSnapshot


SOURCE = 'controlled_internal_event_feed'
BUILD = 'win-x64:controlled-internal-event-feed'
CID = 'char_367_swllow'
ROOTS = dict(controller='0x10000', native='0x20000', logger='0x30000', stats='0x40000', level='0x50000')
SCOPE = ':'.join(ROOTS.values()) + ':autochess:1'
QUALITY = dict(damage_unknown=False, engagement_unknown=False, skill_unknown=False,
               shield_unknown=False, mitigation_ready=False, mitigation_unknown=True,
               source_attribution_version=1, damage_settlement_ready=True, damage_settlement_unknown=False,
               partial_damage_version=7, enemy_damage_ready=True, enemy_damage_unknown=False,
               baseline_discarded_skill_events=[], baseline_discarded_skill_events_complete=True)


def _event(sequence, kind, amount, **proof):
    return dict(sequence=sequence, frame=30, game_time=1, source=CID, kind=kind,
                amount=amount, **proof)


def _builtin_fixture(scenario):
    autoc = scenario == 'autochess'
    stage = 'Activities/ACT3AUTOCHESS/level_act3autochess_m11' if autoc else 'Obt/Main/level_main_01-07'
    session = f'10:1234:controlled-{scenario}'
    roster = [dict(cid=CID, unique_id=1, player_side=0, is_token=False,
                   is_predefined=False, host_key=None, host_uid=None)] if not autoc else []
    players = [dict(index=i, uid=str(910000000 + i), nick_name=f'演示玩家 {i}', nick_number=f'{i:04d}')
               for i in range(1, 5)]
    members = [dict(cid=CID, data_uid=100 * i + 1, player_index=i, player_side=i,
                    is_token=False, is_predefined=False, host_key=None, host_uid=None)
               for i in range(1, 5)]
    scope = dict(protocol_version=1, status='available', verified_same_frame=True,
                 scope_key=SCOPE, phase=4, round=1)
    if autoc:
        events = [_event(i, 'damage_settlement', i * 100, source_character_uid=100 * i + 1,
                         nominal_amount=i * 100, target='enemy_1007_slime', target_uid=str(i),
                         target_instance=f'controlled-target-{i}', damage_type=1,
                         shield_absorbed=0, settlement_complete=True, killed=False,
                         native_autochess_scope=dict(scope, frame=30, game_time=1))
                  for i in range(1, 5)]
    else:
        target = dict(target='enemy_1007_slime', target_uid='1', target_instance='controlled-target-1',
                      damage_type=1, shield_absorbed=0, killed=False)
        events = [_event(1, 'skill_start', 1, skill_key='controlled-skill', source_character_uid=1),
                  _event(2, 'damage', 20, source_character_uid=1, target_type=0,
                         hp_before=100, hp_after=80, real_delta=-20, **target),
                  _event(3, 'damage_settlement', 20, source_character_uid=1, nominal_amount=20,
                         settlement_complete=True, **target),
                  _event(4, 'healing', 6, source_character_uid=1),
                  _event(5, 'shield_absorbed', 3, source_character_uid=1)]
    frames = []
    for when, state in enumerate((2, 2, 3)):
        frame = 30 * when
        damage = 0 if autoc or not when else 20
        heal = 0 if autoc or not when else 6
        rows = [] if autoc else [dict(id=CID, damage=damage, healing=heal, physical=damage,
                                      arts=0, true=0, elemental=0)]
        metadata = dict(platform='native_pc', source_build=BUILD, stage_id=stage, practice=None,
                        outcome='victory' if state == 3 else None, evidence=SOURCE)
        snapshot = dict(session=session, frame=frame, game_time=when, state=state,
                        rows=rows, global_total=damage, global_heal=heal)
        life = dict(battle=[f'controlled-{scenario}'], state=state, ready=True,
                    transiting=False, source='', target='', metadata=metadata)
        tick_events = events if when == 1 else []
        if when == 2 and not autoc:
            tick_events = [dict(_event(6, 'skill_end', 1, skill_key='controlled-skill', source_character_uid=1),
                                frame=frame, game_time=when)]
        sequence = 0 if not when else len(events) + (when == 2 and not autoc)
        mark = dict(QUALITY, native_session=session, source_build=BUILD, sequence=sequence,
                    epoch=sequence, active=0, frame=frame, game_time=when)
        if autoc:
            mark.update(native_identity=ROOTS, native_autochess_context=dict(
                scope, frame=frame, game_time=when, self_player_index=3, players=players,
                memberships=members, collection_mode='all'))
        frames.append(dict(lifecycle=life, snapshot=snapshot, roster=roster,
                           mark=mark, events=tick_events))
    frames.append(dict(lifecycle=dict(frames[-1]['lifecycle'], battle=None), snapshot=None,
                       roster=[], mark=frames[-1]['mark'], events=[], terminal_mark=frames[-1]['mark']))
    return dict(format=1, scenario=scenario, frames=frames)


def _load_fixture(fixture):
    if isinstance(fixture, str) and fixture in ('normal', 'autochess'):
        value = _builtin_fixture(fixture)
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    else:
        maximum_fixture_bytes = 32 * 1024 * 1024
        with Path(fixture).open('rb') as stream:
            data = stream.read(maximum_fixture_bytes + 1)
        if len(data) > maximum_fixture_bytes:
            raise ValueError('controlled capture fixture exceeds 32 MiB')
        value = json.loads(data)
    if (not isinstance(value, dict) or type(value.get('format')) is not int or value['format'] != 1
            or value.get('scenario') not in ('normal', 'autochess')
            or not isinstance(value.get('frames'), list) or not value['frames']):
        raise ValueError('invalid controlled capture fixture')
    for frame in value['frames']:
        if (not isinstance(frame, dict) or not isinstance(frame.get('lifecycle'), dict)
                or not isinstance(frame.get('mark'), dict) or not isinstance(frame.get('events'), list)
                or not isinstance(frame.get('roster'), list) or 'snapshot' not in frame):
            raise ValueError('invalid controlled capture fixture frame')
    return value, hashlib.sha256(data).hexdigest()


class _FixtureCollector:
    """Play the independently supplied snapshots without reading an OS process."""

    def __init__(self):
        self.process = SimpleNamespace(pid=10, process_instance=(10, 1234))
        self.diagnostics = SimpleNamespace(platform='native_pc', connection_ok=True, evidence=SOURCE)
        self.event_capability = {}
        self.frame = None
        self._life = None

    def connect(self, *, locate):
        pass

    def locate_lifecycle(self):
        pass

    def close(self):
        self.frame = self._life = None

    def advance(self, frame):
        self.frame = frame
        self._life = None

    def read_lifecycle(self):
        life = deepcopy(self.frame['lifecycle'])
        if life['battle'] is not None:
            life['battle'] = tuple(life['battle'])
        life['metadata'].update(platform='native_pc', evidence=SOURCE,
                                evidence_sources=[SOURCE])
        self._life = life
        return life

    def read_guarded_pair(self, lifecycle):
        if self._life is not lifecycle:
            raise AssertionError('controlled snapshot must be read with its own lifecycle guard')
        sample = dict(deepcopy(self.frame['snapshot']), battle_identity=lifecycle['battle'],
                      battle_metadata=deepcopy(lifecycle['metadata']), observed_at=time.time(),
                      captured_monotonic=time.monotonic(), event_capability=dict(self.event_capability))
        entries = tuple(CharacterEntry(**entry) for entry in self.frame['roster'])
        roster = RosterSnapshot(sample['frame'], entries=entries, present_known=True,
                                present_unique_ids=frozenset(entry.unique_id for entry in entries),
                                local_player_side=self.frame.get('local_player_side', 0))
        return sample, roster


def run_internal_capture(host, fixture, state_dir, *, publish=None):
    """Return the real lifecycle output and persist an isolated history and journal.

    `fixture` is a controlled scenario name or the path to versioned fixture data.
    The snapshot owner and the host never discover or connect to a game process.
    """
    from native_pc.internal_event_source import InternalEventProbe

    value, digest = _load_fixture(fixture)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    journal = state_dir / f'internal-capture-{uuid4().hex}.jsonl'
    history = BattleHistory(state_dir / 'history')
    collector = _FixtureCollector()
    probe = InternalEventProbe(Path(host))
    archives = []
    samples = 0
    last_sample = None
    source_failure = None
    with journal.open('x', encoding='utf-8') as stream:
        def audit(kind, data):
            stream.write(json.dumps(dict(kind=kind, data=data), ensure_ascii=False,
                                    default=json_value, allow_nan=False) + '\n')
            if kind == 'history_saved' and data['written']:
                archives.append(data['battle_id'])

        audit('controlled_source', dict(scenario=value['scenario'], fixture_sha256=digest,
                                        evidence=SOURCE, game_connected=False))
        session = CollectionSession(None, None, collector=collector, frida_remote=None,
                                    observer_factory=lambda *_args: probe, history=history,
                                    state_dir=state_dir, audit=audit, autochess_enabled=True)
        session.enable_lifecycle()
        try:
            session.connect()
            if session.observer is not probe or session.event_status != 'ready':
                raise RuntimeError(session.event_error or 'controlled event host did not become ready')
            for frame in value['frames']:
                collector.advance(frame)
                if source_failure is None:
                    try:
                        if not probe.advance(frame['mark'], frame['events'], terminal_mark=frame.get('terminal_mark')):
                            raise RuntimeError('controlled event host did not accept its next frame')
                    except (RuntimeError, OSError) as error:
                        source_failure = dict(reason=str(error), error_type=type(error).__name__,
                                              frame=frame['mark'].get('frame'))
                        audit('observer_advance_failed', source_failure)
                        probe.stop()
                try:
                    last_sample = session.read()
                except CaptureRetry:
                    if frame['lifecycle']['battle'] is not None and source_failure is None:
                        raise
                    continue
                samples += 1
                if publish is not None:
                    publish(last_sample)
        finally:
            session.close()
        cleanup = shutdown_report(session)
        if not cleanup['ok'] or session.history_error:
            raise RuntimeError(session.history_error or f'controlled event host cleanup failed: {cleanup}')
    if not samples or not archives:
        raise ValueError('controlled capture has no published samples or archived battle')
    status = 'ok' if source_failure is None and last_sample['event_status'] == 'finished' else 'degraded'
    return dict(status=status, source=SOURCE, scenario=value['scenario'], game_connected=False,
                fixture_sha256=digest, samples=samples, archives=len(archives), battle_ids=archives,
                last_sample=last_sample, journal=str(journal), source_failure=source_failure)
