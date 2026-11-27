# Stage D scope primitive, 2026-11-27

Source frozen at 0dbc5ba (exact HEAD and files in source-identity.json). Same binary/script ABBA, count 2000/full scope/pub-every1000. Both arms enable B direct. Control removes the actual __arkdpsOwnedGraphPrimitives helper before business-source prepare; sealed native provider and its functions remain intact. No production source changed for timing.

| Arm | unchanged/s | damage/s | heal/s | MaxHP dirty/s |
| --- | ---: | ---: | ---: | ---: |
| A1 GraphCheck | 13930 | 4688 | 5352 | 5577 |
| B1 primitive | 13009 | 4657 | 5535 | 5312 |
| B2 primitive | 13218 | 4153 | 5687 | 5844 |
| A2 GraphCheck | 14229 | 4777 | 5601 | 5651 |

Changed/heal/dirty: format1 calls control=0/treatment=2002; format0 calls control=2004/treatment=2 (publication). Every arm issued/adopted3998 native HP observations, direct entry2000/leave1998, publication fallback2, ArgsProxy0. Existing endpoint, unknown, active, complete-lease, publication fence checks pass. ABBA business event kinds/count, sequence deltas, publication counts and scope stamps match.

A supplementary AB pair in run2 strengthens correctness: asserts disabled graph schema contains zero observation fields, enabled schema has observation fields; hashes exact full business event JSON including order and values, and requires matching SHA256 between arms. All branch hashes match. This supplementary pair was already in progress when root requested no more performance repeats; it is correctness evidence, not another throughput conclusion. Its raw stdout remains locally; compact receipt retains event hashes. No additional performance repeats were made.

Conclusion: small varying throughput differences do not establish a useful speed improvement; ~5.5k heal/s remains far below30k. No FIFO repetition or game/FPS claim. C++ timing profiler off. NativeCall counters are explicit measurement overhead. Inclusive profile is separate next work.

run/receipt.json has ABBA binary/script/source identities; run2/receipt.json binds strengthened schema/event assertions. Frozen final harness source under harness-source/. All test programs access owned fixture memory only.
