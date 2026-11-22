// Execute the actual MuMu queue API functions with a retained pre-baseline hit.
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[2],'utf8');
function section(start,end){
 const from=source.indexOf('function '+start+'('),to=source.indexOf('function '+end+'(',from+1);
 if(from<0||to<0)throw Error('Production queue function missing');
 return source.slice(from,to);
}
const context={};vm.createContext(context);
vm.runInContext(`
 let sequence=2,epoch=2,active=0,fatal=null,stopped=false;
 let acknowledgedSequence=0,readThroughSequence=0,hpContextDropped=0;
 let frame=1,game_time=0;const coalesceEnemyHp=false,activeHooks=new Map(),hpContext=[];
 const queue=[
  {sequence:1,frame:6000,game_time:200,source:'char_a',kind:'damage',amount:999},
  {sequence:2,frame:1,game_time:0,source:'char_a',kind:'damage',amount:7}
 ];
 function time(){return {frame,game_time};}
 function currentTerminalBoundary(){return null;}
 function eventQuality(){return {quality_contract_version:1,damage_unknown:false,
  engagement_unknown:false,shield_unknown:false,skill_unknown:false,
  mitigation_ready:true,mitigation_unknown:false};}
 ${section('readEvents','ackEvents')}
 ${section('ackEvents','refreshEnemyEntryFrame')}
 ${section('markEvents','receiveEventRequest')}
 const first={mark:markEvents(),batch:readEvents(0,2)};
 first.ack=ackEvents(2);
 frame=2;game_time=1;sequence=3;epoch=3;
 queue.push({sequence:3,frame,game_time,source:'char_a',kind:'damage',amount:20});
 const second={mark:markEvents(),batch:readEvents(2,3)};
 globalThis.result={first,second};
`,context);
process.stdout.write(JSON.stringify(context.result));
