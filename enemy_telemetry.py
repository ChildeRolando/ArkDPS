"""Live enemy telemetry built on the verified read-only EnemyReader.

Enemy data is supplemental to the authoritative operator counters. A failed
locate or poll never changes statistics; it only reports telemetry as
unavailable so the UI cannot render invented enemies or HP.
"""
from __future__ import annotations

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

ENTERED_LIFECYCLES = ('active', 'departed')
MAX_TIMELINE_POINTS = 7200
MAX_SERIES_POINTS = 360


def _finite(value, default=0.0):
    return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else default)


def _text(value):
    return value if isinstance(value, str) else ''


def enemy_rows(snapshot):
    """Serializable entered-enemy rows, excluding not-yet-spawned plan entries."""
    rows = []
    for enemy in snapshot.get('enemies') or ():
        lifecycle = _text(getattr(enemy, 'lifecycle', 'active')) or 'active'
        if lifecycle not in ENTERED_LIFECYCLES:
            continue
        max_hp = _finite(getattr(enemy, 'max_hp', 0.0))
        hp = _finite(getattr(enemy, 'hp', 0.0))
        state_id = getattr(enemy, 'state_id', None)
        if not isinstance(state_id, int):
            state_id = None
        rows.append({
            'roster_id': getattr(enemy, 'roster_id', None),
            'spawn_order': getattr(enemy, 'spawn_order', None),
            'eid': _text(getattr(enemy, 'eid', '')),
            'name': _text(getattr(enemy, 'name', '')),
            'code': _text(getattr(enemy, 'code', '')),
            'hp': hp,
            'max_hp': max_hp,
            'hp_known': max_hp > 0,
            'hp_ratio': min(1.0, max(0.0, hp / max_hp)) if max_hp > 0 else None,
            'atk': _finite(getattr(enemy, 'atk', 0.0)),
            'def': _finite(getattr(enemy, 'def_', 0.0)),
            'res': _finite(getattr(enemy, 'res', 0.0)),
            'move_speed': _finite(getattr(enemy, 'mspd', 0.0)),
            'attack_speed': _finite(getattr(enemy, 'aspd', 0.0)),
            'alive': bool(getattr(enemy, 'alive', False)),
            'lifecycle': lifecycle,
            'state_id': state_id,
            'state': ENEMY_STATE_NAMES.get(state_id, f'未知({state_id})') if state_id is not None else '未知',
            'spawn_frame': getattr(enemy, 'spawn_frame', None),
            'end_frame': getattr(enemy, 'end_frame', None),
            'end_reason': getattr(enemy, 'end_reason', None),
            'is_summon': bool(getattr(enemy, 'is_summon', False)),
            'position': {
                'x': _finite(getattr(enemy, 'pos_x', 0.0)),
                'y': _finite(getattr(enemy, 'pos_y', 0.0)),
            },
        })
    rows.sort(key=lambda row: (row['spawn_order'] is None,
                               row['spawn_order'] or 0, row['roster_id'] or 0))
    return rows


def enemy_totals(rows):
    entered = [row for row in rows if row['lifecycle'] in ENTERED_LIFECYCLES]
    known = [row for row in entered if row['hp_known']]
    return {
        'entered_count': len(entered),
        'alive_count': sum(1 for row in entered if row['alive']),
        'entered_total_hp': sum(row['max_hp'] for row in known),
        'remaining_hp': sum(max(0.0, row['hp']) for row in known if row['alive']),
        'hp_complete': len(known) == len(entered) and bool(entered),
    }


class EnemyTimeline:
    """One point per observed game second; the UI downsamples for drawing."""

    def __init__(self):
        self.points = []

    def reset(self):
        self.points = []

    def add(self, game_time, rows, our_damage):
        if not isinstance(game_time, (int, float)) or isinstance(game_time, bool):
            return None
        if not isinstance(our_damage, (int, float)) or isinstance(our_damage, bool):
            return None
        if not math.isfinite(game_time) or not math.isfinite(our_damage):
            return None
        if self.points and game_time <= self.points[-1]['game_time']:
            return None
        totals = enemy_totals(rows)
        point = {
            'game_time': float(game_time),
            'entered_total_hp': totals['entered_total_hp'],
            'remaining_hp': totals['remaining_hp'],
            'our_damage': float(our_damage),
            'entered_count': totals['entered_count'],
            'alive_count': totals['alive_count'],
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
    saved_clock = getattr(reader, '_bc_static_fields', 0)
    try:
        reader._load_spawn_plan()
    except Exception:
        reader._set_spawn_plan([])
        if saved_clock:
            reader._bc_static_fields = saved_clock
        else:
            reader._resolve_battle_clock()
    if saved_clock and not getattr(reader, '_bc_static_fields', 0):
        reader._bc_static_fields = saved_clock
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
        self.ready = False
        self.error = None
        self._last_bootstrap = float('-inf')
        self._last_poll = float('-inf')
        self._status = 'starting'
        self._rows = []

    def reset(self):
        self.timeline.reset()
        self.ready = False
        self.error = None
        self._last_bootstrap = float('-inf')
        self._last_poll = float('-inf')
        self._status = 'starting'
        self._rows = []

    def _state(self, status, *, rows=None, error=None):
        self._status = status
        self.error = error
        if rows is not None:
            self._rows = list(rows)
        current = list(self._rows)
        return {
            'status': status,
            'error': error,
            'ready': self.ready,
            'rows': current,
            'totals': enemy_totals(current),
            'series': self.timeline.series(),
            'last_point': self.timeline.last,
        }

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

    def sample(self, *, game_time, our_damage):
        if not self.ready and not self._bootstrap():
            return self._state(self._status, error=self.error)
        now = self.clock()
        if now - self._last_poll < self.poll_interval:
            return self._state(self._status, error=self.error)
        self._last_poll = now
        try:
            snapshot = self.reader.poll_fast()
        except Exception as exc:
            return self._state('unavailable', error=f'敌人读取失败：{exc}')
        if not snapshot or not snapshot.get('ok'):
            message = (snapshot or {}).get('msg') or '敌人快照不完整'
            return self._state('stale', error=message)
        rows = enemy_rows(snapshot)
        self.timeline.add(game_time, rows, our_damage)
        return self._state('ready', rows=rows)
