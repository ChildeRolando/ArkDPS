// Narrow compatibility surface for the unmodified PC event probe. Not a Frida runtime.
'use strict';
function __integer(value) {
 if(value instanceof NativePointer)return value.value;
 if(value instanceof NativeInteger)return value.value;
 if(typeof value==='bigint')return value;
 if(typeof value==='number'&&!Number.isSafeInteger(value))throw new TypeError('unsafe integer');
 return BigInt(value);
}
class NativeInteger {
 constructor(value){this.value=BigInt(value);}
 toNumber(){return Number(this.value);}
 toString(radix=10){return this.value.toString(radix);}
}
class NativePointer {
 constructor(value){this.value=BigInt.asUintN(64,__integer(value));}
 add(value){return new NativePointer(this.value+__integer(value));}
 sub(value){return new NativePointer(this.value-__integer(value));}
 equals(value){return this.value===ptr(value).value;}
 isNull(){return this.value===0n;}
 toString(){return '0x'+this.value.toString(16);}
 toInt32(){return Number(BigInt.asIntN(32,this.value));}
 readByteArray(size){return __read(this.value,size);}
 __view(size){return new DataView(this.readByteArray(size));}
 readPointer(){return new NativePointer(this.__view(8).getBigUint64(0,true));}
 readU8(){return this.__view(1).getUint8(0);}
 readU16(){return this.__view(2).getUint16(0,true);}
 readU32(){return this.__view(4).getUint32(0,true);}
 readS32(){return this.__view(4).getInt32(0,true);}
 readU64(){return new NativeInteger(this.__view(8).getBigUint64(0,true));}
 readS64(){return new NativeInteger(this.__view(8).getBigInt64(0,true));}
 readFloat(){return this.__view(4).getFloat32(0,true);}
 readCString(){return __cstring(this.value);}
 readUtf16String(length){return __utf16(this.value,length);}
 writeU64(value){const b=new ArrayBuffer(8);new DataView(b).setBigUint64(0,BigInt.asUintN(64,__integer(value)),true);__write(this.value,b);return this;}
 writePointer(value){return this.writeU64(value);}
}
function ptr(value){return value instanceof NativePointer?value:new NativePointer(value);}
const Memory={alloc(size){return ptr(__alloc(size));}};
function __module(name){
 const found=__moduleInfo(String(name));
 return {name:found.name,base:ptr(found.base),size:found.size,
  getExportByName(symbol){return ptr(__export(found.name,String(symbol)));}};
}
const Process={platform:'windows',arch:'x64',id:__pid(),
 getModuleByName:__module,
 findModuleByAddress(address){const found=__findModule(ptr(address).value);return found===null?null:__module(found);}};
function NativeFunction(address,result,parameters){
 if(!Array.isArray(parameters)||parameters.length>12)throw new Error('unsupported native signature');
 const allowed=new Set(['pointer','uint','int','size_t','bool','void']);
 if(!allowed.has(result)||parameters.some(t=>!allowed.has(t)||t==='void'))throw new Error('only GP IL2CPP signatures are supported');
 const target=ptr(address).value;
 return function(...args){
  if(args.length!==parameters.length)throw new Error('native argument count mismatch');
  const raw=__nativeCall(target,args.map((v,i)=>parameters[i]==='bool'?(v?1n:0n):__integer(v)));
  switch(result){case 'void':return undefined;case 'pointer':return ptr(raw);case 'bool':return BigInt.asUintN(8,raw)!==0n;
   case 'int':return Number(BigInt.asIntN(32,raw));case 'uint':return Number(BigInt.asUintN(32,raw));case 'size_t':return Number(raw);}
 };
}
const Interceptor={attach(address,callbacks){const id=__attach(ptr(address).value,callbacks);return {detach(){__detach(id);}};}};
const rpc={exports:{}};
function recv(){/* Event requests are dispatched synchronously by the authenticated pipe. */}
function send(value){__send(JSON.stringify(value));}
function __arkdpsPointer(value){return ptr(value);}
function __arkdpsRequest(json){
 const command=JSON.parse(json),api=rpc.exports;
 if(!command||typeof command!=='object')throw new Error('invalid request');
 // Do not even advance the replayable-read watermark while a native callback
 // is pending on this JS lock. A retry cannot authorize a later tail ACK.
 if(command.operation==='terminal_read'&&__nativeActive()>0)
  return JSON.stringify({events:[],hp_context:[],retry:true,terminal_only:true,terminal_read_version:1,
   active:__nativeActive(),active_hooks:[],after:command.after,through:command.through});
 let result;
 switch(command.operation){
  case 'mark':
   if(Object.prototype.hasOwnProperty.call(command,'autochess_enabled')&&typeof probeAdapter!=='undefined'&&typeof probeAdapter.setAutoChessMode==='function')
    probeAdapter.setAutoChessMode(command.autochess_enabled);
   result=api.mark();break;
  case 'read':result=api.readEvents(command.after,command.through);break;
  case 'terminal_read':result=api.readTerminalEvents(command.after,command.through);break;
  case 'ack':result=api.ackEvents(command.sequence);break;
  case 'stop':result=api.stop();break;
  default:throw new Error('unknown operation');
 }
 // A native callback can be waiting for this JS lock. Never publish a quiet
 // fence while that invocation exists, even before its JS onEnter executes.
 if((command.operation==='mark'||command.operation==='terminal_read')&&__nativeActive()>0){
  result.active=Math.max(result.active||0,__nativeActive());result.retry=true;
  if(command.operation==='terminal_read'){result.events=[];result.hp_context=[];}
 }
 return JSON.stringify(result);
}
