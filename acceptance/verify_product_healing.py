"""Recompute product healing and paired HP evidence, rejecting false credit."""
import json
import math
import sys
from pathlib import Path

directory=Path(sys.argv[1])
def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
def near(a,b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'counter disagreement: {a} != {b}')
records=lines(directory/'product/capture.jsonl')
summary=json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
if summary['error'] is not None or any(r['kind']=='cleanup_error' for r in records):
    raise ValueError('incomplete capture or cleanup failure')
published=[r for r in records if r['kind']=='published']
raw=lines(directory/'hp-events.jsonl')
if any(r['message'].get('type')=='error' for r in raw):
    raise ValueError('raw HP diagnostic script failed')
events=[e for row in raw for e in row['message'].get('payload',{}).get('events',[])]
heals=[e for e in events if e['name']=='Entity.ApplyModifier'
       and e.get('sourceEntity',{}).get('id')=='char_147_shining'
       and e.get('afterModifier',{}).get('targetType')==0
       and e.get('afterModifier',{}).get('type')==4]
if not heals:
    raise ValueError('no paired healing evidence')
for e in heals:
    if e.get('frame')!=e.get('afterFrame') or e.get('error') or e.get('afterError'):
        raise ValueError('unstable HP callback')
    effective=e['afterEntity']['hp']-e['entity']['hp']
    if effective<0 or effective>e['afterModifier']['value']+.01:
        raise ValueError('HP increase exceeds final healing')
amount=sum(e['afterModifier']['value'] for e in heals)
effective=sum(e['afterEntity']['hp']-e['entity']['hp'] for e in heals)
if amount<=effective:
    raise ValueError('overheal was not exercised')
last=published[-1]['data']
native={r['id']:r for r in last['native_rows']}
near(native['char_147_shining']['healing'],amount)
near(last['statistics']['rows']['char_147_shining']['healing'],amount)
near(last['statistics']['rows']['char_147_shining']['observed_healing'],amount)
maa=lines(directory/'maa.jsonl')
control=next(r['wall'] for r in maa if r['data'].get('details',{}).get('doc','').startswith('full-health-no-target-control'))
control_samples=[r['data'] for r in published if r['wall']>=control+1]
if len(control_samples)<2:
    raise ValueError('missing full-health control window')
control_totals=[next(x['healing'] for x in p['native_rows'] if x['id']=='char_147_shining') for p in control_samples]
for total in control_totals:
    near(total,amount)
mountain=[e for e in events if e['name']=='Entity.ApplyModifier'
          and e.get('entity',{}).get('id')=='char_264_f12yin'
          and e.get('afterModifier',{}).get('targetType')==0]
recovery=[]
for previous,current in zip(mountain,mountain[1:]):
    increase=current['entity']['hp']-previous['afterEntity']['hp']
    if increase>0:
        recovery.append(dict(start=previous['afterFrame'],end=current['frame'],hp_increase=increase))
if not recovery:
    raise ValueError('continuous recovery was not observed')
if any(e.get('afterModifier',{}).get('targetType')==0 and e.get('afterModifier',{}).get('type')==4
       for e in mountain):
    raise ValueError('regeneration window is confounded by modifier healing')
near(native['char_264_f12yin']['healing'],0)
result=dict(head=summary['head'],samples=len(published),
    native_healing=native['char_147_shining']['healing'],final_event_healing=amount,
    actual_hp_restored=effective,overheal=amount-effective,
    full_health_control_samples=len(control_samples),full_health_control_healing_delta=0,
    continuous_recovery=recovery,continuous_recovery_native_healing=0,
    boundary='Mountain S2 HP regeneration is observable but absent from native healing counter; do not certify native HPS as all HP recovery.',
    first_watermark=published[0]['data']['event_watermark'],last_watermark=last['event_watermark'],
    automation='MAA ADB Lite; no human deployment',diagnostics='Separate read-only script sharing the product Frida session')
print(json.dumps(result,ensure_ascii=False,indent=2))
