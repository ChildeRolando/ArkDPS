'use strict';
const fs=require('node:fs'),vm=require('node:vm');
const base=fs.readFileSync(process.argv[2],'utf8'),now=fs.readFileSync('event_probe_native_pc.js','utf8');
const meta={clock:{toString:()=> '0x1000'},string_class:'0x2000',skill_offsets:{charSkill:0x100,owner:0x20,data:0x28,prefab:0x10,id:0x18,name:0x20},skill_wait:0x50,attack_type_offset:0x30,classes:{Character:'0x3000'},factory_addresses:['0x7000','0x8000'],immutable_class_parents:true};
function plans(text){const c=vm.createContext({});vm.runInContext(text.slice(text.indexOf('function nativeCapturePlan(')),c);return Object.fromEntries(['Entity.ApplyModifier','Unit.OnTakeDamage','Entity.OnTakeDamage','Entity.OnAfterOutputDamage','Unit.OnAfterOutputDamage','Buff.OnTakeDamage','BlockDamage._AssignDynamicVar'].map(n=>[n,JSON.parse(JSON.stringify(c.nativeCapturePlan(n,{toString:()=> '0x9000'},meta)))]));}
const result={base:plans(base),current:plans(now)};process.stdout.write(JSON.stringify(result));
