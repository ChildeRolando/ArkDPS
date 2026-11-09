"""Verify full-HP detached healing alongside a separately proven injury control."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('directory',type=Path)
p.add_argument('--injury-control',type=Path,required=True)
args=p.parse_args()
control=subprocess.run([sys.executable,str(Path(__file__).with_name('verify_product_summon_healing.py')),
                        str(args.injury_control)],capture_output=True,text=True)
if control.returncode:
    raise ValueError('separate injured/full-health control failed: '+control.stderr)
injury=json.loads(control.stdout)
directory=args.directory
host,token='char_108_silent','token_10000_silent_healrb'
def lines(path):return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
def near(a,b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'detached healing disagreement: {a} != {b}')
summary=json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
control_summary=json.loads((args.injury_control/'product/summary.json').read_text(encoding='utf-8'))
if (summary['error'] is not None or summary['game_version']!=control_summary['game_version']
        or summary['stage']!=control_summary['stage']):
    raise ValueError('capture failed or controls differ in stage/client version')
records=lines(directory/'product/capture.jsonl')
if any(r['kind']=='cleanup_error' for r in records):raise ValueError('cleanup failed')
samples=[r['data'] for r in records if r['kind']=='published']
if not samples or any(s['event_status']!='recording' for s in samples):
    raise ValueError('continuous event coverage missing')
rosters=[r['data']['roster'] for r in records
         if r['kind']=='lifecycle_observation' and r['data'].get('roster')]
entries={e['cid']:e for r in rosters for e in r['entries']}
if entries[token]['host_key']!=host or entries[token]['host_uid']!=entries[host]['unique_id']:
    raise ValueError('direct token/host mapping is missing')
host_uid,token_uid=entries[host]['unique_id'],entries[token]['unique_id']
detached=next((r for r in rosters if r['present_known'] and
               host_uid not in r['present_unique_ids'] and token_uid in r['present_unique_ids']),None)
if detached is None:raise ValueError('no coherent host-absent/token-present evidence')
raw=lines(directory/'hp-events.jsonl')
if any(r.get('message',{}).get('type')=='error' for r in raw):raise ValueError('HP diagnostic failed')
heals=[e for r in raw for e in r.get('message',{}).get('payload',{}).get('events',[])
       if e['name']=='Entity.ApplyModifier' and e.get('sourceEntity',{}).get('id')==token
       and e.get('afterModifier',{}).get('targetType')==0 and e['afterModifier']['type']==4]
if not heals:raise ValueError('no summon healing')
for e in heals:
    if e['frame']!=e['afterFrame'] or e.get('error') or e.get('afterError'):
        raise ValueError('unstable HP callback')
    if e['entity']['id']!='char_502_nblade':raise ValueError('isolated target changed')
    near(e['afterEntity']['hp']-e['entity']['hp'],0)
    near(e['afterModifier']['realDelta'],0)
    if e['afterModifier']['value']<=0:raise ValueError('not positive full-HP healing')
after=[e for e in heals if e['frame']>detached['frame']]
if not after:raise ValueError('no healing after confirmed host departure')
total=sum(e['afterModifier']['value'] for e in heals)
first,last=samples[0],samples[-1]
baseline={r['id']:r for r in first['native_rows']}
native={r['id']:r for r in last['native_rows']}
near(native[token]['healing']-baseline.get(token,{}).get('healing',0),total)
near(native[host]['healing'],0)
owner=last['statistics']['rows'][host]
near(owner['healing'],native[token]['healing'])
near(owner['observed_healing'],total)
if token in last['statistics']['rows'] or set(owner['sources'])!={host,token}:
    raise ValueError('summon was not merged once into its host')
by_frame={r['frame']:r for r in rosters if r['present_known']}
for s in samples:
    roster=by_frame.get(s['frame'])
    row=s['statistics']['rows'].get(host)
    if roster is not None and row is not None and host_uid not in roster['present_unique_ids'] and row['present']:
        raise ValueError('departed host incorrectly marked present by surviving token')
print(json.dumps(dict(head=summary['head'],samples=len(samples),heal_events=len(heals),
    observed_healing=total,native_healing=native[token]['healing'],owner_healing=owner['healing'],
    actual_hp_restored=0,overheal=total,first_confirmed_host_absent_frame=detached['frame'],
    detached_heal_events=len(after),detached_healing=sum(e['afterModifier']['value'] for e in after),
    first_watermark=first['event_watermark'],last_watermark=last['event_watermark'],
    injury_control=injury,boundary='Detached window proves continuing full-HP healing, not effective HP restoration; injury control is verified separately.'),
    ensure_ascii=False,indent=2))
