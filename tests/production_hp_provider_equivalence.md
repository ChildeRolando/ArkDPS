# HP I/O provider equivalence fixtures

`run_hp_provider_equivalence.py --baseline <fixed-source-snapshot> --candidate
<new-source-snapshot> --output runtime/<receipt>` launches the same real callback
fixture twice and compares the entire JSON envelopes/state. No rounding,
field removal, UID/time/amount tolerance, or inferred settlement is allowed.
Only the owned output path is writable. Both snapshots must contain native/Android
adapters plus shared runtime/autochess. This is pointer-provider semantic parity,
not a substitute for native owned-provider tests.

Forty cases cover native PC and MuMu, compact/raw, first heal/unchanged, XOR and
MaxHP dirty, damage→heal→direct setters, nested same/different thread, UID change,
frame/fixed change, zero frame, and ACK prefix immutability. Self-comparison smoke
passes all40. Actual candidate parity is pending its first source commit.

Additional mandatory native proof checks remain in existing owned tests/new
provider tests: optional absence with no read; requested absence poisons even
through catch; unreadable planned field; independentGood auto; unique clock
conflict/expectedID; parent checkpoint/current record and cross-thread rejection;
expired attribute handle; malformed slot/XOR/ready/count; first-heal MaxHP actual
capture; phase/receiver/root/lease; static scope/current clock; observer/ACK
frontier. These cannot be established by a Node pointer fixture.

Keep allunchanged and allchanged/heal throughput separate. No new FIFO run until
one-call provider meaningfully improves the actual fullscope consumer. Offline
callback numbers and source parity do not establish in-game FPS or30k acceptance.
