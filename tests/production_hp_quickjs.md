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
hierarchy supports strict replay. A few setup-created fixture pointers (including
the clock) route reads through the production pointer methods during consumption.
This interoperability, input writes, and driver loop remain in measured elapsed
cost; do not interpret the rate as an exact production worker ceiling.

Excluded: complete live scope/publication graph, journal materialization, actual
bounded FIFO/IPC, and native E/L snapshots for the nested dirty callback. Dirty
business callbacks themselves execute. These missing layers must be exercised
before declaring the 30k setter/s + 300/500ms pause requirement passed. The report
explicitly remains `NOT_FULL_WORKER_ACCEPTANCE` and never measures game FPS.

Branches check unchanged emits none, every damage change emits one HP observation,
and a monotone heal run coalesces to two records preserving its actual endpoints.
All fused dispatches require complete=true and the same leave receiver; swallowed
structural failures cannot yield a successful receipt. Native read/scalar/point
counts and capture/view-initialization aggregates help identify the next bottleneck.
