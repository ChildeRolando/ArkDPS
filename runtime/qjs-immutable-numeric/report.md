# QuickJS immutable numeric packet experiment

Decision: park; no production API, source, native runtime, or game changes.

Pinned QuickJS is 2025-09-13. Cached upstream archive SHA256 is `6f1f322aea3bb3a90858db85c9fe717013fde4df7dfcafe2f57e78f5bb4b4a0c`. The driver independently compared quickjs.c/.h and all five cached-object source files to the verified archive bytes. Actual linked object hashes and build command are in receipt.json; cached objects were reused rather than rebuilt. quickjs.c SHA256 is `f1c17b0c9bda1e7e7a3436f3b426085962d361294c5ada2a21b719ac39287170`.

## Source evidence

All line numbers refer to the pinned quickjs.c:

- 9377-9396: JS_CreateProperty sees a new numeric array property. `get_prop_flags(flags,0)` must equal C_W_E for fast append. Native ENUMERABLE-only does not, so even the first property converts the empty array to an ordinary property array.
- 8575-8605: convert_fast_array_to_array moves elements into shape/property storage, frees dense storage, clears count, and sets fast_array=0.
- 9753-9766: modifying an existing dense element to non-C_W_E flags also converts it.
- 39141-39184: Object.freeze first prevents extensions, enumerates own keys, then removes configurable/writable via JS_DefineProperty. Thus an ordinary dense array becomes slow too. Re-freezing the already readonly native array still traverses/describes/defines each key.
- 8291-8306: JS_PreventExtensions itself only clears extensible (or calls a class method), and does not convert an array.
- 8385-8464: indexed JS access uses dense storage only while count permits; converted arrays go through ValueToAtom/JS_GetProperty. This is ordinary hash/property lookup, not a claim of linear scan.
- 7689-7695: custom exotic get_property is dispatched through the generic property path.

Product source inspected at root cb16c2938ccd2b8d544f7b4abd96bf009396cae1: owned_replay_js.h native HP/graph output uses JS_DefinePropertyValueUint32 ENUMERABLE-only + readonly length + JS_PreventExtensions; bridge.js additionally freezes packets at lines 433 and 623. No product change was made.

## Owned implementation and checks

micro.cpp builds a real QuickJS exotic class, not a frozen plain JS object. It stores 40 owned JSValues in one allocated packet, resolves setup-owned JSAtoms through a fixed 128-slot exact atom hash table, reports readonly/nonconfigurable own descriptors and keys, rejects mutation, and supplies GC mark/finalizer. It has a NULL prototype and is not an Array-compatible drop-in. In particular no Array.isArray/iterator contract is claimed, and defineProperty rejects even no-op redefinitions. This deliberately limits conclusions to numeric storage/lookup costs.

Four arms, two value mixes, four alternating-order repetitions. Each row reads 40 properties 20,000 times and constructs 12,000 packets. Mix0 is numbers; mix1 has 10 BigInt values and converts them with Number during the read loop on all arms. Times include native factory invocation for construction. No warmup/whole-source/FIFO/FPS conclusion is claimed.

Checks passed: exact length/value endpoints, readonly own descriptor, 40 enumerable numeric keys, out-of-range undefined, Reflect set/delete/define rejection, strict write exception. Final sink is 540160000 across 32 rows, matching the exact count. GC reported made=96010/finalized=96010 and mark_calls=2. JS_ComputeMemoryUsage confirmed readonly native and frozen normal arrays each have fast_array_delta=0; custom has no shape properties (array baseline 41).

## Results (median of four rows)

| Arm | Number construction us/40 values | Number read ns/property | Mixed construction us | Mixed read ns/property |
|---|---:|---:|---:|---:|
| Native ENUM-only + PreventExtensions + JS freeze | 3.689 | 52.84 | 3.612 | 76.07 |
| Normal dense array then JS freeze | 2.606 | 52.57 | 2.820 | 81.71 |
| Custom exotic immutable numeric object | 0.295 | 67.03 | 0.299 | 94.38 |
| Native ENUM-only + PreventExtensions only | 2.334 | 52.50 | 2.322 | 74.83 |

Custom construction is much cheaper, but reads are about 25% slower. Combining one construction with exactly 40 reads is only an indicative arithmetic model: numbers 5.803 -> 2.976 us, mixed 6.655 -> 4.074 us. This is about 2.6-2.8 us per packet, before any additional immutable provider/factory validation and compatibility work. It neither explains the measured ~33 us factory nor solves the ~77 us scope path. Removing duplicate Object.freeze would require a separate actual issuer/immutability contract review; this experiment does not authorize that production change.

The normal sandbox compiler child returned silent status1; the same bounded self-owned compile/run succeeded under the narrow auto-reviewed escalation used by the other agent. No DLL was loaded or rebuilt and no game process was accessed. stdout, stderr, build logs, source/driver/executable/object SHA, and individual samples are retained beside this report.
