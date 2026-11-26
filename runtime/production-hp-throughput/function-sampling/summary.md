# Inclusive function sampling, source6e934a4 old pointer path

5000 setters per business branch, each function independently sampled1/128.
Source snapshot SHA is in profile-provenance.json; run_sampling.py recreates it
from fixed git objects. Native/bridge/binary source bindings are in receipt.json.
The frozen source has the old HPbatch capability; this provider has no registry,
so measured HPcalls use original independent pointer reads, not HookPoint.

Heal means in microseconds per sampled call:
HPentry46.96; HPleave118.37; withCapturedMode.entry43.71/leave115.34;
scope.adapter75.33; entityId13.84; time8.72; enemyMaxHp11.10;
recordEnemyHp4.04; C++NativeGraphCheck4.03 (40samples/5002calls).
Whole setter199.2us (5019/s). Changed damage scope67.03us; dirty76.47us.

All are inclusive and MUST NOT be summed. withCapturedMode includes the callback
body; scope.adapter includes native check, returned dynamic slot decode, clock,
projection and business comparisons. HPentry/leave wrap actual prepared callbacks.
The reassigned named capturedNativeScopePoint captures only one final publication
call because adapter previously retained its original function; use scope.adapter
for steady5000calls/40samples, not that single-call named sample.

Wrapper overhead: added counter/branch/function.apply on each wrapped invocation,
clock only1/128. Ten-thousand clock-pair plus empty-function mean0.110us; that is
not a complete wrapper overhead estimate. Existing all-calls scope instrumentation
also remains in the fixture, so do not subtract samples into a precise production
ceiling. Source callback functions are not rewritten; fixed source semantics and
endpoint assertions still execute. No queue experiment or FPS claim this stage.

QuickJS original archive SHA matches repository pinned upstream.61 extracted
files byte-identical, no patches. Build script uses O2/fwrapv and no DEBUG macros;
object0 has no debug sections. Historical exact compiler flags cannot be inferred
from object bytes alone. SystemGlobalFlag0; executable IFEO key absent; no detected
PAGEHEAP/DEBUG environment override. Five object hashes are recorded.
