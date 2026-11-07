# Product
<!-- impeccable:product-schema 1 -->

## Platform
Windows native utility (Tk); Android game runs in MuMu 12.

## Stack
Delegated by the user: Python standard-library Tk UI and a read-only Android memory collector.

## Users
The user plays Arknights and wants to compare operators' actual damage during a battle.

## Product Purpose
An independent always-on-top small window showing per-operator total damage and DPS.

The product design now includes simultaneous compact HUD and detailed GUI presentation. HUD windows are created using an Open in HUD button on GUI statistic panels; their lock/unlock controls are in the GUI. HUD category, ranking and rate settings remain linked to the source panel, while position, size and opacity are independent. Closing a HUD only closes that display; deleting its source panel closes associated HUDs without deleting battle records. Closing the GUI keeps existing HUDs running with tray access; explicit Quit stops the app. First launch creates one Damage/Totals/Recent DPS panel and no HUD; subsequent launches restore configuration, layout and HUD open state. Live panels remove zero-output entries and omit entries without reliable data without erasing recorded totals. Planned recent DPS/HPS uses actual observed duration when shorter than five game seconds. History supports metadata, screenshots and data export; advanced cross-battle charts are outside V1. See docs/v1-design.md for the consolidated confirmed design and unresolved capability gates. The Demo's current behavior is unchanged until product implementation.

The confirmed product direction is a free, source-available community meter prioritizing trustworthy statistics, reliable connection, glanceable live contribution and then deeper analysis. Planned scope adds client-counter healing, shields actually absorbed and attributed to the shield provider, separate engagement DPS/HPS windows (each event extends its metric's active interval by 1.5 game seconds; only elapsed time counts, clipped at battle end), the most recent 100 battles including practice/failure/exit, and automatic new-battle switching. Damage includes overkill with no separate overkill metric. Bars scale linearly to the highest operator using battle totals in the totals view and five-second output in the live view; summons are attributed to their summoner. Retired operators have gray portraits, remain in totals and history, and stay in the live list until their five-second output reaches zero. Later attributed output still counts; removing a row does not change the statistical denominator. V1 reconnection now targets complete current-battle cumulative totals, without requiring recovery of unrecorded event history. Damage/healing total recovery is demonstrated; complete absorbed-shield total recovery remains unverified. Recent rates restart from a reliable continuous observation window, and global totals must never be divided by partially observed engagement time. See docs/design-interview.md and docs/reconnect-research.md. These plans do not expand the validated Demo's capabilities.

## Operating Context
MuMu 12, official Android game 2.7.91, Root enabled. The game is operated by the user.

## Capabilities and Constraints
Use cumulative character statistics and game logic time. Keep unavailable, stale, and historical observations distinct. Rolling DPS is estimated from samples and requires an observed window. Do not equate the global damage field with the sum of character fields. No gameplay writes or injected hooks.

## Evidence on Hand
Scoped underlying Demo acceptance passed on Android 2.7.91: five isolated Yato hits reconciled against enemy HP, one overkill finishing hit, cumulative preservation after redeployment, and one same-practice restart with no carried Yato damage. Twenty-two regression tests passed. The updated window reads the new paused battle (0.80 game seconds, NPC Provence 373). Totals include overkill; special skill attribution remains unvalidated. See VALIDATION.json and acceptance/evidence-summary.json for exact scope, raw provenance and known capture gaps.
