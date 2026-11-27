"""Research HP reducers are explicitly assembled and absent from product source."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_product_runtime_has_no_leaf_or_coalescer_implementation():
    source = (ROOT / 'event_probe_runtime.js').read_text(encoding='utf8')
    for name in ('applyHpLeafFacts', 'extendEnemyHpRun', 'enemyHpBirths', 'enemyHpRuns',
                 'enemyHpWindow', 'flushCoalescedEnemyHp', 'resetEnemyHpCoalescing',
                 'arkdpsCollectEnemyHpChanges', 'arkdpsCoalesceEnemyHp'):
        assert name not in source
    assert 'const collectEnemyHpChanges=false;' in source
    assert 'const coalesceEnemyHp=false;' in source
    assert not (ROOT / 'native_pc/native_event_runtime/owned_hp_leaf_facts.h').exists()
    assert (ROOT / 'tests/experiments/owned_hp_leaf_facts.h').is_file()
    from native_pc.native_hook_source import RUNTIME_SOURCES
    assert 'owned_hp_leaf_facts.h' not in RUNTIME_SOURCES
    assert 'owned_hp_leaf_facts.h' not in (ROOT / 'native_pc/native_event_runtime/engine.cpp').read_text(encoding='utf8')
    assert 'owned_hp_leaf_facts.h' not in (ROOT / 'native_pc/native_event_runtime/build.py').read_text(encoding='utf8')


def test_product_cannot_enable_research_by_setting_global_flags():
    script = r"""
const assert=require('node:assert/strict'),path=require('node:path');
const {fixture}=require('./tests/native_pc_frida_probe_fixture.js');
let cases=0;
for(const platform of ['native_pc','mumu']){
 const options={platform,sourcePath:path.resolve(platform==='native_pc'?'event_probe_native_pc.js':'event_probe_android2791.js'),
  enemyHpState:true,globals:{arkdpsCollectEnemyHpChanges:true,arkdpsCoalesceEnemyHp:true}};
 const product=fixture({...options,researchHpReference:false});
 const state=product.vm.runInContext('({full:collectEnemyHpChanges,coalesce:coalesceEnemyHp,leaf:typeof applyHpLeafFacts,runs:typeof enemyHpRuns,births:typeof enemyHpBirths,flush:typeof flushCoalescedEnemyHp})',product.context);
 assert.deepEqual(JSON.parse(JSON.stringify(state)),{full:false,coalesce:false,leaf:'undefined',runs:'undefined',births:'undefined',flush:'undefined'});
 const research=fixture({...options,researchHpReference:true});
 assert.equal(research.vm.runInContext('collectEnemyHpChanges&&coalesceEnemyHp&&typeof applyHpLeafFacts===\'function\'&&enemyHpRuns instanceof Map',research.context),true);
 cases++;
}
console.log(JSON.stringify({cases,product_flags_cannot_enable_research:true}));
"""
    run = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                         text=True, encoding='utf8', timeout=20)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)['cases'] == 2
