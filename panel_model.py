"""Shared GUI/HUD panel view model.

A panel chooses one category (damage, healing or absorbed shield), one ranking
basis (battle totals or the recent five-second window) and, for damage/healing,
one rate column (recent five seconds or engagement). Ranking, bar length and
share denominators always use the ranking basis; switching the rate column
cannot silently change them.

Persistence (:class:`PanelConfig`) is separate from runtime state
(:class:`PanelView`): a saved panel restores its configuration, not stale
numbers from the previous battle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from source_classification import classify_unmapped_source
from stats_core import DAMAGE_SCOPE_MISMATCH, exceeds_native_damage

DAMAGE_COLORS = {
    'physical': '#e5484d',   # red
    'arts': '#3d8bfd',       # blue
    'elemental': '#9b5cf6',  # purple
    'true': '#ffffff',       # white
}
DAMAGE_ORDER = ('physical', 'arts', 'elemental', 'true')
SCHEMA_VERSION = 1


class Category(str, Enum):
    DAMAGE = 'damage'
    HEALING = 'healing'
    SHIELD = 'shield'


class Ranking(str, Enum):
    TOTAL = 'total'
    DPS = 'dps'
    RECENT = 'recent'


class RateBasis(str, Enum):
    RECENT_5S = 'recent_5s'
    ENGAGEMENT = 'engagement'
    FULL_CYCLE = 'full_cycle'


CATEGORY_LABELS = {Category.DAMAGE: '伤害', Category.HEALING: '治疗',
                   Category.SHIELD: '有效护盾'}
RANKING_LABELS = {Ranking.TOTAL: '总伤', Ranking.DPS: 'DPS', Ranking.RECENT: '最近 5 秒'}

def ranking_label(category: Category, ranking: Ranking) -> str:
    if ranking is Ranking.TOTAL:
        return {Category.DAMAGE: '总伤', Category.HEALING: '总治疗',
                Category.SHIELD: '总护盾'}[category]
    return RANKING_LABELS[ranking]

RATE_LABELS = {RateBasis.RECENT_5S: '近 5 秒', RateBasis.ENGAGEMENT: '接敌',
               RateBasis.FULL_CYCLE: '全周期DPS'}

@dataclass(frozen=True)
class EventAvailability:
    """Event capability that explains the engagement rate column.

    A missing, failed or interrupted observer never becomes a zero rate or a
    wall-clock substitute; it is reported as an explicit unavailable state so
    the UI cannot present "no data" as a measured value.
    """

    status: str | None = None
    error: str | None = None
    live: bool = True


def _error_summary(error: str | None, limit: int = 120) -> str:
    if not error:
        return ''
    text = ' '.join(str(error).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def rate_notice(event: EventAvailability | None) -> tuple[bool, str, str, str, str]:
    """Return ``(available, reason, hint, action, action_label)``.

    Only a live or recorded event stream certifies engagement data. Every
    other state carries a readable reason and a recovery entry.
    """
    if event is None or event.status in ('recording', 'finished'):
        return True, '', '', '', ''
    error = _error_summary(event.error)
    detail = f'（{error}）' if error else ''
    if event.live:
        if event.status == 'failed':
            reason = f'接敌数据不可用：事件观察未连接{detail}'
            hint = ('接敌 DPS/HPS 与活跃时间需要事件观察。可以重连观察者，'
                    '或打开连接设置检查 Frida 端点。')
            action, label = 'retry', '重连观察者'
        elif event.status == 'gap':
            reason = f'接敌数据已中断：仅保留已覆盖区间{detail}'
            hint = '未覆盖区间保持未知且不补零。重连观察者后会显示新覆盖的数据。'
            action, label = 'retry', '重连观察者'
        elif event.status == 'incompatible':
            reason = '接敌数据不可用：事件观察与当前游戏版本不兼容'
            hint = '请检查游戏版本与连接设置，再重连观察者。'
            action, label = 'settings', '打开连接设置'
        elif event.status in ('unavailable', 'stopped'):
            reason = ('接敌数据不可用：事件观察已关闭'
                      if event.status == 'unavailable'
                      else '接敌数据不可用：事件观察已停止')
            hint = '在连接设置中启用观察者，再点击「应用设置并重连」。'
            action, label = 'settings', '打开连接设置'
        elif event.status is None:
            reason = '接敌数据尚未可用：等待事件能力诊断'
            hint = '等待事件观察诊断；长时间无结果请打开连接设置检查 Frida 端点。'
            action, label = 'settings', '打开连接设置'
        else:
            reason = '接敌数据尚未可用：事件观察正在建立'
            hint = '等待同帧事件基线；长时间未就绪请打开连接设置检查 Frida 端点。'
            action, label = 'settings', '打开连接设置'
    else:
        if event.status == 'failed':
            reason = f'接敌数据不可用：该局事件观察未连接{detail}'
        elif event.status == 'gap':
            reason = f'接敌数据不完整：该局事件观察中途中断{detail}'
        else:
            reason = '接敌数据不可用：该局未开启事件观察'
        hint = '历史记录无法补采事件；开启事件观察后新战局会记录接敌数据。'
        action, label = 'settings', '打开连接设置'
    return False, reason, hint, action, label


DEFAULT_NUMBER_UNITS = 'western'
NUMBER_UNITS = {
    'western': ((1_000_000, 'M'), (1_000, 'K')),
    'chinese': ((100_000_000, '亿'), (10_000, '万')),
}


def format_compact(value: float | None, number_units: str = DEFAULT_NUMBER_UNITS) -> str:
    """Shorten display amounts without changing the underlying measurements."""
    if not _finite(value):
        return '—'
    sign = '-' if value < 0 else ''
    value = abs(value)
    whole = round(value)
    (major, major_suffix), (minor, minor_suffix) = NUMBER_UNITS[number_units]
    if whole >= minor:
        scaled_minor = round(value / minor * 10) / 10
        if whole >= major or scaled_minor >= major / minor:
            return f"{sign}{round(value / major * 10) / 10:.1f}".rstrip('0').rstrip('.') + major_suffix
        return f"{sign}{scaled_minor:.1f}".rstrip('0').rstrip('.') + minor_suffix
    return f'{sign}{whole}'


def format_percent(value: float | None) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value * 100:.1f}%'


def format_one_decimal(value: float | None) -> str:
    """User-visible number with at most one decimal place."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value:.1f}'.rstrip('0').rstrip('.')


@dataclass(frozen=True)
class PanelConfig:
    panel_id: str = 'default'
    category: Category = Category.DAMAGE
    ranking: Ranking = Ranking.TOTAL
    rate_basis: RateBasis = RateBasis.RECENT_5S
    title: str | None = None
    effective_only: bool = False
    boss_only: bool = False

    def __post_init__(self):
        if not self.panel_id:
            raise ValueError('panel_id is required')
        if type(self.boss_only) is not bool:
            raise ValueError('boss_only 必须是布尔值')
        if self.category is Category.SHIELD and self.rate_basis is not RateBasis.RECENT_5S:
            raise ValueError('护盾不使用接敌或全周期速率')
        if self.category is Category.SHIELD and self.ranking is Ranking.DPS:
            raise ValueError('护盾不使用 DPS 排行')

    @property
    def boss_scope(self) -> bool:
        return self.boss_only and self.category is Category.DAMAGE

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        category = ('有效伤害' if self.effective_only and self.category is Category.DAMAGE
                    else CATEGORY_LABELS[self.category])
        if self.boss_scope:
            category = '仅首领 · ' + category
        return f'{category} · {ranking_label(self.category, self.ranking)}'

    def to_dict(self) -> dict:
        return {'panel_id': self.panel_id, 'category': self.category.value,
                'ranking': self.ranking.value, 'rate_basis': self.rate_basis.value,
                'title': self.title, 'effective_only': self.effective_only,
                'boss_only': self.boss_only}

    @classmethod
    def from_dict(cls, raw: object) -> 'PanelConfig':
        if not isinstance(raw, dict):
            raise ValueError('面板配置必须是 JSON 对象')
        try:
            category = Category(raw.get('category', 'damage'))
            ranking = Ranking(raw.get('ranking', 'total'))
            rate_basis = RateBasis(raw.get('rate_basis', 'recent_5s'))
        except ValueError as exc:
            raise ValueError(f'面板配置枚举无效：{exc}') from exc
        title = raw.get('title')
        if title is not None and not isinstance(title, str):
            raise ValueError('title 必须是字符串或 null')
        effective_only = raw.get('effective_only', False)
        if type(effective_only) is not bool:
            raise ValueError('effective_only 必须是布尔值')
        return cls(panel_id=str(raw.get('panel_id') or 'default'),
                   category=category, ranking=ranking,
                   rate_basis=rate_basis, title=title,
                   effective_only=effective_only, boss_only=raw.get('boss_only', False))


