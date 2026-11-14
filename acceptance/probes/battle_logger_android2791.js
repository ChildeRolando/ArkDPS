// Opt-in Android 2.7.91 ARM64 research, never loaded by the product.
// Observes callbacks and reads counters. It does not call game methods.
if (Process.arch !== 'arm64') throw new Error('ARM64 emulated realm required');
const il2cpp = Process.getModuleByName('libil2cpp.so');
const api = (name, result, args) => new NativeFunction(il2cpp.getExportByName(name), result, args);
const sourceFilter = globalThis.arkdpsLoggerSource || 'char_4082_qiubai';
const limit = 600;
const handles = [];
const classes = {};
const className = klass => klass.add(0x10).readPointer().readCString();
const namespace = klass => klass.add(0x18).readPointer().readCString();
const declaring = api('il2cpp_class_get_declaring_type', 'pointer', ['pointer']);
const loggerFields = {m_controller: 0x18, m_stats: 0x30};

function verifyFields(klass, expected) {
    const fields = klass.add(0x80).readPointer();
    const count = klass.add(0x124).readU16();
    if (count > 1000) throw new Error('invalid field metadata');
    const offsets = {};
    for (let index = 0; index < count; index++) {
        const field = fields.add(index * 32);
        if (!field.add(16).readPointer().equals(klass)) throw new Error('invalid field owner');
        offsets[field.readPointer().readCString()] = field.add(24).readS32();
    }
    for (const [name, offset] of Object.entries(expected)) {
        if (offsets[name] !== offset) throw new Error('unsupported layout ' + className(klass) + '.' + name);
    }
}

const paramCount = api('il2cpp_method_get_param_count', 'uint', ['pointer']);
const paramAt = api('il2cpp_method_get_param', 'pointer', ['pointer', 'uint']);
const resultOf = api('il2cpp_method_get_return_type', 'pointer', ['pointer']);
const flagsOf = api('il2cpp_method_get_flags', 'uint', ['pointer', 'pointer']);
const fromType = api('il2cpp_class_from_type', 'pointer', ['pointer']);
const isByRef = api('il2cpp_type_is_byref', 'bool', ['pointer']);
const isEnum = api('il2cpp_class_is_enum', 'bool', ['pointer']);
const assignable = api('il2cpp_class_is_assignable_from', 'bool', ['pointer', 'pointer']);

function resolveMethod(owner, name, count, typeCheck, result) {
    const array = owner.add(0x98).readPointer();
    const length = owner.add(0x120).readU16();
    const flags = Memory.alloc(4);
    const matches = [];
    if (length > 2000) throw new Error('invalid method metadata');
    for (let index = 0; index < length; index++) {
        const method = array.add(index * 8).readPointer();
        if (!method.add(0x20).readPointer().equals(owner) ||
            method.add(0x18).readPointer().readCString() !== name ||
            paramCount(method) !== count || (flagsOf(method, flags) & 0x10)) continue;
        const returnType = resultOf(method);
        if (isByRef(returnType) || className(fromType(returnType)) !== result) continue;
        if (!typeCheck(Array.from({length: count}, (_, i) => paramAt(method, i)))) continue;
        const address = method.readPointer();
        if (!address.isNull() && Process.findModuleByAddress(address)?.name === il2cpp.name)
            matches.push(address);
    }
    if (matches.length !== 1) throw new Error('missing or ambiguous signature ' + name);
    return matches[0];
}

