// Execute the production probe with an owned, in-memory IL2CPP fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const path = require('node:path');
function loadProbeSource(sourcePath) {
  const adapter = fs.readFileSync(sourcePath, 'utf8').replace(/\r\n?/g, '\n');
  if (!adapter.startsWith('// ArkDPS event probe adapter v1;')) return adapter;
  const runtime = fs.readFileSync(path.join(path.dirname(sourcePath), 'event_probe_runtime.js'), 'utf8')
    .replace(/\r\n?/g, '\n');
  const autochess = fs.readFileSync(path.join(path.dirname(sourcePath), 'event_probe_autochess.js'), 'utf8')
    .replace(/\r\n?/g, '\n');
  return autochess + '\n' + adapter + '\n' + runtime;
}

function fixture({platform = 'native_pc', sourcePath = process.argv[2],
  settlement = false, traceHpContext = false, traceNestedParent = false, skillCastState = false,
  loggerOffset = platform === 'native_pc' ? 0xe0 : 0xd8, stateMethod = true, nullClassIndex = null,
  unreadableClassIndex = null, extraNullRow = false, lazySkillFields = false,
  fieldOwnerMismatch = false, fieldCountMismatch = false, fieldIteratorMismatch = false,
  fieldApiLeavesTableNull = false, fieldApiStalls = false, lazySkillMethods = false,
  methodCountMismatch = false, methodIteratorMismatch = false, methodOwnerMismatch = false,
  methodApiLeavesTableNull = false, methodApiStalls = false, globals = {},
  loggerMetadataOffset = loggerOffset, stateMetadataOffset = platform === 'native_pc' ? 0x228 : 0x220} = {}) {
  let next = 0x10000;
  const memory = new Map();
  const methods = new Map();
  const hooks = new Map();
  const callbacks = new Map();
  const hookTrace = [];
  let rejectedHook = null;
  const messages = [];
  const requests = new Map();
  const fieldTables = new Map(),methodTables=new Map(),apiCalls=[];
  class Pointer {
    constructor(value) { this.value = BigInt(value); }
    add(offset) { return new Pointer(this.value + BigInt(offset)); }
    sub(other) { return new Pointer(this.value - other.value); }
    isNull() { return this.value === 0n; }
    equals(other) { return other instanceof Pointer && this.value === other.value; }
    toString() { return '0x' + this.value.toString(16); }
    toInt32() { return Number(BigInt.asIntN(32, this.value)); }
    cell() {
      if (!memory.has(this.toString())) throw Error('Unreadable fixture address ' + this);
      return memory.get(this.toString());
    }
    readPointer() { const value = this.cell(); return value instanceof Pointer ? value : ptr(value); }
    readCString() { return this.cell(); }
    readUtf16String() { return this.cell(); }
    readU8() { return Number(this.cell()); }
    readU16() { return Number(this.cell()); }
    readU32() { return Number(this.cell()); }
    readS32() { return Number(this.cell()); }
    readFloat() { return Number(this.cell()); }
    readS64() {
      const value = BigInt(this.cell());
      return {toString: () => value.toString(), toNumber: () => Number(value)};
    }
    readU64() { return this.readS64(); }
    readByteArray(size) {
      assert(Number.isSafeInteger(size) && size >= 0 && size <= 128, 'owned byte-read bound');
      const bytes = new ArrayBuffer(size), view = new DataView(bytes);
      for (let offset = 0; offset < size;) {
        const key = this.add(offset).toString();
        // Scalar fixture cells omit struct padding; real allocated bytes exist.
        if (!memory.has(key)) { offset += 4; continue; }
        const value = memory.get(key);
        if (value instanceof Pointer || typeof value === 'bigint') {
          assert(offset + 8 <= size, 'owned pointer exceeds byte-read range');
          view.setBigUint64(offset, BigInt.asUintN(64, value instanceof Pointer ? value.value : value), true);
          offset += 8;
        } else {
          view.setInt32(offset, Number(value), true);
          offset += 4;
        }
      }
      return bytes;
    }
    writeU64(value) { write(this, BigInt(value)); }
  }
  function ptr(value) { return value instanceof Pointer ? value : new Pointer(value); }
  function alloc() { const value = ptr(next); next += 0x2000; return value; }
  function write(pointer, value) { memory.set(pointer.toString(), value); }
  function text(value) { const pointer = alloc(); write(pointer, value); return pointer; }
  function managedString(value) {
    const pointer = alloc(); write(pointer.add(0x10), value.length); write(pointer.add(0x14), value); return pointer;
  }
  const classes = [];
  function klass(name, namespace, fields, names = {}) {
    const k = alloc();
    write(k.add(0x10), text(name)); write(k.add(0x18), text(namespace));
    const ft = alloc(); write(k.add(0x80), ft); write(k.add(0x124), Object.keys(fields).length);
    fieldTables.set(k.toString(), {table: ft, count: Object.keys(fields).length});
    Object.entries(fields).forEach(([name, offset], index) => {
      const f = ft.add(index * 32);
      write(f, text(name)); write(f.add(16), k); write(f.add(24), offset);
    });
    const mt = alloc(); write(k.add(0x98), mt); write(k.add(0x120), Object.keys(names).length);
    const records=[];methodTables.set(k.toString(),{table:mt,records});
    Object.entries(names).forEach(([name, argc], index) => {
      const m = alloc(), code = alloc();
      write(mt.add(index * 8), m); write(m, code); write(m.add(0x18), text(name)); write(m.add(0x20), k);
      methods.set(m.toString(), argc); callbacks.set(name + '@' + k, code);
      records.push(m);
    });
    classes.push(k); return k;
  }
  const entity = klass('Entity', 'Torappu.Battle', {'m_hp': 0x40, '<id>k__BackingField': 0x148},
    {ApplyModifier: 2, OnAfterOutputDamage: 1});
  const unitClass = klass('Unit', 'Torappu.Battle', {}, {OnTakeDamage: 2});
  const buffClass = klass('Buff', 'Torappu.Battle', {
    m_source: 0x18, m_blackboard: 0xb8, '<key>k__BackingField': 0x208,
    '<instanceUid>k__BackingField': 0x218, '<hasShield>k__BackingField': 0x27c,
  }, {OnTakeDamage: 1});
  klass('BuffContainer', '', {});
  const blockDamage = klass('BlockDamage', '', {}, {_AssignDynamicVar: 4});
  klass('Modifier', 'Torappu.Battle', {
    m_source: 0x10, m_target: 0x20, m_originalSource: 0x30, m_value: 0x40, realDelta: 0x58,
    '<targetType>k__BackingField': 0x70, '<damageType>k__BackingField': 0x78, '<cancelReason>k__BackingField': 0x80,
  });
  const controllerMethods = {OnDestroy: 0, OnCharacterFinished: 2};
  if (stateMethod) controllerMethods.set_state = 1;
  const controllerClass = klass('BattleController', 'Torappu.Battle', {
    s_fixedFrameCnt: 0x14, s_fixedPlayTime: 0x18, m_logger: loggerMetadataOffset,
    m_levelData: platform === 'native_pc' ? 0x160 : 0x158, m_state: stateMetadataOffset,
  }, controllerMethods);
  const character = klass('Character', 'Torappu.Battle', {'<data>k__BackingField': 0x538, m_skill: 0x3c8},
    {OnSkillStart: 0, OnSkillFinish: 0, OnAfterSkill: 2,
      ...(skillCastState?{SwitchToSkillState:0,OnSkillCastSucceed:0}:{})});
  const basicSkill = klass('BasicSkill', 'Torappu.Battle', {m_owner: 0xa8, m_data: 0xb0,
    ...(skillCastState?{m_waitForSkillEnd:0x98}:{})}, {Tick:0,...(skillCastState?{OnCastFinish:3}:{})});
  const bObject = settlement || skillCastState ? klass('BObject', 'Torappu.Battle', {
    '<instanceUid>k__BackingField': 0x18, '<borned>k__BackingField': 0x25,
    '<isDisappeared>k__BackingField': 0x26,
  }, {Born: 0, OnRecycle: 0}) : null;
  const enemyClass = settlement ? klass('Enemy', 'Torappu.Battle', {m_currentTile: 0x360},
    {_UpdatePlayerSideAndMapLayer: 0}) : null;
  if(lazySkillFields)write(basicSkill.add(0x80),ptr(0));
  if(lazySkillMethods)write(basicSkill.add(0x98),ptr(0));
  if(fieldOwnerMismatch)write(fieldTables.get(basicSkill.toString()).table.add(16),entity);
  if(methodOwnerMismatch){
    const stateIndex=Object.keys(controllerMethods).indexOf('set_state');
    write(methodTables.get(controllerClass.toString()).records[stateIndex].add(0x20),entity);
  }
  const skillData = klass('SkillData', 'Torappu', {prefabKey: 0x50, skillId: 0x18, name: 0x10});
  const characterData = klass('BattleCharacterData', 'Torappu.Battle', {uniqueId: 0x6c});
  const skillCastFieldTypes=new Map(),skillCastMethodTypes=new Map(),skillCastTypeInfo=new Map();
  let abilityClass=null,castSkillClass=null;
  if(skillCastState){
    const options=typeof skillCastState==='object'?skillCastState:{};
    abilityClass=klass('Ability','Torappu.Battle',{});
    castSkillClass=klass('CastSkillWithLimitTimes','Torappu.Battle',{});
    function type(name,namespace,{valueType=false,size=0,byRef=false,isEnum=false,klassPointer=null}={}){
      const k=klassPointer||klass(name,namespace,{}),t=alloc();
      skillCastTypeInfo.set(t.toString(),{klass:k,valueType,size,byRef,isEnum});
      skillCastTypeInfo.set(k.toString(),{klass:k,valueType,size,byRef,isEnum});
      return t;
    }
    const boolean=type('Boolean','System',{valueType:true,size:1});
    const integer=type('Int32','System',{valueType:true,size:4});
    const voidType=type('Void','System',{valueType:true});
    const ability=type('Ability','Torappu.Battle',{klassPointer:abilityClass,byRef:!!options.abilityByRef});
    const reason=type('FinishReason','',{valueType:true,size:options.reasonSize||4,isEnum:!options.reasonNotEnum,byRef:!!options.reasonByRef});
    const last=options.lastInt?integer:options.lastByRef?type('Boolean','System',{valueType:true,size:1,byRef:true}):boolean;
    const fields=fieldTables.get(basicSkill.toString());
    for(let i=0;i<fields.count;i++){
      const field=fields.table.add(i*32);
      if(field.readPointer().readCString()==='m_waitForSkillEnd')skillCastFieldTypes.set(field.toString(),options.waitInt?integer:boolean);
    }
    for(const m of methodTables.get(basicSkill.toString()).records){
      if(m.add(0x18).readPointer().readCString()==='OnCastFinish')skillCastMethodTypes.set(m.toString(),
        {parameters:[ability,reason,last],result:voidType,flags:options.staticMethod?452|0x10:452});
    }
  }
  const parent = alloc(), singletonStatics = alloc(), clockStatics = alloc();
  const controller = alloc(), native = alloc(), logger = alloc(), stats = alloc(), level = alloc();
  const loggerClass = alloc(), statsClass = alloc(), levelClass = klass('LevelData', 'Torappu', {levelId: 0x18});
  // Typed optional stage reflection needs the actual String field type too.
  // Use reserved bytes in this owned allocation; keep every existing fixture
  // address unchanged so the complete frozen normal-wire oracle stays exact.
  const levelIdType=levelClass.add(0x1800);
  write(levelIdType,ptr(0));write(levelIdType.add(8),14<<16);
  write(fieldTables.get(levelClass.toString()).table.add(8),levelIdType);
  for (const [k, name] of [[loggerClass, 'BattleLogger'], [statsClass, 'BattleStats']])
    write(k.add(0x10), text(name));
  write(controllerClass.add(0x58), parent); write(parent.add(0xb8), singletonStatics);
  write(singletonStatics, controller); write(controllerClass.add(0xb8), clockStatics);
  write(controller, controllerClass); write(controller.add(0x10), native); write(native.add(0x28), controller);
  write(controller.add(platform === 'native_pc' ? 0x228 : 0x220), 2);
  write(controller.add(loggerOffset), logger); write(logger, loggerClass); write(logger.add(0x18), controller);
  write(logger.add(0x30), stats); write(stats, statsClass);
  write(controller.add(platform === 'native_pc' ? 0x160 : 0x158), level); write(level, levelClass);
  write(level.add(0x18), managedString('Obt/Weekly/level_weekly_melee_5'));
  function clock(frame, rawTime) { write(clockStatics.add(0x14), frame); write(clockStatics.add(0x18), rawTime); }
  clock(0, 0n);
  const domain = alloc(), assemblyArray = alloc(), assembly = alloc(), image = alloc();
  write(assemblyArray, assembly);
  const exports = {
    il2cpp_domain_get: () => domain, il2cpp_thread_attach: () => alloc(), il2cpp_thread_detach: () => {},
    il2cpp_domain_get_assemblies: (_domain, size) => { size.writeU64(1); return assemblyArray; },
    il2cpp_assembly_get_image: () => image, il2cpp_image_get_name: () => text('Assembly-CSharp.dll'),
    il2cpp_image_get_class_count: () => classes.length + (extraNullRow ? 1 : 0),
    il2cpp_image_get_class: (_image, index) => index === unreadableClassIndex ? ptr(0x10) :
      index === nullClassIndex || index >= classes.length ? ptr(0) : classes[index],
    il2cpp_method_get_param_count: method => methods.get(method.toString()),
    il2cpp_class_is_assignable_from: (base, candidate) => base.equals(candidate) ||
      skillCastState && base.equals(basicSkill) && candidate.equals(castSkillClass) ||
      skillCastState && base.equals(entity) && [unitClass,character,enemyClass].filter(Boolean).some(k=>k.equals(candidate)) ||
      bObject !== null && base.equals(bObject) && [entity, unitClass, character, enemyClass].some(k => k.equals(candidate)),
    il2cpp_class_get_fields: (k,iterator) => {
      const metadata=fieldTables.get(k.toString()),index=iterator.readU64().toNumber();
      if(index===0&&!(fieldApiLeavesTableNull&&k.equals(basicSkill)))write(k.add(0x80),metadata.table);
      const count=metadata.count-(fieldCountMismatch&&k.equals(basicSkill)?1:0);
      if(index>=count)return ptr(0);
      if(!(fieldApiStalls&&k.equals(basicSkill)))iterator.writeU64(index+1);
      return metadata.table.add(index*32+(fieldIteratorMismatch&&k.equals(basicSkill)?32:0));
    },
    il2cpp_class_get_methods: (k,iterator) => {
      const metadata=methodTables.get(k.toString()),index=iterator.readU64().toNumber();
      if(index===0&&!(methodApiLeavesTableNull&&k.equals(basicSkill)))write(k.add(0x98),metadata.table);
      const count=metadata.records.length-(methodCountMismatch&&k.equals(basicSkill)?1:0);
      if(index>=count)return ptr(0);
      if(!(methodApiStalls&&k.equals(basicSkill)))iterator.writeU64(index+1);
      return methodIteratorMismatch&&k.equals(basicSkill)?methodTables.get(entity.toString()).records[0]:metadata.records[index];
    },
  };
  if(skillCastState)Object.assign(exports,{
    il2cpp_class_from_type:t=>skillCastTypeInfo.get(t.toString()).klass,
    il2cpp_type_is_byref:t=>!!skillCastTypeInfo.get(t.toString()).byRef,
    il2cpp_class_get_namespace:k=>k.add(0x18).readPointer(),
    il2cpp_field_get_type:f=>skillCastFieldTypes.get(f.toString()),
    il2cpp_field_get_flags:()=>typeof skillCastState==='object'&&skillCastState.waitStatic?0x10:1,
    il2cpp_class_is_valuetype:k=>!!skillCastTypeInfo.get(k.toString())?.valueType,
    il2cpp_class_is_enum:k=>!!skillCastTypeInfo.get(k.toString())?.isEnum,
    il2cpp_class_value_size:k=>skillCastTypeInfo.get(k.toString())?.size??0,
    il2cpp_method_get_param:(m,i)=>skillCastMethodTypes.get(m.toString()).parameters[i],
    il2cpp_method_get_return_type:m=>skillCastMethodTypes.get(m.toString()).result,
    il2cpp_method_get_flags:m=>skillCastMethodTypes.get(m.toString()).flags,
  });
  const module = {name: platform === 'native_pc' ? 'GameAssembly.dll' : 'libil2cpp.so',
    base: ptr(0x1000), getExportByName: name => name};
  const context = {
    arkdpsTraceHpContext: traceHpContext,
    arkdpsTraceNestedParent: traceNestedParent,
    Process: {platform: platform === 'native_pc' ? 'windows' : 'linux',
      arch: platform === 'native_pc' ? 'x64' : 'arm64', id: 1234,
      getModuleByName: () => module, findModuleByAddress: () => module},
    NativeFunction: function(name) {
      if (!(name in exports)) throw Error('Missing fixture API ' + name);
      return (...args)=>{apiCalls.push(name);return exports[name](...args);};
    },
    Memory: {alloc}, Interceptor: {attach: (address, callback) => {
      if (address.toString() === rejectedHook) throw Error('synthetic mitigation hook installation failure');
      hookTrace.push({operation: 'attach', address: address.toString()});
      hooks.set(address.toString(), callback);
      return {detach: () => {hookTrace.push({operation: 'detach', address: address.toString()}); hooks.delete(address.toString());}};
    }}, ptr, recv: (kind,callback) => requests.set(kind,callback), send: value => messages.push(value), rpc: {},
  };
  Object.assign(context,globals);
  vm.createContext(context);
  const source = loadProbeSource(sourcePath);
  vm.runInContext(source, context);
  function callback(name, owner) {
    const code = callbacks.get(name + '@' + owner);
    const callback = hooks.get(code.toString()); assert(callback, 'Production callback installed for ' + name); return callback;
  }
  function unit(id, uid) {
    const pointer = alloc(), data = alloc(); write(pointer, character); write(data, characterData);
    write(pointer.add(0x148), managedString(id)); write(pointer.add(0x18), uid);
    write(pointer.add(0x538), data); write(data.add(0x6c), uid + 1000); return pointer;
  }
  function modifier(source, target, amount, real, type) {
    const p = alloc(); write(p, source); write(p.add(0x10), target);
    write(p.add(0x30), BigInt(Math.round(amount * 4294967296)));
    write(p.add(0x48), BigInt(Math.round(real * 4294967296)));
    write(p.add(0x60), 0); write(p.add(0x68), type); write(p.add(0x70), 0); return p;
  }
  function installSyntheticMitigation({fail = false} = {}) {
    const plan = {outer: alloc(), internal: alloc(), final: alloc(), damageType: 0x18};
    const before = hooks.size;
    if (fail) rejectedHook = plan.internal.toString();
    context.syntheticMitigationPlan = plan;
    // Supply owned stand-in method addresses, then execute the actual source
    // installer. Never assign mitigationReady or an unknown flag in a test.
    vm.runInContext('mitigationMethods=syntheticMitigationPlan;cfg.mitigationError=null;installMitigationHooks();', context);
    rejectedHook = null;
    return {synthetic: true, installed_hook_delta: hooks.size - before};
  }
  function hookAt(address) {
    const hook = hooks.get(address.toString());
    assert(hook, 'Production callback installed at synthetic address ' + address);
    return hook;
  }
  return {context, messages, requests, write, text, managedString, alloc, ptr, clock, callback, unit, modifier,
    entity, unitClass, character, basicSkill, skillData, buffClass, blockDamage, level, controller, controllerClass,
    apiCalls, fieldTables, methodTables, metadataExports:exports, vm, installSyntheticMitigation, hookTrace, hookAt,
    abilityClass,castSkillClass,skillCastFieldTypes,skillCastMethodTypes,skillCastTypeInfo,characterData,enemyClass};
}