@dataclass(frozen=True)
class PanelCollection:
    panels: tuple[PanelConfig, ...] = (PanelConfig(),)

    def __post_init__(self):
        if not self.panels:
            raise ValueError('至少需要一个面板')
        ids = [p.panel_id for p in self.panels]
        if len(ids) != len(set(ids)):
            raise ValueError('面板 id 不能重复')

    def to_dict(self) -> dict:
        return {'version': SCHEMA_VERSION,
                'panels': [p.to_dict() for p in self.panels]}

    @classmethod
    def from_dict(cls, raw: object) -> 'PanelCollection':
        if not isinstance(raw, dict):
            raise ValueError('面板集合必须是 JSON 对象')
        if raw.get('version') != SCHEMA_VERSION:
            raise ValueError('不支持的面板配置版本')
        panels = raw.get('panels')
        if not isinstance(panels, list):
            raise ValueError('panels 必须是数组')
        return cls(tuple(PanelConfig.from_dict(p) for p in panels))


@dataclass
class PanelSourceRow:
    """Metric row from the statistics adapter or a test double."""

    owner_id: str
    name: str
    present: bool | None = None
    damage: float | None = None
    healing: float | None = None
    shield: float | None = None
    recent_damage: float | None = None
    recent_healing: float | None = None
    recent_shield: float | None = None
    engagement_damage: float | None = None
    engagement_healing: float | None = None
    active_damage: float | None = None
    active_healing: float | None = None
    recent_overkill: float | None = None
    # Observed on-field duration since this session's coverage start. It is not
    # the engagement interval and is None when presence was never certified.
    presence_seconds: float | None = None
    presence_complete: bool = False
    damage_by_type: Mapping[str, float] = field(default_factory=dict)
    # Event-derived overkill is a subset of damage, never part of the ranking
    # denominator. It stays None unless the whole event stream proved it.
    overkill: float | None = None
    overkill_by_type: Mapping[str, float] | None = None
    overkill_complete: bool = False
    overkill_type_complete: bool = False
    overkill_unknown_reason: str | None = None
    burst_damage: float | None = None
    burst_overkill: float | None = None
    burst_settlement: float | None = None
    burst_complete: bool = False
    burst_count: int = 0
    burst_unknown_reason: str | None = None
    effective_damage: float | None = None
    skill_damage: float | None = None
    non_skill_damage: float | None = None
    skill_damage_effective: float | None = None
    non_skill_damage_effective: float | None = None
    observed_skill_damage: float | None = None
    observed_non_skill_damage: float | None = None
    observed_skill_damage_effective: float | None = None
    observed_non_skill_damage_effective: float | None = None
    skill_split_complete: bool = False
    skill_split_effective_complete: bool = False
    skill_split_reason: str | None = None
    skill_active_seconds: float | None = None
    skill_coverage_complete: bool = False
    skill_window_count: int = 0
    skill_seen: bool = False
    counter_complete: bool = True
    event_complete: bool = True
    event_coverage_end: float | None = None
    sources: tuple[str, ...] = ()
    # A window certifies that the full expected recent interval was observed.
    recent_damage_window: tuple[float, float] | None = None
    recent_healing_window: tuple[float, float] | None = None
    recent_shield_window: tuple[float, float] | None = None
    recent_damage_by_type: Mapping[str, float] = field(default_factory=dict)
    # Event-observed totals exist (0 is a reliable zero); None means that event
    # coverage never covered this row.
    observed_damage: float | None = None
    observed_healing: float | None = None
    source_complete: bool = True
    partial_damage: Mapping = field(default_factory=dict)
    settled_damage: Mapping | None = None
    damage_scope_reason: str | None = None
    mitigation_unknown_reason: str | None = None
    observed_mitigation_rate: float | None = None

    def total(self, category: Category) -> float | None:
        damage = self.damage
        if damage is None and (self.source_complete or self.settled_damage is not None):
            damage = (self.settled_damage.get('observed_boss_damage') if self.settled_damage is not None
                      else self.partial_damage.get('observed_boss_damage'))
        shield = self.shield
        if shield is None and self.source_complete:
            shield = self.partial_damage.get('observed_shield_absorbed')
        return {Category.DAMAGE: damage,
                Category.HEALING: self.healing,
                Category.SHIELD: shield}[category]

    def recent(self, category: Category) -> float | None:
        damage = self.recent_damage
        if damage is None and (self.source_complete or self.settled_damage is not None):
            damage = (self.settled_damage.get('observed_boss_recent_damage') if self.settled_damage is not None
                      else self.partial_damage.get('observed_boss_recent_damage'))
        return {Category.DAMAGE: damage,
                Category.HEALING: self.recent_healing,
                Category.SHIELD: self.recent_shield}[category]

    def engagement(self, category: Category) -> float | None:
        if category is Category.DAMAGE:
            if self.settled_damage is not None:
                rate = self.settled_damage.get('engagement_damage')
                return rate if _finite(rate) and rate >= 0 else None
            if self.damage_scope_reason:
                return None
            return (self.engagement_damage if self.engagement_damage is not None else
                    self.partial_damage.get('engagement_damage') if self.source_complete else None)
        if category is Category.HEALING:
            return (self.engagement_healing if self.engagement_healing is not None else
                    self.partial_damage.get('engagement_healing') if self.source_complete else None)
        return None

    def active(self, category: Category) -> float | None:
        if category is Category.DAMAGE:
            return self.active_damage
        if category is Category.HEALING:
            return self.active_healing
        return None

    def effective_recent(self) -> float | None:
        """近期有效 DPS：近期速率减去窗口内过量量摊到每秒。"""
        if self.settled_damage is not None:
            rate = self.settled_damage.get('recent_effective_damage')
            return rate if _valid_window(self.effective_recent_window()) and _finite(rate) and rate >= 0 else None
        if self.damage_scope_reason or self.partial_damage.get('recent_damage_scope_reason'):
            return None
        window = self.recent_damage_window
        if (not _finite(self.recent_damage) or not _finite(self.recent_overkill)
                or not self.overkill_complete or not _valid_window(window)):
            return self.partial_damage.get('recent_effective_damage') if self.source_complete else None
        duration = window[1] - window[0]
        return max(0.0, self.recent_damage - self.recent_overkill / duration)

    def effective_observed(self) -> float | None:
        """接敌有效 DPS：有效事件量除以活跃秒数，不是把量直接当成每秒。"""
        if self.settled_damage is not None:
            rate = self.settled_damage.get('engagement_effective_damage')
            return rate if _finite(rate) and rate >= 0 else None
        if self.damage_scope_reason:
            return None
        if (not _finite(self.observed_damage) or not _finite(self.overkill)
                or not _finite(self.active_damage) or self.active_damage <= 0
                or not self.overkill_complete):
            return self.partial_damage.get('engagement_effective_damage') if self.source_complete else None
        return max(0.0, self.observed_damage - self.overkill) / self.active_damage

    def effective_recent_window(self):
        if self.settled_damage is not None:
            return self.settled_damage.get('recent_effective_damage_window')
        return self.recent_damage_window

    def settled_recent_complete(self) -> bool:
        return self.settled_damage.get('recent_effective_damage_complete',
                                       self.settled_damage.get('complete')) is True

    def skill_coverage(self) -> float | None:
        """Observed skill-on time divided by certified on-field time."""
        if (not self.skill_coverage_complete or not self.presence_complete
                or not _finite(self.skill_active_seconds)
                or not _finite(self.presence_seconds)
                or self.presence_seconds <= 0
                or not 0 <= self.skill_active_seconds <= self.presence_seconds):
            return None
        return self.skill_active_seconds / self.presence_seconds

    def full_cycle(self, category: Category, *, effective: bool = False) -> float | None:
        """总量 / 在场时间, only when numerator and presence are certified."""
        total = (_effective_damage(self) if effective and category is Category.DAMAGE
                 else self.total(category))
        if (not _finite(total) or not _finite(self.presence_seconds)
                or self.presence_seconds <= 0 or not self.presence_complete):
            return None
        # Roster presence sampling can start after the first event. The active
        # engagement interval is evidence that the owner was present, so it is
        # a lower bound for the full-cycle denominator. Without this guard a
        # slowly detected deployment makes full-cycle DPS exceed engagement DPS.
        active = (self.settled_damage.get('active_effective_damage')
                  if effective and category is Category.DAMAGE and self.settled_damage is not None
                  else self.active(category))
        denominator = max(self.presence_seconds,
                          active if _finite(active) else self.presence_seconds)
        if denominator <= 0:
            return None
        return total / denominator

    def rate(self, category: Category, basis: 'RateBasis', *,
             effective: bool = False) -> float | None:
        if basis is RateBasis.RECENT_5S:
            return (self.effective_recent()
                    if effective and category is Category.DAMAGE
                    else self.recent(category))
        if basis is RateBasis.ENGAGEMENT:
            return (self.effective_observed()
                    if effective and category is Category.DAMAGE
                    else self.engagement(category))
        return self.full_cycle(category, effective=effective)

    def observed(self, category: Category) -> float | None:
        if category is Category.DAMAGE:
            return self.observed_damage
        if category is Category.HEALING:
            return self.observed_healing
        return None

    def recent_window(self, category: Category) -> tuple[float, float] | None:
        return {Category.DAMAGE: self.recent_damage_window,
                Category.HEALING: self.recent_healing_window,
                Category.SHIELD: self.recent_shield_window}[category]


