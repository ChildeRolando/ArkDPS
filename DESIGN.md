# Native meter

Operate surface: Windows native Tk controls, compact 本局统计 heading, visible connection/data status and game clock, independent statistic panels, direct 历史战绩/连接设置 navigation and a global skin selector. No simulated game controls or fixture data in the product. Initial size 960 by 700; minimum 440 by 500 scaled to font metrics. One Damage/Totals/Recent DPS panel at first launch, no HUD. Panels sit side by side when each has about 390 logical pixels, then stack with an outer scrollbar. Lists scroll independently.

## Skins

`ui_theme.py` owns presentation tokens and semantic ttk styles. Default 孤星 · LONE TRAIL follows the supplied offline prototype's mission-console direction: warm ivory instruments, ceramic-orange headings/focus, and dark orbital plotting screens. The orbital outlines are static decoration, not measured game trajectories. 原始界面 retains the original blue-gray/dark palette. Only these two skins ship; other prototype event skins are visual references, not implemented product options.

Skin selection is global across live, history, connection settings, HUD, tooltips and full-ranking PNG. The selector changes styles and existing canvas items, never recreates windows, resets models, changes history selection or rewrites statistics. HUD native input flags, geometry and opacity remain untouched. Persist the choice with existing layout configuration; schema and migration belong to [settings and layout](docs/settings-and-layout.md), not a separate preferences file.

Microsoft YaHei UI 10 pt; list rows 56 logical pixels scaled to font metrics; outside spacing 12–16 px. Numeric columns align right. No animations or per-refresh row rebuilds. Reuse canvas items by statistical owner ID. Pointer hover holds row positions while values continue updating; leaving the list reapplies the exact model ranking. Unknown values display an em dash, never zero. Lost reads retain observations with an explicit interrupted/disconnected status; game pause preserves DPS.

Offline operator portraits have a grayscale variant for retired operators; missing portraits use a name badge. Hover portraits for full names and numeric areas for unrounded values. Click a row or use arrow keys to pin a field report with cumulative values, independent engagement metrics, damage components, coverage and sources. Pins survive disappearance from recent ranking and clear on a new battle. See THIRD_PARTY.md for portrait provenance.

The original damage-type colors remain a V1 contract (red physical, blue arts, purple elemental, white true); both skins preserve them rather than silently redefine their meaning. These inherited colors are not a colorblind-safe categorical palette. Text legends and the exact-value component report provide a non-color reading path; theme text/selection contrast is tested at 4.5:1 or better.

## HUD and history

HUD reuses the same list at 380 by 360 logical pixels, minimum 350 by 230, scaled to font metrics. Its draggable header has GUI/Close actions; the footer has status and a resize grip. Configuration, long reports, lock and opacity controls stay with the source panel. Locked HUDs pass mouse input through. One HUD per source; closing it leaves collection running, deleting its source closes it. GUI close hides to the existing tray; if the tray is unavailable, preserve the taskbar recovery route.

History is an independent nonmodal window. Search stage/operator/date text and filter result/mode without altering the currently viewed immutable record, even if filtered out or aged out of the archive. Live panels/HUD follow the current battle. PNG freezes record, panel configuration and skin before the save dialog's nested event loop. JSON remains the unmodified BattleRecord contract.
