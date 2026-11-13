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

    def total(self, category: Category) -> float | None:
        return {Category.DAMAGE: self.damage,
                Category.HEALING: self.healing,
                Category.SHIELD: self.shield}[category]

    def recent(self, category: Category) -> float | None:
        return {Category.DAMAGE: self.recent_damage,
                Category.HEALING: self.recent_healing,
                Category.SHIELD: self.recent_shield}[category]

    def engagement(self, category: Category) -> float | None:
        if category is Category.DAMAGE:
            return self.engagement_damage
        if category is Category.HEALING:
            return self.engagement_healing
        return None

    def active(self, category: Category) -> float | None:
        if category is Category.DAMAGE:
            return self.active_damage
        if category is Category.HEALING:
            return self.active_healing
        return None

    def effective_recent(self) -> float | None:
        """近期有效 DPS：近期速率减去窗口内过量量摊到每秒。"""
        window = self.recent_damage_window
        if (not _finite(self.recent_damage) or not _finite(self.recent_overkill)
                or not self.overkill_complete or not _valid_window(window)):
            return None
        duration = window[1] - window[0]
        return max(0.0, self.recent_damage - self.recent_overkill / duration)

    def effective_observed(self) -> float | None:
        """接敌有效 DPS：有效事件量除以活跃秒数，不是把量直接当成每秒。"""
        if (not _finite(self.observed_damage) or not _finite(self.overkill)
                or not _finite(self.active_damage) or self.active_damage <= 0
                or not self.overkill_complete):
            return None
        return max(0.0, self.observed_damage - self.overkill) / self.active_damage

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
        active = self.active(category)
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