def damage_scope_reason(row: Mapping) -> str | None:
    """Recognize incompatible evidence in both current and archived rows."""
    if reason := row.get('damage_scope_reason'):
        return reason
    partial = row.get('partial_damage') or {}
    total = row.get('damage')
    if not _finite(total):
        return None
    # Old archives did not persist per-source reconciliation. The observed
    # ordinary settlement sum excludes bursts; use only the known supplement.
    observed = row.get('observed_damage')
    credited_burst = row.get('burst_settlement') or row.get('burst_added_settlement') or 0.0
    if (row.get('counter_snapshot_complete') and _finite(observed) and _finite(credited_burst)
            and exceeds_native_damage(observed + credited_burst, total)):
        return DAMAGE_SCOPE_MISMATCH
    values = (row.get('effective_damage'), partial.get('effective_damage'))
    if any(_finite(value) and value > total for value in values):
        return DAMAGE_SCOPE_MISMATCH
    return None


def _settled_damage(row: Mapping) -> Mapping | None:
    lane = row.get('settled_damage')
    return lane if isinstance(lane, Mapping) else None


def panel_statistics(config: PanelConfig, result: Mapping, *, history: bool = False) -> Mapping:
    """Select the panel's target scope before rendering or building details."""
    if not config.boss_scope:
        return result
    available = result.get('boss_damage_available') is True
    reason = result.get('boss_damage_reason') or ''
    unavailable_reason = reason or ('这局战绩未记录首领伤害' if history else
                                    '首领伤害尚无可靠数据，需要事件观察与目标身份')
    rows = {}
    for owner, row in (result.get('rows') or {}).items():
        scoped = row.get('boss_damage')
        has_settled = _settled_damage(row) is not None
        mismatch = None if has_settled else damage_scope_reason(row)
        if has_settled:
            lane = _settled_damage(scoped) if isinstance(scoped, Mapping) else None
            # The legacy boss projection may use a different aggregate than
            # the verified hit lane. Use only the latter for this target scope.
            scoped = {'settled_damage': lane or {},
                      'event_coverage_complete': lane is not None and lane.get('complete') is True,
                      'counter_snapshot_complete': lane is not None and lane.get(
                          'complete' if config.effective_only else 'observed_boss_damage_complete') is True}
        elif mismatch:
            scoped = {'damage_scope_reason': mismatch, 'overkill_unknown_reason': mismatch}
        elif not isinstance(scoped, Mapping):
            scoped = {}
        elif not available:
            # Preserve the observed boss-only skill split when its full
            # totals and ranking cannot be certified.
            scoped = {key: scoped.get(key) for key in (
                'observed_skill_damage', 'observed_non_skill_damage',
                'observed_skill_damage_effective', 'observed_non_skill_damage_effective',
                'partial_damage', 'settled_damage',
            )}
            scoped['skill_split_reason'] = unavailable_reason
        # These values describe the operator, independent of whom they hit.
        identity = {key: row[key] for key in (
            'name', 'present', 'sources', 'presence_seconds', 'presence_complete',
            'skill_active_seconds', 'skill_coverage_complete', 'skill_window_count',
            'skill_seen') if key in row}
        rows[owner] = {
            **scoped, **identity,
            'counter_snapshot_complete': scoped.get('counter_snapshot_complete') is True,
            'event_coverage_complete': scoped.get('event_coverage_complete') is True,
        }
        if scoped.get('overkill_coverage_complete') is not True:
            # The legacy effective-damage fallback subtracts any present
            # overkill value; boss-only requires coverage of the same scope.
            rows[owner]['overkill_damage'] = None
    lanes = [lane for row in rows.values() if (lane := _settled_damage(row)) is not None]
    if lanes:
        settled_key = 'effective_damage' if config.effective_only else 'observed_boss_damage'
        complete_key = 'complete' if config.effective_only else 'observed_boss_damage_complete'
        available = any(_finite(value := lane.get(settled_key)) and
                        (value > 0 or value == 0 and lane.get(complete_key) is True) for lane in lanes)
        if available:
            reason = next((lane['reason'] for lane in lanes if lane.get('reason')), '')
        else:
            reason = next((lane['reason'] for lane in lanes if lane.get('reason')),
                          '尚未核实首领有效伤害' if config.effective_only else '尚未核实首领结算总伤')
    elif not available:
        reason = unavailable_reason
    return {
        **result, 'rows': rows, 'boss_damage_available': available, 'boss_damage_reason': reason,
        'totals_complete': available and all(row['counter_snapshot_complete'] for row in rows.values()),
    }


def rows_from_statistics(result: Mapping) -> list[PanelSourceRow]:
    """Convert an adapter/Statistics result into panel rows without inventing data."""
    rows: list[PanelSourceRow] = []
    unresolved = {source for key, _ in DIAGNOSTIC_LABELS if key != 'missing_fields'
                  for source in _name_list(result.get(key))}
    for owner, row in (result.get('rows') or {}).items():
        sources = tuple(row.get('sources') or ())
        partial = row.get('partial_damage') or {}
        if not partial and row.get('effective_damage') is None:
            # Older archives already retain proven effective hits assigned
            # to a skill window. They lack their full settlement denominator.
            observed = (row.get('observed_skill_damage_effective'),
                        row.get('observed_non_skill_damage_effective'))
            if all(_finite(value) and value >= 0 for value in observed) and sum(observed) > 0:
                partial = {'effective_damage': sum(observed)}
        settled = _settled_damage(row)
        legacy_scope_reason = damage_scope_reason(row)
        scope_reason = None if settled is not None else legacy_scope_reason
        rows.append(PanelSourceRow(
            owner_id=owner,
            name=row.get('name') or owner,
            present=row.get('present'),
            damage=row.get('damage'),
            healing=row.get('healing'),
            shield=row.get('shield_absorbed'),
            recent_damage=row.get('recent_damage'),
            recent_healing=row.get('recent_healing'),
            recent_shield=row.get('recent_shield'),
            engagement_damage=row.get('engagement_damage'),
            engagement_healing=row.get('engagement_healing'),
            active_damage=row.get('active_damage'),
            active_healing=row.get('active_healing'),
            recent_overkill=row.get('recent_overkill_damage'),
            presence_seconds=row.get('presence_seconds'),
            presence_complete=bool(row.get('presence_complete', False)),
            observed_damage=row.get('observed_damage'),
            observed_healing=row.get('observed_healing'),
            damage_by_type=row.get('damage_by_type') or {},
            recent_damage_by_type=row.get('recent_damage_by_type') or {},
            overkill=None if legacy_scope_reason else row.get('overkill_damage'),
            overkill_by_type=row.get('overkill_damage_by_type'),
            overkill_complete=not legacy_scope_reason and bool(row.get('overkill_coverage_complete', False)),
            overkill_type_complete=not legacy_scope_reason and bool(row.get('overkill_type_complete', False)),
            overkill_unknown_reason=legacy_scope_reason or row.get('overkill_unknown_reason'),
            burst_damage=row.get('burst_damage'),
            burst_overkill=row.get('burst_overkill'),
            burst_settlement=row.get('burst_settlement'),
            burst_complete=bool(row.get('burst_complete', False)),
            burst_count=row.get('burst_count') or 0,
            burst_unknown_reason=row.get('burst_unknown_reason'),
            effective_damage=row.get('effective_damage'),
            skill_damage=row.get('skill_damage'),
            non_skill_damage=row.get('non_skill_damage'),
            skill_damage_effective=row.get('skill_damage_effective'),
            non_skill_damage_effective=row.get('non_skill_damage_effective'),
            observed_skill_damage=row.get('observed_skill_damage'),
            observed_non_skill_damage=row.get('observed_non_skill_damage'),
            observed_skill_damage_effective=row.get('observed_skill_damage_effective'),
            observed_non_skill_damage_effective=row.get('observed_non_skill_damage_effective'),
            skill_split_complete=bool(row.get('skill_split_complete', False)),
            skill_split_effective_complete=bool(
                row.get('skill_split_effective_complete', False)),
            skill_split_reason=row.get('skill_split_reason'),
            skill_active_seconds=row.get('skill_active_seconds'),
            skill_coverage_complete=bool(row.get('skill_coverage_complete', False)),
            skill_window_count=row.get('skill_window_count') or 0,
            skill_seen=bool(row.get('skill_seen', False)),
            recent_damage_window=row.get('recent_damage_window'),
            recent_healing_window=row.get('recent_healing_window'),
            recent_shield_window=row.get('recent_shield_window'),
            counter_complete=bool(row.get('counter_snapshot_complete', result.get('totals_complete'))),
            event_complete=bool(row.get('event_coverage_complete', False)),
            event_coverage_end=row.get('event_coverage_end'),
            sources=sources,
            source_complete=owner not in unresolved and unresolved.isdisjoint(sources),
            partial_damage=partial,
            settled_damage=settled,
            damage_scope_reason=scope_reason,
            mitigation_unknown_reason=row.get('mitigation_unknown_reason'),
            observed_mitigation_rate=row.get('observed_mitigation_rate'),
        ))
    return rows


