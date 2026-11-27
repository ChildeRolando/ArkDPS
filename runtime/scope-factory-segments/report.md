# Scope / GraphPrimitives bounded segmented profile

Scope: scratch JS instrumentation only. No production source change, native compile, new API, queue experiment, or game interaction.

Fixed source root HEAD: d107f11e883adbc6d4209c8be9c3279a4ef1c6ef. Frozen six-file control matches that git source after CRLF/LF normalization. Actual frozen original/instrumented file hashes, executed JS hashes, and driver hash are in receipt.json. Frozen sources and executable scripts also remain in the local control/sampled directories. Binary was reused at `arkdps-production-hp-throughput/runtime/direct-hp-d-validation/run/production-hp.exe`, SHA256 `4bffb2f0eaebdcdd847af7dd19ac1bcb3460df4ffb874eddafbb2728e396f9ab`.

5000 heal setters with actual B direct dispatch, full scope and five real publication claims. The unmodified control and sampled arm produced identical full business-event JSON SHA256 `03055ecd0fb2c60eab831337aa6e609ac73175359ed6b81253890ab6cd7a4c32`. All original strict checks passed, including enemyStateUnknown=false, fatal=null, active=0, exact endpoints, event count, native issuance, current lease, receiver identity and successful publication. Both arms: direct E=5000/L=4995, adopted=9995/ordinary=5, actual raw HP observations=10000, graph format1=5005/format0=5. No hidden fallback or ArgsProxy use.

Sampling is independently selected at each owner entry by counter phase1 modulo128. Both owners had 5005 calls/40 samples. Only selected calls invoke added clocks; nonselected calls only test the sampler/step null branch. Existing fixture timing/counters remain on both arms. Within a selected owner, all adjacent segment boundaries use the same token and timestamps. These are inclusive wall times including helper/bookkeeping/timestamp overhead; nested factory sampling overhead also enters the scope preamble. First geometry compilation is included, not removed. No sampled average is multiplied by128 as an exact total.

| Segment | Samples | Mean us | Max us |
|---|---:|---:|---:|
| factory preamble / geometry / lease | 40 | 5.115 | 153.700 |
| factory actual native call (JS dispatch + FFI + native) | 40 | 9.570 | 18.300 |
| factory packet header / original index / type / width validation | 40 | 15.500 | 24.300 |
| factory freeze + private brand mint | 40 | 5.850 | 47.400 |
| factory total | 40 | 37.258 | 196.300 |
| scope preamble including factory, graph compile/lookup, proof/flat access | 40 | 56.928 | 774.200 |
| scope canonical field loop + phase/round/state validation | 40 | 20.977 | 39.200 |
| scope tail captureClockFixed + stringify | 40 | 9.860 | 125.700 |
| scope tail autoScope | 40 | 3.045 | 9.000 |
| scope tail collectionMode + result object | 40 | 4.685 | 13.500 |
| scope total | 40 | 96.373 | 963.800 |

Containment: scope total contains scope preamble; that preamble contains the entire GraphPrimitives factory total. Factory native-call time contains its native executor/return conversion, not just OwnedView.read. Field canonical loop contains existing class role/checkpoint/className validation and JS clock/integer semantics. Tail captureClockFixed is an independent real owned read, not reused from the dynamic slot. Segment totals differ from inclusive totals by boundary helper/return/finally timing. Maxima are independently observed, and must not be added. The first-call maxima show why these 40-sample means are not a replacement claim for earlier steady ~33us factory/~77us scope measurements. Control wall elapsed845.55ms vs sampled906.30ms is instrumentation perturbation, not a product regression estimate.

## Narrow conclusion and possible next experiment

The packet validation loop is the largest factory segment: 15.5us (~42% of the sampled inclusive factory), with max24.3us rather than just one first-geometry outlier. This supports investigating the repeated JS indexed/type/width checks; it does not prove they dominate the entire callback or establish a30k/s solution.

If pursued, the bounded candidate is an actual native-issued typed certificate on the existing format1 GraphCheck result, analogous to the existing issued/adopted HP packet: C++ retains the exact returned JSValue under current lease/ticket/configuration/graph revision/mode/checkpoint, whose original-index order/count and primitive width/sign were mechanically produced from the same immutable compiled codecs. A bridge recognition/adoption step must verify exact issuer-held identity; ordinary objects, public constructors, booleans and cross-item/cross-lease packets cannot mint the certificate. Only after that proof may the bridge omit the duplicate JS type/index loop. All NativeGraphCheck unique/expected-ID/guard/predicate/error-priority checks remain, and source className/clock/phase/round/state/autoScope/mode canonical validation remains. This is a proposal only; no checks were removed here. Its extra recognition-call/immutability/mint cost needs an actual-source comparison before admission. No new registry or second statistics state machine is justified by this profile.