module.exports = {fixture, loadProbeSource};
if (require.main === module) {
const f = fixture();
assert.equal(f.messages.at(-1).ready, true);
const first = f.context.rpc.exports.mark();
assert.equal(first.native_process_pid, 1234);
assert.equal(first.native_frame, 0); assert.equal(first.native_fixed_time, '0');
assert.equal(first.native_generation, 0);
assert.equal(first.native_stage_id, 'Obt/Weekly/level_weekly_melee_5');
f.clock(30, 4294967296n);
const source = f.unit('char_147_shining', 10), target = f.unit('char_122_beagle', 11);
const enemy = f.unit('enemy_test', 12), damage = f.modifier(source, enemy, 40, -40, 1);
f.write(enemy.add(0x40), 50n * 4294967296n);
const damageApply = f.callback('ApplyModifier', f.entity), takeDamage = f.callback('OnTakeDamage', f.unitClass);
const damageCall = {threadId: 1}, targetCall = {threadId: 1};
damageApply.onEnter.call(damageCall, [enemy, damage, f.ptr(0)]);
takeDamage.onEnter.call(targetCall, [enemy, damage, f.ptr(0)]);
f.write(enemy.add(0x40), 10n * 4294967296n);
takeDamage.onLeave.call(targetCall); damageApply.onLeave.call(damageCall);
f.write(target.add(0x40), 50n * 4294967296n);
const heal = f.modifier(source, target, 80, 23.5, 4), apply = f.callback('ApplyModifier', f.entity);
const call = {threadId: 1};
apply.onEnter.call(call, [target, heal, f.ptr(0)]);
assert.equal(f.context.rpc.exports.mark().active, 1);
f.write(target.add(0x40), BigInt(73.5 * 4294967296));
apply.onLeave.call(call);
let mark = f.context.rpc.exports.mark();
assert.equal(mark.active, 0);
let batch = f.context.rpc.exports.readEvents(0, mark.sequence);
assert.equal(batch.events.length, 3); assert.equal(batch.events[2].kind, 'healing');
assert.equal(batch.events[0].kind, 'hp_damage'); assert.equal(batch.events[0].amount, 40);
assert.equal(batch.events[1].kind, 'damage'); assert.equal(batch.events[1].amount, 40);
assert.equal(batch.events[1].hp_after, 10); assert.equal(batch.events[1].real_delta, -40);
assert.equal(batch.events[2].amount, 80); assert.equal(batch.events[2].source_character_uid, 1010);
const healingProof = batch.events[2].proof.direct_healing;
assert.equal(healingProof.amount, 80); assert.equal(healingProof.modifier_actual_delta, 23.5);
assert.equal(healingProof.hp_before, 50); assert.equal(healingProof.hp_after, 73.5);
assert.equal(healingProof.target, 'char_122_beagle'); assert.equal(healingProof.target_instance, target.toString());
assert.equal(healingProof.target_uid, null);
f.write(heal.add(0x48), -5n * 4294967296n);
assert.equal(f.context.directHealingProof(call, 80).modifier_actual_delta, -5);
f.write(heal.add(0x48), BigInt(23.5 * 4294967296));
f.write(heal.add(0x10), enemy);
const changedTarget = f.context.directHealingProof(call, 80);
assert.equal(changedTarget.modifier_actual_delta, 23.5);
assert.equal(changedTarget.hp_before, null); assert.equal(changedTarget.hp_after, null);
assert.equal(changedTarget.target, null); assert.equal(changedTarget.target_instance, null);
f.write(heal.add(0x10), target);
const skill = f.alloc(), skillInfo = f.alloc(); f.write(skill, f.basicSkill); f.write(skillInfo, f.skillData);
f.write(source.add(0x3c8), skill); f.write(skill.add(0xb0), skillInfo);
f.write(skillInfo.add(0x50), f.managedString('shining_s_2')); f.write(skillInfo.add(0x18), f.managedString('skill_2'));
f.write(skillInfo.add(0x10), f.managedString('Shield'));
for (const name of ['OnSkillStart', 'OnSkillFinish']) {
  const hook = f.callback(name, f.character), call = {threadId: 1};
  hook.onEnter.call(call, [source]); hook.onLeave.call(call);
}
function shield(before, incoming, after) {
  const buff = f.alloc(), bb = f.alloc(), array = f.alloc();
  f.write(buff, f.buffClass); f.write(buff.add(0x18), source);
  f.write(buff.add(0x208), f.managedString('shining_s_2')); f.write(buff.add(0x27c), 1);
  f.write(bb.add(0x18), 1); f.write(bb.add(0x10), array);
  f.write(array.add(0x20), f.managedString('dynamic')); f.write(array.add(0x28), before);
  const mod = f.modifier(f.ptr(0), target, incoming, 0, 1);
  const buffHook = f.callback('OnTakeDamage', f.buffClass), block = f.callback('_AssignDynamicVar', f.blockDamage);
  const outer = {threadId: 1}, inner = {threadId: 1};
  buffHook.onEnter.call(outer, [buff, mod]);
  block.onEnter.call(inner, [f.alloc(), bb, mod, f.ptr(0), f.ptr(BigInt(Math.round(Math.min(before, incoming) * 4294967296)))]);
  f.write(array.add(0x28), after);
  block.onLeave.call(inner); buffHook.onLeave.call(outer);
}
shield(100, 40, 60); shield(25, 60, 0);
mark = f.context.rpc.exports.mark(); batch = f.context.rpc.exports.readEvents(0, mark.sequence);
assert.deepEqual(Array.from(batch.events, e => e.kind), ['hp_damage', 'damage', 'healing', 'skill_start', 'skill_end', 'shield_absorbed', 'shield_absorbed']);
assert.deepEqual(Array.from(batch.events.filter(e => e.kind === 'shield_absorbed'), e => e.amount), [40, 25]);
assert.equal(batch.native_fixed_time, '4294967296');
assert.equal(batch.events[5].proof.before, 100); assert.equal(batch.events[6].proof.after, 0);
assert.equal(f.context.rpc.exports.readEvents(0, mark.sequence).events.length, 7);
assert.equal(f.context.rpc.exports.ackEvents(mark.sequence).acknowledged, mark.sequence);
assert.equal(f.context.rpc.exports.readEvents(mark.sequence, mark.sequence).events.length, 0);
f.clock(0, 0n); assert.equal(f.context.rpc.exports.mark().native_generation, 1);
assert.equal(f.context.rpc.exports.mark().native_generation, 1);
f.write(f.level.add(0x18), f.managedString('Obt/Main/level_main_01-07'));
assert.equal(f.context.rpc.exports.mark().native_generation, 2);
assert.equal(f.context.rpc.exports.mark().native_stage_id, 'Obt/Main/level_main_01-07');
f.clock(60, 2n * 4294967296n);
f.write(f.controller.add(0x228), 3);
const setState = f.callback('set_state', f.controllerClass), stateCall = {threadId: 1};
setState.onEnter.call(stateCall, [f.controller, f.ptr(2)]);
assert.equal(f.context.rpc.exports.mark().active, 1);
f.write(f.controller.add(0x228), 2); setState.onLeave.call(stateCall);
assert.equal(f.context.rpc.exports.mark().native_generation, 3);
const repeatedState = {threadId: 1};
setState.onEnter.call(repeatedState, [f.controller, f.ptr(2)]);
setState.onLeave.call(repeatedState);
assert.equal(f.context.rpc.exports.mark().native_generation, 3);
for (const state of [0, 1]) {
  f.write(f.controller.add(0x228), state);
  assert.equal(f.context.rpc.exports.mark().retry, true);
}
f.write(f.controller.add(0x228), 2);
for (const explicitCount of [7, 8]) {
  const raw = 40n * 4294967296n, before = 80n * 4294967296n;
  const origin = explicitCount === 8 ? 60n * 4294967296n : raw;
  const storage = f.alloc(), args = [storage, f.ptr(raw)];
  if (explicitCount === 8) args.push(f.ptr(origin));
  args.push(f.ptr(1), f.ptr(0), source, enemy, f.ptr(0), f.ptr(before));
  f.context.fixtureArgs = args;
  f.context.fixtureFactory = {source, target: enemy, raw, beforeRaw: before, origin};
  vm.runInContext(`{
    const f=fixtureFactory, when=time(), tid=7;
    const candidate={raw:f.raw,beforeRaw:f.beforeRaw,before:80,after:40,source:ptr(0),target:f.target,type:1};
    const key=unownedFormulaKey(f.target,12,1,f.raw)+f.beforeRaw;
    pendingFormulaDamage.set(tid,{...when,unowned:new Map([[key,candidate]]),postFormulaHits:new Map(),hits:new Map(),factories:new Map()});
    globalThis.factoryInvocation={tid};
  }`, f.context);
  f.context.enterDamageFactory.call(f.context.factoryInvocation, args, explicitCount - 6);
  const record = f.context.factoryInvocation.factory;
  assert(record); assert(record.storage.equals(storage));
  assert(record.result.source.equals(source)); assert(record.result.target.equals(enemy));
  assert.equal(record.result.type, 1); assert.equal(record.result.raw, raw);
  assert.equal(record.result.originValue, origin);
  f.context.restoreDamageFactory.call(f.context.factoryInvocation);
}
shield(100, 40, 30);
assert.equal(f.context.rpc.exports.mark().shield_unknown, true);
assert.equal(f.context.rpc.exports.readEvents(mark.sequence, mark.sequence).events.length, 0);
const crossedFrame = {threadId: 2};
apply.onEnter.call(crossedFrame, [target, heal, f.ptr(0)]);
f.clock(61, 2n * 4294967296n);
apply.onLeave.call(crossedFrame);
assert.equal(f.context.rpc.exports.mark().sequence, mark.sequence);
assert(f.context.rpc.exports.mark().unknown_sources.includes('char_147_shining'));
f.context.rpc.exports.stop();
assert.equal(f.context.rpc.exports.mark().stopped, true);
assert.throws(() => fixture({loggerOffset: 0xd8}), /unsupported layout BattleController.m_logger/);
assert.throws(() => fixture({stateMethod: false}), /missing or ambiguous method set_state/);
assert.throws(() => fixture({nullClassIndex: 0}), /class not found Entity/);
assert.throws(() => fixture({unreadableClassIndex: 0}), /Unreadable fixture address 0x20/);
assert.equal(fixture({extraNullRow: true}).messages.at(-1).ready, true);
const lazy=fixture({lazySkillFields:true,lazySkillMethods:true});
assert.equal(lazy.messages.at(-1).ready,true);
assert(!lazy.basicSkill.add(0x80).readPointer().isNull());
assert(!lazy.basicSkill.add(0x98).readPointer().isNull());
assert(lazy.apiCalls.includes('il2cpp_class_get_fields'));
assert(lazy.apiCalls.includes('il2cpp_class_get_methods'));
assert(lazy.apiCalls.every(name=>!['il2cpp_runtime_class_init','il2cpp_runtime_invoke','il2cpp_class_init'].includes(name)));
assert.throws(()=>fixture({lazySkillFields:true,fieldOwnerMismatch:true}),/field owner mismatch/);
assert.throws(()=>fixture({lazySkillFields:true,fieldCountMismatch:true}),/field metadata count mismatch/);
assert.throws(()=>fixture({lazySkillFields:true,fieldIteratorMismatch:true}),/field metadata iterator mismatch/);
assert.throws(()=>fixture({lazySkillFields:true,fieldApiLeavesTableNull:true}),/field metadata table missing/);
assert.throws(()=>fixture({lazySkillFields:true,fieldApiStalls:true}),/field metadata count mismatch/);
assert.throws(()=>fixture({lazySkillFields:true,loggerOffset:0xd8}),/unsupported layout BattleController.m_logger/);
assert.throws(()=>fixture({lazySkillMethods:true,methodCountMismatch:true}),/method metadata count mismatch/);
assert.throws(()=>fixture({lazySkillMethods:true,methodIteratorMismatch:true}),/method metadata iterator mismatch/);
assert.throws(()=>fixture({lazySkillMethods:true,methodApiLeavesTableNull:true}),/method metadata table missing/);
assert.throws(()=>fixture({lazySkillMethods:true,methodApiStalls:true}),/method metadata count mismatch/);
assert.throws(()=>fixture({lazySkillMethods:true,methodOwnerMismatch:true}),/missing or ambiguous method set_state/);
console.log(JSON.stringify({events: 7, shield_amounts: [40, 25], direct_healing: 80,
  damage: 40, queue_ack: true, identity_rollback: true, windows_sret: [7, 8]}));
}