@dataclass
class PanelEntry:
    owner_id: str
    name: str
    value: float
    bar_ratio: float
    share: float | None
    share_display: str
    display_value: str
    exact_value: str
    rate: float | None
    rate_display: str
    rate_label: str
    present: bool | None
    portrait_muted: bool
    incomplete: bool
    segments: tuple[dict, ...]
    segments_complete: bool
    detail: dict
    # value | no_engagement | unknown | unavailable | unsupported
    rate_state: str = 'value'
    # Complete display text for the rate column: '123/s', '无接敌' or '—'.
    rate_text: str = ''
    rate_note: str = ''
    cumulative_display_value: str = '—'
    coverage: dict = field(default_factory=dict)


@dataclass
class PanelView:
    config: PanelConfig
    title: str
    entries: list[PanelEntry]
    total: float
    denominator: float | None
    share_known: bool
    share_reason: str
    rate_supported: bool
    rate_label: str
    metric_label: str
    rate_available: bool = True
    rate_reason: str = ''
    rate_hint: str = ''
    rate_action: str = ''
    rate_action_label: str = ''
    # Present even when shares are known: names sources excluded from the team.
    share_note: str = ''
    details_by_owner: dict[str, dict] = field(default_factory=dict)
    # team | known | unavailable; share_known retains the full-team meaning.
    share_scope: str = 'unavailable'
    footer_total: float | None = None
    footer_rate: float | None = None
    footer_total_complete: bool = False
    footer_rate_complete: bool = False
    footer_total_display: str = '—'
    footer_rate_display: str = '—'


def _finite(value: float | None) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


DIAGNOSTIC_LABELS = (
    ('unknown_sources', '未归属统计来源'),
    ('unmapped_tokens', '未确认归属的召唤物'),
    ('ambiguous_sources', '归属歧义统计来源'),
    ('mapping_conflicts', '归属冲突统计来源'),
    ('missing_fields', '字段缺失统计来源'),
)


def _name_list(value) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def non_team_label(cid: str) -> str:
    """Class label for a proven non-team source, from the classification rules."""
    classified = classify_unmapped_source(cid)
    return classified[1] if classified else '非队伍来源'


def non_team_note(statistics: Mapping, number_units: str = DEFAULT_NUMBER_UNITS) -> str:
    """Name sources proven not to be team members, with any excluded output."""
    names = _name_list(statistics.get('non_team_sources'))
    if not names:
        return ''
    output = statistics.get('non_team_output')
    output = output if isinstance(output, Mapping) else {}
    parts = []
    for cid in names:
        values = output.get(cid)
        values = values if isinstance(values, Mapping) else {}
        damage, healing = values.get('damage'), values.get('healing')
        known = _finite(damage) or _finite(healing)
        label = non_team_label(cid)
        if (_finite(damage) and damage > 0) or (_finite(healing) and healing > 0):
            parts.append(f'{cid}（{label}，未归属输出：伤害 {format_compact(damage, number_units)}，'
                         f'治疗 {format_compact(healing, number_units)}）')
        elif not known:
            parts.append(f'{cid}（{label}，输出未知）')
        else:
            parts.append(f'{cid}（{label}，未观察到输出）')
    return '已排除非队伍来源：' + '；'.join(parts)


def team_status(statistics: Mapping, number_units: str = DEFAULT_NUMBER_UNITS) -> tuple[bool, str, str]:
    """Return ``(complete, blocking_reason, non_team_note)`` for a team panel.

    Completeness follows the adapter's cumulative-totals marker; per-row
    ``counter_snapshot_complete`` remains the fallback for records written
    before that marker existed. Unattributed or ambiguous sources make shares
    unknown and are named, while proven non-team sources are excluded from the
    denominator and reported separately instead of blocking the whole team.
    """
    result = statistics if isinstance(statistics, Mapping) else {}
    gaps = []
    for key, label in DIAGNOSTIC_LABELS:
        names = _name_list(result.get(key))
        if names:
            gaps.append(f'{label}：{"、".join(names)}')
    raw_rows = result.get('rows')
    rows = raw_rows if isinstance(raw_rows, Mapping) else {}
    if 'totals_complete' in result:
        complete = bool(result['totals_complete'])
    else:
        complete = all(isinstance(row, Mapping)
                       and bool(row.get('counter_snapshot_complete'))
                       for row in rows.values())
    if not complete:
        # Name the rows that are actually uncertified. When every row's own
        # cumulative snapshot is certified, the panel is blocked by an
        # unattributed source instead, and claiming an incomplete snapshot
        # here would contradict the rows shown next to the reason.
        stale = [str(row.get('name') or owner) for owner, row in rows.items()
                 if isinstance(row, Mapping) and not row.get('counter_snapshot_complete')]
        if stale:
            gaps.append('累计快照不完整：' + '、'.join(stale))
        elif not gaps:
            gaps.append('累计快照不完整')
    reason = '队伍数据不完整（' + '；'.join(gaps) + '）' if gaps else ''
    return (not gaps), reason, non_team_note(result, number_units)


def _valid_window(window) -> bool:
    return (isinstance(window, (tuple, list)) and len(window) == 2
            and all(_finite(t) and not isinstance(t, bool) for t in window)
            and 0 < window[1] - window[0] <= 5)


def live_damage_rates(statistics: Mapping, game_time: float, *, effective: bool = False
                      ) -> tuple[float | None, list[dict]]:
    """Publish certified damage rates independently of the active panel."""
    operators = []
    windows = []
    for owner, row in sorted((statistics.get('rows') or {}).items()):
        settled = _settled_damage(row) if effective else None
        rate = row.get('recent_damage')
        window = row.get('recent_damage_window')
        certified = (row.get('counter_snapshot_complete') is True
                     and _valid_window(window) and 0 <= window[0] < window[1] == game_time
                     and _finite(rate) and not isinstance(rate, bool) and rate >= 0)
        if settled is not None:
            rate = settled.get('recent_effective_damage')
            window = settled.get('recent_effective_damage_window')
            certified = (settled.get('recent_effective_damage_complete', settled.get('complete')) is True
                         and _valid_window(window) and 0 <= window[0] < window[1] == game_time
                         and _finite(rate) and not isinstance(rate, bool) and rate >= 0)
        elif certified and effective:
            excess = row.get('recent_overkill_damage')
            certified = (row.get('overkill_coverage_complete') is True
                         and not damage_scope_reason(row)
                         and not (row.get('partial_damage') or {}).get('recent_damage_scope_reason')
                         and _finite(excess) and not isinstance(excess, bool) and excess >= 0)
            if certified:
                rate = max(0.0, rate - excess / (window[1] - window[0]))
        operators.append(dict(owner_id=owner, name=row.get('name') or owner,
                              recent_dps=rate if certified else None))
        if certified:
            windows.append(tuple(window))
    if operators and len(windows) == len(operators) and all(w == windows[0] for w in windows):
        recent = sum(operator['recent_dps'] for operator in operators)
        return (recent if _finite(recent) else None), operators
    return None, operators