function resolve() {
    const domain = api('il2cpp_domain_get', 'pointer', [])();
    const thread = api('il2cpp_thread_attach', 'pointer', ['pointer'])(domain);
    try {
        const size = Memory.alloc(8);
        const assemblies = api('il2cpp_domain_get_assemblies', 'pointer', ['pointer', 'pointer'])(domain, size);
        const imageOf = api('il2cpp_assembly_get_image', 'pointer', ['pointer']);
        const imageName = api('il2cpp_image_get_name', 'pointer', ['pointer']);
        const classCount = api('il2cpp_image_get_class_count', 'ulong', ['pointer']);
        const classAt = api('il2cpp_image_get_class', 'pointer', ['pointer', 'ulong']);
        let image;
        for (let index = 0; index < size.readU64().toNumber(); index++) {
            const candidate = imageOf(assemblies.add(index * 8).readPointer());
            if (imageName(candidate).readCString() === 'Assembly-CSharp.dll') {
                image = candidate;
                break;
            }
        }
        if (!image) throw new Error('Assembly-CSharp.dll not found');
        const top = ['BattleLogger', 'BattleController', 'Character', 'BattleCharacterData', 'Entity', 'Modifier'];
        for (let index = 0, count = classCount(image); index < count; index++) {
            const klass = classAt(image, index);
            const name = className(klass);
            if (top.includes(name) && namespace(klass) === 'Torappu.Battle') {
                if (classes[name]) throw new Error('ambiguous class ' + name);
                classes[name] = klass;
            }
        }
        for (const name of top) {
            if (!classes[name]) throw new Error('missing class ' + name);
        }
        for (const [name, owner] of [['BattleStats', 'BattleLogger'], ['CharAdvancedStats', 'BattleStats']]) {
            const matches = [];
            for (let index = 0, count = classCount(image); index < count; index++) {
                const klass = classAt(image, index);
                if (className(klass) === name && declaring(klass).equals(classes[owner])) matches.push(klass);
            }
            if (matches.length !== 1) throw new Error('missing or ambiguous nested class ' + name);
            classes[name] = matches[0];
        }
        verifyFields(classes.Entity, {'<id>k__BackingField': 0x148});
        verifyFields(classes.Character, {'<data>k__BackingField': 0x538});
        verifyFields(classes.BattleCharacterData, {uniqueId: 0x6c});
        verifyFields(classes.BattleController, {m_logger: 0xd8, s_fixedFrameCnt: 0x14, s_fixedPlayTime: 0x18});
        verifyFields(classes.BattleLogger, loggerFields);
        verifyFields(classes.BattleStats, {charAdvancedStats: 0x30});
        verifyFields(classes.CharAdvancedStats, {outputDamageTotal: 0x20, outputDamageByTypeTotal: 0x38});
        // The ABI passes an unboxed Modifier&, 0x10 before the metadata offsets.
        verifyFields(classes.Modifier, {m_source: 0x10, m_target: 0x20, m_originalSource: 0x30,
            m_value: 0x40, realDelta: 0x58, '<targetType>k__BackingField': 0x70,
            '<damageType>k__BackingField': 0x78, '<cancelReason>k__BackingField': 0x80});
        const modifierRef = type => isByRef(type) && fromType(type).equals(classes.Modifier);
        const methods = {
            apply: resolveMethod(classes.Entity, 'ApplyModifier', 2, ([mod, force]) =>
                modifierRef(mod) && !isByRef(force) && className(fromType(force)) === 'Boolean', 'Boolean'),
            log: resolveMethod(classes.BattleLogger, 'LogModifier', 1, ([mod]) => modifierRef(mod), 'Void'),
            side: resolveMethod(classes.BattleLogger, '_CheckPlayerSide', 1, ([type]) => {
                const klass = fromType(type);
                return !isByRef(type) && className(klass) === 'PlayerSide' && isEnum(klass);
            }, 'Boolean'),
        };
        const statics = classes.BattleController.add(0xb8).readPointer();
        if (statics.isNull()) throw new Error('battle clock unavailable');
        return {methods, statics};
    } finally {
        api('il2cpp_thread_detach', 'void', ['pointer'])(thread);
    }
}

const {methods, statics} = resolve();
const fp = address => address.readS64().toNumber() / 4294967296;
function timestamp() {
    const frame = statics.add(0x14).readU32();
    const time = fp(statics.add(0x18));
    return statics.add(0x14).readU32() === frame && time >= 0 ? {frame, time} : null;
}
function stringValue(address) {
    if (address.isNull()) return null;
    const size = address.add(0x10).readS32();
    return size > 0 && size <= 110 ? address.add(0x14).readUtf16String(size) : null;
}
function entity(address) {
    if (address.isNull()) return null;
    const klass = address.readPointer();
    if (!assignable(classes.Entity, klass)) return {pointer: address.toString(), id: null, source_uid: null};
    const id = stringValue(address.add(0x148).readPointer());
    let sourceUid = null;
    if (klass.equals(classes.Character)) {
        const data = address.add(0x538).readPointer();
        if (!data.isNull() && data.readPointer().equals(classes.BattleCharacterData))
            sourceUid = data.add(0x6c).readU32() || null;
    }
    return {pointer: address.toString(), id, source_uid: sourceUid};
}
function modifier(address) {
    return {pointer: address.toString(), source: entity(address.readPointer()),
        target: entity(address.add(0x10).readPointer()), original_source: entity(address.add(0x20).readPointer()),
        nominal: fp(address.add(0x30)), real_delta: fp(address.add(0x48)),
        target_type: address.add(0x60).readS32(), damage_type: address.add(0x68).readS32(),
        cancel: address.add(0x70).readS32()};
}

