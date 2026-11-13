"""Versioned GUI/HUD layout persistence and reachable-area restoration.

Version 1 is the detached :class:`panel_model.PanelCollection` document written
by earlier builds (panels only). Version 2 adds GUI window geometry, one
placement per HUD and the event-observation preference. Version 3 adds the
application theme and optional per-panel HUD styles. Older documents migrate in
memory and write version 3 on the next save.

A damaged or future-version file is never overwritten silently: it is moved to
``layout.json.corrupt-<timestamp>`` and the caller receives a warning plus the
usable default layout. Restored rectangles are clamped into a monitor work area
so a removed display or a changed DPI scale cannot leave a window unreachable.

Only configuration is persisted; battle numbers, history and live values never
enter this file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from panel_model import DEFAULT_NUMBER_UNITS, NUMBER_UNITS, PanelConfig

ROOT = Path(__file__).resolve().parent
LAYOUT_VERSION = 3
DEFAULT_THEME = 'lone-trail'
THEMES = (DEFAULT_THEME, 'people', 'babel')
DEFAULT_GUI = (960, 700)
DEFAULT_GUI_MINIMUM = (440, 500)
DEFAULT_HUD_MINIMUM = (350, 230)
# Coordinates are absolute virtual-desktop pixels and may be negative.
_COORDINATE_LIMIT = 100_000
_SIZE_LIMIT = 20_000

Rect = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass(frozen=True)
class GuiGeometry:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return {'x': self.x, 'y': self.y, 'width': self.width, 'height': self.height}

    @classmethod
    def from_dict(cls, raw: object) -> 'GuiGeometry':
        if not isinstance(raw, dict):
            raise ValueError('窗口几何必须是 JSON 对象')
        return cls(*_geometry_fields(raw, '窗口几何'))


@dataclass(frozen=True)
class HudPlacement:
    panel_id: str
    x: int
    y: int
    width: int
    height: int
    opacity: float = 0.9
    locked: bool = False

    def to_dict(self) -> dict:
        return {'panel_id': self.panel_id, 'x': self.x, 'y': self.y,
                'width': self.width, 'height': self.height,
                'opacity': round(self.opacity, 4), 'locked': bool(self.locked)}

    @classmethod
    def from_dict(cls, raw: object) -> 'HudPlacement':
        if not isinstance(raw, dict):
            raise ValueError('HUD 布局必须是 JSON 对象')
        panel_id = raw.get('panel_id')
        if not isinstance(panel_id, str) or not panel_id.strip():
            raise ValueError('HUD 布局缺少 panel_id')
        x, y, width, height = _geometry_fields(raw, 'HUD 布局')
        opacity = raw.get('opacity', 0.9)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
            raise ValueError('HUD 透明度必须是数字')
        if not 0 <= opacity <= 1.0:
            raise ValueError('HUD 透明度必须在 0 到 1.0 之间')
        locked = raw.get('locked', False)
        if not isinstance(locked, bool):
            raise ValueError('HUD 锁定状态必须是布尔值')
        return cls(panel_id=panel_id.strip(), x=x, y=y, width=width, height=height,
                   opacity=float(opacity), locked=locked)

    @property
    def geometry(self) -> GuiGeometry:
        return GuiGeometry(self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class HudStyle:
    """User-adjustable HUD dimensions in DIPs and overall size multiplier."""

    title_font: float = 20
    name_font: float = 15
    number_font: float = 15
    width: float = 484
    row_height: float = 46
    title_height: float = 44
    portrait_size: float = 38
    rank_width: float = 28
    portrait_width: float = 52
    name_width: float = 92
    bar_width: float = 158
    total_width: float = 78
    rate_width: float = 76
    share_width: float = 68
    row_gap: float = 1
    column_gap: float = 2
    padding: float = 4
    scale: float = 1.0

    def __post_init__(self):
        for field in fields(self):
            key = field.name
            value = getattr(self, key)
            if key.endswith('_font'):
                minimum, maximum = 6, 64
            elif key == 'scale':
                minimum, maximum = 0.5, 2.0
            elif key == 'width':
                minimum, maximum = 240, 2000
            elif key == 'row_height':
                minimum, maximum = 16, 160
            elif key == 'title_height':
                minimum, maximum = 20, 160
            elif key == 'portrait_size':
                minimum, maximum = 0, 128
            elif key.endswith('_width'):
                minimum, maximum = 0, 600
            else:
                minimum, maximum = 0, 48
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not minimum <= value <= maximum):
                raise ValueError(f'HUD 样式的 {key} 必须在 {minimum} 到 {maximum} 之间')

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> 'HudStyle':
        if not isinstance(raw, dict):
            raise ValueError('HUD 样式必须是 JSON 对象')
        if raw.keys() - {field.name for field in fields(cls)}:
            raise ValueError('HUD 样式含未知字段')
        return cls(**raw)


def _geometry_fields(raw: dict, label: str) -> tuple[int, int, int, int]:
    values = []
    for key in ('x', 'y', 'width', 'height'):
        value = raw.get(key)
        # bool is an int subclass; it is never a valid coordinate.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f'{label}的 {key} 必须是整数')
        values.append(value)
    x, y, width, height = values
    if not (-_COORDINATE_LIMIT <= x <= _COORDINATE_LIMIT and
            -_COORDINATE_LIMIT <= y <= _COORDINATE_LIMIT):
        raise ValueError(f'{label}坐标越界')
    if not (1 <= width <= _SIZE_LIMIT and 1 <= height <= _SIZE_LIMIT):
        raise ValueError(f'{label}尺寸越界')
    return x, y, width, height


@dataclass(frozen=True)
class Layout:
    panels: tuple[PanelConfig, ...]
    huds: tuple[HudPlacement, ...] = ()
    gui: GuiGeometry | None = None
    event_observation: bool = True
    theme: str = DEFAULT_THEME
    number_units: str = DEFAULT_NUMBER_UNITS
    hud_styles: tuple[tuple[str, HudStyle], ...] = ()

    def __post_init__(self):
        if self.theme not in THEMES:
            raise ValueError('未知皮肤')
        if not isinstance(self.number_units, str) or self.number_units not in NUMBER_UNITS:
            raise ValueError('未知数值单位')
        if not self.panels:
            raise ValueError('至少需要一个面板')
        ids = [panel.panel_id for panel in self.panels]
        if len(ids) != len(set(ids)):
            raise ValueError('面板 id 不能重复')
        hud_ids = [hud.panel_id for hud in self.huds]
        if len(hud_ids) != len(set(hud_ids)):
            raise ValueError('同一面板只能有一个 HUD')
        style_ids = [panel_id for panel_id, _ in self.hud_styles]
        if len(style_ids) != len(set(style_ids)) or set(style_ids) - set(ids):
            raise ValueError('HUD 样式必须对应唯一的源面板')

    def to_dict(self) -> dict:
        return {'version': LAYOUT_VERSION, 'theme': self.theme,
                'event_observation': bool(self.event_observation),
                'number_units': self.number_units,
                'gui': self.gui.to_dict() if self.gui else None,
                'panels': [panel.to_dict() for panel in self.panels],
                'huds': [hud.to_dict() for hud in self.huds],
                'hud_styles': {panel_id: style.to_dict() for panel_id, style in self.hud_styles}}

    @classmethod
    def default(cls) -> 'Layout':
        """First-launch layout: one Damage/Totals/Recent DPS panel, no HUD."""
        return cls(panels=(PanelConfig(),))


@dataclass
class LayoutLoadResult:
    layout: Layout
    ok: bool
    warning: str = ''
    migrated_from: int | None = None
    backup: Path | None = None


def _parse_panels(raw: object, warnings: list[str]) -> tuple[PanelConfig, ...]:
    if not isinstance(raw, list):
        raise ValueError('panels 必须是数组')
    panels: list[PanelConfig] = []
    seen: set[str] = set()
    skipped = 0
    for item in raw:
        try:
            panel = PanelConfig.from_dict(item)
        except ValueError:
            skipped += 1
            continue
        if panel.panel_id in seen:
            skipped += 1
            continue
        seen.add(panel.panel_id)
        panels.append(panel)
    if skipped:
        warnings.append(f'已跳过 {skipped} 个无效或重复的面板配置')
    return tuple(panels)


def _parse_huds(raw: object, panels: Sequence[PanelConfig],
                warnings: list[str]) -> tuple[HudPlacement, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        warnings.append('HUD 布局无效，已忽略')
        return ()
    known = {panel.panel_id for panel in panels}
    placements: dict[str, HudPlacement] = {}
    skipped = 0
    for item in raw:
        try:
            placement = HudPlacement.from_dict(item)
        except ValueError:
            skipped += 1
            continue
        if placement.panel_id not in known or placement.panel_id in placements:
            skipped += 1
            continue
        placements[placement.panel_id] = placement
    if skipped:
        warnings.append(f'已跳过 {skipped} 个无效 HUD 布局')
    return tuple(placements.values())


def _parse_hud_styles(raw: object, panels: Sequence[PanelConfig],
                      warnings: list[str]) -> tuple[tuple[str, HudStyle], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        warnings.append('HUD 样式无效，已忽略')
        return ()
    known = {panel.panel_id for panel in panels}
    styles = []
    skipped = 0
    for panel_id, style in raw.items():
        if panel_id not in known:
            skipped += 1
            continue
        try:
            styles.append((panel_id, HudStyle.from_dict(style)))
        except ValueError:
            skipped += 1
    if skipped:
        warnings.append(f'已跳过 {skipped} 个无效 HUD 样式')
    return tuple(styles)


def _parse_layout(raw: object) -> tuple[Layout, list[str], int | None]:
    """Return (layout, warnings, migrated_from); raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError('布局文件必须是 JSON 对象')
    version = raw.get('version')
    warnings: list[str] = []
    migrated_from = None
    if version is None:
        # A bare panel list was never a documented format, but accepting it
        # costs nothing and keeps an interrupted early write usable.
        if 'panels' not in raw:
            raise ValueError('布局文件缺少 version')
        version = 1
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError('布局版本必须是整数')
    if version == 1:
        migrated_from = 1
        panels = _parse_panels(raw.get('panels'), warnings)
        if not panels:
            raise ValueError('布局文件没有可用面板')
        warnings.append('已从旧版（仅面板）布局迁移')
        return Layout(panels=panels), warnings, migrated_from
    if version not in (2, LAYOUT_VERSION):
        raise ValueError(f'不支持的布局版本 {version}（当前 {LAYOUT_VERSION}）')
    if version == 2:
        migrated_from = 2
    panels = _parse_panels(raw.get('panels'), warnings)
    if not panels:
        raise ValueError('布局文件没有可用面板')
    event = raw.get('event_observation', True)
    if not isinstance(event, bool):
        warnings.append('事件观察开关无效，已使用默认开启')
        event = True
    gui = None
    if raw.get('gui') is not None:
        try:
            gui = GuiGeometry.from_dict(raw['gui'])
        except ValueError as exc:
            warnings.append(f'GUI 窗口几何无效，已使用默认位置：{exc}')
    huds = _parse_huds(raw.get('huds'), panels, warnings)
    hud_styles = _parse_hud_styles(raw.get('hud_styles'), panels, warnings)
    theme = raw.get('theme', DEFAULT_THEME)
    if theme not in THEMES:
        warnings.append('已保存的皮肤不可用，已切换为孤星')
        theme = DEFAULT_THEME
    number_units = raw.get('number_units', DEFAULT_NUMBER_UNITS)
    if not isinstance(number_units, str) or number_units not in NUMBER_UNITS:
        warnings.append('数值单位设置无效，已使用 K / M')
        number_units = DEFAULT_NUMBER_UNITS
    return Layout(panels=panels, huds=huds, gui=gui,
                  event_observation=event, theme=theme,
                  number_units=number_units, hud_styles=hud_styles), warnings, migrated_from