def settled_boss_damage_rates(statistics: Mapping, game_time: float, *, effective: bool):
    """Return scoped rates for charting, with a separate observed subset."""
    rows = sorted((statistics.get('rows') or {}).items())
    if not any(_settled_damage(row) is not None for _, row in rows):
        return None
    rate_key, window_key, complete_key = (
        ('recent_effective_damage', 'recent_effective_damage_window', 'recent_effective_damage_complete')
        if effective else ('observed_boss_recent_damage', 'observed_boss_recent_window',
                           'observed_boss_recent_damage_complete'))
    operators, windows = [], set()
    for owner, row in rows:
        lane = _settled_damage(row) or {}
        rate, window = lane.get(rate_key), lane.get(window_key)
        valid = _valid_window(window) and 0 <= window[0] < window[1] == game_time and _finite(rate) and rate >= 0
        complete = valid and lane.get(complete_key) is True
        if valid:
            windows.add(tuple(window))
        operators.append(dict(owner_id=owner, name=row.get('name') or owner,
                              recent_dps=rate if complete else None,
                              observed_recent_dps=rate if valid and not complete else None))
    team = sum(row['recent_dps'] for row in operators) if (
        len(windows) == 1 and all(row['recent_dps'] is not None for row in operators)) else None
    return (team if _finite(team) else None), operators


def _effective_damage(row: PanelSourceRow) -> float | None:
    if row.settled_damage is not None:
        effective = row.settled_damage.get('effective_damage')
        return effective if _finite(effective) and effective >= 0 else None
    if row.damage_scope_reason:
        return None
    total = row.total(Category.DAMAGE)
    if _finite(row.effective_damage):
        return float(row.effective_damage) if not _finite(total) or row.effective_damage <= total else None
    if _finite(row.damage) and _finite(row.overkill):
        if not 0 <= row.overkill <= row.damage:
            return None
        return float(row.damage) - float(row.overkill)
    effective = row.partial_damage.get('effective_damage')
    return effective if row.source_complete and _finite(effective) and (
        not _finite(total) or effective <= total) else None


def _effective_by_type(row: PanelSourceRow) -> dict | None:
    total = _effective_damage(row)
    if total is None:
        return None
    if row.settled_damage is not None:
        return _observed_damage_types(row.settled_damage.get('effective_damage_by_type'), total)
    if not row.overkill_type_complete or row.overkill_by_type is None:
        # An untyped overkill event still has one possible damage type when the
        # operator's entire observed output is that type. This is evidence from
        # the type totals, not a proportional guess.
        live_types = [kind for kind, value in row.damage_by_type.items()
                      if _finite(value) and value > 0]
        if len(live_types) == 1 and _finite(row.overkill):
            kind = live_types[0]
            return {kind: max(0.0, float(row.damage_by_type[kind]) - float(row.overkill))}
        observed = row.partial_damage.get('effective_damage_by_type')
        return _observed_damage_types(observed, total)
    return {kind: max(0.0, float(value) - float(row.overkill_by_type.get(kind, 0.0)))
            for kind, value in row.damage_by_type.items() if _finite(value)}


def _damage_by_type(row: PanelSourceRow) -> Mapping:
    if row.settled_damage is not None:
        if row.damage is None:
            observed = row.settled_damage.get('observed_boss_damage')
            return _observed_damage_types(row.settled_damage.get('damage_by_type'), observed) or {}
        return row.damage_by_type
    if row.damage is None and not row.damage_scope_reason and row.source_complete:
        return row.partial_damage.get('damage_by_type') or row.damage_by_type
    return row.damage_by_type


def _observed_damage_types(observed, total) -> dict | None:
    if not isinstance(observed, Mapping) or not _finite(total):
        return None
    typed = {kind: value for kind, value in observed.items()
             if kind in DAMAGE_ORDER and _finite(value) and value > 0}
    return typed if typed and not exceeds_native_damage(sum(typed.values()), total) else None


def _segments(row: PanelSourceRow, ranking: Ranking,
              effective: bool = False) -> tuple[tuple[dict, ...], bool]:
    if effective:
        total = _effective_damage(row)
        by_type = _effective_by_type(row)
        if by_type is None:
            if row.settled_damage is not None or (not row.overkill_complete and row.partial_damage):
                return (), False
            by_type = {kind: max(0.0, float(value) -
                                 float((row.overkill_by_type or {}).get(kind, 0.0)))
                       for kind, value in row.damage_by_type.items() if _finite(value)}
    else:
        total = row.total(Category.DAMAGE) if ranking is Ranking.TOTAL else row.recent_damage
        by_type = _damage_by_type(row) if ranking is Ranking.TOTAL else row.recent_damage_by_type
    if total is None or not _finite(total) or total <= 0:
        return (), False
    segments = []
    known = 0.0
    for kind in DAMAGE_ORDER:
        value = by_type.get(kind)
        if not _finite(value) or value <= 0:
            continue
        known += value
        segment = {'kind': kind, 'value': value,
                   'ratio': min(1.0, value / total),
                   'color': DAMAGE_COLORS[kind]}
        # Overkill is marked only on the totals basis: the five-second window
        # does not derive a separate event-window overkill value.
        if (not effective and ranking is Ranking.TOTAL
                and row.overkill_type_complete and row.overkill_by_type):
            overkill = row.overkill_by_type.get(kind)
            if _finite(overkill) and overkill > 0:
                segment['overkill'] = overkill
                segment['overkill_ratio'] = min(1.0, overkill / value)
        segments.append(segment)
    complete = abs(known - total) <= max(0.01, total * 1e-6)
    if effective and row.settled_damage is not None:
        complete &= row.settled_damage.get('complete') is True
    elif effective and not row.overkill_type_complete and row.partial_damage.get('effective_damage_by_type'):
        complete = False
    if not effective and row.damage is None:
        complete = False
    return tuple(segments), complete


def _rate_display(row: PanelSourceRow, category: Category, rate: float | None,
                  available: bool, *, engagement: bool = True,
                  full_cycle: bool = False,
                  number_units: str = DEFAULT_NUMBER_UNITS) -> tuple[str, str, str]:
    """Classify one row's rate column without inventing a value."""
    if _finite(rate):
        return 'value', f'{format_compact(rate, number_units)}/s', ''
    if full_cycle:
        if not row.presence_complete or row.presence_seconds is None:
            return 'unknown', '—', '在场时间未建立可靠覆盖；全周期 DPS 未知'
        if row.presence_seconds <= 0:
            return 'unknown', '—', '未观察到在场时间；全周期 DPS 未知'
        return 'unknown', '—', '总量或在场时间不完整；全周期 DPS 未知'
    if not engagement:
        return 'unknown', '—', ''
    observed, active = row.observed(category), row.active(category)
    settled = row.settled_damage if category is Category.DAMAGE else None
    no_events = (settled.get('complete') is True and settled.get('observed_hits') == 0
                 if settled is not None else row.event_complete and observed == 0 and active == 0)
    if available and no_events:
        return 'no_engagement', '无接敌', '已覆盖区间内未观察到接敌事件（可靠零）'
    if available:
        return 'unknown', '—', '该干员事件覆盖不完整，接敌数据未知'
    return 'unavailable', '—', '接敌数据不可用：需要事件观察（不以 0 或墙钟替代）'


def detail_rates(rows: Iterable[PanelSourceRow],
                 event: EventAvailability | None = None, *,
                 effective: bool = False,
                 number_units: str = DEFAULT_NUMBER_UNITS) -> dict:
    """Per-owner engagement display state for both damage and healing.

    Detail views use the same classification as the panel rate column so a
    reliable zero, an unknown operator and an unavailable observer never look
    alike.
    """
    available, *_ = rate_notice(event)
    result: dict[str, dict] = {}
    for row in rows:
        kinds = {}
        for kind, category in (('damage', Category.DAMAGE), ('healing', Category.HEALING)):
            raw = (row.effective_observed()
                   if effective and category is Category.DAMAGE
                   else row.engagement(category))
            state, text, note = _rate_display(
                row, category, raw if _finite(raw) else None, available,
                number_units=number_units)
            if category is Category.DAMAGE and row.damage_scope_reason:
                note = row.damage_scope_reason
            if category is Category.DAMAGE and row.settled_damage is not None:
                note = row.settled_damage.get('reason') or note
            kinds[kind] = {'state': state, 'text': text, 'note': note, 'value': raw}
        result[row.owner_id] = kinds
    return result


