"""Reproduce the scoped live-evidence checks; never connects to the game."""
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
YATO = 'char_502_nblade'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def totals(sample):
    return {r['id']: r['damage'] for r in sample['rows']}


def guarded(sample):
    g = sample['guard']
    return g['complete'] and g['start'] == g['end'] == sample['frame']


def main():
    audit_path = HERE/'audit-20261107-054941.jsonl'
    records = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    accepted = [r for r in records if 'damage' in r]
    assert all(guarded(r['damage']) and guarded(r['enemy_observation']) and
               r['damage']['frame'] == r['enemy_observation']['frame'] for r in accepted)
    hits = []
    for before, after in zip(accepted, accepted[1:]):
        old, new = before['damage'], after['damage']
        delta = totals(new).get(YATO, 0) - totals(old).get(YATO, 0)
        if not delta:
            continue
        assert old['session'] == new['session']
        assert {k:v for k,v in totals(old).items() if k != YATO} == {k:v for k,v in totals(new).items() if k != YATO}
        enemies_before = {e['address']: e for e in before['enemy_observation']['enemies']}
        changed = []
        for e in after['enemy_observation']['enemies']:
            prev = enemies_before.get(e['address'])
            if prev and 'hp' in e and 'hp' in prev and prev['hp'] != e['hp']:
                assert prev['identity'] == e['identity'] and prev['live'] and e['live']
                changed.append(dict(address=e['address'],before=prev['hp'],after=e['hp'],finish=e['finish']))
        assert len(changed) == 1
        hp = changed[0]
        effective = hp['before'] - hp['after']
        global_delta = new['global_total'] - old['global_total']
        assert abs(effective - global_delta) < .001
        assert abs(effective - min(delta, hp['before'])) < .001
        hits.append(dict(frame=new['frame'],game_time=new['game_time'],character_delta=delta,
                         timestamp_basis='later observation, not a native hit-event timestamp',
                         observation_interval=dict(start_frame=old['frame'],end_frame=new['frame'],
                             start_game_time=old['game_time'],end_game_time=new['game_time'],bounds='(start,end]'),
                         global_delta=global_delta,hp=hp))
    assert [h['character_delta'] for h in hits] == [280,280,200,200,200]
    redeploy = read(HERE/'redeploy-observation.json')
    new_battle = read(HERE/'new-battle-observation.json')
    assert [e['opName'] for e in redeploy['events'] if e['charId'] == YATO] == ['SPAWN','WITHDRAW','SPAWN']
    assert [r['damage'] for r in redeploy['sample']['rows'] if r['id'] == YATO] == [2560]
    assert totals(accepted[-1]['damage'])[YATO] == 1160
    assert new_battle['sample']['session'] != redeploy['sample']['session']
    assert YATO not in totals(new_battle['sample']) and new_battle['sample']['sum_damage'] == 373
    assert new_battle['events'] == []
    capture_path = ROOT/'runtime/captures/20261107-052746.jsonl'
    capture = [json.loads(line) for line in capture_path.read_text(encoding='utf-8').splitlines()]
    assert all(guarded(s) for s in capture)
    assert {s['speed'] for s in capture} == {1,2}
    assert capture[-1]['game_time'] > capture[0]['game_time']
    result = dict(
        scope='Android 2.7.91 / MuMu 12: direct damage prototype; excludes universal skill-attribution acceptance',
        verdict='PASS for scoped underlying statistics Demo',
        audit=dict(accepted_samples=len(accepted),rejected=dict(Counter(r['rejected'] for r in records if 'rejected' in r)),
                   errors=[r for r in records if 'error' in r],hits=hits),
        redeploy=dict(before=1160,after=2560,rows_for_yato=1,per_hit_hp_after_redeploy='not captured'),
        new_battle=dict(game_time=new_battle['sample']['game_time'],yato_present=False,
                        npc_damage=373,session_changed=True),
        clock_capture=dict(accepted_samples=len(capture),distinct_frames=len({s['frame'] for s in capture}),
                           speeds=[1,2],start=capture[0]['game_time'],end=capture[-1]['game_time']),
        provenance={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (audit_path,HERE/'redeploy-observation.json',HERE/'new-battle-observation.json',capture_path)},
        limitations=[
            'The HP audit stopped on a transient enemy-list change before redeployment attacks; retry code was corrected but not revalidated during active combat.',
            'Overkill demonstrated on one isolated Yato finishing hit; earlier NPC/global discrepancies are not fully attributed.',
            'One real restart of the same practice was verified; arbitrary map transitions, menu auto-recovery and process restarts remain untested.',
            'Summons, damage over time, complex skills, elemental attribution and effective per-operator HP damage remain outside this Demo acceptance.',
            'Rolling five-second DPS uses sampled/interpolated boundaries; export save dialog has not been end-to-end accepted.'
        ])
    (HERE/'evidence-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
