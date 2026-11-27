# Experimental typed Graph packet recognition

This branch is a performance candidate, not a throughput/FPS claim or production enablement. Base: b308490b401cbb2f23716f2f14c5e5aaf989e07a. Source canonical scope decoding/clock/className/phase/round/state/autoScope/mode are unchanged. All original OwnedView/unique/expected field ID/checkpoint/status/static predicates execute before native format1 emission.

The existing native GraphCheck gains format2 plus its fifth packet argument. It recognizes only the exact retained JSValue emitted by format1 for this active context, raw ticket, configuration pointer+id, check-program pointer, revision, mode and checkpoint. Begin/end/rebind/reissue release the retained reference. There is no graph registry, additional native read, or amount/state policy. Capability `__nativeOwnedGraphTypedPackets:1` is readonly data in the pinned provider bundle and is only an availability declaration. Recognition must return the packet itself; a boolean, copy, constructor instance or exception cannot authorize the fast path. Old providers without capability keep the complete original JS type/index/width loop and Object.freeze. Present-provider failures do not fall back.

Skipping the old loop initially exposed an important geometry boundary: a caller-made graph clone could retain revision/mode/count while changing signedness. That is now rejected using an exact non-hashed layout string. Both source geometry and immutable CompiledReadProgram derive it once: `v1|totalFields|originalIndex:fieldBytes:codecCount:kind@offset...`. Each packet carries that native-derived string in a nonenumerable, readonly/nonconfigurable property. Header/length/layout equality remains in the bridge before successful identity recognition can skip the duplicate loop/freeze. Clones changing kind, offset, width or original field index are tested. This does not add a second signature of static predicates/roles that the old JS loop never verified; actual native predicates and role/cp/field-ID checks remain, and production source graphs retain their existing private-map/deep-seal owner.

Five-axis review:

- Correctness: same executor/predicates and canonical source owner; raw values, indices, signed ranges are mechanically constructed by the compiled codecs, not inferred from amount or scope. Header/layout are still checked. Unsupported geometry retains old strict behavior.
- Identity/concurrency: exact context/object/ticket/config/program/revision/mode/checkpoint; no latest-config lookup or cross-record value cache. Recognition reads no game memory. It does not relax any Game-thread or terminal/ACK gate.
- Ownership/lifetime: one native-held reference under the existing worker Binding, cleared at the existing lifecycle boundaries. Packet itself has readonly descriptors/length and is nonextensible. The actual C++ test verifies Object.isFrozen plus failed property/layout mutation.
- Compatibility/failure: cap absent uses the original validator; invalid/partial capability is rejected; recognition errors never choose a slower success path. The original executor still prioritizes later structural failures over earlier planned uncertainty.
- Cost/scope: one static layout string per immutable native program and source geometry; one readonly string property per packet and one extra recognition call. No benefit is assumed until same-binary actual-source measurement.

Validation:

- Actual cached-QuickJS C++ owned graph test passed after adding immutable packet and recognition positives and fake array/boolean/foreign context/ticket/checkpoint/config/revision/mode/rebind/reissue/end negatives. Existing signed/high-bit/predicate/guard/concurrent/129-byte unsupported cases also ran in this same executable.
- Removing only the C++ exact packet identity comparison causes the fake-array assertion to fail. cpp-receipt.json preserves both compiler commands/binary and source hashes and the mutant failure.
- Final lightweight suite: 15 passed, 2 native pytest cases intentionally unconfigured/skipped. The actual native graph case was separately compiled/executed as stated above, not relabeled from a skip. Node covers issuer copy/boolean/throw rejection, header/length/layout, public constructor, old lease, old API fallback, and original signed-width failures.
- Two final JS mutations are killed: disabling layout equality and disabling recognition return identity. Outputs are preserved in js-mutations.json and stderr files.

No DLL or Game operation, no benchmark yet. P6's pending Binding lease/status counters are separate changes; this patch does not modify those methods and merge must retain both counters.