def _coverage(row: PanelSourceRow) -> dict:
    partial = row.partial_damage
    coverage = {}
    if row.damage_scope_reason:
        for key in ('effective_damage', 'recent_effective_damage', 'engagement_effective_damage',
                    'engagement', 'mitigation', 'skill_damage', 'skill_effective_damage'):
            coverage[key] = dict(failed_share=None, basis=None, reason=row.damage_scope_reason)
    elif partial.get('recent_damage_scope_reason'):
        coverage['recent_effective_damage'] = dict(failed_share=None, basis=None,
                                                    reason=partial['recent_damage_scope_reason'])
    if not partial:
        return _settled_coverage(row, coverage)
    reason = row.overkill_unknown_reason
    if not row.damage_scope_reason:
        if row.damage is None and _damage_by_type(row):
            coverage['damage_by_type'] = dict(failed_share=None, basis=None,
                                               reason=reason or '仅统计已观察且属性已确认的命中')
        if (not row.overkill_type_complete and _effective_by_type(row)
                and partial.get('effective_damage_by_type')):
            coverage['effective_damage_by_type'] = dict(
                failed_share=None, basis=None, reason=reason or '仅统计已核实有效伤害且属性已确认的命中')
    candidates = (
        ('boss_damage', partial.get('observed_boss_damage'), row.damage is not None,
         None, row.overkill_unknown_reason),
        ('recent_boss_damage', partial.get('observed_boss_recent_damage'),
         row.recent_damage is not None, None, row.overkill_unknown_reason),
        ('effective_damage', partial.get('effective_damage'),
         row.effective_damage is not None, '累计结算伤害', row.overkill_unknown_reason),
        ('recent_effective_damage', partial.get('recent_effective_damage'),
         row.overkill_complete and row.recent_overkill is not None,
         '近 5 秒结算伤害', row.overkill_unknown_reason),
        ('engagement_effective_damage', partial.get('engagement_effective_damage'),
         row.overkill_complete and _finite(row.effective_observed()) and row.event_complete,
         '累计结算伤害', row.overkill_unknown_reason),
        ('engagement', partial.get('engagement_damage'),
         row.event_complete and row.engagement_damage is not None, '累计伤害',
         '使用已观察到的接敌伤害与时长'),
        ('engagement_healing', partial.get('engagement_healing'),
         row.event_complete and row.engagement_healing is not None, '累计治疗量',
         '使用已观察到的接敌治疗与时长'),
        ('shield', partial.get('observed_shield_absorbed'), row.shield is not None and row.event_complete,
         None, '仅统计已观察到的有效护盾'),
        ('mitigation', row.observed_mitigation_rate,
         not row.mitigation_unknown_reason, '累计结算伤害', row.mitigation_unknown_reason),
        ('skill_damage', row.observed_skill_damage,
         row.skill_split_complete, '累计结算伤害', row.skill_split_reason),
        ('skill_effective_damage', row.observed_skill_damage_effective,
         row.skill_split_effective_complete, '累计结算伤害',
         row.skill_split_reason or row.overkill_unknown_reason),
    )
    for key, value, complete, basis, reason in candidates:
        if key in coverage or (row.damage_scope_reason and key in ('boss_damage', 'recent_boss_damage')):
            continue
        share = partial.get(('engagement_damage' if key == 'engagement' else key) + '_unverified_share')
        if key == 'engagement_effective_damage' and share is None:
            share = partial.get('effective_damage_unverified_share')
        if key == 'shield':
            window = partial.get('shield_observation_window')
            if (isinstance(window, (tuple, list)) and len(window) == 2
                    and all(_finite(point) for point in window) and window[1] >= window[0]):
                start, end = window
                reason += f'，覆盖 {format_one_decimal(start)} 至 {format_one_decimal(end)} 游戏秒'
        if not complete and (_finite(value) or _finite(share)):
            coverage[key] = dict(failed_share=share, basis=basis,
                                 reason=reason or '使用已核实的部分')
    return _settled_coverage(row, coverage)


def _settled_coverage(row: PanelSourceRow, coverage: dict) -> dict:
    lane = row.settled_damage
    if lane is None:
        return coverage
    complete = lane.get('complete') is True
    reason = lane.get('reason') or '仅统计已核实的命中'
    typed = _effective_by_type(row)
    effective = _effective_damage(row)
    for key in ('effective_damage', 'recent_effective_damage', 'engagement_effective_damage',
                'effective_damage_by_type', 'engagement', 'skill_damage', 'skill_effective_damage', 'mitigation'):
        coverage.pop(key, None)

    def mark(key, share_key=None, *, basis=None, reason=reason):
        share = lane.get(share_key) if share_key else None
        if not _finite(share) or not 0 <= share <= 1:
            share = basis = None
        coverage[key] = dict(failed_share=share, basis=basis, reason=reason)

    if not complete:
        mark('effective_damage', 'unverified_share', basis='可对账的伤害命中数')
        mark('engagement_effective_damage', 'engagement_effective_damage_unverified_share', basis='可对账的伤害命中数')
    if not row.settled_recent_complete():
        mark('recent_effective_damage', 'recent_effective_damage_unverified_share', basis='近 5 秒可对账的伤害命中数')
    if lane.get('engagement_damage_complete', complete) is not True:
        mark('engagement', 'engagement_damage_unverified_share', basis='接敌区间内可对账的伤害命中数')
    if typed and (not complete or exceeds_native_damage(effective, sum(typed.values()))):
        mark('effective_damage_by_type')
    if lane.get('skill_split_complete') is not True:
        mark('skill_damage', 'skill_damage_unverified_share', basis='可对账的伤害命中数')
    if lane.get('skill_split_effective_complete') is not True:
        mark('skill_effective_damage', 'skill_effective_damage_unverified_share', basis='可对账的伤害命中数')
    if lane.get('mitigation_complete') is not True:
        mark('mitigation', 'mitigation_unverified_share', basis='可对账的伤害命中数',
             reason=lane.get('mitigation_reason') or reason)
    if row.damage is None and _finite(lane.get('observed_boss_damage')):
        if lane.get('observed_boss_damage_complete') is not True:
            mark('boss_damage')
        typed_nominal = _damage_by_type(row)
        if typed_nominal and (lane.get('observed_boss_damage_complete') is not True
                             or exceeds_native_damage(lane['observed_boss_damage'], sum(typed_nominal.values()))):
            mark('damage_by_type')
    if row.recent_damage is None and _finite(lane.get('observed_boss_recent_damage')):
        if lane.get('observed_boss_recent_damage_complete') is not True:
            mark('recent_boss_damage')
    return coverage


