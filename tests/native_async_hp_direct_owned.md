# Controlled actual-DLL direct HP functionality

Run only against a local build in new self-owned child processes:

```powershell
python tests/native_async_hp_direct_owned.py --binary <candidate.dll> --source-root <same-source-root> --receipt runtime/async-evidence/hp-direct.json
```

Reuses the normal productionHPplan generator/finalizer and complete contract from
the earlier I/O binding fixture. No Game PID, network, installed application or
new DLL export is involved. The child only owns fixed buffers and calls existing
Integer/Nested functions. `call-owned-hp` gains a literalbool `nested` selector;
it still accepts no caller-supplied address and permits only1..4calls.

The test first invokes a warm callback withoutmark. The next hostmark and real
callback supply the publicationfence. Current actual predicate makes cumulative
HPdirectE2/L1 and pubLfallback1, argsProxy0, rawfrontier4. A realNested parent
causes its HPchild to use fullcallbacks: parentE/L1, HPfirst2rawfallback, then
normalpubE direct/Lfallback, argsProxy2 and rawfrontier6. No rawpremerge or dropped
callbacks are used. All integer ABI results remain actor+649 or2actor+649.

Wrongroot/phase failures exist only in directcallbacks; ordinarycallbacks remain
safe. If direct silently disables, a mark succeeds and the negative test fails.
The already-approved constructor regression similarly runs only in directcallback:
old code can brand a public-constructed empty payload and publishes; fixed code
must keep its brand invalid and reject the caught structuralgap. This is a local
software regression, not an investigation of another process.

Pre-fix851b47f DLL SHA b6562399bcafcdd7f9d1724054194e13021ff7e7b7d7450117c0c4119e4438c7
passed ordinary/nested/explicitfault functionality but FAILED the constructor
regression exactly as intended: mark succeeded/failure0 where rejection required.
Full original FAIL receipt remains under runtime/direct-dll-prefixed. It is not a
finalgatePASS. The final2243fd2-or-later DLL rerun remains pending.

These are functionality, raw-lifecycle and statistic-fact consistency checks.
No throughput, FIFOcapacity, FPS or complete Game metadata acceptance is claimed.
