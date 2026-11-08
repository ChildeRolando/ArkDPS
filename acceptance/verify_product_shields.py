"""Verify measured shield order/expiry without crediting unused balances."""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
directory=Path(sys.argv[1])
def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
def near(a,b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'shield disagreement: {a} != {b}')
summary=json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
if summary['error'] is not None:raise ValueError('capture incomplete')
journal=lines(directory/'product/capture.jsonl')
published=[r['data'] for r in journal if r['kind']=='published']
if any(r['kind']=='cleanup_error' for r in journal):raise ValueError('cleanup failed')
raw=lines(directory/'hp-events.jsonl')
if any(r['message'].get('type')=='error' for r in raw):raise ValueError('diagnostic script failed')
states=[r['message']['payload'] for r in raw
        if r['message'].get('payload',{}).get('diagnostic')=='unit_state']
if not states or any(s['frame']!=s['afterFrame'] for s in states):
    raise ValueError('missing or unstable membership snapshots')
events=[e for row in raw for e in row['message'].get('payload',{}).get('events',[])
        if e['name']=='BlockDamage._AssignDynamicVar']
observed=defaultdict(float)
by_uid=defaultdict(list)
source_keys={u['address']:u['id'] for s in states for u in s['units']}
for e in events:
    if e['frame']!=e['afterFrame']:raise ValueError('unstable absorption callback')
    source=source_keys[e['currentBuff']['source']]
    before=e['blackboard']['dynamic']
    after=e['afterBlackboard']['dynamic']
    amount=int(e['damageArg'],16)
    if amount>=2**63:amount-=2**64
    amount/=2**32
    near(before-after,amount)
    near(min(before,e['modifier']['value']),amount)
    observed[source]+=amount
    by_uid[e['currentBuff']['uid']].append(dict(frame=e['frame'],absorbed=amount))
last=published[-1]
near(last['statistics']['rows']['char_147_shining']['shield_absorbed'],observed['char_147_shining'])
near(last['statistics']['rows']['char_4162_cathy']['shield_absorbed'],observed['token_10041_cathy_catsld'])
members=defaultdict(list)
for state in states:
    for unit in state['units']:
        for buff in unit['buffs']:
            members[buff['uid']].append(dict(frame=state['frame'],target=unit['id'],**buff))
expiry=[]
for uid,samples in members.items():
    first,end=samples[0],samples[-1]
    if first['key']!='shining_s_2' or end['remaining']>.001 or end['bb']['dynamic']<=0:
        continue
    absent=next((s for s in states if s['frame']>end['frame'] and
        any(u['id']==end['target'] and all(b['uid']!=uid for b in u['buffs']) for u in s['units'])),None)
    if absent is not None:
        amount=sum(e['absorbed'] for e in by_uid[uid])
        near(first['bb']['dynamic']-end['bb']['dynamic'],amount)
        expiry.append(dict(uid=uid,last_frame=end['frame'],absent_by_frame=absent['frame'],
            unconsumed=end['bb']['dynamic'],absorbed=amount,remaining=end['remaining']))
if not expiry:raise ValueError('no unused shield natural expiry was observed')
orders=[]
for before,after in zip(events,events[1:]):
    if (before['frame']==after['frame'] and before['modifier']['target']==after['modifier']['target']
        and before['currentBuff']['key']=='catsld_t_1[core]'
        and after['currentBuff']['key']=='shining_s_2'):
        orders.append(dict(frame=before['frame'],first_uid=before['currentBuff']['uid'],
            second_uid=after['currentBuff']['uid'],first_balance=before['blackboard']['dynamic'],
            second_before=after['blackboard']['dynamic'],second_after=after['afterBlackboard']['dynamic']))
if not orders:raise ValueError('two-source callback order was not exercised')
print(json.dumps(dict(head=summary['head'],samples=len(published),
    shield_totals=dict(observed),natural_expiry=expiry,two_source_order=orders,
    replacement='Not established: distinct Shining UIDs separated by expiry; do not certify replacement or general stacking.',
    diagnostic_state_samples=len(states),first_watermark=published[0]['event_watermark'],
    last_watermark=last['event_watermark']),ensure_ascii=False,indent=2))
