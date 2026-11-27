// Execute the actual MuMu queue API functions with a retained pre-baseline hit.
const fs=require('node:fs'),vm=require('node:vm');
const {loadProbeSource}=require('./native_pc_frida_probe_fixture.js');
const source=loadProbeSource(process.argv[2]);
function section(start,end){
 const from=source.indexOf('function '+start+'('),to=source.indexOf('function '+end+'(',from+1);
 if(from<0||to<0)throw Error('Production queue function missing');
 return source.slice(from,to);
}
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../event_probe_store.js'),'utf8'),context);
const terminalDiagnosticFrom=source.indexOf('const terminalDiagnostics='),
 terminalDiagnosticTo=source.indexOf('function terminalIdentity(',terminalDiagnosticFrom);
if(terminalDiagnosticFrom<0||terminalDiagnosticTo<0)throw Error('Production terminal diagnostics missing');
vm.runInContext(`
 let sequence=2,epoch=2,active=0,fatal=null,stopped=false;
 let hpContextDropped=0;const eventStore=new ArkdpsEventStore();
 let frame=1,game_time=0;const coalesceEnemyHp=false,traceNestedParent=false,activeHooks=new Map(),hpContext=eventStore.contexts;
 const probeAdapter={readEnvelope:()=>({fields:{},ready:true}),markEnvelope:()=>({fields:{},ready:true})};
 const initial=[
  {sequence:1,frame:6000,game_time:200,source:'char_a',kind:'damage',amount:999},
  {sequence:2,frame:1,game_time:0,source:'char_a',kind:'damage',amount:7}
 ];
 for(const event of initial)eventStore.append(event);const queue=eventStore.events;
 function time(){return {frame,game_time};}
 function currentTerminalBoundary(){return null;}
 function eventQuality(){return {quality_contract_version:1,damage_unknown:false,
  engagement_unknown:false,shield_unknown:false,skill_unknown:false,
  mitigation_ready:true,mitigation_unknown:false};}
 ${source.slice(terminalDiagnosticFrom,terminalDiagnosticTo)}
 ${section('readEvents','ackEvents')}
 ${section('ackEvents','refreshEnemyEntryFrame')}
 ${section('markEvents','receiveEventRequest')}
 const first={mark:markEvents(),batch:readEvents(0,2)};
 first.ack=ackEvents(2);
 frame=2;game_time=1;sequence=3;epoch=3;
 eventStore.append({sequence:3,frame,game_time,source:'char_a',kind:'damage',amount:20});
 const second={mark:markEvents(),batch:readEvents(2,3)};
 globalThis.result={first,second};
`,context);
process.stdout.write(JSON.stringify(context.result));
