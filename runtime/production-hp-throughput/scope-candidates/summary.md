# Two isolated scope candidates, one binary

All source input writes were confined to own runtime snapshots. Base source4c6deda;
control bridge7de38c3; candidate closure copied only readonly dirty adapter from
arkdps-scope-adapter-local; descriptor copied only readonly dirty bridge from
arkdps-native-queue-throughput. Exact input SHA perreceipt. No production changes.

2000setters,64scopefields/785pub guards, same nativebinary:

|branch|control|closure|descriptor|
|---|---:|---:|---:|
|damage|4293|4347|4517|
|heal|5743|5683|5340|
|dirty|5726|5561|5665|
|unchanged|12551|11084|13369|
|inputonly|538546|578035|590982|

Neither candidate meets10percent gain. Input-only drift7.3/9.7percent means
small differences are uncertain; do not claim optimization success.
No JS sampling wrappers in this comparison; fixed reusedbinary still has native
GraphCheck1/128 chrono instrumentation (~16pairs per2000setters), identical in
allgroups. Noqueue rerun/FPS acceptance. All endpoint assertions stayed strict.