function arrayItems(list, maximum) {
    if (list.isNull()) throw new Error('null native list');
    const count = list.add(0x18).readS32();
    const items = list.add(0x10).readPointer();
    if (count < 0 || count > maximum || (count > 0 &&
        (items.isNull() || !items.add(0x10).readPointer().isNull() || items.add(0x18).readU32() < count)))
        throw new Error('unsupported native list layout');
    return {count, version: list.add(0x1c).readS32(), items};
}
function counters(logger) {
    if (logger.isNull() || !logger.readPointer().equals(classes.BattleLogger))
        throw new Error('unexpected logger type');
    const controller = logger.add(loggerFields.m_controller).readPointer();
    if (controller.isNull() || !controller.readPointer().equals(classes.BattleController) ||
        !controller.add(0xd8).readPointer().equals(logger)) throw new Error('logger is not owned by controller');
    const native = controller.add(0x10).readPointer();
    if (native.isNull() || !native.add(0x28).readPointer().equals(controller))
        throw new Error('controller native owner unavailable');
    const stats = logger.add(loggerFields.m_stats).readPointer();
    if (stats.isNull() || !stats.readPointer().equals(classes.BattleStats)) throw new Error('unexpected stats type');
    const list = stats.add(0x30).readPointer();
    const {count, version, items} = arrayItems(list, 128);
    let row = null;
    for (let index = 0; index < count; index++) {
        const pair = items.add(0x20 + index * 16);
        if (stringValue(pair.readPointer()) !== sourceFilter) continue;
        if (row) throw new Error('duplicate native statistic key');
        const value = pair.add(8).readPointer();
        if (value.isNull() || !value.readPointer().equals(classes.CharAdvancedStats))
            throw new Error('unexpected statistic row type');
        const typeList = arrayItems(value.add(0x38).readPointer(), 6);
        if (typeList.count !== 6) throw new Error('unexpected damage-type count');
        const typed = Array.from({length: 6}, (_, i) => typeList.items.add(0x20 + i * 4).readFloat());
        const raw = value.add(0x20).readFloat();
        if (![raw, ...typed].every(Number.isFinite)) throw new Error('invalid statistic counter');
        row = {pointer: value.toString(), raw, typed};
    }
    if (version !== list.add(0x1c).readS32() || !logger.add(loggerFields.m_stats).readPointer().equals(stats) ||
        !stats.add(0x30).readPointer().equals(list) || !list.add(0x10).readPointer().equals(items))
        throw new Error('statistic collection changed during read');
    return {controller: controller.toString(), stats: stats.toString(), key: sourceFilter, row};
}
function safeCounters(logger) {
    try { return counters(logger); }
    catch (error) { return {error: String(error)}; }
}
function sameClock(left, right) {
    return left && right && left.frame === right.frame && left.time === right.time;
}
function sameEntity(left, right) {
    return left?.pointer === right?.pointer && left?.id === right?.id && left?.source_uid === right?.source_uid;
}

let serial = 0;
let logEpoch = 0;
let activeLogs = 0;
let observed = 0;
let dropped = 0;
let errors = 0;
let queue = [];
const applyScopes = new Map();
const loggerScopes = new Map();
function emit(event) {
    if (observed >= limit) return void dropped++;
    observed++;
    queue.push(event);
}
function push(scopes, thread, value) {
    const stack = scopes.get(thread) || [];
    stack.push(value);
    scopes.set(thread, stack);
}
function pop(scopes, thread) {
    const stack = scopes.get(thread);
    if (stack.length === 1) scopes.delete(thread);
    return stack.pop();
}
function sourceMatches(address) {
    const source = address.readPointer();
    return !source.isNull() && stringValue(source.add(0x148).readPointer()) === sourceFilter;
}