def _owner_detail(row: PanelSourceRow, *, rate_reason: str) -> dict:
    if row.event_complete:
        event_reason = ''
    elif rate_reason:
        event_reason = rate_reason
    elif _finite(row.event_coverage_end):
        event_reason = ('事件仅覆盖到 ' + format_one_decimal(row.event_coverage_end)
                        + ' 游戏秒，之后未知')
    else:
        event_reason = '事件观察未建立覆盖'
    detail = {'damage_by_type': dict(_damage_by_type(row)),
            'damage': row.total(Category.DAMAGE), 'healing': row.healing,
            'damage_scope_reason': row.damage_scope_reason,
            'shield_absorbed': row.total(Category.SHIELD),
            'overkill_damage': row.overkill,
            'overkill_damage_by_type': (dict(row.overkill_by_type)
                                        if row.overkill_type_complete else None),
            'overkill_type_complete': row.overkill_type_complete,
            'overkill_coverage_complete': row.overkill_complete,
            'overkill_unknown_reason': row.overkill_unknown_reason,
            'effective_damage': _effective_damage(row),
            'effective_damage_by_type': _effective_by_type(row),
            'effective_engagement_damage': row.effective_observed(),
            'skill_damage': row.skill_damage,
            'non_skill_damage': row.non_skill_damage,
            'skill_damage_effective': row.skill_damage_effective,
            'non_skill_damage_effective': row.non_skill_damage_effective,
            'observed_skill_damage': row.observed_skill_damage,
            'observed_non_skill_damage': row.observed_non_skill_damage,
            'observed_skill_damage_effective': row.observed_skill_damage_effective,
            'observed_non_skill_damage_effective': row.observed_non_skill_damage_effective,
            'skill_split_complete': row.skill_split_complete,
            'skill_split_effective_complete': row.skill_split_effective_complete,
            'skill_split_reason': row.damage_scope_reason or row.skill_split_reason,
            'skill_active_seconds': row.skill_active_seconds,
            'skill_coverage': row.skill_coverage(),
            'skill_coverage_complete': row.skill_coverage_complete,
            'skill_window_count': row.skill_window_count,
            'skill_seen': row.skill_seen,
            'recent_overkill_damage': row.recent_overkill,
            'presence_seconds': row.presence_seconds,
            'presence_complete': row.presence_complete,
            'burst_damage': row.burst_damage,
            'burst_overkill': row.burst_overkill,
            'burst_settlement': row.burst_settlement,
            'burst_complete': row.burst_complete,
            'burst_count': row.burst_count,
            'burst_unknown_reason': row.burst_unknown_reason,
            'event_coverage_complete': row.event_complete,
            'event_reason': event_reason,
            'active_damage': row.active_damage,
            'active_healing': row.active_healing,
            'observed_damage': row.observed_damage,
            'observed_healing': row.observed_healing,
            'coverage': _coverage(row),
            'sources': list(row.sources)}
    lane = row.settled_damage
    if lane is not None:
        detail['settled_damage'] = dict(lane)
        detail['engagement_damage'] = row.engagement(Category.DAMAGE)
        for suffix in ('', '_effective'):
            complete_key = 'skill_split' + suffix + '_complete'
            split = lane.get(complete_key) is True
            detail[complete_key] = split
            for kind in ('skill', 'non_skill'):
                key = kind + '_damage' + suffix
                observed = detail['observed_' + key] = lane.get('observed_' + key)
                detail[key] = observed if split else None
            if not split:
                detail['skill_split' + suffix + '_reason'] = lane.get('skill_split_reason') or lane.get('reason')
    return detail


