# Stage B owned HP direct dispatch, 2026-11-27

Source frozen from main 4f33715069adfd258550a7d221188e2ba1194b18; exact file hashes in source-identity.json and run2/receipt.json. Same binary and same generated JS, ABBA order, count 2000, full scope, pub-every 1000. All actual business and dispatch count assertions pass. No game or installed process accessed.

| Arm | unchanged/s | damage/s | heal/s | MaxHP dirty/s |
| --- | ---: | ---: | ---: | ---: |
| A1 ordinary | 13973 | 4540 | 5736 | 6028 |
| B1 direct | 14220 | 4968 | 5886 | 5374 |
| B2 direct | 14053 | 4885 | 6191 | 6018 |
| A2 ordinary | 13180 | 4626 | 5843 | 6174 |

Each direct business branch: actual entry 2000, leave 1998, native issued packets and adopted requests 3998, publication fallback 2, ArgsProxy 0. Ordinary: issue/adopt 0, HP requests 4000, ArgsProxy 2000. Native C++ timing profiler disabled. HP endpoints, unknown=false, active=0, closed replay lease and actual publication fence checks pass. Control keeps sealed provider and original native helper functions.

Conclusion: direct dispatch is active but this fixture still does not establish 30000 setter/s capacity. No FIFO re-run or real FPS claim. IPC, hooks/trampoline and dirty callback native snapshots remain excluded.

First attempt: sandbox compiler returned empty error; escalated compiler succeeded. Initial executable completed JS assertions/output but aborted at QuickJS runtime GC because harness did not close its last owned lease before context disposal. Fixed with owned_js::end() before JS_FreeContext; run2 is the clean ABBA candidate. Original run executable/script remain locally under run/. Do not treat its exit as PASS.

Commands and SHA256 for the clean candidate are in run2/receipt.json; generated script and binary stay locally beside the receipt. Frozen harness source is under harness-source/. This is synthetic owned-memory evidence, not full worker acceptance or real FPS improvement.
