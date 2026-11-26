# Actual DLL raw HP observation engine binding

```powershell
python tests/native_async_hp_observation_owned.py --binary <final-candidate.dll> --source-root <same-candidate-source> --receipt runtime/async-evidence/hp-observation-engine.json
```

This creates new self-owned Python children, copies the specified DLL into their
private temporary directory, and loads only those copies. It never accepts a PID,
process handle or external memory address. `smoke.child` has two fixed operations:
`owned-hp-buffer` returns its own fixed actor/clock/class/string/attribute buffers;
`call-owned-hp` calls existing ArkDpsTestInteger with the actor pointer and2..12,
count1..4. The expected untouched ABI result is actorAddress+649. No new DLL export
or production hook path is introduced.

The test obtains the HP contract from the exact nativeCapturePlan and finalizer,
retains required projections and full actual cold template, and validates normal
productionHP shape. Setup metadata/classes are self-owned synthetic fixture data.
The finalizer capability list is an explicit owned-fixture capability certificate;
the DLL still independently compiles/rejects unsupported requirements. It is not
product readiness or Game metadata validation. Scope/publication graph is a warm,
guarded self-owned word. Cold traversal is not exercised or claimed.

Correct case consumes real engine-supplied raw observation fields at E and L,
then installs observer mode1 through the real host mark command while the hook's
installed contract remains mode0. The next callback pair must bind mode1. The
no-callback switch request truthfully retries instead of inventing a publication.
Wrong receiver and wrong phase getter are caught in JS but must still fail with
native replay code6 and reject ACK. Original integer ABI must remain correct in
all cases. Every bounded extra native call used to obtain a fresh publication is
recorded; no sleep manufactures an in-flight game operation.

Static validation: Python compilation, actual producer plan/finalizer output
completeHP11fields/11observations/full3fieldcoldbracket, and generated JS syntax.
Actual DLL execution remains pending a final native build; do not treat static
checks as engine integration acceptance.