def render_panel(config: PanelConfig,
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True,
                 team_reason: str = '',
                 team_note: str = '',
                 event: EventAvailability | None = None,
                 number_units: str = DEFAULT_NUMBER_UNITS) -> PanelView:
    """Build one panel view; unknown metrics are omitted, never zero-filled."""
    rows = list(rows)
    category, ranking = config.category, config.ranking
    effective = config.effective_only and category is Category.DAMAGE
    recent_ranking = ranking is Ranking.RECENT or (
        ranking is Ranking.DPS and config.rate_basis is RateBasis.RECENT_5S)
    engagement_ranking = ranking is Ranking.DPS and config.rate_basis is RateBasis.ENGAGEMENT
    full_cycle_ranking = ranking is Ranking.DPS and config.rate_basis is RateBasis.FULL_CYCLE
    rate_supported = category is not Category.SHIELD
    rate_label = RATE_LABELS[config.rate_basis] if rate_supported else ''
    if rate_supported:
        rate_available, rate_reason, rate_hint, rate_action, rate_action_label = rate_notice(event)
    else:
        rate_available, rate_reason, rate_hint, rate_action, rate_action_label = (
            True, '', '', '', '')
    if rate_available and event is not None and any(not row.event_complete for row in rows):
        rate_hint = '部分干员事件覆盖不完整；未覆盖区间保持未知，不补零。'

    def metric_value(row: PanelSourceRow) -> float | None:
        if ranking is Ranking.DPS:
            return row.rate(category, config.rate_basis, effective=effective)
        if ranking is Ranking.TOTAL:
            return _effective_damage(row) if effective else row.total(category)
        return row.effective_recent() if effective else row.recent(category)

    def total_value(row: PanelSourceRow) -> float | None:
        return _effective_damage(row) if effective else row.total(category)

    total_gap = ('shield' if category is Category.SHIELD else
                 'recent_effective_damage' if effective and ranking is Ranking.RECENT else
                 'effective_damage' if effective else
                 'recent_boss_damage' if config.boss_scope and ranking is Ranking.RECENT else
                 'boss_damage' if config.boss_scope else None)
    rate_gap = {
        RateBasis.RECENT_5S: ('recent_effective_damage' if effective else
                             'recent_boss_damage' if config.boss_scope else None),
        RateBasis.ENGAGEMENT: ('engagement_effective_damage' if effective else
                               'engagement' if category is Category.DAMAGE else 'engagement_healing'),
        RateBasis.FULL_CYCLE: ('effective_damage' if effective else
                              'boss_damage' if config.boss_scope else None),
    }[config.rate_basis] if rate_supported else None
    ranking_gap = rate_gap if ranking is Ranking.DPS else total_gap

    def metric_complete(row: PanelSourceRow, basis: RateBasis | None = None,
                        *, recent: bool = False) -> bool:
        if category is Category.DAMAGE and basis is RateBasis.ENGAGEMENT and row.settled_damage is not None:
            no_events = (row.settled_damage.get('complete') is True
                         and row.settled_damage.get('observed_hits') == 0)
            if effective:
                return row.settled_damage.get('complete') is True and (row.effective_observed() is not None or no_events)
            return (row.settled_damage.get('engagement_damage_complete', row.settled_damage.get('complete')) is True
                    and (row.engagement(category) is not None or no_events))
        if effective and row.settled_damage is not None:
            if recent or basis is RateBasis.RECENT_5S:
                return row.settled_recent_complete() and row.effective_recent() is not None
            if row.settled_damage.get('complete') is not True:
                return False
            if basis is RateBasis.FULL_CYCLE:
                return row.full_cycle(category, effective=True) is not None
            return _effective_damage(row) is not None
        if config.boss_scope and row.settled_damage is not None and category is Category.DAMAGE:
            if (recent or basis is RateBasis.RECENT_5S) and row.recent_damage is None:
                return row.settled_damage.get('observed_boss_recent_damage_complete') is True
            if not recent and basis is not RateBasis.RECENT_5S and row.damage is None:
                return (row.settled_damage.get('observed_boss_damage_complete') is True
                        and (basis is not RateBasis.FULL_CYCLE or row.presence_complete))
        if not row.source_complete:
            return False
        if recent or basis is RateBasis.RECENT_5S:
            complete = row.event_complete if category is Category.SHIELD else row.counter_complete
            return complete and _valid_window(row.recent_window(category))
        if basis is RateBasis.ENGAGEMENT:
            return rate_available and row.event_complete and (
                not effective or not row.partial_damage or row.overkill_complete)
        if category is Category.SHIELD:
            return row.event_complete and _finite(row.shield)
        if effective and not (_finite(row.effective_damage) or row.overkill_complete):
            return False
        return row.counter_complete and (basis is not RateBasis.FULL_CYCLE or row.presence_complete)

    def rate_info(row: PanelSourceRow) -> tuple[float | None, str, str, str]:
        if not rate_supported:
            return None, 'unsupported', '', ''
        rate = metric_value(row) if ranking is Ranking.DPS else row.rate(
            category, config.rate_basis, effective=effective)
        if not _finite(rate):
            rate = None
        state, text, note = (
            ('unsupported', '', '') if not rate_supported else
            _rate_display(row, category, rate, rate_available,
                          engagement=config.rate_basis is RateBasis.ENGAGEMENT,
                          full_cycle=config.rate_basis is RateBasis.FULL_CYCLE,
                          number_units=number_units))
        if rate is None and category is Category.DAMAGE:
            if effective or config.rate_basis is RateBasis.ENGAGEMENT:
                note = (row.settled_damage.get('reason') if row.settled_damage is not None else None) or row.damage_scope_reason or (
                    row.partial_damage.get('recent_damage_scope_reason')
                    if effective and config.rate_basis is RateBasis.RECENT_5S else None) or note
        return rate, state, text, note

    values: dict[str, float] = {}
    complete: dict[str, bool] = {}
    row_by_owner: dict[str, PanelSourceRow] = {}
    rates: dict[str, tuple[float | None, str, str, str]] = {}
    totals = []
    footer_rates = []
    certified_totals = 0
    certified_rates = 0
    rate_windows = set()
    ranking_basis = config.rate_basis if ranking is Ranking.DPS else None
    coverage_by_owner = {row.owner_id: _coverage(row) for row in rows}
    recent_windows = {}
    has_fallback_value = False
    for row in rows:
        value = metric_value(row)
        row_by_owner[row.owner_id] = row
        rates[row.owner_id] = rate_info(row)
        rate, state, _, _ = rates[row.owner_id]
        if config.boss_scope and engagement_ranking and state == 'no_engagement':
            values[row.owner_id] = 0.0
            complete[row.owner_id] = metric_complete(row, ranking_basis)
        elif _finite(value):
            values[row.owner_id] = float(value)
            complete[row.owner_id] = metric_complete(row, ranking_basis, recent=ranking is Ranking.RECENT)
        coverage = coverage_by_owner[row.owner_id]
        if row.owner_id in complete and ranking_gap in coverage:
            complete[row.owner_id] = False
            has_fallback_value = has_fallback_value or value is not None
        if recent_ranking:
            recent_windows[row.owner_id] = row.recent_window(category)
            if effective and row.settled_damage is not None:
                recent_windows[row.owner_id] = row.effective_recent_window()
            elif config.boss_scope and row.settled_damage is not None and row.recent_damage is None:
                recent_windows[row.owner_id] = row.settled_damage.get('observed_boss_recent_window')
            elif ranking_gap in coverage:
                fallback = row.settled_damage if row.settled_damage is not None else row.partial_damage
                window_key = ('recent_effective_damage_window' if effective else 'observed_boss_recent_window')
                recent_windows[row.owner_id] = fallback.get(window_key)

        # The footer aggregates the displayed columns independently of sorting.
        column_total = value if ranking is Ranking.RECENT else total_value(row)
        total_complete = metric_complete(row, recent=ranking is Ranking.RECENT) and total_gap not in coverage
        if (_finite(column_total) and column_total >= 0
                and (total_complete or total_gap in coverage)):
            totals.append(column_total)
            certified_totals += int(total_complete)
        if rate_supported:
            rate_complete = metric_complete(row, config.rate_basis) and rate_gap not in coverage
            if rate is not None and rate >= 0 and (rate_complete or rate_gap in coverage):
                footer_rates.append(rate)
                certified_rates += int(rate_complete)
                if config.rate_basis is RateBasis.RECENT_5S:
                    window = row.recent_window(category)
                    if effective and row.settled_damage is not None:
                        window = row.effective_recent_window()
                    elif config.boss_scope and row.settled_damage is not None and row.recent_damage is None:
                        window = row.settled_damage.get('observed_boss_recent_window')
                    elif not rate_complete:
                        fallback = row.settled_damage if row.settled_damage is not None else row.partial_damage
                        window_key = ('recent_effective_damage_window' if effective else 'observed_boss_recent_window')
                        window = fallback.get(window_key)
                    if _valid_window(window):
                        rate_windows.add(tuple(window))
            elif state == 'no_engagement' and rate_complete:
                footer_rates.append(0.0)
                certified_rates += 1

    displayed = {owner: value for owner, value in values.items() if value > 0}
    known_values = {
        owner: value for owner, value in values.items() if value >= 0 and (
            complete[owner] or (row_by_owner[owner].source_complete
            or category is Category.DAMAGE and row_by_owner[owner].settled_damage is not None
            and (effective or engagement_ranking or config.boss_scope))
            and ranking_gap in coverage_by_owner[owner]
            and (not recent_ranking or _valid_window(recent_windows[owner])))
    }
    windows = ({tuple(recent_windows[owner]) for owner in known_values}
               if recent_ranking else set())
    footer_total = sum(totals) if totals and (ranking is not Ranking.RECENT or len(windows) <= 1) else None
    footer_rate = sum(footer_rates) if footer_rates and len(rate_windows) <= 1 else None
    footer_total = footer_total if _finite(footer_total) else None
    footer_rate = footer_rate if _finite(footer_rate) else None
    known_total = sum(known_values.values())
    denominator = known_total if _finite(known_total) and known_total > 0 and len(windows) <= 1 else None
    missing_rows = len(known_values) != len(rows) or not all(complete.values())
    share_known = denominator is not None and team_complete and not missing_rows
    share_scope = ('team' if share_known else 'known' if denominator is not None else 'unavailable')
    reasons = [team_reason] if team_reason and not share_known else []
    if len(windows) > 1:
        reasons.append('近期窗口不一致，无法计算占比')
    elif missing_rows:
        if recent_ranking:
            reasons.append('部分干员的近期数值或窗口未完整观察')
        elif engagement_ranking:
            reasons.append('部分干员的接敌事件未完整观察')
        elif full_cycle_ranking:
            reasons.append('部分干员的总量或在场时间未完整观察')
        elif effective:
            reasons.append(DAMAGE_SCOPE_MISMATCH if any(row.damage_scope_reason for row in rows)
                           else '有效伤害覆盖不完整，按已核实部分计算' if has_fallback_value
                           else '部分干员的有效伤害未知')
        else:
            reasons.append('部分干员的统计值未知或不完整')
    if not team_complete and not reasons:
        reasons.append('队伍数据不完整')
    if denominator is None and not reasons:
        reasons.append('暂无可计算占比的输出')
    share_reason = ' · '.join(reasons)
    max_value = max(displayed.values(), default=0.0)

    metric_label = (('有效伤害 · DPS' if effective else CATEGORY_LABELS[category] + ' · DPS')
                    if ranking is Ranking.DPS else
                    '有效伤害 · ' + ranking_label(category, ranking) if effective else
                    CATEGORY_LABELS[category] + ' · ' + ranking_label(category, ranking))
    if config.boss_scope:
        metric_label = '仅首领 · ' + metric_label

    details = {owner: _owner_detail(row, rate_reason=rate_reason)
               for owner, row in row_by_owner.items()}
    entries: list[PanelEntry] = []
    for owner, value in displayed.items():
        row = row_by_owner[owner]
        segment_ranking = Ranking.TOTAL if ranking is Ranking.DPS else ranking
        segments, segments_complete = (
            _segments(row, segment_ranking, effective)
            if category is Category.DAMAGE and not (effective and ranking is Ranking.RECENT)
            else ((), False))
        share = value / denominator if denominator is not None and owner in known_values else None
        rate, rate_state, rate_text, rate_note = rates[owner]
        cumulative = value if ranking is Ranking.TOTAL else total_value(row)
        cumulative_display = format_compact(cumulative, number_units)
        shown = format_compact(value, number_units) if ranking is Ranking.RECENT else cumulative_display
        entries.append(PanelEntry(
            owner_id=owner, name=row.name, value=value,
            bar_ratio=(value / max_value) if max_value > 0 else 0.0,
            share=share, share_display=format_percent(share),
            display_value=shown,
            exact_value=f'{value!r}',
            rate=rate, rate_display=format_compact(rate, number_units), rate_label=rate_label,
            present=row.present, portrait_muted=row.present is False,
            incomplete=not complete.get(owner, False),
            segments=segments, segments_complete=segments_complete,
            detail=details[owner],
            rate_state=rate_state, rate_text=rate_text, rate_note=rate_note,
            cumulative_display_value=cumulative_display,
            coverage={key: gap for key, gap in coverage_by_owner[owner].items()
                      if key in (total_gap, rate_gap)}))
    entries.sort(key=lambda e: (-e.value, e.name, e.owner_id))
    return PanelView(config=config, title=config.display_title, entries=entries,
                     total=sum(displayed.values()), denominator=denominator,
                     share_known=share_known, share_reason=share_reason,
                     rate_supported=rate_supported, rate_label=rate_label,
                     metric_label=metric_label, share_note=team_note,
                     rate_available=rate_available, rate_reason=rate_reason,
                     rate_hint=rate_hint, rate_action=rate_action,
                     rate_action_label=rate_action_label, details_by_owner=details,
                     share_scope=share_scope,
                     footer_total=footer_total, footer_rate=footer_rate,
                     footer_total_display=format_compact(footer_total, number_units),
                     footer_rate_display=f'{format_compact(footer_rate, number_units)}/s' if footer_rate is not None else '—',
                     footer_total_complete=(footer_total is not None and team_complete
                                            and certified_totals == len(rows)),
                     footer_rate_complete=(footer_rate is not None and team_complete
                                           and certified_rates == len(rows)))


def render_board(configs: Iterable[PanelConfig],
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True,
                 team_reason: str = '',
                 team_note: str = '',
                 number_units: str = DEFAULT_NUMBER_UNITS) -> list[PanelView]:
    rows = list(rows)
    return [render_panel(config, rows, team_complete=team_complete,
                         team_reason=team_reason, team_note=team_note,
                         number_units=number_units)
            for config in configs]
