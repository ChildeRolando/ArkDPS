"""Independent HP reconciliation capture. No gameplay writes.

Run while a user controls a single-attacker practice battle. Retained enemy
objects are explicitly marked; identity changes invalidate those observations.
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import collector
from collector import Collector,gs,u64,i32,SampleUnavailable
collector.PORT=27382


def read_enemies(c,known):
    ch=c.core._chan
    B=gs.BattleControllerFields
    U=gs.UnitManagerFields
    A=gs.UnorderedArrayFields
    base=[('direct',c.core.bc_addr,max(B.UNIT_MANAGER+8,B.M_REAL_PLAY_TIME+4)),
          ('deref',0,B.UNIT_MANAGER,0,0x30),
          ('deref',1,U.ENEMIES,0,0x28)]
    preliminary=ch.transaction_read(base)
    if any(x is None for x in preliminary):raise SampleUnavailable('enemy header unavailable')
    count=i32(preliminary[2],A.COUNT)
    if not 0<=count<=128:raise SampleUnavailable('enemy count invalid')
    ops=list(base)
    if count:
        ops.append(('deref',2,A.ITEMS,0x20,count*8))
    live_ops=[]
    for n in range(count):
        idx=len(ops)
        ops.append(('deref',3,n*8,0,0x190))
        live_ops.append(idx)
    historical_ops=[]
    for ptr,identity in list(known.items()):
        idx=len(ops);ops.append(('direct',ptr,0x190))
        historical_ops.append((ptr,identity,idx))
    tail=len(ops)
    ops.extend([('direct',c.core.bc_addr,max(B.UNIT_MANAGER+8,B.M_REAL_PLAY_TIME+4)),
          ('deref',tail,B.UNIT_MANAGER,0,0x30),
          ('deref',tail+1,U.ENEMIES,0,0x28)])
    values,guard=ch.guarded_transaction_read(ops,
        c.core._bc_static_fields+gs.BattleControllerStaticFields.FIXED_FRAME_COUNT)
    if not guard['complete'] or any(values[n] is None for n in (0,1,2,tail,tail+1,tail+2)):
        raise SampleUnavailable('enemy guard incomplete')
    if not u64(values[0],B.UNITY_CACHED_PTR) or not u64(values[tail],B.UNITY_CACHED_PTR):
        raise SampleUnavailable('battle retired')
    if i32(values[2],A.COUNT)!=count or i32(values[tail+2],A.COUNT)!=count:
        raise SampleUnavailable('enemy list changed')
    if u64(values[2],A.ITEMS)!=u64(values[tail+2],A.ITEMS):
        raise SampleUnavailable('enemy array changed')
    current={u64(values[3],n*8):values[idx] for n,idx in enumerate(live_ops)} if count else {}
    observations=[]
    for ptr,block in current.items():
        if block is None:raise SampleUnavailable('enemy block unavailable')
        identity=(u64(block),u64(block,gs.EntityFields.ID))
        known[ptr]=identity
        observations.append(dict(address=ptr,identity=identity,live=True,
            hp=gs.fp_to_float(u64(block,gs.EntityFields.M_HP)),
            finish=i32(block,gs.EntityFields.FINISH_REASON)))
    for ptr,identity,idx in historical_ops:
        if ptr in current:continue
        block=values[idx]
        if block is None or (u64(block),u64(block,gs.EntityFields.ID))!=identity:
            known.pop(ptr,None)
            observations.append(dict(address=ptr,live=False,identity_valid=False))
            continue
        observations.append(dict(address=ptr,identity=identity,live=False,identity_valid=True,
            hp=gs.fp_to_float(u64(block,gs.EntityFields.M_HP)),
            finish=i32(block,gs.EntityFields.FINISH_REASON)))
    return dict(frame=guard['start'],enemies=observations,guard=guard)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=300)
    args=parser.parse_args()
    HERE.mkdir(exist_ok=True)
    path=HERE/f'audit-{datetime.now(timezone(timedelta(hours=8))):%Y%m%d-%H%M%S}.jsonl'
    stopfile=HERE/'STOP'
    if stopfile.exists():raise SystemExit('Remove acceptance/STOP before starting a new audit')
    c=Collector(log=lambda x:print(x,flush=True))
    c.cache_path=HERE/'audit_hint.json'
    known={}
    try:
        c.connect(force=True)
        end=time.monotonic()+args.seconds
        previous=None
        with path.open('w',encoding='utf-8') as out:
            print('READY',path,flush=True)
            while time.monotonic()<end and not stopfile.exists():
                try:
                    damage=c.read()
                    enemies=read_enemies(c,known)
                    if damage['frame']!=enemies['frame']:
                        out.write(json.dumps(dict(rejected='different_frames',damage_frame=damage['frame'],enemy_frame=enemies['frame']))+'\n')
                        continue
                    sample=dict(damage=damage,enemy_observation=enemies)
                    out.write(json.dumps(sample,ensure_ascii=False)+'\n');out.flush()
                    signature=(damage['session'],damage['sum_damage'],damage['global_total'])
                    if signature!=previous:
                        print(json.dumps(dict(frame=damage['frame'],time=damage['game_time'],
                            operators=[(x['name'],x['damage']) for x in damage['rows']],
                            global_total=damage['global_total'],enemies=enemies['enemies']),ensure_ascii=False),flush=True)
                        previous=signature
                except SampleUnavailable as exc:
                    out.write(json.dumps(dict(rejected=str(exc),wall_time=time.time()),ensure_ascii=False)+'\n');out.flush()
                    time.sleep(.035)
                    continue
                except Exception as exc:
                    out.write(json.dumps(dict(error=str(exc),wall_time=time.time()),ensure_ascii=False)+'\n');out.flush()
                    print('READ_ERROR',str(exc),flush=True)
                    break
                time.sleep(.035)
    finally:
        c.close()
    print('FINISHED',path,flush=True)


if __name__=='__main__':main()
