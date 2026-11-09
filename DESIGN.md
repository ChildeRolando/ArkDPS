# Native meter

The user selected an independent always-on-top small window. Use familiar Windows title-bar, table, checkbox and button interactions; no new brand identity or decorative illustration.

Operate surface: connection/data status and game clock above independently configured statistic panels, with reconnect and export below. Initial size 960 by 700; minimum 440 by 500 scaled to font metrics. One Damage/Totals/Recent DPS panel at first launch, no HUD. Panels sit side by side when each has about 390 logical pixels, then stack with an outer scrollbar. Each list scrolls independently. Keep standard Windows buttons and read-only combo boxes.

Tokens: background #182127; raised surface #26343d; primary text #edf2f4; secondary text #b9d2de; button #33434d; selection #305569; warning #f1bc82. Microsoft YaHei UI 10 pt; list rows 56 logical pixels, scaled to font metrics; outside spacing 12–16 px. Dark surfaces reduce the contrast jump against the active game. Numeric columns align right. No animations or per-refresh row rebuilds. Reuse canvas items by statistical owner ID. Pointer hover holds row positions while values continue updating; leaving the list reapplies the model's exact ranking.

Offline operator portraits have a grayscale variant for retired operators; missing portraits use a name badge. Hover portraits for the full name and numeric areas for unrounded values. Click a portrait/row or use arrow keys to pin details by owner ID. Details remain pinned when a recent row disappears and clear on a new battle. See THIRD_PARTY.md and the portrait manifest for art provenance.

Unknown values display an em dash, never zero. Lost reads keep last observations with an explicit interrupted status. A paused game preserves DPS because game time has stopped.

HUD reuses the same list at a compact 380 by 360 logical pixels, minimum 350 by 230, scaled to font metrics. Keep a draggable header with GUI/Close actions, the source panel title and list, data status below and a resize grip. Configuration and long details stay in the GUI; hover numbers remain available when unlocked. The source panel owns lock and opacity controls (70–100%). Locked HUDs pass mouse input through. Each source opens one HUD. Until ARK-11 supplies the tray, closing the GUI with open HUDs minimizes it to preserve a taskbar recovery route for locked displays.
