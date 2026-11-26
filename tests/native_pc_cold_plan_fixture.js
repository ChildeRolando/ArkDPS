'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../native_pc/native_event_runtime/cold_plan.js'),'utf8'),context);
const result=vm.runInContext(`
const names=['BattleController','SingletonHost','AutoChessGameManager','AutoChessGameStatus','AutoChessGameStateModel',
 'AutoChessGameStateMachine','AutoChessDataCenter','AutoChessPlayerDataModel','ScenePlayerData','PlayerCard',
 'AutoChessBattleDataModel','ChessInst','BattleCharacterData','UnitManager','Character'];
const classes=Object.fromEntries(names.map((name,i)=>[name,'0x'+(0x10000+i*0x1000).toString(16)]));
const metadata={cold_metadata_ready:true,classes,string_class:'0x30000'};
const plan=__arkdpsBuildColdCapturePlan(metadata);
({metadata,plan});
`,context);
const {plan,metadata}=JSON.parse(JSON.stringify(result));let checks=0;
function check(condition){assert.ok(condition);checks++;}
check(plan.fields.length===2&&plan.fields[0].required===true&&plan.fields[1].required===false);
check(plan.projections.length<=128);
let deepest=0;
function visit(index,depth,ancestors){
 check(index>=0&&index<plan.projections.length&&!ancestors.has(index));
 const next=new Set([...ancestors,index]),node=plan.projections[index];deepest=Math.max(deepest,depth);
 check(node.pointer_offsets.length<=12&&node.max_visits<=256&&node.max_visits>0);
 check(!('type_gate'in node));
 if(node.kind==='utf16')check(node.expected_class===metadata.string_class&&node.max_units===128);
 for(let i=0;i<node.child_count;i++)visit(node.child_begin+i,depth+1,next);
}
for(const f of plan.fields)visit(f.projection_root,1,new Set());
check(deepest>12&&deepest<=24);
const arrays=plan.projections.filter(node=>node.kind==='array');
check(arrays.some(node=>node.max_count===32&&node.element_bytes===24&&node.skip_negative_i32_offset===0));
check(arrays.some(node=>node.max_count===64&&node.element_bytes===8));
check(arrays.some(node=>node.max_count===128&&node.count_offset===0x20));
check(plan.projections.some(node=>node.gate==='ancestor'&&node.expected_class===metadata.classes.Character&&node.ancestry_depth===10));
check(plan.max_bytes===1048576&&plan.max_reads===65536);
assert.throws(()=>vm.runInContext('__arkdpsBuildColdCapturePlan({...metadata,cold_metadata_ready:false})',context));checks++;
assert.throws(()=>vm.runInContext('__arkdpsColdMetadataRead({toString:()=>"0xdead"},8)',context));checks++;
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(plan));
process.stdout.write(JSON.stringify({checks,nodes:plan.projections.length,depth:deepest,live_reads:0,live_calls:0})+'\n');
