"""Same native binary, old nested values vs current flat view; owned fixtures only."""
from pathlib import Path
from hashlib import sha256
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from production_hp_quickjs_bundle import bundle
from run_production_hp_quickjs import seal_business_events
from profile_production_hp_quickjs import run as profile

OUT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 'arkdps-production-hp-throughput/runtime/direct-hp-d-validation'
BINARY = PRIOR / 'run/production-hp.exe'
COUNTERS = """
let flatCalls=0,valuesCalls=0;
const priorValues=__ArkDpsGraphPrimitives.prototype.values;
__ArkDpsGraphPrimitives.prototype.values=function(index){valuesCalls++;return priorValues.call(this,index);};
if(typeof __ArkDpsGraphPrimitives.prototype.flat==='function'){
 const priorFlat=__ArkDpsGraphPrimitives.prototype.flat;
 __ArkDpsGraphPrimitives.prototype.flat=function(){flatCalls++;return priorFlat.call(this);};
}
"""

def main():
    frozen = OUT / 'source'
    names = [p.relative_to(PRIOR / 'source') for p in (PRIOR / 'source').rglob('*') if p.is_file()]
    for name in names:
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    report = {'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'binary_sha256': sha256(BINARY.read_bytes()).hexdigest(), 'arms': {}}
    for label, source in [('nested', PRIOR / 'source'), ('flat', frozen)]:
        script = bundle(2000, True, source, source, True, 1000)
        script = script.replace('const results=[];', COUNTERS + '\nconst results=[];')
        script = script.replace('__ownedProfile(true);nativeRequests=', '__ownedProfile(true);flatCalls=valuesCalls=0;nativeRequests=')
        script = script.replace('results.push({business_events_json:', 'results.push({flat_calls:flatCalls,values_calls:valuesCalls,business_events_json:')
        target = OUT / (label + '.js')
        target.write_text(script, encoding='utf-8')
        command = [str(BINARY), str(target), '--direct-hp']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
        (OUT / (label + '-stdout.json')).write_text(result.stdout, encoding='utf-8')
        (OUT / (label + '-stderr.txt')).write_text(result.stderr, encoding='utf-8')
        assert result.returncode == 0, result.stderr
        arm = seal_business_events(json.loads(result.stdout))
        for row in arm['results']:
            if row['branch'] in ('changed_damage', 'heal_coalesced', 'maxhp_dirty'):
                assert row['flat_calls'] > 0 and row['values_calls'] == 0 if label == 'flat' else row['flat_calls'] == 0 and row['values_calls'] > 0
        arm['executed_script_sha256'] = sha256(target.read_bytes()).hexdigest()
        arm['source_files'] = {str(p.relative_to(source)): sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
        arm['command'] = command
        report['arms'][label] = arm
    for a, b in zip(report['arms']['nested']['results'], report['arms']['flat']['results']):
        assert a['business_events_sha256'] == b['business_events_sha256'], (a['branch'], b['branch'])
    (OUT / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({label: {row['branch']: round(row['setters_per_second']) for row in arm['results']} for label, arm in report['arms'].items()}))
    profile(frozen, BINARY, OUT / 'profile')

if __name__ == '__main__':
    main()
