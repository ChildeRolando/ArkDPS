"""Live enemy telemetry built on the verified read-only EnemyReader.

Enemy data is supplemental to the authoritative operator counters. A failed
locate or poll never changes statistics; it only reports telemetry as
unavailable so the UI cannot render invented enemies or HP.
"""
from __future__ import annotations

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

ENTERED_LIFECYCLES = ('active', 'departed', 'entered')
MAX_TIMELINE_POINTS = 7200
MAX_SERIES_POINTS = 360


def _finite(value, default=0.0):
    return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else default)


def _text(value):
    return value if isinstance(value, str) else ''


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
        rows.append({
            'roster_id': getattr(enemy, 'roster_id', None),
            'instance_uid': getattr(enemy, 'instance_uid', None),
            'spawn_order': getattr(enemy, 'spawn_order', None),
            'eid': _text(getattr(enemy, 'eid', '')),
            'name': _text(getattr(enemy, 'name', '')),
            'code': _text(getattr(enemy, 'code', '')),
            'hp': hp,
            'max_hp': max_hp,
            'hp_known': max_hp > 0,
            'hp_ratio': min(1.0, max(0.0, hp / max_hp)) if max_hp > 0 else None,
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
            'is_summon': bool(getattr(enemy, 'is_summon', False)),
            'planned': bool(getattr(enemy, 'planned', False)),
            'spawn_kind': _text(getattr(enemy, 'spawn_kind', '')) or 'scheduled',
            'hp_inferred': False,
            'position': {
                'x': _finite(getattr(enemy, 'pos_x', 0.0)),
                'y': _finite(getattr(enemy, 'pos_y', 0.0)),
            },
        })
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
    return _finite(row.get('max_hp'), None) if row['hp_known'] else None


def _phase_key(row):
    uid = row.get('instance_uid')
    return (uid, row['eid']) if uid is not None else None


def _hp_pool(row, planned_hp=None):
    base = _max_hp(row)
    phase_hp = _finite(row.get('phase_total_hp'), None)
    if phase_hp is None:
        return base
    if row.get('phase_from_birth'):
        return phase_hp
    # Without the first stage, the observed max HP might already be from a
    # counted stage. Either observation gives a lower bound, but summing them
    # could count the same stage twice.
    return max(hp for hp in (base, planned_hp, phase_hp) if hp is not None)


def enemy_totals(rows, planned=None):
    """Estimate the stage HP pool and independently total observed current HP."""
    entered = [row for row in rows if row['lifecycle'] in ENTERED_LIFECYCLES]
    unknown_lifecycle_count = sum(row['alive'] is None for row in entered)
    observed_pool = [_hp_pool(row) for row in entered]
    observed_total = sum(hp for hp in observed_pool if hp is not None)
    remaining = [_finite(row.get('hp'), None) for row in entered if row['alive']]
    remaining_complete = (bool(entered) and not unknown_lifecycle_count
                          and all(hp is not None for hp in remaining))
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
    if planned:
        missing_enemies = Counter(row['eid'] for row in entered) - Counter(row['eid'] for row in planned)
        total_complete = (phases_complete and matched == len(phases)
                          and not missing_enemies
                          and all(hp is not None for hp in planned_pool)
                          and (observed_total <= adjusted_planned_total
                               or math.isclose(observed_total, adjusted_planned_total)))
    else:
        total_complete = bool(entered) and phases_complete and all(hp is not None for hp in observed_pool)
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
        'entered_total_hp': max(adjusted_planned_total, observed_total),
        'observed_total_hp': observed_total,
        'remaining_hp': sum(max(0.0, hp) for hp in remaining if hp is not None),
        'total_hp_complete': total_complete,
        'remaining_hp_complete': remaining_complete,
        'hp_complete': total_complete and remaining_complete,
    }


