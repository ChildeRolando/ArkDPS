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

The confirmed product direction is a free, source-available community meter prioritizing trustworthy statistics, reliable connection, glanceable live contribution and then deeper analysis. Planned scope adds client-counter healing, shields actually absorbed and attributed to the shield provider, separate engagement DPS/HPS windows (each event extends its metric's active interval by 1.5 game seconds), the most recent 100 battles including practice/failure/exit, and automatic new-battle switching. Damage includes overkill with no separate overkill metric. Bars scale linearly to the highest operator; summons are attributed to their summoner. Retired operators remain in totals and history but are hidden in the live list; their later attributed output still counts. Full event-history recovery remains under investigation; see docs/design-interview.md and docs/reconnect-research.md. These plans do not expand the validated Demo's capabilities.

## Operating Context
MuMu 12, official Android game 2.7.91, Root enabled. The game is operated by the user.

## Capabilities and Constraints
Use cumulative character statistics and game logic time. Keep unavailable, stale, and historical observations distinct. Rolling DPS is estimated from samples and requires an observed window. Do not equate the global damage field with the sum of character fields. No gameplay writes or injected hooks.

## Evidence on Hand
Scoped underlying Demo acceptance passed on Android 2.7.91: five isolated Yato hits reconciled against enemy HP, one overkill finishing hit, cumulative preservation after redeployment, and one same-practice restart with no carried Yato damage. Twenty-two regression tests passed. The updated window reads the new paused battle (0.80 game seconds, NPC Provence 373). Totals include overkill; special skill attribution remains unvalidated. See VALIDATION.json and acceptance/evidence-summary.json for exact scope, raw provenance and known capture gaps.
