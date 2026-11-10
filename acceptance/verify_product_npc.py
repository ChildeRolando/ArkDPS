"""Reconcile predefined NPC output, unknown traps, and retained departure totals."""
import json
import math
import sys
from pathlib import Path

directory = Path(sys.argv[1])
summary = json.loads((directory/'product/summary.json').read_text(encoding='utf-8'))
if summary['error'] is not None:
    raise ValueError('capture incomplete')
records = [json.loads(x) for x in (directory/'product/capture.jsonl').read_text(encoding='utf-8').splitlines()]
if any(r['kind']=='cleanup_error' for r in records):
    raise ValueError('cleanup failed')
samples = [r['data'] for r in records if r['kind']=='published']
first, last = samples[0], samples[-1]
rosters = [r['data']['roster'] for r in records
           if r['kind']=='lifecycle_observation' and r['data'].get('roster')]
if not any(e['cid']=='char_391_rosmon' and e['unique_id']==1 and e['is_predefined']
           for roster in rosters for e in roster['entries']):
    raise ValueError('missing predefined NPC provenance')
def near(a, b):
    if not math.isfinite(a) or not math.isfinite(b) or abs(a-b)>.01:
        raise ValueError(f'NPC counter disagreement: {a} != {b}')
native = {r['id']:r for r in last['native_rows']}
rows = last['statistics']['rows']
if native['char_391_rosmon']['damage']<=0 or native['trap_024_npcsld']['damage']<=0:
    raise ValueError('NPC and trap output were not exercised')
for cid in ('char_391_rosmon', 'char_264_f12yin', 'char_502_nblade'):
    near(native[cid]['damage'], rows[cid]['damage'])
    if rows[cid]['sources']!=[cid]:
        raise ValueError('unrelated sources were merged into known NPC/player')
# ARK-24: a recognised map trap is a non-team source. It must be classified
# and named with its output, never attributed to a team row, and it must not
# block the team denominator forever.
trap = 'trap_024_npcsld'
stats = last['statistics']
if (trap not in stats['non_team_sources'] or trap in stats['unknown_sources']
        or trap not in stats['non_team_output']
        or any(trap in r['sources'] for r in rows.values())):
    raise ValueError('known map trap was attributed to the team or left unclassified')
near(stats['non_team_output'][trap]['damage'], native[trap]['damage'])
if (not last['statistics_complete'] or any(r['share'] is None for r in last['rows'])
        or set(stats['unknown_present_unique_ids'])!={2,3,4,5}):
    raise ValueError('complete team counters did not produce shares or NPC presence was lost')
present = [s for s in samples if s['statistics']['rows'].get('char_502_nblade',{}).get('present')]
if not present:
    raise ValueError('unit was never present')
departed = [s for s in samples if s['frame']>present[-1]['frame']
            and s['statistics']['rows']['char_502_nblade']['present'] is False]
if not departed:
    raise ValueError('unit departure was not observed')
for sample in departed:
    near(sample['statistics']['rows']['char_502_nblade']['damage'], native['char_502_nblade']['damage'])
hp = [json.loads(x) for x in (directory/'native-hp.jsonl').read_text(encoding='utf-8').splitlines()]
if not hp or any(r.get('diagnostic_error') for r in hp):
    raise ValueError('read-only HP diagnostic failed')
if any(not r['guard']['complete'] or r['frame']!=r['guard']['start']
       or r['frame']!=r['guard']['end'] for r in hp):
    raise ValueError('HP reference crossed frames')
maa = [json.loads(x) for x in (directory/'maa.jsonl').read_text(encoding='utf-8').splitlines()]
if any(r['data'].get('what')=='CopilotAction'
       and r['data'].get('details',{}).get('action')=='Retreat' for r in maa):
    raise ValueError('departure window contains a confounding retreat action')
print(json.dumps(dict(head=summary['head'], samples=len(samples), first_frame=first['frame'],
    last_frame=last['frame'], npc_damage=native['char_391_rosmon']['damage'],
    player_damage=native['char_264_f12yin']['damage'],
    classified_non_team_trap_damage=stats['non_team_output']['trap_024_npcsld']['damage'],
    departed_unit_damage=native['char_502_nblade']['damage'], departed_samples=len(departed),
    hp_reference_samples=len(hp), unknown_present_unique_ids=last['statistics']['unknown_present_unique_ids'],
    boundary='No same-frame HP=0 was captured for Yato; certify retained departure total, not an independently proven death mechanism.',
    events='disabled; event rates and shield absorption remain unknown'),ensure_ascii=False,indent=2))