handles.push(Interceptor.attach(methods.apply, {
    onEnter(args) {
        if (observed >= limit || args[1].isNull()) return;
        try {
            if (!sourceMatches(args[1])) return;
            this.scope = {id: ++serial, address: args[1], before: modifier(args[1]), start: timestamp(),
                log_ids: [], side_checks: []};
            push(applyScopes, this.threadId, this.scope);
        } catch (error) { errors++; }
    },
    onLeave(result) {
        if (!this.scope) return;
        const scope = pop(applyScopes, this.threadId);
        try {
            const finish = timestamp();
            emit({kind: 'apply_modifier', id: scope.id, thread: this.threadId, start: scope.start, finish,
                same_clock: Boolean(sameClock(scope.start, finish)), before: scope.before,
                after: modifier(scope.address), result: (result.toInt32() & 1) !== 0, log_ids: scope.log_ids,
                side_checks: scope.side_checks});
        } catch (error) { errors++; }
    },
}));
handles.push(Interceptor.attach(methods.log, {
    onEnter(args) {
        this.epoch = ++logEpoch;
        this.isolated = ++activeLogs === 1;
        if (observed >= limit || args[1].isNull()) return;
        try {
            if (!sourceMatches(args[1])) return;
            const parents = (applyScopes.get(this.threadId) || []).filter(scope => scope.address.equals(args[1]));
            const id = ++serial;
            const parent = parents.length === 1 ? parents[0] : null;
            if (parent) parent.log_ids.push(id);
            this.scope = {id, parent: parent?.id ?? null, address: args[1], logger: args[0],
                start: timestamp(), before: modifier(args[1]), native_before: safeCounters(args[0]), side_checks: []};
            push(loggerScopes, this.threadId, this.scope);
        } catch (error) { errors++; }
    },
    onLeave() {
        if (this.epoch !== undefined) activeLogs--;
        if (!this.scope) return;
        const scope = pop(loggerScopes, this.threadId);
        try {
            const after = modifier(scope.address);
            const nativeAfter = safeCounters(scope.logger);
            const finish = timestamp();
            const before = scope.native_before;
            const sameKey = before.stats && before.stats === nativeAfter.stats && before.controller === nativeAfter.controller &&
                (!before.row || !nativeAfter.row || before.row.pointer === nativeAfter.row.pointer);
            const comparable = Boolean(sameKey && this.isolated && logEpoch === this.epoch &&
                sameClock(scope.start, finish) && sameEntity(scope.before.source, after.source) &&
                sameEntity(scope.before.target, after.target) && nativeAfter.row);
            emit({kind: 'log_modifier', id: scope.id, apply_id: scope.parent, thread: this.threadId,
                logger: scope.logger.toString(), start: scope.start, finish, modifier_before: scope.before,
                modifier_after: after, side_checks: scope.side_checks, native_before: before, native_after: nativeAfter,
                comparable_native_delta: comparable ? nativeAfter.row.typed.map(
                    (value, index) => value - (before.row?.typed[index] ?? 0)) : null});
        } catch (error) { errors++; }
    },
}));
handles.push(Interceptor.attach(methods.side, {
    onEnter(args) {
        let scope = loggerScopes.get(this.threadId)?.at(-1);
        if (!scope?.logger.equals(args[0])) scope = applyScopes.get(this.threadId)?.at(-1);
        if (scope && scope.side_checks.length < 8) {
            this.scope = scope;
            this.logger = args[0].toString();
            this.side = args[1].toInt32();
        }
    },
    onLeave(result) {
        if (this.scope && this.scope.side_checks.length < 8)
            this.scope.side_checks.push({logger: this.logger, side: this.side, accepted: (result.toInt32() & 1) !== 0});
    },
}));

function flush() {
    if (!queue.length) return;
    send({events: queue});
    queue = [];
}
const timer = setInterval(flush, 100);
rpc.exports = {
    stop() {
        for (const handle of handles) handle.detach();
        clearInterval(timer);
        flush();
        send({summary: {observed, dropped, errors}});
    },
};
send({ready: true, probe: 'battle_logger', arch: Process.arch, source: sourceFilter, limit,
    methods: Object.fromEntries(Object.entries(methods).map(([name, address]) => [name, address.toString()]))});
