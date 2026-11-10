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
            reason = f'接敌数据不可用：事件观测未连接{detail}'
            hint = ('接敌 DPS/HPS 与活跃时间需要事件观测；可重试事件观测，'
                    '或打开连接设置检查 Frida 端点。')
            action, label = 'retry', '重试事件观测'
        elif event.status == 'gap':
            reason = f'接敌数据已中断：仅保留已覆盖区间{detail}'
            hint = '未覆盖区间保持未知且不补零；重试事件观测后新覆盖数据会自动显示。'
            action, label = 'retry', '重试事件观测'
        elif event.status == 'incompatible':
            reason = '接敌数据不可用：事件观测与当前游戏版本不兼容'
            hint = '请更新支持的游戏版本或检查连接设置后再重新定位。'
            action, label = 'settings', '打开连接设置'
        elif event.status in ('unavailable', 'stopped'):
            reason = ('接敌数据不可用：事件观测已关闭'
                      if event.status == 'unavailable'
                      else '接敌数据不可用：事件观测已停止')
            hint = '在连接设置中开启事件观测并重新定位后，接敌数据会自动恢复。'
            action, label = 'settings', '打开连接设置'
        elif event.status is None:
            reason = '接敌数据尚未可用：等待事件能力诊断'
            hint = '等待事件观测诊断；长时间无结果请打开连接设置检查 Frida 端点。'
            action, label = 'settings', '打开连接设置'
        else:
            reason = '接敌数据尚未可用：事件观测正在建立'
            hint = '等待同帧事件基线；长时间未就绪请打开连接设置检查 Frida 端点。'
            action, label = 'settings', '打开连接设置'
    else:
        if event.status == 'failed':
            reason = f'接敌数据不可用：该局事件观测未连接{detail}'
        elif event.status == 'gap':
            reason = f'接敌数据不完整：该局事件观测中途中断{detail}'
        else:
            reason = '接敌数据不可用：该局未开启事件观测'
        hint = '历史记录无法补采事件；开启事件观测后新战局会记录接敌数据。'
        action, label = 'settings', '打开连接设置'
    return False, reason, hint, action, label


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
    # Event-observed totals exist (0 is a reliable zero); None means that event
    # coverage never covered this row.
    observed_damage: float | None = None
    observed_healing: float | None = None

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
            observed_damage=row.get('observed_damage'),
            observed_healing=row.get('observed_healing'),
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
    # value | no_engagement | unknown | unavailable | unsupported
    rate_state: str = 'value'
    # Complete display text for the rate column: '123/s', '无接敌' or '—'.
    rate_text: str = ''
    rate_note: str = ''


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


def _finite(value: float | None) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


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


def _rate_display(row: PanelSourceRow, category: Category, rate: float | None,
                  available: bool, *, engagement: bool = True) -> tuple[str, str, str]:
    """Classify one row's rate column without inventing a value."""
    if _finite(rate):
        return 'value', f'{format_compact(rate)}/s', ''
    if not engagement:
        return 'unknown', '—', ''
    observed, active = row.observed(category), row.active(category)
    if available and row.event_complete and observed == 0 and active == 0:
        return 'no_engagement', '无接敌', '已覆盖区间内未观察到接敌事件（可靠零）'
    if available:
        return 'unknown', '—', '该干员事件覆盖不完整，接敌数据未知'
    return 'unavailable', '—', '接敌数据不可用：需要事件观测（不以 0 或墙钟替代）'


def detail_rates(rows: Iterable[PanelSourceRow],
                 event: EventAvailability | None = None) -> dict:
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
            raw = row.engagement(category)
            state, text, note = _rate_display(
                row, category, raw if _finite(raw) else None, available)
            kinds[kind] = {'state': state, 'text': text, 'note': note}
        result[row.owner_id] = kinds
    return result


def render_panel(config: PanelConfig,
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True,
                 event: EventAvailability | None = None) -> PanelView:
    """Build one panel view; unknown metrics are omitted, never zero-filled.

    ``team_complete=False`` means the caller knows some team members or sources
    are missing; shares are then reported as unknown instead of using a local
    subset as if it were the whole team.

    ``event`` describes the event-observation capability. When it is absent or
    degraded the engagement rate column reports a readable reason and recovery
    entry instead of silently rendering empty values. Data that was never
    observed stays unknown; it is never replaced by zero or a wall-clock rate.
    """
    rows = list(rows)
    category, ranking = config.category, config.ranking
    metric_label = (CATEGORY_LABELS[category] + ' · ' +
                    RANKING_LABELS[ranking])
    rate_supported = category is not Category.SHIELD
    rate_label = RATE_LABELS[config.rate_basis] if rate_supported else ''
    if rate_supported:
        rate_available, rate_reason, rate_hint, rate_action, rate_action_label = rate_notice(event)
    else:
        rate_available, rate_reason, rate_hint, rate_action, rate_action_label = (
            True, '', '', '', '')
    if rate_available and event is not None and any(not row.event_complete for row in rows):
        rate_hint = '部分干员事件覆盖不完整；未覆盖区间保持未知，不补零。'

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
                     if not window_known else '队伍数据不完整，无法计算全队同口径占比'))
    max_value = max(displayed.values(), default=0.0)

    entries: list[PanelEntry] = []
    for owner, value in displayed.items():
        row = row_by_owner[owner]
        segments, segments_complete = (_segments(row, ranking) if category is Category.DAMAGE
                                       else ((), False))
        share = (value / team_denominator
                 if share_known and team_denominator > 0 else None)
        rate = None
        rate_state, rate_text, rate_note = (
            ('unsupported', '', '') if not rate_supported else ('unknown', '—', ''))
        if rate_supported:
            rate = (row.recent(category) if config.rate_basis is RateBasis.RECENT_5S
                    else row.engagement(category))
            if not _finite(rate):
                rate = None
            rate_state, rate_text, rate_note = _rate_display(
                row, category, rate, rate_available,
                engagement=config.rate_basis is RateBasis.ENGAGEMENT)
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
                    'observed_damage': row.observed_damage,
                    'observed_healing': row.observed_healing,
                    'event_coverage_complete': row.event_complete,
                    'sources': list(row.sources)},
            rate_state=rate_state, rate_text=rate_text, rate_note=rate_note))
    entries.sort(key=lambda e: (-e.value, e.name, e.owner_id))
    return PanelView(config=config, title=config.display_title, entries=entries,
                     total=sum(displayed.values()), denominator=team_denominator,
                     share_known=share_known, share_reason=share_reason,
                     rate_supported=rate_supported, rate_label=rate_label,
                     metric_label=metric_label,
                     rate_available=rate_available, rate_reason=rate_reason,
                     rate_hint=rate_hint, rate_action=rate_action,
                     rate_action_label=rate_action_label)


def render_board(configs: Iterable[PanelConfig],
                 rows: Iterable[PanelSourceRow], *,
                 team_complete: bool = True) -> list[PanelView]:
    rows = list(rows)
    return [render_panel(config, rows, team_complete=team_complete)
            for config in configs]
