# D treatment + B direct inclusive profile, 2026-11-27

Frozen source: ../direct-hp-d-validation/source-identity.json (0dbc5ba). Existing binary: D run2 binary. 5000 setter/branch, full scope, pub-every8571. Owned-memory fixture only. Actual business callbacks, strict scopes, unknown/active/publication, native issued/adopt/direct/fallback and format1 assertions pass. Heal: raw edges10000, direct entry5000/leave4999, issued/adopt9999, publication fallback1; no premerge of raw facts. Output remains coalesced by existing business logic.

Independent phase counters sample1/128 (40 entry and40 leave). Every value is inclusive, overlapping, contains runtime wrapper/instrumentation overhead, and is not added to its children. Capture/view C++ totals are all-edge timers enabled by existing --sample-functions; native GraphCheck C++ sampling excludes sealed direct calls and cannot be treated as zero cost. JS NativeCall sampling observes actual routed native calls. Do not compare this instrumented throughput against uninstrumented ABBA.

Heal sampled microseconds:

| Function | Calls | Samples | Mean us |
| --- | ---: | ---: | ---: |
| __arkdpsReplayDispatch.entry | 5000 | 40 | 53.43 |
| __arkdpsReplayDispatch.leave | 5000 | 40 | 140.36 |
| __arkdpsPackedCaptureRecord.entry | 5000 | 40 | 2.79 |
| __arkdpsPackedCaptureRecord.leave | 5000 | 40 | 2.48 |
| __arkdpsReplayBegin.entry | 5000 | 40 | 4.52 |
| __arkdpsReplayBegin.leave | 5000 | 40 | 4.21 |
| __arkdpsReplayEnd.entry | 5000 | 40 | 1.61 |
| __arkdpsReplayEnd.leave | 5000 | 40 | 1.57 |
| __arkdpsPreparedHpObservation.entry | 5000 | 40 | 10.53 |
| __arkdpsPreparedHpObservation.leave | 4999 | 40 | 9.42 |
| applyHpEntry.entry | 5000 | 40 | 15.82 |
| applyHpLeave.leave | 5000 | 40 | 106.66 |
| scope.adapter.leave | 5000 | 40 | 83.69 |
| __arkdpsOwnedGraphPrimitives.leave | 5001 | 40 | 41.76 |
| nativeIO.__nativeOwnedGraphCheck.leave | 5002 | 40 | 7.1 |
| recordEnemyHp.leave | 5000 | 40 | 3.88 |
| native.prepareHp.outside | 9999 | 79 | 2.85 |

Scope.adapter (~84us) is within applyHpLeave (~107us), primitive (~42us) is within scope and native GraphCheck is within primitive. These point to remaining scope/validation and representation cost; they do not establish an exact CPU attribution. MaxHP dirty has a long-tail sampled leave; retain it instead of discarding the sample. Samples are sparse and the fixture is not live game load.

First profile sampled mixed Dispatch E/L with one counter, aliasing all 1/128 selected boundaries to entry. That raw result remains sampled.json and is not used for E/L comparison. Corrected result is phase-correct.json with separate phase labels and exact executed script SHA256. No production source files modified: runtime wrappers are in generated script; unmodified source hashes and binary hash in profile-identity.json, corrected script hash/command in phase-correct.json. Reproducer: tests/profile_production_hp_quickjs.py. No full worker/FIFO acceptance and no FPS improvement claim.
