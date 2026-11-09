"""Reconcile product summon healing, HP recovery, and optional detached output."""
import argparse
import json
import math
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('directory',type=Path)
parser.add_argument('--require-detached',action='store_true')
args=parser.parse_args()
directory=args.directory
host,token='char_108_silent','token_10000_silent_healrb'
def lines(path):return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
def near(a,b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'summon healing disagreement: {a} != {b}')
summary=json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
records=lines(directory/'product/capture.jsonl')
if summary['error'] is not None or any(r['kind']=='cleanup_error' for r in records):
    raise ValueError('capture incomplete or cleanup failed')
publications=[r['data'] for r in records if r['kind']=='published']
if not publications or any(s['event_status']!='recording' for s in publications):
    raise ValueError('continuous product event coverage is missing')
raw=lines(directory/'hp-events.jsonl')
if any(r.get('message',{}).get('type')=='error' for r in raw):
    raise ValueError('HP diagnostic failed')
events=[e for r in raw for e in r.get('message',{}).get('payload',{}).get('events',[])]
heals=[e for e in events if e['name']=='Entity.ApplyModifier'
       and e.get('sourceEntity',{}).get('id')==token
       and e.get('afterModifier',{}).get('targetType')==0
       and e['afterModifier']['type']==4]
if not heals:raise ValueError('no actual summon healing was observed')
effective=total=0
full=0
for e in heals:
    if e['frame']!=e['afterFrame'] or e.get('error') or e.get('afterError'):
        raise ValueError('HP callback crossed frames or failed')
    if e['entity']['id']!='char_502_nblade':
        raise ValueError('isolated healing target changed')
    amount=e['afterModifier']['value']
    restored=e['afterEntity']['hp']-e['entity']['hp']
    near(restored,e['afterModifier']['realDelta'])
    if not 0<=restored<=amount:raise ValueError('invalid effective healing')
    total+=amount
    effective+=restored
    full+=restored==0
if not 0<effective<total or not full:
    raise ValueError('injured/full-health/overheal controls were not all exercised')
first,last=publications[0],publications[-1]
native={r['id']:r for r in last['native_rows']}
baseline={r['id']:r for r in first['native_rows']}
near(native[token]['healing']-baseline.get(token,{}).get('healing',0),total)
near(native[host]['healing'],0)
row=last['statistics']['rows'][host]
if token in last['statistics']['rows'] or set(row['sources'])!={host,token}:
    raise ValueError('summon was not merged exactly once into host')
near(row['healing'],native[token]['healing']+native[host]['healing'])
near(row['observed_healing'],total)
rosters=[r['data']['roster'] for r in records
         if r['kind']=='lifecycle_observation' and r['data'].get('roster')]
entries={e['cid']:e for roster in rosters for e in roster['entries']}
if entries[token]['host_key']!=host or entries[token]['host_uid']!=entries[host]['unique_id']:
    raise ValueError('summon host mapping is not directly evidenced')
host_uid=entries[host]['unique_id']
token_uid=entries[token]['unique_id']
detached_rosters=[r for r in rosters if host_uid not in r['present_unique_ids']
                 and token_uid in r['present_unique_ids']]
detached=[]
if detached_rosters:
    detached=[e for e in heals if e['frame']>detached_rosters[0]['frame']]
if args.require_detached and not detached:
    raise ValueError('no healing after independently confirmed host departure')
if args.require_detached:
    by_frame={r['frame']:r for r in rosters if r['present_known']}
    for publication in publications:
        roster=by_frame.get(publication['frame'])
        owner=publication['statistics']['rows'].get(host)
        if roster is not None and owner is not None and host_uid not in roster['present_unique_ids']:
            if owner['present']:
                raise ValueError('a departed host was marked present because its summon survived')
print(json.dumps(dict(head=summary['head'],samples=len(publications),heal_events=len(heals),
    native_healing_start=baseline.get(token,{}).get('healing',0),native_healing_end=native[token]['healing'],
    observed_healing=total,actual_hp_restored=effective,overheal=total-effective,
    full_health_events=full,owner_healing=row['healing'],sources=row['sources'],
    first_watermark=first['event_watermark'],last_watermark=last['event_watermark'],
    first_confirmed_host_absent_frame=detached_rosters[0]['frame'] if detached_rosters else None,
    detached_heal_events=len(detached),detached_healing=sum(e['afterModifier']['value'] for e in detached),
    boundary='Detached output is certified only when require-detached passes; task completion alone does not prove retreat.'),
    ensure_ascii=False,indent=2))
