# Actual-engine HP workload

This fixture reuses the existing prepared four-player / 40-Chess world and actual shared HP callbacks. Its C++ entry includes `engine.cpp`; records enter `CaptureLanes`, then the production `drain_captures` core, including native-owned batch advancement and the same single-record prepare/finalize kernel. The internal `drain_captures_with_batch(false)` seam changes only dispatch selection. Production's zero-argument wrapper always selects batching; no host setting exists.

Build using an existing verified offline dependency cache:

```powershell
python tests/build_owned_bounded_replay.py --fixture production-hp --build-only --optimization O2 --stage <existing-QJS-stage> --minhook-header <MinHook.h> --output runtime/actual-engine-build --compiler C:/msys64/ucrt64/bin/g++.exe
python tests/run_production_hp_actual_engine.py --binary runtime/actual-engine-build/owned-batch-test.exe --output runtime/actual-engine-pairs --counts 500 2000
```

The generated embedded-source headers are unused link declarations. The fixture evaluates the actual bridge and source files from disk. MinHook and assembly entry symbols are unused stubs; no detour or external process is involved. The earlier standalone runner is included solely for its owned region/read/write and fixture helpers, never its old queue consumer or dispatcher.

Both arms prefill identical bounded inputs, then drain them. The receipt verifies exact E/L counts, final HP/MaxHP, event JSON SHA, unknown flags, publication watermarks, and actual batch/single counters. It is not paced FIFO or Game FPS acceptance.

The older isolated fixture inserted synthetic scope revision 101 into a Map without advancing the source revision owner. This adapter uses the real incremented source counter before registering the same graph, preserving the engine's retirement validation. The current prepared snapshot has 64 scope fields and **1604 publication fields / 802 guard pairs**; retain the actual count rather than trimming it to an older 785-guard description.
