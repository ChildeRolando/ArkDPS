"use strict";
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('event_probe_native_pc.js','utf8'),c=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function rawGuardComparison('),source.indexOf('function scopeReadGeometry(')),c);
function comparison(kind,expected,size){c.kind=kind;c.expected=expected;c.size=size;return vm.runInContext('rawGuardComparison(kind,expected,size)',c);}
function matches(view,predicate){c.view=view;c.predicate=predicate;return vm.runInContext('matchesRawGuard(view,predicate)',c);}
function bytes(size){return new DataView(new ArrayBuffer(size));}
for(const [kind,value,width,put]of [['b',255,1,'setUint8'],['i',-2147483648,4,'setInt32'],['u',4294967295,4,'setUint32']]){
 const v=bytes(width);v[put](0,value,true);const p=comparison(kind,value,width);assert(p&&matches(v,p));v.setUint8(0,v.getUint8(0)^1);assert(!matches(v,p));
}
const pointer=bytes(8);pointer.setBigUint64(0,0xfedcba9876543210n,true);const pp=comparison('p','0xfedcba9876543210',8);assert(matches(pointer,pp));pointer.setUint32(4,0,true);assert(!matches(pointer,pp));
const numeric=bytes(8);numeric.setBigUint64(0,9007199254740993n,true);const np=comparison('n',Number(9007199254740993n),8);assert(matches(numeric,np));numeric.setBigUint64(0,9007199254740992n,true);assert(matches(numeric,np),'preserve original Number(U64), not stricter exactbits');
const block=bytes(12);block.setInt32(0,-7,true);block.setBigUint64(4,0x7fff12345678n,true);const bp=comparison('block',[['i',0,-7],['p',4,0x12345678,0x7fff]],12);assert(matches(block,bp));block.setUint32(8,0x7ffe,true);assert(!matches(block,bp));
for(const text of ['', 'A\0\u4e2d\ud800\udfff\ud83d\ude00']){
 const view=bytes(12+text.length*2);view.setInt32(8,text.length,true);for(let i=0;i<text.length;i++)view.setUint16(12+2*i,text.charCodeAt(i),true);
 const p=comparison('text',text,0);assert(matches(view,p));assert(!JSON.stringify(p).includes('\\ud800'));
 view.setInt32(8,text.length+1,true);assert(!matches(view,p));
}
for(const [kind,value,size]of [['b',256,1],['i',2147483648,4],['u',-1,4],['n',NaN,8],['p','-1',8],['block',[['i',2,7]],4],['block',[],4],['text','x'.repeat(201),0],['unknown',1,8]])assert.equal(comparison(kind,value,size),null);
c.mutable={version:1,words:[{offset:0,kind:'u32',value:7}],utf16:[0xd800]};
vm.runInContext('globalThis.sealed=sealRawComparison(mutable)',c);c.mutable.words[0].value=8;c.mutable.utf16[0]=65;
assert.equal(c.sealed.words[0].value,7);assert.equal(c.sealed.utf16[0],0xd800);
assert(Object.isFrozen(c.sealed)&&Object.isFrozen(c.sealed.words)&&Object.isFrozen(c.sealed.words[0])&&Object.isFrozen(c.sealed.utf16));
assert(!Object.isFrozen(c.mutable)&&!Object.isFrozen(c.mutable.words[0]));
console.log('PASS_RAW_GRAPH_COMPARISONS exactbits/NumberU64/UTF16units/signed/block/unsupported');