class LayoutStore:
    """Atomic JSON persistence; damaged files are backed up, never lost."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else ROOT / 'runtime' / 'layout.json'
        self.last_backup: Path | None = None
        self._save_error = None

    def _preserve_corrupt(self) -> Path | None:
        if not self.path.exists():
            return None
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        target = self.path.with_name(f'{self.path.name}.corrupt-{stamp}')
        try:
            self.path.replace(target)
            self.last_backup = target
            return target
        except OSError:
            return None

    def load(self) -> LayoutLoadResult:
        self._save_error = None
        if not self.path.exists():
            return LayoutLoadResult(Layout.default(), True)
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            layout, warnings, migrated = _parse_layout(raw)
        except (OSError, ValueError) as exc:
            backup = self._preserve_corrupt()
            note = f'；原文件已备份为 {backup.name}' if backup else ''
            if backup is None:
                self._save_error = '原文件备份失败，已禁止保存布局；请备份原文件后重启'
                note = '；' + self._save_error
            return LayoutLoadResult(Layout.default(), False,
                                    f'布局配置无法读取，已使用默认布局：{exc}{note}',
                                    backup=backup)
        return LayoutLoadResult(layout, True, '；'.join(warnings),
                                migrated_from=migrated)

    def save(self, layout: Layout) -> None:
        if self._save_error:
            raise OSError(self._save_error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(layout.to_dict(), ensure_ascii=False, indent=2) + '\n'
        fd, tmp = tempfile.mkstemp(prefix='.layout-', suffix='.json',
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def monitor_work_areas() -> list[Rect]:
    """Usable rectangles of every monitor, in virtual-screen coordinates."""
    if sys.platform != 'win32':
        return []
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    found: list[Rect] = []

    def visit(handle, _dc, _rect, _param):
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            work = info.work
            found.append((work.left, work.top, work.right, work.bottom))
        return True

    try:
        user.EnumDisplayMonitors(None, None, callback_type(visit), 0)
    except OSError:
        return []
    return found


def _intersection(rect: Rect, area: Rect) -> int:
    left, top = max(rect[0], area[0]), max(rect[1], area[1])
    right, bottom = min(rect[2], area[2]), min(rect[3], area[3])
    return max(0, right - left) * max(0, bottom - top)


def _distance(rect: Rect, area: Rect) -> int:
    x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    near_x = min(max(x, area[0]), area[2])
    near_y = min(max(y, area[1]), area[3])
    return (x - near_x) ** 2 + (y - near_y) ** 2


def clamp_geometry(geometry: GuiGeometry, work_areas: Iterable[Rect],
                   minimum: tuple[int, int] = DEFAULT_HUD_MINIMUM) -> GuiGeometry:
    """Return a rectangle fully inside one work area.

    The monitor with the largest overlap wins; if none overlaps, the nearest
    monitor is used. Width and height are reduced only when the saved window no
    longer fits, which covers a removed display or an increased DPI scale. With
    no monitor information the geometry is returned unchanged.
    """
    areas = [tuple(int(value) for value in area) for area in work_areas]
    areas = [area for area in areas if area[2] > area[0] and area[3] > area[1]]
    if not areas:
        return geometry
    width, height = geometry.width, geometry.height
    rect = (geometry.x, geometry.y, geometry.x + width, geometry.y + height)
    chosen = max(areas, key=lambda area: (_intersection(rect, area), -_distance(rect, area)))
    left, top, right, bottom = chosen
    width = max(min(width, right - left), min(minimum[0], right - left))
    height = max(min(height, bottom - top), min(minimum[1], bottom - top))
    x = min(max(geometry.x, left), right - width)
    y = min(max(geometry.y, top), bottom - height)
    return GuiGeometry(x, y, width, height)
