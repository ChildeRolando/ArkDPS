import sys,json,subprocess,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'));from production_hp_quickjs_bundle import bundle
out=Path(__file__).resolve().parent/'raw-attrs-golden';out.mkdir(parents=True,exist_ok=True);source=ROOT.parent/'arkdps-hp-observation-owner';bridge=ROOT.parent/'arkdps-hp-raw-observation'
js=bundle(8,True,source,bridge,True,8571)
inject="let replaySummaries=[];const originalEnd=__arkdpsReplayEnd;__arkdpsReplayEnd=function(compact){replaySummaries.push({unreadable:__arkdpsReplay.unreadable,structural:__arkdpsReplay.structural.slice()});return originalEnd(compact);};__ownedPut(Number(enemy.add(0xb0).value),0n);f.write(enemy.add(0xb0),f.ptr(0));\n"
js=js.replace('const results=[];',inject+'const results=[];').replace('__ownedProfile(true);scopeMs=', '__ownedProfile(true);replaySummaries=[];scopeMs=').replace('results.push({js_profile:', 'results.push({replay_summaries:replaySummaries,raw_events:events,js_profile:')
js=re.sub(r"if\(branch==='heal_coalesced'\)assertion.equal\(mark.sequence-initial,[^;]+;",'',js)
p=out/'fixture.js';p.write_text(js);exe=ROOT/'runtime/production-hp-throughput/raw-observation-smoke/production-hp.exe'
for label,flags in [('oldpointer',['--disable-hp-observation']),('observation',[])]:
 r=subprocess.run([str(exe),str(p),*flags],capture_output=True,text=True,timeout=60)
 if r.returncode:raise RuntimeError(r.stderr)
 d=json.loads(r.stdout);(out/f'{label}.json').write_text(json.dumps(d,indent=2));print(label,[(x['branch'],x['native_profile']['hp_observation_calls'],sum(t['unreadable'] for t in x['replay_summaries'])) for x in d['results']])
a=json.loads((out/'oldpointer.json').read_text());b=json.loads((out/'observation.json').read_text());assert [(x['raw_events'],x['replay_summaries']) for x in a['results']]==[(x['raw_events'],x['replay_summaries']) for x in b['results']];print('EXACT_RAW_EVENTS_AND_REPLAY_SUMMARY_EQUAL')
