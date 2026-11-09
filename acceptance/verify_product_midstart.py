"""Verify native cold-start totals and shield suffix without historical backfill."""
import json
import math
import sys
from pathlib import Path
directory=Path(sys.argv[1])
reference=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
def lines(path):return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
def near(a,b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'cold-start disagreement: {a} != {b}')
summary=json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
if summary['error'] is not None:raise ValueError('capture incomplete')
records=lines(directory/'product/capture.jsonl')
if any(r['kind']=='cleanup_error' for r in records):raise ValueError('cleanup failed')
published=[r['data'] for r in records if r['kind']=='published']
first,last=published[0],published[-1]
if first['frame']!=reference['after']['frame'] or first['event_watermark']['sequence']!=0:
    raise ValueError('cold baseline is not the paused native reference')
if first['game_time']<=0:raise ValueError('not a mid-battle start')
if not(reference['guard']['complete'] and reference['before']['frame']==reference['after']['frame']):
    raise ValueError('reference is not same-frame coherent')
native={r['id']:r for r in reference['after']['rows']}
if native['char_502_nblade']['damage']<=0 or native['char_147_shining']['healing']<=0:
    raise ValueError('existing damage/healing were not exercised')
for row in first['native_rows']:
    for metric in ('damage','healing'):
        near(row[metric],native[row['id']][metric])
for row in first['statistics']['rows'].values():
    near(row['shield_absorbed'],0)
    near(row['shield_coverage_start'],first['game_time'])
unit=next(u for u in reference['units'] if u['id']=='char_502_nblade')
shield=next(b for b in unit['buffs'] if b['key']=='catsld_t_1[core]')
balance=shield['blackboard']['dynamic']
if balance<=0:raise ValueError('no existing shield balance')
raw=lines(directory/'hp-events.jsonl')
events=[e for r in raw for e in r.get('message',{}).get('payload',{}).get('events',[])
        if e['name']=='BlockDamage._AssignDynamicVar' and e.get('currentBuff',{}).get('uid')==shield['uid']]
if not events:raise ValueError('existing shield was never consumed after startup')
absorbed=0
for e in events:
    if e['frame']!=e['afterFrame'] or e['frame']<first['frame']:
        raise ValueError('unstable or pre-start absorption')
    amount=int(e['damageArg'],16)/2**32
    near(e['blackboard']['dynamic']-e['afterBlackboard']['dynamic'],amount)
    absorbed+=amount
near(last['statistics']['rows']['char_4162_cathy']['shield_absorbed'],absorbed)
near(absorbed,balance)
print(json.dumps(dict(head=summary['head'],samples=len(published),baseline_frame=first['frame'],
    coverage_start=first['game_time'],existing_yato_damage=native['char_502_nblade']['damage'],
    existing_shining_healing=native['char_147_shining']['healing'],
    existing_shield_balance=balance,initial_shield_absorbed=0,observed_suffix_absorbed=absorbed,
    first_watermark=first['event_watermark'],last_watermark=last['event_watermark'],
    replacement='A second-device action was attempted; replacement was not established by this evidence.'),
    ensure_ascii=False,indent=2))
