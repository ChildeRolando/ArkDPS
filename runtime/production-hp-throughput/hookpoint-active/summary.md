# HookPoint same-binary candidate evidence

Native/bridge: performance tree HookPoint dirty candidate; source candidate
EB16CCBB391340F3E10AE4126A4D466EC988F756. The retained receipt.json hashes the
actual sources/native providers/binary used for the active smoke. Control uses
root4c6deda pointer path without HookPoint, with the same native/bridge/binary.

Fullscope2000 same-binary serial control/candidate setters/s:
unchanged12773/13373; damage4261/4608; heal5710/5470; dirty5909/5572.
Input619540/625078. Candidate4000 actualHookPoint calls per branch; control0.
No branch demonstrated10percent gain. MissingHookSlot calls0 on these valid
fixtures; these measurements do not replace negative geometry/poison tests.

Same actual FIFO requested300000 setters at30000/s with publication every8571:

| pause ms | code | producer ms | submitted setters | consumed raw |
|---|---:|---:|---:|---:|
|0|4|799.4137|23796|9399|
|300|4|727.8316|21734|5274|
|500|4|672.8268|20080|1967|

Pause0 consumer11774.75 raw/s (~5887 setters/s), insufficient60000raw/s.
Requested ten seconds did not complete. No queue/fullscope/FPS pass claimed.

## No-cycle bridge followup, same binary

Removed unused replay->packet->replay strong cycle. Same2000 control-repeat and
candidate-no-cycle: damage4218/4525, heal4811/6105, dirty5345/5102,
unchanged12877/11945 setters/s; input606171/574267 (5.3percent drift).
Candidate4000 realHookPoint calls. Improvement is branch-dependent.
Actual FIFO0pause still code4 at803.0166ms,24116setters,10038raw consumed,
service12520.74raw/s. Actualproducer30031.76setters/s. Relative to prior
HookPoint FIFO11774.75raw/s, service improves6.3percent, not26.9percent.
Requested10seconds still not completed; no acceptance claimed.
