"""Live enemy telemetry built on the verified read-only EnemyReader.

Enemy data is supplemental to the authoritative operator counters. A failed
locate or poll never changes statistics; it only reports telemetry as
unavailable so the UI cannot render invented enemies or HP.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
import math
import time

try:  # The vendored reader is available whenever collector.py is imported.
    from tools.enemy_health import game_structs as _gs
except ImportError:  # Unit tests import this module without the vendor path.
    _gs = None

ENEMY_STATE_NAMES = {
    -1: '终止', 0: '默认', 1: '移动', 2: '攻击', 3: '战斗', 4: '眩晕',
    5: '死亡', 6: '出生', 7: '到达终点', 8: '重生', 9: '失衡',
    10: '坠落', 11: '消失', 12: '闪现', 13: '冻结', 14: '浮空',
    15: '剧情', 16: '麻痹',
}
if _gs is not None:
    ENEMY_STATE_NAMES = dict(getattr(_gs, 'ENEMY_STATE_NAMES', ENEMY_STATE_NAMES))

ENTERED_LIFECYCLES = ('active', 'departed', 'entered', 'rebirthing')
MAX_TIMELINE_POINTS = 7200
MAX_SERIES_POINTS = 360
EVENT_CALIBRATION_INTERVAL = 5.0
_LIVE_FIELDS = ('hp', 'alive', 'lifecycle', 'state_id', 'state', 'end_frame',
                'end_reason', 'finish_reason')
_ATTRIBUTES = ('atk', 'def', 'res', 'move_speed', 'attack_speed')
_FINISH_STATES = {1: ('reach_exit', 7, '到达终点'), 2: ('death', 5, '死亡')}


def _finite(value, default=0.0):
    return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else default)


def _text(value):
    return value if isinstance(value, str) else ''


def _hp_ratio(hp, max_hp):
    if hp is not None and max_hp is not None and max_hp > 0:
        return min(1.0, max(0.0, hp / max_hp))
    return None


def _snapshot_attribute(enemy, native_key, field):
    attributes = getattr(enemy, 'attributes', None)
    if _gs is not None and attributes is not None:
        index = getattr(_gs.AttributeType, native_key)
        if index not in attributes:
            return None
    return _finite(getattr(enemy, field, None), None)


def enemy_rows(snapshot, *, include_pending=False):
    """Serializable enemy rows; pending plan entries are opt-in."""
    allowed = ENTERED_LIFECYCLES + (('pending',) if include_pending else ())
    rows = []
    for enemy in snapshot.get('enemies') or ():
        lifecycle = _text(getattr(enemy, 'lifecycle', 'active')) or 'active'
        if lifecycle not in allowed:
            continue
        max_hp = _finite(getattr(enemy, 'max_hp', 0.0))
        hp = _finite(getattr(enemy, 'hp', 0.0))
        state_id = getattr(enemy, 'state_id', None)
        if not isinstance(state_id, int):
            state_id = None
        row = {
            'roster_id': getattr(enemy, 'roster_id', None),
            'instance_uid': getattr(enemy, 'instance_uid', None),
            'spawn_order': getattr(enemy, 'spawn_order', None),
            'eid': _text(getattr(enemy, 'eid', '')),
            'name': _text(getattr(enemy, 'name', '')),
            'code': _text(getattr(enemy, 'code', '')),
            'hp': hp,
            'max_hp': max_hp,
            'hp_known': max_hp > 0,
            'hp_ratio': _hp_ratio(hp, max_hp),
            'atk': _snapshot_attribute(enemy, 'ATK', 'atk'),
            'def': _snapshot_attribute(enemy, 'DEF', 'def_'),
            'res': _snapshot_attribute(enemy, 'MAGIC_RESISTANCE', 'res'),
            'move_speed': _snapshot_attribute(enemy, 'MOVE_SPEED', 'mspd'),
            'attack_speed': _snapshot_attribute(enemy, 'ATTACK_SPEED', 'aspd'),
            'alive': bool(getattr(enemy, 'alive', False)),
            'lifecycle': lifecycle,
            'entry_inferred': lifecycle == 'departed' and getattr(enemy, 'addr', None) == 0,
            'state_id': state_id,
            'state': ENEMY_STATE_NAMES.get(state_id, f'未知({state_id})') if state_id is not None else '未知',
            'spawn_frame': getattr(enemy, 'spawn_frame', None),
            'end_frame': getattr(enemy, 'end_frame', None),
            'end_reason': getattr(enemy, 'end_reason', None),
            'finish_reason': getattr(enemy, 'finish_reason', None),
            'is_summon': bool(getattr(enemy, 'is_summon', False)),
            'planned': bool(getattr(enemy, 'planned', False)),
            'spawn_kind': _text(getattr(enemy, 'spawn_kind', '')) or 'scheduled',
            'hp_inferred': False,
            'position': {
                'x': _finite(getattr(enemy, 'pos_x', 0.0)),
                'y': _finite(getattr(enemy, 'pos_y', 0.0)),
            },
        }
        rows.append(_remember_capacity_floor(row))
    if include_pending:
        # A planned enemy that died between polls has no live attributes. The
        # same enemy type's observed MaxHP is still a valid denominator; never
        # invent a value for a type that was never observed.
        observed_hp = {}
        for row in rows:
            if row['lifecycle'] in ENTERED_LIFECYCLES and row['hp_known']:
                observed_hp[row['eid']] = max(observed_hp.get(row['eid'], 0.0),
                                             row['max_hp'])
        for row in rows:
            if (not row['hp_known'] and row['lifecycle'] == 'pending'
                    and row['eid'] in observed_hp):
                row['max_hp'] = observed_hp[row['eid']]
                row['hp_known'] = True
                row['hp_inferred'] = True
    rows.sort(key=lambda row: (row['spawn_order'] is None,
                               row['spawn_order'] or 0, row['roster_id'] or 0))
    return rows


def _max_hp(row):
    hp = _finite(row.get('max_hp'))
    return hp if row['hp_known'] and hp > 0 else None


def _same_hp(left, right):
    return left is not None and right is not None and math.isclose(
        left, right, rel_tol=1e-9, abs_tol=1e-6)


def _phase_capacity(row):
    return max((value for value in (_max_hp(row), row.get('phase_max_hp'),
                                    row.get('phase_max_hp_lower_bound')) if value is not None),
               default=None)


def _phase_expansion(row):
    initial = row.get('phase_initial_max_hp')
    capacity = _phase_capacity(row)
    return max(0, capacity - initial) if initial is not None and capacity is not None else 0


def _phase_capacity_is_partial(row):
    return row.get('phase_max_hp_lower_bound') is not None


def _observe_max_hp(row, hp, observed_max_hp=None, *, seed=False):
    """Keep native MaxHP distinct from the historical floor proved by current HP."""
    current_max = _max_hp(row)
    initial = row.get('phase_initial_max_hp', current_max)
    if initial is None and seed and row.get('phase_max_hp') is None:
        initial = observed_max_hp
    peak = max(row.get('phase_max_hp') or 0, current_max or 0, observed_max_hp or 0)
    phase_floor = row.get('phase_max_hp_lower_bound') or 0
    current_floor = row.get('max_hp_lower_bound') or 0
    if observed_max_hp is not None:
        row = dict(row, max_hp=observed_max_hp, hp_known=True)
        current_max = observed_max_hp
        current_floor = 0
    if hp is not None and current_max is not None and hp > current_max:
        current_floor = max(current_floor, hp)
        phase_floor = max(phase_floor, hp)
    return dict(row, phase_initial_max_hp=initial, phase_max_hp=peak or None,
                phase_max_hp_lower_bound=phase_floor if phase_floor > peak else None,
                max_hp_lower_bound=current_floor or None)


def _next_hp_phase(row):
    expansion = _phase_expansion(row)
    earlier_capacity = max((value for value in (_phase_capacity(row),
                                                row.get('earlier_phase_capacity'))
                            if value is not None), default=None)
    return dict(
        row, max_hp=None, hp_known=False, phase_initial_max_hp=None,
        phase_max_hp=None, phase_max_hp_lower_bound=None, max_hp_lower_bound=None,
        earlier_phase_capacity=earlier_capacity,
        completed_phase_hp_expansion=row.get('completed_phase_hp_expansion', 0) + expansion,
        initial_phase_hp_expansion=row.get('initial_phase_hp_expansion', expansion),
        prior_phase_capacity_partial=(row.get('prior_phase_capacity_partial', False)
                                      or _phase_capacity_is_partial(row)),
    )


def _current_hp_ratio(row):
    if row.get('max_hp_lower_bound') is not None:
        return None
    return _hp_ratio(row['hp'], row['max_hp'])


def _phase_key(row):
    uid = row.get('instance_uid')
    return (uid, row['eid']) if uid is not None else None


def _observed_hp_floor(row):
    if _phase_key(row) is None or row.get('entry_inferred'):
        return 0
    if row['lifecycle'] == 'departed':
        hp = row.get('hp_at_finish')
    else:
        hp = row.get('hp') if row['alive'] else None
    return max(_finite(hp), row.get('observed_hp_floor', 0))


def _remember_capacity_floor(row, native_hp=0):
    if _phase_key(row) is None or row.get('entry_inferred') or _phase_capacity(row) is not None:
        return row
    floor = max(_observed_hp_floor(row), native_hp)
    if floor > row.get('observed_hp_floor', 0):
        return dict(row, observed_hp_floor=floor)
    return row


def _hp_pool(row, planned_hp=None):
    base = _phase_capacity(row)
    phase_hp = _finite(row.get('phase_total_hp'), None)
    if phase_hp is None:
        return max((hp for hp in (base, row.get('earlier_phase_capacity')) if hp is not None),
                   default=None)
    expansion = row.get('completed_phase_hp_expansion', 0) + _phase_expansion(row)
    if row.get('phase_from_birth'):
        return phase_hp + expansion
    # Without the first stage, the observed max HP might already be from a
    # counted stage. Either observation gives a lower bound, but summing them
    # could count the same stage twice.
    covered_expansion = expansion - row.get('initial_phase_hp_expansion', 0)
    return max(hp for hp in (base, planned_hp, row.get('earlier_phase_capacity'),
                              phase_hp + max(0, covered_expansion)) if hp is not None)


def _observed_pool(row):
    pool = _hp_pool(row)
    # Positive HP is a floor for an observed instance with missing MaxHP.
    # A known phase sum may already cover this HP.
    floor = _observed_hp_floor(row) if _phase_capacity(row) is None else 0
    return (floor, True) if floor > (pool or 0) else (pool, False)


def enemy_totals(rows, planned=None, *, entered_coverage_complete=True):
    """Total confirmed entrants and current HP, alongside the stage estimate."""
    entered = [row for row in rows if row['lifecycle'] in ENTERED_LIFECYCLES
               and not row.get('entry_inferred')]
    unknown_lifecycle_count = sum(row['alive'] is None for row in entered)
    observed_pool = [_observed_pool(row) for row in entered]
    observed_total = sum(hp for hp, _ in observed_pool if hp is not None)
    recovered_hp = sum(_finite(row.get('recovered_hp')) for row in entered)
    remaining = [_finite(row.get('hp'), None) for row in entered if row['alive']]
    remaining_complete = (bool(entered) and not unknown_lifecycle_count
                          and all(hp is not None for hp in remaining))
    departed_hp = [hp for row in entered if row['lifecycle'] == 'departed'
                   and (hp := _finite(row.get('hp_at_finish'))) > 0]
    planned = list(planned or ())
    planned_hp = [_max_hp(row) for row in planned]
    planned_known = [hp for hp in planned_hp if hp is not None]
    plan_complete = bool(planned) and len(planned_known) == len(planned)
    phases = [row for row in entered if _finite(row.get('phase_total_hp'), None) is not None]
    phase_by_uid = {key: row for row in phases if (key := _phase_key(row)) is not None}
    planned_pool = []
    anonymous_plan = defaultdict(list)
    matched = 0
    for row, hp in zip(planned, planned_hp):
        key = _phase_key(row)
        phase = phase_by_uid.pop(key, None)
        if phase is not None:
            # Replace this instance's single-stage value. The phase total
            # already contains its initial HP.
            hp = _hp_pool(phase, planned_hp=hp)
            matched += 1
        elif key is None:
            anonymous_plan[row['eid']].append(hp or 0)
        planned_pool.append(hp)
    adjusted_planned_total = sum(hp for hp in planned_pool if hp is not None)
    if planned:
        unmatched_pool = defaultdict(list)
        for row in phase_by_uid.values():
            unmatched_pool[row['eid']].append(_hp_pool(row))
        for eid, observations in unmatched_pool.items():
            candidates = sorted(anonymous_plan[eid], reverse=True)
            # Give each observation the largest remaining potential overlap.
            # This retains a conservative contribution from observed stages
            # without assuming which unbound plan slot supplied the first one.
            for index, hp in enumerate(sorted(observations, reverse=True)):
                overlap = candidates[index] if index < len(candidates) else 0
                adjusted_planned_total += max(0, hp - overlap)
    phases_complete = all(row.get('phase_from_birth') and row.get('phase_coverage_complete')
                          for row in phases)
    capacity_complete = (not any(_phase_capacity_is_partial(row)
                                 or row.get('prior_phase_capacity_partial') for row in entered)
                         and not any(partial for _, partial in observed_pool))
    total_complete = (entered_coverage_complete and bool(entered) and phases_complete
                      and capacity_complete and all(hp is not None for hp, _ in observed_pool))
    if planned:
        missing_enemies = Counter(row['eid'] for row in entered) - Counter(row['eid'] for row in planned)
        stage_complete = (phases_complete and capacity_complete and matched == len(phases)
                          and not missing_enemies and all(hp is not None for hp in planned_pool)
                          and (observed_total <= adjusted_planned_total
                               or math.isclose(observed_total, adjusted_planned_total)))
    else:
        stage_complete = False
    return {
        'entered_count': len(entered),
        'alive_count': sum(1 for row in entered if row['alive']),
        'unknown_lifecycle_count': unknown_lifecycle_count,
        'planned_count': len(planned),
        'planned_known_count': len(planned_known),
        'planned_total_hp': sum(planned_known),
        'planned_complete': plan_complete,
        'planned_unknown': [{'eid': row.get('eid'), 'name': row.get('name'),
                             'spawn_order': row.get('spawn_order')}
                            for row, hp in zip(planned, planned_hp) if hp is None],
        'entered_total_hp': observed_total,
        'observed_total_hp': observed_total,
        'recovered_hp': recovered_hp,
        'damage_capacity_with_recovery': observed_total + recovered_hp,
        'stage_total_hp_estimate': max(adjusted_planned_total, observed_total),
        'stage_total_hp_complete': stage_complete,
        'remaining_hp': sum(max(0.0, hp) for hp in remaining if hp is not None),
        'departed_hp': sum(departed_hp),
        'departed_hp_count': len(departed_hp),
        'total_hp_complete': total_complete,
        'remaining_hp_complete': remaining_complete,
        'hp_complete': total_complete and remaining_complete,
    }


class EnemyTimeline:
    """One point per published battle time; the UI downsamples for drawing."""

    def __init__(self):
        self.points = []

    def reset(self):
        self.points = []

    def add(self, game_time, rows, our_effective_damage, planned=None, *,
            replace_current=False, entered_coverage_complete=True, damage_complete=True):
        if not isinstance(game_time, (int, float)) or isinstance(game_time, bool):
            return None
        if not math.isfinite(game_time):
            return None
        damage_known = (isinstance(our_effective_damage, (int, float))
                        and not isinstance(our_effective_damage, bool)
                        and math.isfinite(our_effective_damage))
        last_time = self.points[-1]['game_time'] if self.points else None
        if last_time is not None and (game_time < last_time
                                     or (game_time == last_time and not replace_current)):
            return None
        totals = enemy_totals(rows, planned, entered_coverage_complete=entered_coverage_complete)
        point = {
            'game_time': float(game_time),
            'entered_total_hp': totals['entered_total_hp'],
            'observed_total_hp': totals['observed_total_hp'],
            'recovered_hp': totals['recovered_hp'],
            'damage_capacity_with_recovery': totals['damage_capacity_with_recovery'],
            'planned_count': totals['planned_count'],
            'planned_total_hp': totals['planned_total_hp'],
            'planned_complete': totals['planned_complete'],
            'stage_total_hp_estimate': totals['stage_total_hp_estimate'],
            'stage_total_hp_complete': totals['stage_total_hp_complete'],
            'remaining_hp': totals['remaining_hp'],
            'our_effective_damage': float(our_effective_damage) if damage_known else None,
            'our_damage_known': damage_known,
            'our_damage_complete': damage_known and damage_complete,
            'entered_count': totals['entered_count'],
            'alive_count': totals['alive_count'],
            'unknown_lifecycle_count': totals['unknown_lifecycle_count'],
            'total_hp_complete': totals['total_hp_complete'],
            'remaining_hp_complete': totals['remaining_hp_complete'],
            'hp_complete': totals['hp_complete'],
        }
        if game_time == last_time:
            self.points[-1] = point
        else:
            self.points.append(point)
        if len(self.points) > MAX_TIMELINE_POINTS:
            del self.points[:len(self.points) - MAX_TIMELINE_POINTS]
        return point

    def series(self, limit=MAX_SERIES_POINTS):
        points = self.points
        if len(points) <= limit:
            return [dict(point) for point in points]
        step = (len(points) - 1) / (limit - 1)
        indexes = sorted({round(index * step) for index in range(limit)})
        return [dict(points[index]) for index in indexes]

    @property
    def last(self):
        return dict(self.points[-1]) if self.points else None


def _load_enemy_db():
    try:
        from tools.enemy_health.enemy_db import load_enemy_db
        return load_enemy_db()
    except Exception:
        return None


def bootstrap_at_battle(reader):
    """Locate the enemy chain from the battle controller already certified.

    Collector.locate_battle() proves exactly one BattleController and the
    logical clock. This avoids a second heap-wide scan that could bind a
    different battle, and reuses the reader's frame-guarded channel.
    """
    mc = reader.mc
    bc = getattr(reader, 'bc_addr', 0)
    if not mc.is_ptr(bc):
        return False
    scheduler = reader._read_ptr(bc + _gs.BattleControllerFields.SCHEDULER)
    if not mc.is_ptr(scheduler) or mc.read_klass_name(scheduler) != 'Scheduler':
        return False
    list_addr = reader._read_ptr(
        scheduler + _gs.SchedulerFields.M_MANAGED_WAVE_ENEMIES)
    if not mc.is_ptr(list_addr):
        return False
    enemies = reader._read_live_enemy_list(list_addr)
    if enemies is None:
        return False
    reader.sched_addr = scheduler
    reader.list_addr = list_addr
    unit_enemies = []
    if reader._resolve_unit_manager(bc):
        unit_enemies = list(reader._uf_ptrs)
    reader.enemy_addrs = reader._union_enemy_ptrs(unit_enemies, enemies)
    # Live enemy names/attributes and lifecycle do not need the static spawn
    # plan. The reader's names catalog is optional and tolerant of a missing
    # generated handbook table; it is still required by runtime action records.
    if getattr(reader, '_db', None) is None:
        reader._db = _load_enemy_db() or {}
    for name in ('_names', '_identity_sources', '_attr_cache', '_attr_snapshot',
                 '_runtime_snapshot', '_runtime_ptrs', '_skill_cd', '_skill_enriched',
                 '_skill_lp', '_skill_ap'):
        cache = getattr(reader, name, None)
        if cache is not None and hasattr(cache, 'clear'):
            cache.clear()
    reader._spawn_plan = []
    reader._runtime_spawn_plan = []
    reader._runtime_action_records = {}
    reader._plan_by_id = {}
    reader._roster_last = {}
    reader._addr_to_roster = {}
    reader._dynamic_roster_seq = 0
    reader._roster_initialized = False
    reader._action_meta = {}
    reader._action_queue_ptr = 0
    reader._action_queue_items = 0
    reader._action_queue_version = -1
    reader._action_queue_entries = []
    reader._f_ptrs = []
    reader._f_items = 0
    reader._f_cnt = 0
    reader._f_version = -1
    reader._uf_ptrs = []
    reader._uf_items = 0
    reader._uf_cnt = 0
    # The LevelData plan is the complete enemy-HP denominator; a short-lived
    # enemy can otherwise spawn and die between polls without ever entering
    # the live roster. Names/HP come from the local enemy DB, never a network.
    try:
        reader._load_spawn_plan()
    except Exception:
        pass
    try:
        reader._prefill()
    except Exception:
        pass
    return True


class EnemyTelemetry:
    """Reduce enemy events with an initial snapshot and polling fallback."""

    def __init__(self, reader, *, log=None, poll_interval=1.0,
                 bootstrap_retry=3.0, clock=time.monotonic,
                 bootstrap=None, calibration_interval=EVENT_CALIBRATION_INTERVAL):
        self.reader = reader
        self.bootstrap = bootstrap or bootstrap_at_battle
        self.log = log or (lambda *_args: None)
        self.poll_interval = poll_interval
        self.bootstrap_retry = bootstrap_retry
        self.calibration_interval = calibration_interval
        self.clock = clock
        self.timeline = EnemyTimeline()
        self.reset()

    def reset(self):
        self.timeline.reset()
        self.ready = False
        self.error = None
        self._last_bootstrap = float('-inf')
        self._last_poll = float('-inf')
        self._status = 'starting'
        self._rows = []
        self._planned_rows = []
        self._damage_baseline = None
        self._entry_events = {}
        self._entry_events_complete = None
        self._entry_events_start_frame = None
        self._phase_data = None
        self._stream_complete = None
        self._stream_start_frame = None
        self._last_sequence = -1
        self._snapshot_frame = None
        self._seeded = False
        self._row_frames = {}
        self._event_frames = {}
        self._recovery_events = {}
        self._spawn_hp_evidence = {}
        self._pending_events = defaultdict(list)
        self._finished = set()
        self._phase_calibration = set()
        self._trap_catalog = None

    def _enemy_name(self, eid, name):
        if name and name != eid:
            return name
        if not eid.startswith('trap_'):
            return name
        if self._trap_catalog is None:
            self._trap_catalog = _load_enemy_db() or {}
        return (self._trap_catalog.get(eid) or {}).get('name') or eid

    def _record_entries(self, observation):
        if observation is None:
            return
        self._entry_events_complete = observation['complete']
        self._entry_events_start_frame = observation['start_frame']
        for event in observation['events']:
            self._entry_events.setdefault(event['instance_uid'], event)

    @staticmethod
    def _attach_entry_attributes(row, entry):
        attributes = entry.get('enemy_entry_attributes')
        if not attributes:
            return row
        fallback = {name: value for name, value in attributes.items()
                    if name != 'max_hp' and row.get(name) is None}
        if (not row['hp_known'] and 'max_hp' in attributes
                and 'initial_phase_hp_expansion' not in row):
            max_hp = attributes['max_hp']
            hp = row['hp']
            fallback.update(max_hp=max_hp, hp_known=True, hp_ratio=_hp_ratio(hp, max_hp))
        return dict(row, attributes_from_entry=bool(fallback or row.get('attributes_from_entry')),
                    **fallback)

    def _event_row(self, eid, uid):
        identity = (getattr(self.reader, '_db', None) or {}).get(eid) or {}
        return dict(
            instance_uid=uid, roster_id=None, spawn_order=None, eid=eid,
            name=self._enemy_name(eid, identity.get('name')) or eid,
            code=identity.get('code') or '',
            lifecycle='entered', alive=None, state_id=None, state='未知',
            spawn_frame=None, spawn_time=None, end_frame=None, end_reason=None,
            finish_reason=None, hp=None, max_hp=None, hp_known=False,
            hp_ratio=None, hp_inferred=False, entry_inferred=False,
        )

    def _settle_spawn_max_hp(self, row, event, key, hp, max_hp):
        birth = self._spawn_hp_evidence.get(key)
        if birth is None:
            return row
        birth_frame, birth_hp = birth
        before = _finite(event.get('hp_before'), None)
        if (not self._stream_complete or not birth_frame <= event['frame'] <= birth_frame + 1
                or not _same_hp(row['hp'], before)):
            self._spawn_hp_evidence.pop(key)
            return row
        old_max = _max_hp(row)
        if (max_hp is not None and old_max is not None and max_hp < old_max
                and _same_hp(before, old_max) and _same_hp(hp, max_hp)
                and _same_hp(_phase_capacity(row), old_max)):
            # An attribute refresh immediately after a full-HP birth can
            # clamp HP to the final initial maximum without taking damage.
            return dict(row, max_hp=max_hp, phase_initial_max_hp=max_hp, phase_max_hp=max_hp,
                        observed_hp_floor=min(row.get('observed_hp_floor', 0), max_hp),
                        birth_hp_adjustment=max_hp - birth_hp)
        if hp < before:
            self._spawn_hp_evidence.pop(key)
        return row

    def _record_recovery(self, row, event, key, hp, max_hp):
        before = _finite(event.get('hp_before'), None)
        spawn_frame = row.get('spawn_frame')
        event_key = (event['frame'], event['sequence'])
        if (row['lifecycle'] != 'active' or row['alive'] is not True or row.get('entry_inferred')
                or before is None or before <= 0 or hp <= before
                or not _same_hp(row['hp'], before) or not _same_hp(_max_hp(row), max_hp)
                or (hp > max_hp and not _same_hp(hp, max_hp))
                or (spawn_frame is not None and event['frame'] <= spawn_frame + 1)
                or event_key <= self._recovery_events.get(key, (-1, -1))):
            return row
        self._recovery_events[key] = event_key
        return dict(row, recovered_hp=row.get('recovered_hp', 0) + (hp - before))

    def _apply_event(self, row, event, key):
        kind, frame = event['kind'], event['frame']
        if kind == 'enemy_enter':
            row = self._attach_entry_attributes(
                dict(row, spawn_frame=frame, spawn_time=event['game_time']), event)
        if key in self._finished:
            return row
        fake_departure = (self._stream_complete and kind == 'enemy_fake_death'
                          and row['lifecycle'] == 'departed')
        if kind != 'enemy_finish' and not fake_departure and frame < self._row_frames.get(key, -1):
            return row
        hp = _finite(event.get('hp_after'), None)
        if kind == 'enemy_enter' or kind == 'enemy_reborn':
            if hp is None and kind == 'enemy_enter':
                hp = row['hp']
            row = dict(row, lifecycle='active', alive=True, state_id=None, state='在场',
                       end_frame=None, end_reason=None, finish_reason=None, hp=hp)
            if kind == 'enemy_reborn':
                self._spawn_hp_evidence.pop(key, None)
                row = _next_hp_phase(row)
                self._phase_calibration.add(key)
            elif (key not in self._event_frames and self._stream_complete
                  and _same_hp(_finite(event.get('hp_after'), None), _max_hp(row))):
                self._spawn_hp_evidence[key] = (frame, hp)
        elif kind == 'enemy_fake_death':
            self._spawn_hp_evidence.pop(key, None)
            row = dict(row, lifecycle='rebirthing', alive=True, state_id=8,
                       state='等待重生', end_frame=None, end_reason=None, finish_reason=None, hp=hp)
        elif kind == 'enemy_finish':
            self._spawn_hp_evidence.pop(key, None)
            reason = event.get('finish_reason')
            end_reason, state_id, state = _FINISH_STATES.get(
                reason, (f'finish_{reason}', None, '已离场'))
            row = dict(row, lifecycle='departed', alive=False, state_id=state_id, state=state,
                       end_frame=frame, end_reason=end_reason,
                       finish_reason=reason, hp=hp, hp_at_finish=hp)
            self._finished.add(key)
            self._phase_calibration.discard(key)
        elif kind == 'enemy_hp' and hp is not None:
            max_hp = _finite(event.get('enemy_max_hp'), None)
            if max_hp is not None and max_hp <= 0:
                max_hp = None
            row = self._record_recovery(row, event, key, hp, max_hp)
            row = _remember_capacity_floor(row, max(hp, _finite(event.get('hp_before'))))
            row = self._settle_spawn_max_hp(row, event, key, hp, max_hp)
            old_floor = row.get('max_hp_lower_bound')
            row = _observe_max_hp(dict(row, hp=hp), hp, max_hp,
                                  seed='initial_phase_hp_expansion' in row)
            if row.get('max_hp_lower_bound') is not None and row['max_hp_lower_bound'] != old_floor:
                self._phase_calibration.add(key)
            elif max_hp is not None:
                self._phase_calibration.discard(key)
        self._row_frames[key] = frame if fake_departure else max(frame, self._row_frames.get(key, -1))
        self._event_frames[key] = frame
        return _remember_capacity_floor(dict(row, hp_ratio=_current_hp_ratio(row)))

    def _apply_pending(self, row, key):
        for event in self._pending_events.pop(key, ()):
            row = self._apply_event(row, event, key)
        return row

    def _record_state_events(self, observation):
        complete = bool(observation and observation['complete'])
        if self._stream_complete and not complete:
            self._seeded = False
            self._spawn_hp_evidence.clear()
        if observation is None:
            if self._stream_complete is not None:
                self._stream_complete = False
            return
        start_frame = observation['start_frame']
        if self._stream_start_frame != start_frame:
            self._seeded = False
            self._last_sequence = -1
            self._spawn_hp_evidence.clear()
        self._stream_complete = complete
        self._stream_start_frame = start_frame
        events = observation['events']
        start = bisect_right(events, self._last_sequence, key=lambda event: event['sequence'])
        if start == len(events):
            return
        rows = {_phase_key(row): index for index, row in enumerate(self._rows)
                if _phase_key(row) is not None}
        for event in events[start:]:
            self._last_sequence = event['sequence']
            uid = event.get('instance_uid') or event.get('target_instance')
            eid = event.get('eid') or event.get('target')
            key = (uid, eid)
            index = rows.get(key)
            if index is None:
                entry = self._entry_events.get(uid)
                if event['kind'] != 'enemy_enter' and (entry is None or entry['eid'] != eid):
                    self._pending_events[key].append(event)
                    continue
                row = self._apply_pending(self._event_row(eid, uid), key)
                rows[key] = len(self._rows)
                self._rows.append(self._apply_event(row, event, key))
            else:
                self._rows[index] = self._apply_event(self._rows[index], event, key)

    def _merged_rows(self, rows):
        if self._entry_events_complete is None:
            return [row for row in rows if not row['entry_inferred']]
        merged = []
        by_uid = {}
        for row in rows:
            if row['entry_inferred']:
                # The scheduler's initial count establishes a plan prefix.
                # It does not establish that any particular enemy entered.
                continue
            uid = row['instance_uid']
            entry = self._entry_events.get(uid)
            if entry is not None and row['eid'] == entry['eid']:
                row = self._attach_entry_attributes(
                    dict(row, spawn_frame=entry['frame'], spawn_time=entry['game_time']), entry)
            if uid in by_uid:
                index = by_uid[uid]
                if row['lifecycle'] == 'active':
                    merged[index] = row
                continue
            if uid is not None:
                by_uid[uid] = len(merged)
            merged.append(row)
        for uid, entry in self._entry_events.items():
            if uid in by_uid:
                continue
            row = dict(self._event_row(entry['eid'], uid),
                       spawn_frame=entry['frame'], spawn_time=entry['game_time'])
            merged.append(self._attach_entry_attributes(row, entry))
        return merged

    def _rows_with_phases(self, rows):
        current = self._merged_rows(rows)
        if self._phase_data:
            phases = self._phase_data['instances']
            for index, row in enumerate(current):
                phase = phases.get(row['instance_uid'])
                if phase and row['eid'] == phase['eid']:
                    phase_hp = phase['phase_total_hp']
                    if phase['phase_from_birth']:
                        # The probe's phase sum includes HP at the Born callback,
                        # before a verified initial clamp can settle that value.
                        phase_hp += row.get('birth_hp_adjustment', 0)
                    current[index] = dict(
                        row, phase_total_hp=phase_hp,
                        phase_count=phase['phase_count'],
                        phase_from_birth=phase['phase_from_birth'],
                        phase_coverage_complete=self._phase_data['complete'],
                    )
        return current

    def _current_plan(self, rows):
        if self._stream_complete is None:
            return self._planned_rows
        by_instance = {}
        by_eid = defaultdict(set)
        for row in rows:
            # A later phase's max HP does not estimate the initial stage.
            hp = row.get('phase_initial_max_hp', _max_hp(row))
            if hp is not None and row.get('phase_count', 0) <= 1:
                key = _phase_key(row)
                if key is not None:
                    by_instance[key] = hp
                by_eid[row['eid']].add(hp)
        planned = []
        for row in self._planned_rows:
            key = _phase_key(row)
            hp = by_instance.get(key)
            inferred = False
            if hp is None and key is None and len(by_eid[row['eid']]) == 1:
                hp = next(iter(by_eid[row['eid']]))
                inferred = True
            if hp is not None and (not row['hp_known'] or row['hp_inferred']):
                row = dict(row, max_hp=hp, hp_known=True, hp_inferred=inferred)
            planned.append(row)
        return planned

    def _state(self, status, *, error=None, frame=None, game_time=None, our_damage=None,
               enemy_damage=None):
        self._status = status
        self.error = error
        current = self._rows_with_phases(self._rows)
        stream_ready = self._stream_ready(frame)
        if (self._stream_complete and frame is not None and self._snapshot_frame is not None
                and frame < self._snapshot_frame):
            # The seed is stored for later reconciliation. Only publish
            # entrants already confirmed by an event at this watermark.
            current = [row for row in current if row.get('spawn_time') is not None]
        entered_coverage = (status == 'ready' and self._entry_events_complete is not False
                            and (stream_ready if self._stream_complete is not None
                                 else self._entry_events_complete is True))
        planned = self._current_plan(current)
        totals = enemy_totals(current, planned, entered_coverage_complete=entered_coverage)
        if self._stream_complete and not stream_ready:
            totals.update(remaining_hp_complete=False, hp_complete=False)
        if game_time is not None and (not self._stream_complete or stream_ready):
            self._update_timeline(game_time, our_damage, current, planned, entered_coverage,
                                  enemy_damage)
        state = {
            'status': status,
            'error': error,
            'ready': self.ready,
            'rows': current,
            'totals': totals,
            'damage_baseline': self._damage_baseline,
            'damage_scope': 'entered' if enemy_damage is not None else 'legacy',
            'series': self.timeline.series(),
            'last_point': self.timeline.last,
        }
        if self._entry_events_complete is not None:
            state['entry_events_complete'] = self._entry_events_complete
            state['entry_events_start_frame'] = self._entry_events_start_frame
        if self._stream_complete is not None:
            state['enemy_state_complete'] = stream_ready
        return state

    def _stream_ready(self, frame):
        return bool(self._stream_complete and self._seeded
                    and (frame is None or self._snapshot_frame is None
                         or frame >= self._snapshot_frame))

    def _calibration_pending(self):
        return any(
            row['alive'] is not False
            and (_phase_key(row) is None or not row['hp_known'] or row['hp'] is None
                 or _phase_key(row) in self._phase_calibration
                 or any(row.get(attribute) is None for attribute in _ATTRIBUTES))
            for row in self._rows
        )

    @staticmethod
    def _with_snapshot_details(current, observed, *, keep_live, older):
        merged = dict(current)
        merged.update({name: value for name, value in observed.items()
                       if value is not None and name not in _LIVE_FIELDS
                       and name not in ('hp_known', 'hp_ratio', 'max_hp', 'observed_hp_floor')})
        if not keep_live:
            merged.update({name: observed[name] for name in _LIVE_FIELDS})
        elif current['alive'] is None and observed['alive'] and not older:
            merged.update(lifecycle='active', alive=True, state_id=observed['state_id'],
                          state=observed['state'])
            if current['hp'] is None:
                merged['hp'] = observed['hp']
        elif (current['hp'] is None and current['lifecycle'] == observed['lifecycle']
              and current['alive'] == observed['alive']):
            merged['hp'] = observed['hp']
        if observed['hp_known'] and not older:
            merged = _observe_max_hp(merged, observed['hp'], observed['max_hp'], seed=True)
        merged['hp_ratio'] = _current_hp_ratio(merged)
        return _remember_capacity_floor(merged, observed.get('observed_hp_floor', 0) if not older else 0)

    def _reconcile_snapshot(self, rows, frame, *, calibration):
        previous = {_phase_key(row): row for row in self._rows
                    if _phase_key(row) is not None}
        prior_anonymous = {row['roster_id']: row for row in self._rows if _phase_key(row) is None}
        merged = []
        for row in rows:
            key = _phase_key(row)
            current = previous.pop(key, None)
            anonymous = prior_anonymous.pop(row['roster_id'], None)
            if current is None and calibration and anonymous is None and key not in self._pending_events:
                continue
            if current is not None:
                current_frame = self._row_frames.get(key, -1)
                current_event = self._event_frames.get(key, -1) >= self._stream_start_frame
                keep_live = (key in self._finished or calibration or current_frame >= frame
                             or (self._stream_complete and current_event))
                row = self._with_snapshot_details(current, row, keep_live=keep_live,
                                                  older=frame < current_frame)
                if row['hp_known'] and row['alive'] and frame >= current_frame:
                    self._phase_calibration.discard(key)
                if not keep_live:
                    self._row_frames[key] = frame
            elif key is not None:
                self._row_frames[key] = frame
                row = self._apply_pending(row, key)
            merged.append(row)
        if calibration:
            merged.extend(prior_anonymous.values())
        for key, row in previous.items():
            if (not self._stream_complete and row['alive']
                    and self._row_frames.get(key, -1) < frame):
                # After a gap, a missing row gives no reason to infer how the
                # enemy left or how much HP it has.
                row = dict(row, lifecycle='entered', alive=None, hp=None, hp_ratio=None,
                           state='未知', state_id=None)
                self._row_frames[key] = frame
            merged.append(row)
        self._rows = merged

    def _bootstrap(self):
        now = self.clock()
        if now - self._last_bootstrap < self.bootstrap_retry:
            return False
        self._last_bootstrap = now
        try:
            if not self.bootstrap(self.reader):
                self._state('locating', error='等待敌人出场或战斗对象就绪')
                return False
        except Exception as exc:
            self._state('unavailable', error=f'敌人定位失败：{exc}')
            return False
        self.ready = True
        self.log('敌人遥测已定位')
        return True

    def _cache_plan(self, planned, *, calibration):
        if not calibration:
            self._planned_rows = planned
            return
        refreshed = {(row['roster_id'], row['eid']): row for row in planned}
        retained = []
        for row in self._planned_rows:
            new = refreshed.pop((row['roster_id'], row['eid']), None)
            if new is None:
                retained.append(row)
            elif row['hp_known'] and not new['hp_known']:
                retained.append(dict(new, max_hp=row['max_hp'], hp_known=True,
                                     hp_inferred=row['hp_inferred']))
            else:
                retained.append(new)
        self._planned_rows = retained + list(refreshed.values())

    def _poll(self, *, frame, calibration):
        try:
            snapshot = self.reader.poll_fast()
        except Exception as exc:
            # poll_fast normally closes its channel on failure. On the product
            # path that channel is shared with authoritative counter reads, so
            # an enemy-only failure must not tear down the collection session.
            if getattr(self.reader, 'shared_channel', False) and getattr(self.reader, '_chan', None) is None:
                try:
                    self.reader._chan = self.reader.mc.channel()
                except Exception:
                    pass
            return 'unavailable', f'敌人读取失败：{exc}'
        if not snapshot or not snapshot.get('ok'):
            message = (snapshot or {}).get('msg') or '敌人快照不完整'
            return 'stale', message
        rows = enemy_rows(snapshot)
        for row in rows:
            row['name'] = self._enemy_name(row['eid'], row['name'])
        if self._stream_complete is None:
            self._rows = rows
        else:
            observed_frame = snapshot.get('fixed_frame')
            if observed_frame is None:
                observed_frame = frame if frame is not None else self._stream_start_frame
            if self._snapshot_frame is not None and observed_frame < self._snapshot_frame:
                return 'stale', '敌人快照帧早于已读取帧'
            if self._stream_complete and not calibration and observed_frame < self._stream_start_frame:
                return 'stale', '敌人快照早于事件流起点'
            self._reconcile_snapshot(rows, observed_frame, calibration=calibration)
            if not calibration:
                self._snapshot_frame = observed_frame
                self._seeded = self._stream_complete
        planned = enemy_rows(snapshot, include_pending=True)
        for row in planned:
            row['name'] = self._enemy_name(row['eid'], row['name'])
        # Conditional branches only count once observed; scheduled waves form
        # the deterministic stage HP pool even when a poll misses a short life.
        self._cache_plan([row for row in planned if row['lifecycle'] in ENTERED_LIFECYCLES
                          or row['spawn_kind'] == 'scheduled'], calibration=calibration)
        return 'ready', None

    def _update_timeline(self, game_time, our_damage, rows, planned, entered_coverage,
                         enemy_damage):
        if enemy_damage is not None:
            by_uid = enemy_damage['by_uid']
            observed = 0.0
            complete = enemy_damage['complete'] and entered_coverage
            counted = set()
            for row in rows:
                if row['lifecycle'] not in ENTERED_LIFECYCLES:
                    continue
                uid = row['instance_uid']
                damage = by_uid.get(uid)
                if uid is None or (damage and damage['eid'] != row['eid']):
                    complete = False
                elif damage and uid not in counted:
                    observed += damage['amount']
                    counted.add(uid)
            self.timeline.add(game_time, rows, observed if complete or observed > 0 else None,
                              planned, replace_current=self._stream_complete is True,
                              entered_coverage_complete=entered_coverage, damage_complete=complete)
            return
        our_damage = _finite(our_damage, None)
        if self._damage_baseline is None and our_damage is not None:
            # Align the damage curve with the enemy-observation window. Native
            # counters can include damage dealt before ArkDPS attached; comparing
            # that cumulative value with entered-enemy MaxHP would overstate it.
            self._damage_baseline = float(our_damage)
        adjusted = (max(0.0, float(our_damage) - self._damage_baseline)
                    if self._damage_baseline is not None and our_damage is not None else None)
        self.timeline.add(game_time, rows, adjusted, planned,
                          replace_current=self._stream_complete is True,
                          entered_coverage_complete=entered_coverage)

    def sample(self, *, game_time, our_damage, enemy_entry=None, enemy_phases=None,
               enemy_state=None, enemy_damage=None, frame=None):
        self._record_entries(enemy_entry)
        if enemy_phases is not None:
            self._phase_data = enemy_phases
        self._record_state_events(enemy_state)
        if not self.ready and not self._bootstrap():
            return self._state(self._status, error=self.error, frame=frame)
        now = self.clock()
        calibration = self._stream_complete and self._seeded
        if calibration:
            if (now - self._last_poll >= self.calibration_interval
                    and self._calibration_pending()):
                self._last_poll = now
                self._poll(frame=frame, calibration=True)
        elif now - self._last_poll >= self.poll_interval:
            self._last_poll = now
            status, error = self._poll(frame=frame, calibration=False)
            if status != 'ready':
                return self._state(status, error=error, frame=frame)
        else:
            return self._state(self._status, error=self.error, frame=frame)
        return self._state('ready', frame=frame, game_time=game_time, our_damage=our_damage,
                           enemy_damage=enemy_damage)