def panel_statistics(config: PanelConfig, result: Mapping, *, history: bool = False) -> Mapping:
    """Select the panel's target scope before rendering or building details."""
    if not config.boss_scope:
        return result
    available = result.get('boss_damage_available') is True
    reason = result.get('boss_damage_reason') or ''
    if not available and not reason:
        reason = ('这局战绩未记录首领伤害' if history else
                  '首领伤害尚无可靠数据，需要事件观察与目标身份')
    rows = {}
    for owner, row in (result.get('rows') or {}).items():
        scoped = row.get('boss_damage')
        if not isinstance(scoped, Mapping):
            scoped = {}
        elif not available:
            # Preserve the observed boss-only skill split when its full
            # totals and ranking cannot be certified.
            scoped = {key: scoped.get(key) for key in (
                'observed_skill_damage', 'observed_non_skill_damage',
                'observed_skill_damage_effective', 'observed_non_skill_damage_effective',
            )}
            scoped['skill_split_reason'] = reason
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
            overkill=row.get('overkill_damage'),
            overkill_by_type=row.get('overkill_damage_by_type'),
            overkill_complete=bool(row.get('overkill_coverage_complete', False)),
            overkill_type_complete=bool(row.get('overkill_type_complete', False)),
            overkill_unknown_reason=row.get('overkill_unknown_reason'),
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
        rate = row.get('recent_damage')
        window = row.get('recent_damage_window')
        certified = (row.get('counter_snapshot_complete') is True
                     and _valid_window(window) and 0 <= window[0] < window[1] == game_time
                     and _finite(rate) and not isinstance(rate, bool) and rate >= 0)
        if certified and effective:
            excess = row.get('recent_overkill_damage')
            certified = (row.get('overkill_coverage_complete') is True
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


def _effective_damage(row: PanelSourceRow) -> float | None:
    if _finite(row.effective_damage):
        return float(row.effective_damage)
    if _finite(row.damage) and _finite(row.overkill):
        return max(0.0, float(row.damage) - float(row.overkill))
    return None


def _effective_by_type(row: PanelSourceRow) -> dict | None:
    if not row.overkill_type_complete or row.overkill_by_type is None:
        # An untyped overkill event still has one possible damage type when the
        # operator's entire observed output is that type. This is evidence from
        # the type totals, not a proportional guess.
        live_types = [kind for kind, value in row.damage_by_type.items()
                      if _finite(value) and value > 0]
        if len(live_types) == 1 and _finite(row.overkill):
            kind = live_types[0]
            return {kind: max(0.0, float(row.damage_by_type[kind]) - float(row.overkill))}
        return None
    return {kind: max(0.0, float(value) - float(row.overkill_by_type.get(kind, 0.0)))
            for kind, value in row.damage_by_type.items() if _finite(value)}

def _segments(row: PanelSourceRow, ranking: Ranking,
              effective: bool = False) -> tuple[tuple[dict, ...], bool]:
    if effective:
        total = _effective_damage(row)
        by_type = _effective_by_type(row)
        if by_type is None:
            by_type = {kind: max(0.0, float(value) -
                                 float((row.overkill_by_type or {}).get(kind, 0.0)))
                       for kind, value in row.damage_by_type.items() if _finite(value)}
    else:
        total = row.damage if ranking is Ranking.TOTAL else row.recent_damage
        by_type = row.damage_by_type if ranking is Ranking.TOTAL else row.recent_damage_by_type
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
    return tuple(segments), abs(known - total) <= max(0.01, total * 1e-6)


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
    if available and row.event_complete and observed == 0 and active == 0:
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
            kinds[kind] = {'state': state, 'text': text, 'note': note}
        result[row.owner_id] = kinds
    return result


def _owner_detail(row: PanelSourceRow, *, effective: bool, rate_reason: str) -> dict:
    if row.event_complete:
        event_reason = ''
    elif rate_reason:
        event_reason = rate_reason
    elif _finite(row.event_coverage_end):
        event_reason = ('事件仅覆盖到 ' + format_one_decimal(row.event_coverage_end)
                        + ' 游戏秒，之后未知')
    else:
        event_reason = '事件观察未建立覆盖'
    return {'damage_by_type': dict(row.damage_by_type),
            'damage': row.damage, 'healing': row.healing,
            'shield_absorbed': row.shield,
            'overkill_damage': row.overkill,
            'overkill_damage_by_type': (dict(row.overkill_by_type)
                                        if row.overkill_type_complete else None),
            'overkill_type_complete': row.overkill_type_complete,
            'overkill_coverage_complete': row.overkill_complete,
            'overkill_unknown_reason': row.overkill_unknown_reason,
            'effective_damage': _effective_damage(row) if effective else row.effective_damage,
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
            'skill_split_reason': row.skill_split_reason,
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
            'sources': list(row.sources)}


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

    def metric_complete(row: PanelSourceRow, basis: RateBasis | None = None,
                        *, recent: bool = False) -> bool:
        if not row.source_complete:
            return False
        if recent or basis is RateBasis.RECENT_5S:
            complete = row.event_complete if category is Category.SHIELD else row.counter_complete
            return complete and _valid_window(row.recent_window(category))
        if basis is RateBasis.ENGAGEMENT:
            return rate_available and row.event_complete
        if category is Category.SHIELD:
            return row.event_complete
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
        return rate, state, text, note

    values: dict[str, float] = {}
    complete: dict[str, bool] = {}
    row_by_owner: dict[str, PanelSourceRow] = {}
    rates: dict[str, tuple[float | None, str, str, str]] = {}
    totals = []
    footer_rates = []
    rate_windows = set()
    ranking_basis = config.rate_basis if ranking is Ranking.DPS else None
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

        # The footer aggregates the displayed columns independently of sorting.
        column_total = value if ranking is Ranking.RECENT else total_value(row)
        if (_finite(column_total) and column_total >= 0
                and metric_complete(row, recent=ranking is Ranking.RECENT)):
            totals.append(column_total)
        if rate_supported and metric_complete(row, config.rate_basis):
            if rate is not None and rate >= 0:
                footer_rates.append(rate)
                if config.rate_basis is RateBasis.RECENT_5S:
                    rate_windows.add(tuple(row.recent_window(category)))
            elif state == 'no_engagement':
                footer_rates.append(0.0)

    displayed = {owner: value for owner, value in values.items() if value > 0}
    known_values = {owner: value for owner, value in values.items()
                    if value >= 0 and complete[owner]}
    windows = ({tuple(row_by_owner[owner].recent_window(category)) for owner in known_values}
               if recent_ranking else set())
    footer_total = sum(totals) if totals and (ranking is not Ranking.RECENT or len(windows) <= 1) else None
    footer_rate = sum(footer_rates) if footer_rates and len(rate_windows) <= 1 else None
    footer_total = footer_total if _finite(footer_total) else None
    footer_rate = footer_rate if _finite(footer_rate) else None
    known_total = sum(known_values.values())
    denominator = known_total if _finite(known_total) and known_total > 0 and len(windows) <= 1 else None
    missing_rows = len(known_values) != len(rows)
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
            reasons.append('部分干员的有效伤害未知')
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

    details = {owner: _owner_detail(row, effective=effective, rate_reason=rate_reason)
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
            cumulative_display_value=cumulative_display))
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
                                            and len(totals) == len(rows)),
                     footer_rate_complete=(footer_rate is not None and team_complete
                                           and len(footer_rates) == len(rows)))


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
