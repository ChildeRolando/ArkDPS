#define main original_hp_observation_test_main
#include "owned_hp_observation_test.cpp"
#undef main
#include <fstream>
#include <iterator>
static CompiledPlan* active_code;static CaptureConfigurations* active_config;static Memory* active_memory;static Snapshot* active_snapshot;
static JSValue reenter(JSContext*,JSValueConst,int,JSValueConst*){begin(*active_code,*active_config,*active_memory,*active_snapshot);return JS_UNDEFINED;}
static JSValue execute(JSContext* c,const std::string& text){
 auto value=JS_Eval(c,text.data(),text.size(),"opaque-io-test.js",JS_EVAL_TYPE_GLOBAL);
 if(JS_IsException(value)){auto error=JS_GetException(c);const auto* message=JS_ToCString(c,error);std::cerr<<(message?message:"JS exception")<<"\n";if(message)JS_FreeCString(c,message);JS_FreeValue(c,error);assert(false);}return value;
}
static void ensure(JSContext* c,const std::string& text){auto value=execute(c,text);assert(JS_ToBool(c,value)==1);JS_FreeValue(c,value);}
static void statement(JSContext* c,const std::string& text){auto value=execute(c,text);JS_FreeValue(c,value);}
static void expose(JSContext* c,const char* name,JSValue value){auto global=JS_GetGlobalObject(c);assert(JS_SetPropertyStr(c,global,name,value)>=0);JS_FreeValue(c,global);}
static std::string contract_json(const CompiledPlan& code,const CaptureConfigurations& config){
 Json contract{{"address","0x9000"},{"mode_epoch",0},{"fields",Json::array()}};
 static const char* kinds[]{"u8","u32","u64","i64","utf16","i32"};
 for(unsigned phase=0;phase<2;++phase)for(const auto& field:code.hp_observations[phase]){
  Json values=Json::array();for(const auto& value:field.values)values.push_back({{"kind",kinds[value.kind]},{"offset",value.offset}});
  contract["fields"].push_back({{"phase",phase?"leave":"entry"},{"name",field.name},{"role",config.roles.at(field.raw.role_id-1)},
   {"kind",field.raw.kind?"utf16":"bytes"},{"bytes",field.raw.bytes},{"max_units",field.raw.max_units},{"observation",{{"name",field.name},{"values",values}}}});
 }return contract.dump();
}
int main(int argc,char** argv){
 assert(argc==2);auto* runtime=JS_NewRuntime();auto* c=JS_NewContext(runtime);owned::install(c,[]()noexcept{poisoned=true;});
 std::ifstream input(argv[1]);std::string bridge((std::istreambuf_iterator<char>(input)),{});assert(!bridge.empty());bridge.resize(bridge.find("const Memory="));statement(c,bridge);
 Memory memory;memory.initialize();auto code=plan(memory);CaptureConfigurations config;config.intern(code);
 assert(code.hp_io_layouts[0].supported&&code.hp_io_layouts[1].supported);
 auto snapshot=std::make_unique<Snapshot>();capture_point(code,memory,*snapshot);
 statement(c,"globalThis.contract="+contract_json(code,config)+";globalThis.enemy=ptr("+std::to_string(memory.root())+"n);globalThis.reject=fn=>{try{fn();return false}catch(error){return true}};");
 const auto open=[&](Phase phase=Phase::entry){capture_point(code,memory,*snapshot,phase);begin(code,config,memory,*snapshot,phase);statement(c,std::string("__arkdpsReplayBegin({version:1,native_view:true,ticket:'17',edge:'")+(phase==Phase::entry?"entry":"leave")+"',checkpoint:3,facts:{mode_epoch:5}});");};
 const auto without_id=[&](){
  Context context;context.args[0]=memory.root();context.args_valid=1;context.entry_refs=context.args;context.entry_valid=1;context.globals=code.globals;context.globals_valid=code.globals_valid;context.checkpoint=3;
  assert(capture_begin(*snapshot,Phase::entry,context,8192,128)==Error::ok);
  const auto id=code.hp_observations[0][index(code,"hp.id")].presence_id;
  for(unsigned i=0;i<code.plan->field_count;++i)if(code.plan->fields[i].id!=id)capture_append(*code.plan,Phase::entry,context,Memory::read,&memory,*snapshot,i,i+1);
  capture_finalize(*snapshot);
 };
 const auto close=[&](){statement(c,"globalThis.summary=__arkdpsReplayEnd();");owned::end();};
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");
 auto global=JS_GetGlobalObject(c);auto io=JS_GetPropertyStr(c,global,"io");assert(JS_GetOpaque(io,owned::hp_io_class_id));JS_FreeValue(c,io);JS_FreeValue(c,global);
 ensure(c,"Object.isFrozen(io)&&Object.isFrozen(Object.getPrototypeOf(io))&&Object.getPrototypeOf(Object.getPrototypeOf(io))===null&&io.constructor===undefined&&!Reflect.set(io,'identity',()=>0)");
 ensure(c,"io.identity(enemy)[0]===18446744073709551615n&&io.identity(enemy)[1]===73&&io.born(enemy)===1&&io.hp(enemy)===-9223372036854775808n");
 ensure(c,"(()=>{const x=io.clock();return x.length===3&&x[0]===30&&x[1]===-4294967296n&&x[2]===30})()");
 ensure(c,"(()=>{const s=io.id(enemy);return s.length===9&&[65,0,0x4e2d,0xd800,66,0xdfff,67,0xd83d,0xde00].every((v,i)=>s.charCodeAt(i)===v)})()");
 JS_RunGC(runtime);ensure(c,"io.identity(Number(enemy.value))[1]===73&&io.trapClass(enemy)===18446744073709551615n");
 // Reissue within one native/JS lease does not expire already recognized IO.
 auto packet=owned::prepare_hp_observation(c);JS_FreeValue(c,packet);ensure(c,"io.identity(enemy)[1]===73");
 statement(c,"globalThis.savedIo=io;globalThis.summary=__arkdpsReplayEnd();");ensure(c,"reject(()=>io.identity(enemy))");assert(poisoned);owned::end();
 // No JS replay means no constructor token, even while native view is active.
 capture_point(code,memory,*snapshot);begin(code,config,memory,*snapshot);packet=owned::prepare_hp_observation(c);
 JSValue noReplay[]{JS_NewBigUint64(c,0x9000),JS_NewBigUint64(c,memory.root()),JS_DupValue(c,packet),JS_NewInt32(c,2),JS_NewString(c,code.hp_io_layouts[0].signature.c_str())};
 auto absent=owned::hp_observation(c,JS_UNDEFINED,5,noReplay);assert(JS_IsException(absent)&&poisoned);auto thrown=JS_GetException(c);JS_FreeValue(c,thrown);for(auto arg:noReplay)JS_FreeValue(c,arg);JS_FreeValue(c,packet);owned::end();
 // The caller cannot supply a fake token as a fifth argument.
 open();packet=owned::prepare_hp_observation(c);JSValue fakeArgs[]{JS_NewBigUint64(c,0x9000),JS_NewBigUint64(c,memory.root()),JS_DupValue(c,packet),JS_NewInt32(c,2),JS_NewObject(c)};
 auto fake=owned::hp_observation(c,JS_UNDEFINED,5,fakeArgs);assert(JS_IsUndefined(fake)&&poisoned);for(auto arg:fakeArgs)JS_FreeValue(c,arg);JS_FreeValue(c,packet);close();
 // Source-side descriptors cannot relabel or reinterpret an authentic packet.
 for(const auto* edit:{"fields.find(x=>x.name==='hp.identity').observation.values[0].kind='i64'","fields.find(x=>x.name==='hp.identity').observation.values[1].offset=20","fields.find(x=>x.name==='hp.identity').bytes=32","fields.find(x=>x.name==='hp.id').max_units=199","fields.find(x=>x.name==='hp.born').role='other'"}){
  open();statement(c,std::string("globalThis.altered=JSON.parse(JSON.stringify(contract));(()=>{const fields=altered.fields;")+edit+"})();");ensure(c,"reject(()=>__arkdpsOwnedHpObservation(altered,enemy))");assert(poisoned);close();
 }
 // Same raw ticket, pointer, phase, cp and mode still has a fresh Begin nonce.
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");begin(code,config,memory,*snapshot);ensure(c,"reject(()=>io.identity(enemy))");assert(poisoned);close();
 for(const auto& receiver:{"enemy.value+1n","enemy.value+0x10000000000000000n","9007199254740992","({value:enemy.value})"}){
  open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,std::string("reject(()=>io.identity(")+receiver+"))");assert(poisoned);close();
 }
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);enemy.value=Number(enemy.value);");ensure(c,"reject(()=>io.identity(enemy))");assert(poisoned);statement(c,"enemy.value=BigInt(enemy.value);");close();
 // Original converter remains pinned, not a later global replacement.
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);globalThis.originalInteger=__integer;__integer=()=>enemy.value;");ensure(c,"reject(()=>io.identity(enemy.value+1n))");assert(poisoned);statement(c,"__integer=originalInteger;");close();
 // A conversion that reenters Begin is rejected after the callback returns.
 active_code=&code;active_config=&config;active_memory=&memory;active_snapshot=snapshot.get();expose(c,"reenterNative",JS_NewCFunction(c,reenter,"reenterNative",0));
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,"reject(()=>io.identity({[Symbol.toPrimitive](){reenterNative();return enemy.value}}))");assert(poisoned);close();
 // Failed fields remain deferred until the exact demanded method.
 for(const auto status:{Error::field_unreadable,Error::missing_plan_field}){
  if(status==Error::missing_plan_field)without_id();else{capture_point(code,memory,*snapshot);for(unsigned i=0;i<snapshot->field_count;++i)if(snapshot->fields[i].id==code.hp_observations[0][index(code,"hp.id")].presence_id)snapshot->fields[i].error=status;}
  begin(code,config,memory,*snapshot);statement(c,"__arkdpsReplayBegin({version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:3,facts:{mode_epoch:5}});globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");assert(!poisoned);ensure(c,"io.identity(enemy)[1]===73");
  ensure(c,"reject(()=>io.id(enemy))");assert(poisoned==(status==Error::missing_plan_field));close();
  ensure(c,status==Error::field_unreadable?"summary.complete&&summary.unreadable_reads===1":"!summary.complete");
 }
 // Unused structural status never becomes an eager constructor failure.
 without_id();
 begin(code,config,memory,*snapshot);statement(c,"__arkdpsReplayBegin({version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:3,facts:{mode_epoch:5}});globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,"io.born(enemy)===1");assert(!poisoned);close();ensure(c,"summary.complete&&summary.unreadable_reads===0");
 // Two IOs in one lease cannot exchange their attribute handle.
 open(Phase::leave);statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);globalThis.attrs=io.attributes(enemy);globalThis.otherIo=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,"io.attributeCount(attrs)===1&&io.attributeWords(attrs)[0]===18446744073709551615n&&io.closing(enemy)[1]===-9223372036854775808n");ensure(c,"reject(()=>otherIo.attributeCount(attrs))");assert(poisoned);close();
 // An independent conflicting class segment is invisible to identity28 but
 // must still be rejected by trapClass's separate auto 8-byte read.
 capture_point(code,memory,*snapshot);std::uint64_t conflict=1;witness(*snapshot,code.hp_io_layouts[0].body_role,60000,memory.root(),&conflict,8);
 begin(code,config,memory,*snapshot);statement(c,"__arkdpsReplayBegin({version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:3,facts:{mode_epoch:5}});globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,"io.identity(enemy)[1]===73");assert(!poisoned);ensure(c,"reject(()=>io.trapClass(enemy))");assert(poisoned);close();
 // Closing fixed extension keeps the old 3-tuple schema supported above.
 auto code4=plan(memory);for(auto& spec:code4.observation_specs)if(spec.name=="clock_close")spec.values.push_back({4,3});CaptureConfigurations config4;config4.intern(code4);assert(code4.hp_io_layouts[0].supported);
 capture_point(code4,memory,*snapshot);begin(code4,config4,memory,*snapshot);statement(c,"globalThis.contract4="+contract_json(code4,config4)+";__arkdpsReplayBegin({version:1,native_view:true,ticket:'17',edge:'entry',checkpoint:3,facts:{mode_epoch:5}});globalThis.io=__arkdpsOwnedHpObservation(contract4,enemy);");ensure(c,"io.clock().length===4&&io.clock()[3]===-4294967296n");close();
 // All public method entry points require the private native instance.
 open();statement(c,"globalThis.io=__arkdpsOwnedHpObservation(contract,enemy);");ensure(c,"reject(()=>io.identity.call(Object.create(Object.getPrototypeOf(io)),enemy))");assert(poisoned);close();
 statement(c,"io=savedIo=otherIo=attrs=undefined;");JS_RunGC(runtime);owned::end();JS_FreeContext(c);JS_FreeRuntime(runtime);
 std::cout<<"PASS_NATIVE_HP_OPAQUE_IO actual schema/UTF16/raw widths/receiver/nonce/logical End/deferred failure/trap/handle/GC\n";return 0;
}
