# Flat owned scope packet experiment

Candidate source is frozen in receipt.json (HEAD b0e35cf, includes 93018f9).
Both arms use the same prior D native executable and direct HP path, 2000
setters per branch with full scope/publication. Only the bridge/source view
of the same typed packet changes. No raw facts or predicates are removed.

The old arm calls values(); the new arm calls flat() and makes zero values()
calls. Complete ordered business-event SHA256 agrees for every branch. Node
regressions cover signed clocks/state, class-name guards, field indices,
malformed packets, old leases and both source view formats.

The single unsampled pair measured heal 4357 vs 4871 setters/s, but the input
baseline drifted 455187 to 554139/s, so it does not establish a stable speed
gain. The separate inclusive candidate profile (5000 setters, 40 entry and
40 leave samples) measured scope 77.11us including primitive acquisition
32.62us; dispatch entry/leave 57.43/139.96us. These values include sampling
overhead and nested work, cannot be added or used as uninstrumented throughput.

The change eliminates redundant per-record arrays and retains one immutable
packet. It does not pass 30000 setter/s, queue pause/catch-up or real FPS gates.
Raw generated scripts and stdout are retained locally beside the receipt;
the reproduction script and compact provenance/results are versioned.
