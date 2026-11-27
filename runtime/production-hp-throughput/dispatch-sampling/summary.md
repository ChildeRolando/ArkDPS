# Fixed outer replay dispatch costs

Source and bridge8502fe3 frozen via gitshow into own runtime. Only temporary
ReplayDispatch is segmented; all original callbacks/record constructors/Begin/End,
receiver and argument construction still execute.5000setters perbranch, entry and
leave independently sample1/128 (40samples each), using separate phase counters.
Metrics phase inference uses receiver===undefined at harness E; no production
semantics use this metric. No native/production files changed.

Heal sampled meanmicroseconds perE/L:
packedRecord2.97/2.64; Begin6.12/5.50; receiver3.62/.70;
args3.17/1.37; actualcallback41.70/116.24; healthprefix1.09/1.41;
End2.11/2.34; result.86/.78. The sequential noncallback segments sum32.67us
per setter. These are disjoint sections within this dispatch instrumentation,
not means subtracted from independent function samples. Callback remains inclusive.

Unchanged callback40.01/16.42us and outer~29.5us. Threshold30000setters/s
permits33.33us for the entire setter, so even fixed envelope costs nearly consume
that budget before business/capture/view. No single giant outer function: Begin
~11.6us, packedrecord~5.6us,receiver~4.3us,args~4.5us,End~4.5us.

Sampling adds bitcounter/branch and temporarymetrics references peredge; timed
samples invoke8QPC boundaries. Prior clockpair/emptycall0.110us is only a lower
level overhead indication and is not subtracted. Last publication may not fall on
selected sample, so healthprefix means are predominantly steady edges, not full
publication cost. Captured native/readproofs and endpoint assertions unchanged.
Source/binary/script SHA recorded in sampled.json. Reused binary has native
GraphCheck1/128 timing too, identical fixed provider. Not liveFPS/30k acceptance.
