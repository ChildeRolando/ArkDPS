# Fixed-source owned worker evidence

Source/native/bridge: root 6e934a4. Actual first-cold fixture, 40 Chess plus Token,
64-field live scope and 785 publication guards. Standalone QuickJS uses actual
production source and fused replay dispatcher, actual CaptureConfigurations,
OwnedReplayView, CaptureLanes serialization/FIFO and four 32MiB spill banks.

500-setter serial fullscope: damage 3896/s; heal 5119/s; dirty 5345/s.
Changed/heal each executed 502 native GraphCheck calls and zero GraphReadBatch
calls. All native static owned reads/comparisons remain; internal read count is
not measured. Short serial sample includes forced final publication.

Requested producer: 300000 setters, 30000/s, ten seconds; publication every8571.
All three actual queue runs failed code4 before completing the request:

| Pause ms | Actual producer ms | Submitted setters | Consumed raw | Max age ms |
|---|---:|---:|---:|---:|
|0|797.8934|23792|9390|640.0489|
|300|724.5646|21630|5066|640.7642|
|500|672.1724|20053|1913|639.5254|

Pause0 service11913 raw/s (~5957 setters/s); not the required60000 raw/s.
Frozen3989 before had4572 raw/s. Optimization helps but does not close the deficit.
No ten-second completion, FPS acceptance or successful full-prefix certification
is claimed. Queue4 stops replay before claiming a complete battle.

Same source/provider/binary pureHP count2000 enabled/disabled ownbatch:
damage9320/9847, heal9602/10634, dirty9813/10710 setters/s. Calls enabled14000
versus disabled0. This pair shows no benefit; input-loop drift5.2% remains.

Excluded: actual native hook trampoline/IPC and game scheduling/FPS; dirty branch
executes business callbacks but not additional native dirty callback snapshots.