class EnemyTimeline:
    """One point per observed game second; the UI downsamples for drawing."""

    def __init__(self):
        self.points = []

    def reset(self):
        self.points = []

    def add(self, game_time, rows, our_effective_damage, planned=None):
        if not isinstance(game_time, (int, float)) or isinstance(game_time, bool):
            return None
        if not math.isfinite(game_time):
            return None
        damage_known = (isinstance(our_effective_damage, (int, float))
                        and not isinstance(our_effective_damage, bool)
                        and math.isfinite(our_effective_damage))
        if self.points and game_time <= self.points[-1]['game_time']:
            return None
        totals = enemy_totals(rows, planned)
        point = {
            'game_time': float(game_time),
            'entered_total_hp': totals['entered_total_hp'],
            'observed_total_hp': totals['observed_total_hp'],
            'planned_count': totals['planned_count'],
            'planned_total_hp': totals['planned_total_hp'],
            'planned_complete': totals['planned_complete'],
            'remaining_hp': totals['remaining_hp'],
            'our_effective_damage': float(our_effective_damage) if damage_known else None,
            'our_damage_known': damage_known,
            'entered_count': totals['entered_count'],
            'alive_count': totals['alive_count'],
            'unknown_lifecycle_count': totals['unknown_lifecycle_count'],
            'total_hp_complete': totals['total_hp_complete'],
            'remaining_hp_complete': totals['remaining_hp_complete'],
            'hp_complete': totals['hp_complete'],
        }
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
    """Throttled bootstrap/poll wrapper; never raises into collection."""

    def __init__(self, reader, *, log=None, poll_interval=1.0,
                 bootstrap_retry=3.0, clock=time.monotonic,
                 bootstrap=None):
        self.reader = reader
        self.bootstrap = bootstrap or bootstrap_at_battle
        self.log = log or (lambda *_args: None)
        self.poll_interval = poll_interval
        self.bootstrap_retry = bootstrap_retry
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
        fallback = {name: value for name, value in attributes.items() if row.get(name) is None}
        if not row['hp_known'] and 'max_hp' in attributes:
            max_hp = attributes['max_hp']
            hp = row['hp']
            fallback.update(max_hp=max_hp, hp_known=True,
                            hp_ratio=min(1.0, max(0.0, hp / max_hp)) if hp is not None else None)
        return dict(row, attributes_from_entry=bool(fallback), **fallback)

    def _merged_rows(self, rows):
        if self._entry_events_complete is None:
            return list(rows)
        merged = []
        by_uid = {}
        for row in rows:
            if row['entry_inferred'] and (self._entry_events_complete or self._entry_events):
                # Scheduler's initial count only establishes a plan prefix.
                # It cannot identify the same instances as the event stream.
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
        catalog = getattr(self.reader, '_db', None) or {}
        for uid, entry in self._entry_events.items():
            if uid in by_uid:
                continue
            eid = entry['eid']
            identity = catalog.get(eid) or {}
            row = dict(
                instance_uid=uid, roster_id=None, spawn_order=None, eid=eid,
                name=identity.get('name') or eid, code=identity.get('code') or '',
                lifecycle='entered', alive=None, state_id=None, state='未知',
                spawn_frame=entry['frame'], spawn_time=entry['game_time'],
                end_frame=None, end_reason=None, hp=None, max_hp=None,
                hp_known=False, hp_ratio=None, hp_inferred=False,
            )
            merged.append(self._attach_entry_attributes(row, entry))
        return merged

    def _rows_with_phases(self, rows):
        current = self._merged_rows(rows)
        if self._phase_data:
            phases = self._phase_data['instances']
            for index, row in enumerate(current):
                phase = phases.get(row['instance_uid'])
                if phase and row['eid'] == phase['eid']:
                    current[index] = dict(
                        row, phase_total_hp=phase['phase_total_hp'],
                        phase_count=phase['phase_count'],
                        phase_from_birth=phase['phase_from_birth'],
                        phase_coverage_complete=self._phase_data['complete'],
                    )
        return current

    def _state(self, status, *, rows=None, error=None):
        self._status = status
        self.error = error
        if rows is not None:
            self._rows = list(rows)
        current = self._rows_with_phases(self._rows)
        state = {
            'status': status,
            'error': error,
            'ready': self.ready,
            'rows': current,
            'totals': enemy_totals(current, self._planned_rows),
            'damage_baseline': self._damage_baseline,
            'series': self.timeline.series(),
            'last_point': self.timeline.last,
        }
        if self._entry_events_complete is not None:
            state['entry_events_complete'] = self._entry_events_complete
            state['entry_events_start_frame'] = self._entry_events_start_frame
        return state

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

    def sample(self, *, game_time, our_damage, enemy_entry=None, enemy_phases=None):
        self._record_entries(enemy_entry)
        if enemy_phases is not None:
            self._phase_data = enemy_phases
        if not self.ready and not self._bootstrap():
            return self._state(self._status, error=self.error)
        now = self.clock()
        if now - self._last_poll < self.poll_interval:
            return self._state(self._status, error=self.error)
        self._last_poll = now
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
            return self._state('unavailable', error=f'敌人读取失败：{exc}')
        if not snapshot or not snapshot.get('ok'):
            message = (snapshot or {}).get('msg') or '敌人快照不完整'
            return self._state('stale', error=message)
        rows = enemy_rows(snapshot)
        planned = enemy_rows(snapshot, include_pending=True)
        # Conditional branches only count once observed; scheduled waves form
        # the deterministic stage HP pool even when a poll misses a short life.
        self._planned_rows = [row for row in planned
                              if row['lifecycle'] in ENTERED_LIFECYCLES
                              or row['spawn_kind'] == 'scheduled']
        if self._damage_baseline is None and isinstance(our_damage, (int, float)):
            # Align the damage curve with the enemy-observation window. Native
            # counters can include damage dealt before ArkDPS attached; comparing
            # that cumulative value with entered-enemy MaxHP would overstate it.
            self._damage_baseline = float(our_damage)
        adjusted = (max(0.0, float(our_damage) - self._damage_baseline)
                    if self._damage_baseline is not None
                    and isinstance(our_damage, (int, float)) else None)
        self.timeline.add(game_time, self._rows_with_phases(rows), adjusted, self._planned_rows)
        return self._state('ready', rows=rows)
