#include "quickjs.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
static constexpr unsigned N=40,H=128;
static JSClassID cid; static JSAtom atoms[N+1],keys[H];static unsigned ids[H];static unsigned made=0,freed=0,marked=0;
struct Packet{JSValue values[N];};
static int indexof(JSAtom a){unsigned h=(a*2654435761u)&(H-1);for(unsigned j=0;j<H;++j){if(keys[h]==a)return int(ids[h]);if(!keys[h])return -1;h=(h+1)&(H-1);}return -1;}
static JSValue value(JSContext*c,Packet*p,int i){return i==int(N)?JS_NewUint32(c,N):JS_DupValue(c,p->values[i]);}
static int own(JSContext*c,JSPropertyDescriptor*d,JSValueConst o,JSAtom a){int i=indexof(a);if(i<0)return 0;auto*p=(Packet*)JS_GetOpaque(o,cid);if(!p)return 0;if(d){d->flags=i==int(N)?0:JS_PROP_ENUMERABLE;d->value=value(c,p,i);d->getter=JS_UNDEFINED;d->setter=JS_UNDEFINED;}return 1;}
static JSValue get(JSContext*c,JSValueConst o,JSAtom a,JSValueConst){int i=indexof(a);auto*p=(Packet*)JS_GetOpaque(o,cid);return i<0||!p?JS_UNDEFINED:value(c,p,i);}
static int names(JSContext*c,JSPropertyEnum**tab,uint32_t*n,JSValueConst){*n=N+1;*tab=(JSPropertyEnum*)js_malloc(c,sizeof(JSPropertyEnum)*(N+1));if(!*tab)return -1;for(unsigned i=0;i<=N;++i){(*tab)[i].atom=JS_DupAtom(c,atoms[i]);(*tab)[i].is_enumerable=i<N;}return 0;}
static int del(JSContext*,JSValueConst,JSAtom a){return indexof(a)<0;}
static int define(JSContext*,JSValueConst,JSAtom,JSValueConst,JSValueConst,JSValueConst,int){return 0;}
static int set(JSContext*c,JSValueConst,JSAtom,JSValueConst,JSValueConst,int flags){if(flags&JS_PROP_THROW_STRICT){JS_ThrowTypeError(c,"immutable packet");return -1;}return 0;}
static void finalize(JSRuntime*r,JSValue o){auto*p=(Packet*)JS_GetOpaque(o,cid);if(p){for(auto v:p->values)JS_FreeValueRT(r,v);js_free_rt(r,p);++freed;}}
static void mark(JSRuntime*r,JSValueConst o,JS_MarkFunc*f){auto*p=(Packet*)JS_GetOpaque(o,cid);if(p){for(auto v:p->values)JS_MarkValue(r,v,f);++marked;}}
static JSValue factory(JSContext*c,JSValueConst,int argc,JSValueConst*argv){int type=0,mixed=0;JS_ToInt32(c,&type,argv[0]);if(argc>1)JS_ToInt32(c,&mixed,argv[1]);JSValue o=type==2?JS_NewObjectProtoClass(c,JS_NULL,cid):JS_NewArray(c);if(JS_IsException(o))return o;Packet*p=nullptr;if(type==2){p=(Packet*)js_malloc(c,sizeof(Packet));if(!p){JS_FreeValue(c,o);return JS_EXCEPTION;}for(auto&v:p->values)v=JS_UNDEFINED;JS_SetOpaque(o,p);++made;}for(unsigned i=0;i<N;++i){auto v=mixed&&i%4==0?JS_NewBigInt64(c,int64_t(i)+1):JS_NewInt32(c,i+1);if(p)p->values[i]=v;else if(JS_DefinePropertyValueUint32(c,o,i,v,(type==0||type==3)?JS_PROP_ENUMERABLE:JS_PROP_C_W_E)<0){JS_FreeValue(c,o);return JS_EXCEPTION;}}if(type==0||type==3)JS_DefinePropertyValueStr(c,o,"length",JS_NewUint32(c,N),0);if(type!=1)JS_PreventExtensions(c,o);return o;}
static JSValue now(JSContext*c,JSValueConst,int,JSValueConst*){return JS_NewFloat64(c,std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count());}
static void fatal(JSContext*c){auto e=JS_GetException(c);const char*s=JS_ToCString(c,e);fprintf(stderr,"%s\n",s?s:"exception");auto st=JS_GetPropertyStr(c,e,"stack");const char*t=JS_ToCString(c,st);if(t)fprintf(stderr,"%s\n",t);exit(2);}
static JSValue eval(JSContext*c,const char*s){auto v=JS_Eval(c,s,strlen(s),"micro.js",JS_EVAL_TYPE_GLOBAL);if(JS_IsException(v))fatal(c);return v;}
int main(){auto*r=JS_NewRuntime();auto*c=JS_NewContext(r);JS_NewClassID(&cid);static JSClassExoticMethods ex{};ex.get_own_property=own;ex.get_own_property_names=names;ex.delete_property=del;ex.define_own_property=define;ex.get_property=get;ex.set_property=set;JSClassDef def{};def.class_name="ImmutableNumericPacket";def.finalizer=finalize;def.gc_mark=mark;def.exotic=&ex;JS_NewClass(r,cid,&def);for(unsigned i=0;i<=N;++i){atoms[i]=i==N?JS_NewAtom(c,"length"):JS_NewAtomUInt32(c,i);unsigned h=(atoms[i]*2654435761u)&(H-1);while(keys[h])h=(h+1)&(H-1);keys[h]=atoms[i];ids[h]=i;}auto g=JS_GetGlobalObject(c);JS_SetPropertyStr(c,g,"make",JS_NewCFunction(c,factory,"make",2));JS_SetPropertyStr(c,g,"now",JS_NewCFunction(c,now,"now",0));JS_FreeValue(c,g);
const char*source=R"JS(
function check(x){if(x.length!==40||x[0]!==1||x[39]!==40)throw Error('value');if(!Object.isFrozen(x))throw Error('not immutable');const d=Object.getOwnPropertyDescriptor(x,'0');if(d.writable||d.configurable||!d.enumerable)throw Error('descriptor');if(Reflect.set(x,'0',9)||Reflect.deleteProperty(x,'0')||Reflect.defineProperty(x,'40',{value:4}))throw Error('mutable');if(x[40]!==undefined||Object.keys(x).length!==40)throw Error('keys');try{(function(){'use strict';x[0]=9;})();throw Error('strict write accepted');}catch(e){if(e.message==='strict write accepted')throw e;}}
check(make(0,0));check(Object.freeze(make(1,0)));check(make(2,0));
let rows=[],sink=0;const count=12000,reads=20000;
for(let mixed=0;mixed<2;mixed++)for(let rep=0;rep<4;rep++)for(const type of (rep%2?[2,1,0,3]:[3,0,1,2])){
 const freeze=type===0||type===1,x=freeze?Object.freeze(make(type,mixed)):make(type,mixed);let t=now();
 for(let k=0;k<reads;k++)for(let i=0;i<40;i++)sink+=mixed?Number(x[i]):x[i];
 let readms=now()-t;t=now();
 for(let k=0;k<count;k++){const a=make(type,mixed);if(freeze)Object.freeze(a);sink+=Number(a[39]);}
 rows.push({mixed,rep,type,read_ns:(readms*1e6)/(reads*40),construct_ns:(now()-t)*1e6/count});
}
JSON.stringify({rows,sink,checks:true});
)JS";auto result=eval(c,source);const char*out=JS_ToCString(c,result);puts(out);JS_FreeCString(c,out);JS_FreeValue(c,result);
for(int type=0;type<3;++type){JSMemoryUsage a{},b{};JS_RunGC(r);JS_ComputeMemoryUsage(r,&a);JSValue args[2]={JS_NewInt32(c,type),JS_NewInt32(c,0)};auto o=factory(c,JS_UNDEFINED,2,args);if(type==1){auto gl=JS_GetGlobalObject(c);JS_SetPropertyStr(c,gl,"held",JS_DupValue(c,o));JS_FreeValue(c,gl);auto v=eval(c,"Object.freeze(held)");JS_FreeValue(c,v);}JS_RunGC(r);JS_ComputeMemoryUsage(r,&b);fprintf(stderr,"type=%d fast_array_delta=%lld properties_delta=%lld\n",type,(long long)(b.fast_array_count-a.fast_array_count),(long long)(b.prop_count-a.prop_count));JS_FreeValue(c,o);}
JS_RunGC(r);for(auto a:atoms)JS_FreeAtom(c,a);JS_FreeContext(c);JS_RunGC(r);fprintf(stderr,"custom made=%u finalized=%u mark_calls=%u\n",made,freed,marked);JS_FreeRuntime(r);return made==freed?0:3;}
