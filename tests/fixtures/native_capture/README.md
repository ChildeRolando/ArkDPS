# Frozen cold capture plan

`cold-plan-dbf93c8.json` is output from the production cold builder's owned-metadata fixture:

```
node tests/native_pc_cold_plan_fixture.js <output.json>
```

The 52-node/depth-14 contract was introduced by root commit `dbf93c8`.
This copy was generated from root `44033a4ea94feda26e6a7029fb65e303705e91f6`;
`cold_plan.js` git blob was `edd87011e676de532d5fb97b65b51b6bb3e74503`.
Class addresses are the existing fixture's owned labels, never live pointers.

`test_actual_cold_plan_and_384_owned_entities` translates this exact JSON into
initialization-only C++ assignments. It does not reimplement a production parser.
The C++ fixture materializes the complete layout in its own 8MiB arena:
256 ChessInst, 128 Unit, 128-unit CID and host strings, eight players,
a 32-slot dictionary with 31 unrelated values, and a ten-class Unit lineage.
It checks the old byte cap rejects initialization, the complete new plan fits,
a visit-cap failure preserves the cold body as unavailable, owned reads survive
source poisoning, and native capture allocates no heap memory.

Observed owned-fixture work: 16826 reads, 586616 attempted bytes, 293308 owned
bytes, 8413 segments. This is a resource/ownership test, not game FPS evidence.

`cold-plan-b91da2b.json` retains the root's subsequent exact three-field plan:
required common-root open/close guard pair around the optional cold body;
52 nodes/depth 14 remain, common controller block is 24 bytes and stage strings
allow 200 units. The earlier two-field JSON is retained separately. The same
384-entity fixture checks both contracts. Current three-field work is 16862
reads, 588200 attempted bytes, 294100 owned bytes, 8431 segments.
