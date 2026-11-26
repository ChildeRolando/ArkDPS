"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Pointer{constructor(x){this.value=BigInt(x);}toString(){return '0x'+this.value.toString(16);}isNull(){return this.value===0n;}equals(p){return this.value===p.value;}}
const c=vm.createContext({ptr:x=>new Pointer(x),ArrayBuffer,Map,Set});
vm.runInContext(fs.readFileSync('native_pc/native_event_runtime/cold_plan.js','utf8'),c);
const graph=new Map([['0x1','0x0'],['0x2','0x0']]);for(let i=3;i<=40;i++)graph.set('0x'+i.toString(16),'0x'+(i===3?1:i-1).toString(16));
c.graph=graph;vm.runInContext('__arkdpsSealClassHierarchy(graph)',c);
assert.equal(vm.runInContext('__arkdpsColdAssignable(ptr(1),ptr(40))',c),true);
assert.equal(vm.runInContext('__arkdpsColdAssignable(ptr(2),ptr(40))',c),false);
assert.equal(vm.runInContext('__arkdpsColdClassLineage(ptr(40)).length',c),39);
assert.equal(vm.runInContext('Object.isFrozen(__arkdpsColdClassLineage(ptr(40)))',c),true);
assert.throws(()=>vm.runInContext('__arkdpsColdAssignable(ptr(1),ptr(99))',c),/unverified/);
assert.throws(()=>vm.runInContext('__arkdpsColdAssignable(ptr(99),ptr(99))',c),/unverified/);
for(const bad of [new Map([['0x1','0x2'],['0x2','0x1']]),new Map([['0x1','0x2']])]){c.bad=bad;assert.throws(()=>vm.runInContext('__arkdpsSealClassHierarchy(bad)',c),/cycle|incomplete/);}
// Failed replacement cannot publish partial new relationships.
assert.equal(vm.runInContext('__arkdpsColdAssignable(ptr(1),ptr(40))',c),true);
console.log('PASS_CLASS_HIERARCHY long/foreign/unknown/cycle/missing/atomic');
