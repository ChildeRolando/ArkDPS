# Staged production HP QuickJS experiment

Run only against an existing verified native build cache. This builds a standalone
owned-memory executable, never a DLL and never opens another process.

```powershell
python tests/run_production_hp_quickjs.py --stage <existing-stage> --output runtime/hp-consumer --count 10000
```

`--source-root`, `--native-root`, and `--bridge-root` allow fixed-source A/B runs.
Every receipt hashes all business/bridge/native sources and the five cached
QuickJS objects. Run comparisons serially, with no concurrent pressure/build.

Included: production-generated HP plan and `compile_plan`, native capture over
owned bytes, the actual native OwnedReplayView and provider, production bridge
pointer/scalar/string methods and packed fused dispatch, argument mask, receiver
identity, actual adapter mode wrapper, and actual shared HP setter callbacks.
Fixtures supply metadata/native setup. A setup-sealed explicit fixture class
hierarchy supports strict replay. The clock is promoted to the real production NativePointer prototype; setup
metadata pointer prototypes inherit NativePointer so comparisons take the value
path, with a throwing-toString check to reject accidental BigInt(object) parsing.
No hybrid rest/apply read wrappers remain in the timed path. The source fixture
ptr factory still has a setup/replay branch. Input writes and the driver loop
remain in measured elapsed cost; do not interpret the rate as an exact production worker ceiling.

With `--full-scope`, the real first-cold fixture prepares metadata and a 40-Chess
plus Token world, then exports the production 64-field scope and 785-guard
publication plans. C++ publishes the actual CaptureConfigurations and binds the
exact retained configuration after every begin. The real source closed-prefix
and publication journal execute, including the returned publication fence.

`--queue-rate 30000 --pause-ms 500 --count 300000 --full-scope --pub-every 8571`
uses actual CaptureLanes with its bounded private queue and four 32MiB spill
banks. A paced independent producer captures real E/L snapshots; the consumer
replays serialized owned bytes with strict raw-ticket ordering. Repeat pause
0/300/500 serially. Requested duration, actual producer duration, queue failure,
service rate, bytes, age and 100ms backlog trend are reported separately. A
queue-full stop never counts as completing the requested ten seconds.

Native FFI entry counts and returned slots are measured; internal OwnedView
field read counts are not measured. GraphCheck may return proof without dynamic
slots but still validates all required owned fields. Excluded: IPC/native hook
trampoline, real game scheduling/FPS, and native E/L snapshots of the nested dirty
callback. Dirty business callbacks execute. Results remain
`NOT_FULL_WORKER_ACCEPTANCE`; this is an owned workload, not live acceptance.

To reproduce the pure-HP ownbatch negative control with one binary and source,
first generate/build the lite bundle using `--count 2000` (omit `--full-scope`).
Run its `production-hp.exe production-hp.js --disable-batch`, then the same
command without the flag. The flag removes both native register/read entrypoints
before source prepare, so source takes its original independent reads. Require
`batch_calls == 0` for control and nonzero calls plus two registered plans for
treatment; do not compare different native/bridge builds. Reverse order if a
small improvement needs confirmation. Frozen `predicate-profile/mini-*.json`
records the completed pair; no improvement was observed.

For a replacement HookPoint implementation, record its native entry calls
separately from underlying owned field reads. A legal unrequested slot may be
absent; actually requesting an absent slot must preserve native structural
poison and JS replay-gap. Do not count a fallback path as an active treatment.
