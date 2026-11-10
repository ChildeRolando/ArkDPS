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
    RECENT = 'recent'


class RateBasis(str, Enum):
    RECENT_5S = 'recent_5s'
    ENGAGEMENT = 'engagement'


CATEGORY_LABELS = {Category.DAMAGE: '伤害', Category.HEALING: '治疗',
                   Category.SHIELD: '有效护盾'}
RANKING_LABELS = {Ranking.TOTAL: '本局总量', Ranking.RECENT: '最近 5 秒'}
RATE_LABELS = {RateBasis.RECENT_5S: '近 5 秒', RateBasis.ENGAGEMENT: '接敌'}


def format_compact(value: float | None) -> str:
    """Integer display with 1/K/M units; calculation and sorting stay raw."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    sign = '-' if value < 0 else ''
    value = abs(value)
    rounded = round(value)
    if rounded < 1000:
        return f'{sign}{int(rounded)}'
    if value < 1_000_000:
        text = f'{value / 1000:.1f}'.rstrip('0').rstrip('.')
        return f'{sign}{text}K'
    text = f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.')
    return f'{sign}{text}M'


def format_percent(value: float | None) -> str:
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    return f'{value * 100:.1f}%'


@dataclass(frozen=True)
class PanelConfig:
    panel_id: str = 'default'
    category: Category = Category.DAMAGE
    ranking: Ranking = Ranking.TOTAL
    rate_basis: RateBasis = RateBasis.RECENT_5S
    title: str | None = None

    def __post_init__(self):
        if not self.panel_id:
            raise ValueError('panel_id is required')
        if self.category is Category.SHIELD and self.rate_basis is RateBasis.ENGAGEMENT:
            raise ValueError('护盾不使用接敌速率')

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        return f'{CATEGORY_LABELS[self.category]} · {RANKING_LABELS[self.ranking]}'

    def to_dict(self) -> dict:
        return {'panel_id': self.panel_id, 'category': self.category.value,
                'ranking': self.ranking.value, 'rate_basis': self.rate_basis.value,
                'title': self.title}

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
        return cls(panel_id=str(raw.get('panel_id') or 'default'),
                   category=category, ranking=ranking,
                   rate_basis=rate_basis, title=title)


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
    damage_by_type: Mapping[str, float] = field(default_factory=dict)
    counter_complete: bool = True
    event_complete: bool = True
    sources: tuple[str, ...] = ()
    # A window certifies that the full expected recent interval was observed.
    recent_damage_window: tuple[float, float] | None = None
    recent_healing_window: tuple[float, float] | None = None
    recent_shield_window: tuple[float, float] | None = None
    recent_damage_by_type: Mapping[str, float] = field(default_factory=dict)

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

    def recent_window(self, category: Category) -> tuple[float, float] | None:
        return {Category.DAMAGE: self.recent_damage_window,
                Category.HEALING: self.recent_healing_window,
                Category.SHIELD: self.recent_shield_window}[category]


def rows_from_statistics(result: Mapping) -> list[PanelSourceRow]:
    """Convert an adapter/Statistics result into panel rows without inventing data."""
    rows: list[PanelSourceRow] = []
    for owner, row in (result.get('rows') or {}).items():
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
            damage_by_type=row.get('damage_by_type') or {},
            recent_damage_by_type=row.get('recent_damage_by_type') or {},
            recent_damage_window=row.get('recent_damage_window'),
            recent_healing_window=row.get('recent_healing_window'),
            recent_shield_window=row.get('recent_shield_window'),
            counter_complete=bool(row.get('counter_snapshot_complete', True)),
            event_complete=bool(row.get('event_coverage_complete', True)),
            sources=tuple(row.get('sources') or ()),
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
    # Present even when shares are known: names sources excluded from the team.
    share_note: str = ''


def _finite(value: float | None) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


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


def non_team_note(statistics: Mapping) -> str:
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
            parts.append(f'{cid}（{label}，未归属输出：伤害 {format_compact(damage)}，'
                         f'治疗 {format_compact(healing)}）')
        elif not known:
            parts.append(f'{cid}（{label}，输出未知）')
        else:
            parts.append(f'{cid}（{label}，未观察到输出）')
    return '已排除非队伍来源：' + '；'.join(parts)


def team_status(statistics: Mapping) -> tuple[bool, str, str]:
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
    reason = ('' if not gaps else
              '队伍数据不完整，无法计算全队同口径占比（' + '；'.join(gaps) + '）')
    return (not gaps), reason, non_team_note(result)


def _metric_complete(row: PanelSourceRow, category: Category,
                     ranking: Ranking) -> bool:
    if category is Category.SHIELD:
        return row.event_complete
    if ranking is Ranking.RECENT:
        return row.counter_complete
    return row.counter_complete


def _segments(row: PanelSourceRow, ranking: Ranking) -> tuple[tuple[dict, ...], bool]:
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
        segments.append({'kind': kind, 'value': value,
                         'ratio': min(1.0, value / total),
                         'color': DAMAGE_COLORS[kind]})
    return tuple(segments), abs(known - total) <= max(0.01, total * 1e-6)


def render_panel(config: PanelConfig,
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True,
                 team_reason: str = '',
                 team_note: str = '') -> PanelView:
    """Build one panel view; unknown metrics are omitted, never zero-filled.

    ``team_complete=False`` means the caller knows some team members or sources
    are missing; shares are then reported as unknown instead of using a local
    subset as if it were the whole team. ``team_reason`` names the blocking
    sources, and ``team_note`` reports sources excluded from the team
    denominator (shown even when shares are known).
    """
    rows = list(rows)
    category, ranking = config.category, config.ranking
    metric_label = (CATEGORY_LABELS[category] + ' · ' +
                    RANKING_LABELS[ranking])
    rate_supported = category is not Category.SHIELD
    rate_label = RATE_LABELS[config.rate_basis] if rate_supported else ''

    values: dict[str, float] = {}
    complete: dict[str, bool] = {}
    row_by_owner: dict[str, PanelSourceRow] = {}
    for row in rows:
        value = row.total(category) if ranking is Ranking.TOTAL else row.recent(category)
        row_by_owner[row.owner_id] = row
        if _finite(value):
            values[row.owner_id] = float(value)
            complete[row.owner_id] = _metric_complete(row, category, ranking)

    # Recent views drop zero-output rows; totals keep retired cumulative rows.
    displayed = {owner: value for owner, value in values.items()
                 if (value > 0 if ranking is Ranking.RECENT else value > 0)}
    team_denominator = sum(values.values()) if values else 0.0
    # Any row whose selected metric is unknown, plus any caller-reported
    # missing team member, makes the whole-team denominator unreliable.
    any_unknown_row = any(
        not _finite(r.total(category) if ranking is Ranking.TOTAL else r.recent(category))
        for r in rows)
    windows = [r.recent_window(category) for r in rows] if ranking is Ranking.RECENT else []
    def valid_window(window):
        return (isinstance(window, (tuple, list)) and len(window) == 2
                and all(_finite(t) for t in window)
                and 0 < window[1] - window[0] <= 5)
    window_known = (ranking is Ranking.TOTAL or
                    (bool(windows) and all(valid_window(w) for w in windows)
                     and all(tuple(w) == tuple(windows[0]) for w in windows)))
    share_known = (bool(values) and team_complete and not any_unknown_row and window_known
                   and all(complete.values()) and team_denominator > 0)
    share_reason = ('' if share_known else
                    ('近期窗口不完整或不一致，无法计算全队同口径占比'
                     if not window_known else
                     team_reason or '队伍数据不完整，无法计算全队同口径占比'))
    max_value = max(displayed.values(), default=0.0)

    entries: list[PanelEntry] = []
    for owner, value in displayed.items():
        row = row_by_owner[owner]
        segments, segments_complete = (_segments(row, ranking) if category is Category.DAMAGE
                                       else ((), False))
        share = (value / team_denominator
                 if share_known and team_denominator > 0 else None)
        rate = None
        if rate_supported:
            rate = (row.recent(category) if config.rate_basis is RateBasis.RECENT_5S
                    else row.engagement(category))
            if not _finite(rate):
                rate = None
        entries.append(PanelEntry(
            owner_id=owner, name=row.name, value=value,
            bar_ratio=(value / max_value) if max_value > 0 else 0.0,
            share=share, share_display=format_percent(share),
            display_value=format_compact(value), exact_value=f'{value!r}',
            rate=rate, rate_display=format_compact(rate), rate_label=rate_label,
            present=row.present, portrait_muted=row.present is False,
            incomplete=not complete.get(owner, False),
            segments=segments, segments_complete=segments_complete,
            detail={'damage_by_type': dict(row.damage_by_type),
                    'damage': row.damage, 'healing': row.healing,
                    'shield_absorbed': row.shield,
                    'active_damage': row.active_damage,
                    'active_healing': row.active_healing,
                    'sources': list(row.sources)}))
    entries.sort(key=lambda e: (-e.value, e.name, e.owner_id))
    return PanelView(config=config, title=config.display_title, entries=entries,
                     total=sum(displayed.values()), denominator=team_denominator,
                     share_known=share_known, share_reason=share_reason,
                     rate_supported=rate_supported, rate_label=rate_label,
                     metric_label=metric_label, share_note=team_note)


def render_board(configs: Iterable[PanelConfig],
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True,
                 team_reason: str = '',
                 team_note: str = '') -> list[PanelView]:
    rows = list(rows)
    return [render_panel(config, rows, team_complete=team_complete,
                         team_reason=team_reason, team_note=team_note)
            for config in configs]
