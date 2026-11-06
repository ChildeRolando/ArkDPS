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

## Operating Context
MuMu 12, official Android game 2.7.91, Root enabled. The game is operated by the user.

## Capabilities and Constraints
Use cumulative character statistics and game logic time. Keep unavailable, stale, and historical observations distinct. Rolling DPS is estimated from samples and requires an observed window. Do not equate the global damage field with the sum of character fields. No gameplay writes or injected hooks.

## Evidence on Hand
Scoped underlying Demo acceptance passed on Android 2.7.91: five isolated Yato hits reconciled against enemy HP, one overkill finishing hit, cumulative preservation after redeployment, and one same-practice restart with no carried Yato damage. Twenty-two regression tests passed. The updated window reads the new paused battle (0.80 game seconds, NPC Provence 373). Totals include overkill; special skill attribution remains unvalidated. See VALIDATION.json and acceptance/evidence-summary.json for exact scope, raw provenance and known capture gaps.
