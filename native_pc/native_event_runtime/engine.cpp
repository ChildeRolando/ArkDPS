#include "engine.h"
#include <windows.h>
#include <psapi.h>
#include <MinHook.h>
extern "C" {
#include <quickjs.h>
}
#include "bridge_source.h"
#include "json.hpp"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <cstddef>
#include <exception>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

extern "C" void arkdps_hook_entry();
extern "C" void arkdps_hook_leave();

namespace {
constexpr std::size_t max_read = 1024 * 1024;
constexpr std::size_t max_allocated = 64 * 1024 * 1024;
struct Hook {
    void* address = nullptr;
    void* original = nullptr;
    void* thunk = nullptr;
    JSValue callbacks = JS_UNDEFINED;
    bool detached = false;
};
struct Allocation { std::uintptr_t base; std::size_t size; };
struct Runtime {
    std::recursive_mutex mutex;
    JSRuntime* runtime = nullptr;
    JSContext* context = nullptr;
    JSValue pointer = JS_UNDEFINED;
    JSValue argument_view = JS_UNDEFINED;
    JSValue dispatch = JS_UNDEFINED;
    std::vector<std::unique_ptr<Hook>> hooks;
    std::vector<Allocation> allocations;
    std::unordered_set<std::uintptr_t> exports;
    std::size_t allocated = 0;
    std::atomic<unsigned> active{0};
    std::atomic<std::uint64_t> activity_generation{0};
    std::atomic<bool> stopping{false};
    std::atomic<bool> capture_paused{false};
    bool capture_hooks_disabled = false;
    std::uint64_t capture_revision = 0;
    std::uint64_t pending_pause_revision = 0;
    bool capture_target_paused = false;
    std::string ready;
    std::string fatal;
    bool initialized = false;
};
// Intentionally process-lifetime: disabled hooks can still be returning through
// their trampolines. A transport disconnect must never free those instructions.
Runtime& state() { static auto* value = new Runtime; return *value; }
struct EntryFrame {
    std::uint64_t rax, rcx, rdx, r8, r9, r10, hook, stack;
    std::array<std::uint8_t, 96> xmm;
    std::uint64_t original_r11;
    std::uint64_t return_target;
};
static_assert(offsetof(EntryFrame, return_target) == 0xa8);
struct ReturnFrame { std::uint64_t rax, rdx; std::array<std::uint8_t, 32> xmm; };
struct Invocation { Hook* hook; std::uintptr_t return_address; JSValue receiver; };
struct ThreadInvocations { unsigned depth = 0; std::array<Invocation, 256> calls; };
thread_local ThreadInvocations invocations;

std::string string(JSContext* context, JSValueConst value) {
    const char* text = JS_ToCString(context, value);
    if (!text) throw std::runtime_error("JavaScript string conversion failed");
    const std::string result(text);
    JS_FreeCString(context, text);
    return result;
}
std::string exception(JSContext* context) {
    const auto value = JS_GetException(context);
    std::string result;
    try {
        result = string(context, value);
        const auto stack = JS_GetPropertyStr(context, value, "stack");
        if (!JS_IsUndefined(stack)) result += "\n" + string(context, stack);
        JS_FreeValue(context, stack);
    } catch (...) { result = "unprintable JavaScript exception"; }
    JS_FreeValue(context, value);
    return result;
}
void disable_hooks() noexcept {
    for (const auto& hook : state().hooks) {
        MH_DisableHook(hook->address);
        hook->detached = true;
    }
}
void fail(const std::string& message) noexcept {
    auto& runtime = state();
    try { if (runtime.fatal.empty()) runtime.fatal = message; } catch (...) {}
    // Do not suspend threads from inside a callback. Queue disables; the next
    // pipe operation applies them after releasing the JS lock.
    for (const auto& hook : runtime.hooks) MH_QueueDisableHook(hook->address);
}
std::uint64_t integer(JSContext* context, JSValueConst value) {
    std::int64_t result = 0;
    if (JS_ToBigInt64(context, &result, value) < 0)
        throw std::runtime_error("expected a BigInt address or integer");
    return static_cast<std::uint64_t>(result);
}
std::size_t size(JSContext* context, JSValueConst value, std::size_t limit = max_read) {
    std::int64_t result = -1;
    if (JS_ToInt64(context, &result, value) < 0 || result < 0 || static_cast<std::uint64_t>(result) > limit)
        throw std::runtime_error("memory length exceeds the bounded runtime limit");
    return static_cast<std::size_t>(result);
}
void read(std::uintptr_t address, void* destination, std::size_t count) {
    if (count > max_read || address + count < address) throw std::runtime_error("invalid read range");
    SIZE_T actual = 0;
    if (count && (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), destination, count, &actual) || actual != count))
        throw std::runtime_error("unreadable native memory");
}
JSValue pointer(JSContext* context, std::uintptr_t address) {
    auto argument = JS_NewBigUint64(context, address);
    auto value = JS_Call(context, state().pointer, JS_UNDEFINED, 1, &argument);
    JS_FreeValue(context, argument);
    return value;
}
template <typename Operation>
JSValue guarded(JSContext* context, Operation&& operation) {
    try { return operation(); }
    catch (const std::exception& error) { return JS_ThrowInternalError(context, "%s", error.what()); }
    catch (...) { return JS_ThrowInternalError(context, "native runtime failure"); }
}
JSValue js_read(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2) throw std::runtime_error("read requires address and length");
        std::vector<std::uint8_t> bytes(size(c, args[1]));
        read(integer(c, args[0]), bytes.data(), bytes.size());
        return JS_NewArrayBufferCopy(c, bytes.data(), bytes.size());
    });
}
JSValue js_write(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2) throw std::runtime_error("write requires address and bytes");
        const auto address = integer(c, args[0]);
        std::size_t bytes = 0;
        auto* contents = JS_GetArrayBuffer(c, &bytes, args[1]);
        if (!contents || bytes > max_read || address + bytes < address) throw std::runtime_error("invalid owned memory write");
        bool owned = false;
        for (const auto& allocation : state().allocations)
            if (address >= allocation.base && address + bytes <= allocation.base + allocation.size) { owned = true; break; }
        if (!owned) throw std::runtime_error("writes are limited to Memory.alloc buffers");
        std::memcpy(reinterpret_cast<void*>(address), contents, bytes);
        return JS_UNDEFINED;
    });
}
JSValue js_allocate(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 1) throw std::runtime_error("alloc requires length");
        const auto bytes = size(c, args[0]);
        auto& runtime = state();
        if (!bytes || runtime.allocated + bytes > max_allocated) throw std::runtime_error("allocation limit exceeded");
        auto* memory = VirtualAlloc(nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!memory) throw std::runtime_error("allocation failed");
        runtime.allocations.push_back({reinterpret_cast<std::uintptr_t>(memory), bytes});
        runtime.allocated += bytes;
        return JS_NewBigUint64(c, reinterpret_cast<std::uintptr_t>(memory));
    });
}
JSValue js_cstring(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 1) throw std::runtime_error("CString requires address");
        auto address = integer(c, args[0]);
        std::string result;
        for (std::size_t i = 0; i < 65536; ++i) {
            char value = 0; read(address + i, &value, 1);
            if (!value) return JS_NewStringLen(c, result.data(), result.size());
            result.push_back(value);
        }
        throw std::runtime_error("unterminated bounded native string");
    });
}
JSValue js_utf16(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2) throw std::runtime_error("UTF16 requires address and length");
        const auto length = size(c, args[1], max_read / 2);
        std::wstring value(length, L'\0');
        read(integer(c, args[0]), value.data(), length * sizeof(wchar_t));
        if (!length) return JS_NewString(c, "");
        const auto required = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(length), nullptr, 0, nullptr, nullptr);
        if (required <= 0) throw std::runtime_error("invalid UTF16 native string");
        std::string result(required, '\0');
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(length), result.data(), required, nullptr, nullptr))
            throw std::runtime_error("UTF16 conversion failed");
        return JS_NewStringLen(c, result.data(), result.size());
    });
}
std::string module_name(HMODULE module) {
    std::array<wchar_t, 32768> path{};
    const auto length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
    if (!length || length >= path.size()) throw std::runtime_error("module name unavailable");
    std::wstring name(path.data(), length);
    const auto separator = name.find_last_of(L"\\/");
    name = name.substr(separator == std::wstring::npos ? 0 : separator + 1);
    std::string result;
    for (const auto character : name) {
        if (character > 127) throw std::runtime_error("unsupported non-ASCII module name");
        result.push_back(static_cast<char>(character));
    }
    return result;
}
JSValue js_module(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 1) throw std::runtime_error("module lookup requires name");
        const auto name = string(c, args[0]);
        auto module = GetModuleHandleA(name.c_str());
        MODULEINFO information{};
        if (!module || !GetModuleInformation(GetCurrentProcess(), module, &information, sizeof(information))) throw std::runtime_error("module unavailable");
        auto result = JS_NewObject(c);
        JS_SetPropertyStr(c, result, "name", JS_NewString(c, module_name(module).c_str()));
        JS_SetPropertyStr(c, result, "base", JS_NewBigUint64(c, reinterpret_cast<std::uintptr_t>(module)));
        JS_SetPropertyStr(c, result, "size", JS_NewUint32(c, information.SizeOfImage));
        return result;
    });
}
JSValue js_find_module(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 1) throw std::runtime_error("module lookup requires address");
        HMODULE module = nullptr;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               reinterpret_cast<LPCWSTR>(integer(c, args[0])), &module)) return JS_NULL;
        return JS_NewString(c, module_name(module).c_str());
    });
}
JSValue js_export(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2) throw std::runtime_error("export lookup requires module and symbol");
        const auto name = string(c, args[0]), symbol = string(c, args[1]);
        if (_stricmp(name.c_str(), "GameAssembly.dll") || symbol.rfind("il2cpp_", 0) != 0) throw std::runtime_error("only official IL2CPP exports may be called");
        auto module = GetModuleHandleA(name.c_str());
        auto address = module ? GetProcAddress(module, symbol.c_str()) : nullptr;
        if (!address) throw std::runtime_error("required IL2CPP export missing");
        state().exports.insert(reinterpret_cast<std::uintptr_t>(address));
        return JS_NewBigUint64(c, reinterpret_cast<std::uintptr_t>(address));
    });
}
JSValue js_native_call(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2) throw std::runtime_error("native call requires function and arguments");
        const auto address = integer(c, args[0]);
        if (!state().exports.count(address)) throw std::runtime_error("unregistered IL2CPP export");
        auto length_value = JS_GetPropertyStr(c, args[1], "length");
        const auto length = size(c, length_value, 12);
        JS_FreeValue(c, length_value);
        std::array<std::uint64_t, 12> values{};
        for (std::size_t index = 0; index < length; ++index) {
            auto value = JS_GetPropertyUint32(c, args[1], static_cast<std::uint32_t>(index));
            values[index] = integer(c, value);
            JS_FreeValue(c, value);
        }
        using Function = std::uint64_t (*)(std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t,
                                          std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t,std::uint64_t);
        const auto result = reinterpret_cast<Function>(address)(values[0],values[1],values[2],values[3],values[4],values[5],
                                                               values[6],values[7],values[8],values[9],values[10],values[11]);
        return JS_NewBigUint64(c, result);
    });
}
JSValue js_attach(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2 || !JS_IsObject(args[1])) throw std::runtime_error("hook requires address and callbacks");
        auto& runtime = state();
        if (runtime.hooks.size() >= 512 || runtime.stopping.load() || !runtime.fatal.empty())
            throw std::runtime_error("native hook registration is unavailable");
        auto hook = std::make_unique<Hook>();
        hook->address = reinterpret_cast<void*>(integer(c, args[0]));
        hook->callbacks = JS_DupValue(c, args[1]);
        // A leaf thunk: movabs volatile r11,Hook*; jmp [rip+0]; .quad entry.
        // It changes no stack slots, so ordinary Windows leaf unwind remains valid.
        std::array<std::uint8_t, 24> code{{0x49,0xbb}};
        const auto identity = reinterpret_cast<std::uintptr_t>(hook.get());
        std::memcpy(code.data() + 2, &identity, 8);
        code[10]=0xff;code[11]=0x25;
        const auto entry = reinterpret_cast<std::uintptr_t>(&arkdps_hook_entry);
        std::memcpy(code.data() + 16, &entry, 8);
        hook->thunk = VirtualAlloc(nullptr, code.size(), MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (!hook->thunk) throw std::runtime_error("hook thunk allocation failed");
        std::memcpy(hook->thunk, code.data(), code.size());
        DWORD old = 0;
        if (!VirtualProtect(hook->thunk, code.size(), PAGE_EXECUTE_READ, &old)) throw std::runtime_error("hook thunk protection failed");
        FlushInstructionCache(GetCurrentProcess(), hook->thunk, code.size());
        const auto status = MH_CreateHook(hook->address, hook->thunk, &hook->original);
        if (status != MH_OK) throw std::runtime_error(std::string("native hook creation failed: ") + MH_StatusToString(status));
        const auto id = runtime.hooks.size();
        runtime.hooks.push_back(std::move(hook));
        if (runtime.initialized && !runtime.capture_paused.load()
                && MH_QueueEnableHook(runtime.hooks.back()->address) != MH_OK) {
            runtime.hooks.back()->detached = true;
            MH_QueueDisableHook(runtime.hooks.back()->address);
            throw std::runtime_error("could not queue a new native capture hook");
        }
        return JS_NewUint32(c, static_cast<std::uint32_t>(id));
    });
}
JSValue js_detach(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 1) throw std::runtime_error("detach requires hook id");
        const auto id = size(c, args[0], 511);
        if (id >= state().hooks.size()) throw std::runtime_error("invalid hook id");
        auto& hook = state().hooks[id];
        if (!hook->detached) {
            const auto status = MH_QueueDisableHook(hook->address);
            if (status != MH_OK) throw std::runtime_error("could not queue hook disable");
            hook->detached = true;
        }
        return JS_UNDEFINED;
    });
}
JSValue js_send(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] { if (count != 1) throw std::runtime_error("send requires JSON"); state().ready = string(c, args[0]); return JS_UNDEFINED; });
}
JSValue js_active(JSContext* c, JSValueConst, int, JSValueConst*) { return JS_NewUint32(c, state().active.load()); }
JSValue js_pid(JSContext* c, JSValueConst, int, JSValueConst*) { return JS_NewUint32(c, GetCurrentProcessId()); }
void function(JSContext* c, JSValueConst global, const char* name, JSCFunction* callback, int arguments) {
    JS_SetPropertyStr(c, global, name, JS_NewCFunction(c, callback, name, arguments));
}
void evaluate(JSContext* c, const std::string& source, const char* filename) {
    auto value = JS_Eval(c, source.data(), source.size(), filename, JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(value)) throw std::runtime_error(exception(c));
    JS_FreeValue(c, value);
}
void callback(JSContext* c, Hook* hook, const char* name, JSValueConst receiver, int arguments, JSValueConst* args) {
    auto method = JS_GetPropertyStr(c, hook->callbacks, name);
    if (JS_IsFunction(c, method)) {
        auto result = JS_Call(c, method, receiver, arguments, args);
        if (JS_IsException(result)) { JS_FreeValue(c, method); throw std::runtime_error(exception(c)); }
        JS_FreeValue(c, result);
    }
    JS_FreeValue(c, method);
}

// The caller owns the JS lock. MinHook changes must be applied after releasing it.
std::string dispatch_request(Runtime& runtime, const std::string& json) {
    if (!runtime.context) throw std::runtime_error("native runtime has no JavaScript context");
    JS_UpdateStackTop(runtime.runtime);
    auto argument = JS_NewStringLen(runtime.context, json.data(), json.size());
    auto value = JS_Call(runtime.context, runtime.dispatch, JS_UNDEFINED, 1, &argument);
    JS_FreeValue(runtime.context, argument);
    if (JS_IsException(value)) throw std::runtime_error(exception(runtime.context));
    auto result = string(runtime.context, value);
    JS_FreeValue(runtime.context, value);
    return result;
}

void queue_capture_hooks(Runtime& runtime, bool paused) {
    for (const auto& hook : runtime.hooks) {
        // A listener detached by the probe is never resurrected by resume.
        if (hook->detached) continue;
        const auto status = paused ? MH_QueueDisableHook(hook->address) : MH_QueueEnableHook(hook->address);
        if (status != MH_OK) throw std::runtime_error("could not queue native capture hook changes");
    }
}

bool capture_quiet(Runtime& runtime, std::uint64_t& generation) {
    using Clock = std::chrono::steady_clock;
    const auto deadline = Clock::now() + std::chrono::milliseconds(250);
    auto quiet_since = Clock::now();
    generation = runtime.activity_generation.load();
    for (;;) {
        const auto current = runtime.activity_generation.load();
        const auto active = runtime.active.load();
        const auto now = Clock::now();
        if (active || current != generation) { quiet_since = now; generation = current; }
        if (!active && now - quiet_since >= std::chrono::milliseconds(25)) return true;
        if (now >= deadline) return false;
        Sleep(1); // In-flight onLeave must retain access to the JS mutex.
    }
}

std::string capture_control(Runtime& runtime, const nlohmann::json& command) {
    using Json = nlohmann::json;
    const bool valid = command.contains("paused") && command["paused"].is_boolean()
        && command.contains("revision") && command["revision"].is_number_unsigned()
        && command["revision"].get<std::uint64_t>() > 0
        && command["revision"].get<std::uint64_t>() <= 2147483647;
    const auto revision = valid ? command["revision"].get<std::uint64_t>() : 0;
    const bool target = valid && command["paused"].get<bool>();
    Json status;
    auto snapshot = [&] {
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        if (!runtime.initialized || !runtime.fatal.empty() || runtime.stopping.load())
            throw std::runtime_error(runtime.fatal.empty() ? "native runtime is not ready" : runtime.fatal);
        return Json::parse(dispatch_request(runtime, "{\"operation\":\"capture_status\"}"));
    };
    auto reply = [&](bool quiet, bool retry, const std::string& error = std::string()) {
        if (!status.is_object()) status = Json{{"capture_pause_version", 1}, {"capture_generation", 0},
            {"sequence", 0}, {"epoch", 0}, {"active", 0}, {"active_hooks", Json::array()}, {"error", nullptr}};
        status["applied_revision"] = status.value("revision", Json(0));
        status["revision"] = revision;
        status["target_paused"] = target;
        status["paused"] = runtime.capture_paused.load();
        status["native_paused"] = runtime.capture_paused.load();
        status["native_quiet"] = quiet;
        status["retry"] = retry;
        if (!quiet) status["active"] = std::max(status.value("active", 0u), runtime.active.load());
        if (!error.empty()) status["error"] = error;
        return status.dump();
    };
    try {
        if (!valid) return reply(false, false, "invalid capture control request");
        if (revision < runtime.capture_revision || (revision == runtime.capture_revision
                && target != runtime.capture_target_paused)) {
            status = snapshot();
            return reply(false, false, "stale capture control revision");
        }
        runtime.capture_revision = revision;
        runtime.capture_target_paused = target;
        if (target) {
            // Gate entrants before waiting for the VM lock. Existing onLeave
            // work is still admitted and must drain before the JS transition.
            runtime.capture_paused.store(true);
            runtime.pending_pause_revision = revision;
        }
        status = snapshot();
        if (!status.value("error", Json()).is_null()) return reply(false, false);
        if (!target && !runtime.capture_paused.load() && !runtime.capture_hooks_disabled) {
            // An already-running request must not introduce a silent pause or
            // transient state reset just to acknowledge a newer no-op revision.
            return reply(false, false);
        }
        runtime.capture_paused.store(true);
        {
            std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
            queue_capture_hooks(runtime, true);
        }
        if (MH_ApplyQueued() != MH_OK) throw std::runtime_error("could not disable native capture hooks");
        runtime.capture_hooks_disabled = true;
        std::uint64_t generation = 0;
        if (!capture_quiet(runtime, generation)) {
            status = snapshot();
            return reply(false, true);
        }
        {
            std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
            if (runtime.active.load() || runtime.activity_generation.load() != generation) {
                status = Json::parse(dispatch_request(runtime, "{\"operation\":\"capture_status\"}"));
                return reply(false, true);
            }
            auto transition = [&](bool paused, std::uint64_t applied_revision) {
                return Json::parse(dispatch_request(runtime, Json{{"operation", "capture_control"},
                    {"paused", paused}, {"revision", applied_revision}}.dump()));
            };
            // A superseding resume must still expose a gap if native hooks
            // stopped before a busy pause could commit its JS transition.
            if (!target && !status.value("paused", false) && runtime.pending_pause_revision) {
                status = transition(true, runtime.pending_pause_revision);
                if (status.value("retry", false) || !status.value("error", Json()).is_null()) return reply(true, status.value("retry", false));
            }
            status = transition(target, revision);
            if (status.value("retry", false) || !status.value("error", Json()).is_null()) return reply(true, status.value("retry", false));
            runtime.pending_pause_revision = 0;
            if (!target) queue_capture_hooks(runtime, false);
        }
        if (!target) {
            // Never enable while holding the JS lock: MinHook suspends threads.
            if (MH_ApplyQueued() != MH_OK) throw std::runtime_error("could not enable native capture hooks");
            runtime.capture_hooks_disabled = false;
            runtime.capture_paused.store(false);
        }
        return reply(true, false);
    } catch (const std::exception& error) {
        // Control errors are typed replies, not a transport disconnect. Keep the
        // owner and pause gate so an incomplete transition is never a false resume.
        return reply(false, false, error.what());
    }
}
}

extern "C" void* arkdps_on_enter(Hook* hook, EntryFrame* frame) noexcept {
    auto& runtime = state();
    runtime.active.fetch_add(1);
    runtime.activity_generation.fetch_add(1);
    std::uintptr_t original_return = 0;
    // The caller stack is necessarily readable for a valid function invocation.
    std::memcpy(&original_return, reinterpret_cast<void*>(frame->stack), sizeof(original_return));
    frame->return_target = 2; // active-only scope if the bounded TLS stack is full
    if (invocations.depth >= invocations.calls.size()) {
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        fail("native hook nesting limit exceeded");
        return hook->original;
    }
    auto& invocation = invocations.calls[invocations.depth++];
    invocation = {hook, original_return, JS_UNDEFINED};
    frame->return_target = 1;
    try {
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        if (!runtime.fatal.empty() || runtime.stopping.load() || runtime.capture_paused.load()) return hook->original;
        auto* c = runtime.context;
        JS_UpdateStackTop(runtime.runtime);
        invocation.receiver = JS_NewObject(c);
        JS_SetPropertyStr(c, invocation.receiver, "threadId", JS_NewUint32(c, GetCurrentThreadId()));
        JS_SetPropertyStr(c, invocation.receiver, "returnAddress", pointer(c, original_return));
        std::array<std::uint64_t, 16> values{{frame->rcx, frame->rdx, frame->r8, frame->r9}};
        constexpr std::size_t stack_first = 4;
        constexpr std::size_t slot_size = sizeof(values[0]);
        const auto stack_arguments = frame->stack + 40;
        try {
            read(stack_arguments, values.data() + stack_first, (values.size() - stack_first) * slot_size);
        } catch (const std::runtime_error&) {
            // Keep the original per-slot validation when a bulk read fails.
            for (std::size_t slot = stack_first; slot < values.size(); ++slot)
                read(stack_arguments + (slot - stack_first) * slot_size, &values[slot], slot_size);
        }
        auto bytes = JS_NewArrayBufferCopy(c, reinterpret_cast<const std::uint8_t*>(values.data()),
                                          values.size() * slot_size);
        if (JS_IsException(bytes)) throw std::runtime_error(exception(c));
        auto args = JS_Call(c, runtime.argument_view, JS_UNDEFINED, 1, &bytes);
        JS_FreeValue(c, bytes);
        if (JS_IsException(args)) throw std::runtime_error(exception(c));
        try { callback(c, hook, "onEnter", invocation.receiver, 1, &args); }
        catch (...) { JS_FreeValue(c, args); throw; }
        JS_FreeValue(c, args);
    } catch (const std::exception& error) { std::lock_guard<std::recursive_mutex> lock(runtime.mutex); fail(error.what()); }
    catch (...) { std::lock_guard<std::recursive_mutex> lock(runtime.mutex); fail("native onEnter failed"); }
    return hook->original;
}

extern "C" void* arkdps_on_leave(ReturnFrame* frame, std::uint64_t mode) noexcept {
    auto& runtime = state();
    if (mode == 2) {
        runtime.active.fetch_sub(1);
        runtime.activity_generation.fetch_add(1);
        return nullptr;
    }
    if (!invocations.depth) return nullptr;
    // Keep the slot on the stack until after JS: native calls made by an onLeave
    // can themselves enter instrumented functions and need a distinct TLS slot.
    auto& invocation = invocations.calls[invocations.depth - 1];
    const auto target = invocation.return_address;
    try {
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        JS_UpdateStackTop(runtime.runtime);
        if (runtime.fatal.empty() && !JS_IsUndefined(invocation.receiver)) {
            auto value = pointer(runtime.context, frame->rax);
            try { callback(runtime.context, invocation.hook, "onLeave", invocation.receiver, 1, &value); }
            catch (...) { JS_FreeValue(runtime.context, value); throw; }
            JS_FreeValue(runtime.context, value);
        }
        JS_FreeValue(runtime.context, invocation.receiver);
        invocation.receiver = JS_UNDEFINED;
    } catch (const std::exception& error) { std::lock_guard<std::recursive_mutex> lock(runtime.mutex); fail(error.what()); }
    catch (...) { std::lock_guard<std::recursive_mutex> lock(runtime.mutex); fail("native onLeave failed"); }
    --invocations.depth;
    runtime.active.fetch_sub(1);
    runtime.activity_generation.fetch_add(1);
    return reinterpret_cast<void*>(target);
}

namespace arkdps {
void initialize(const std::string& source) {
    auto& runtime = state();
    std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
    if (runtime.runtime) throw std::runtime_error("native runtime can only initialize once per process");
    if (source.empty() || source.size() > 2 * max_read) throw std::runtime_error("invalid probe source size");
    const auto status = MH_Initialize();
    if (status != MH_OK) throw std::runtime_error("could not initialize native hook library");
    runtime.runtime = JS_NewRuntime();
    if (!runtime.runtime) throw std::runtime_error("could not create JavaScript runtime");
    JS_SetMemoryLimit(runtime.runtime, 512 * 1024 * 1024);
    JS_SetMaxStackSize(runtime.runtime, 256 * 1024);
    runtime.context = JS_NewContext(runtime.runtime);
    if (!runtime.context) throw std::runtime_error("could not create JavaScript context");
    auto* c = runtime.context;
    auto global = JS_GetGlobalObject(c);
    function(c,global,"__read",js_read,2);function(c,global,"__write",js_write,2);function(c,global,"__alloc",js_allocate,1);
    function(c,global,"__cstring",js_cstring,1);function(c,global,"__utf16",js_utf16,2);
    function(c,global,"__moduleInfo",js_module,1);function(c,global,"__findModule",js_find_module,1);function(c,global,"__export",js_export,2);
    function(c,global,"__nativeCall",js_native_call,2);function(c,global,"__attach",js_attach,2);function(c,global,"__detach",js_detach,1);
    function(c,global,"__send",js_send,1);function(c,global,"__nativeActive",js_active,0);function(c,global,"__pid",js_pid,0);
    try {
        evaluate(c, arkdps_bridge_source, "native-bridge.js");
        runtime.pointer = JS_GetPropertyStr(c, global, "__arkdpsPointer");
        runtime.argument_view = JS_GetPropertyStr(c, global, "__arkdpsArguments");
        runtime.dispatch = JS_GetPropertyStr(c, global, "__arkdpsRequest");
        evaluate(c, source, "event-probe.js");
        if (runtime.ready.empty()) throw std::runtime_error("probe did not publish readiness");
        for (const auto& hook : runtime.hooks) {
            if (hook->detached) continue;
            if (MH_QueueEnableHook(hook->address) != MH_OK) throw std::runtime_error("could not queue native hooks");
        }
        // All JS definitions and retained function handles now exist before the
        // first target thread can execute one of the enabled callbacks.
        runtime.initialized = true;
        if (MH_ApplyQueued() != MH_OK) throw std::runtime_error("could not enable native hooks");
        JS_FreeValue(c, global);
    } catch (...) {
        JS_FreeValue(c, global);
        fail("native probe initialization failed");
        disable_hooks();
        throw;
    }
}
std::string readiness() {
    auto& runtime = state();
    std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
    if (!runtime.initialized || !runtime.fatal.empty()) throw std::runtime_error(runtime.fatal.empty() ? "native runtime is not ready" : runtime.fatal);
    return runtime.ready;
}
std::string request(const std::string& json) {
    auto& runtime = state();
    if (json.size() > 8 * max_read) throw std::runtime_error("request is too large");
    const auto command = nlohmann::json::parse(json, [](int depth, nlohmann::json::parse_event_t, nlohmann::json&) {
        if (depth > 64) throw std::runtime_error("request nesting exceeds limit");
        return true;
    });
    if (command.is_object() && command.value("operation", nlohmann::json()) == "capture_control")
        return capture_control(runtime, command);
    std::string result;
    std::exception_ptr failure;
    bool stopping = false;
    try {
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        stopping = command.is_object() && command.value("operation", nlohmann::json()) == "stop";
        if (stopping) {
            // This path is available even after a fatal callback. Do not leave
            // native detours enabled merely because JS can no longer run.
            runtime.stopping.store(true);
            for (const auto& hook : runtime.hooks) {
                const auto status = MH_QueueDisableHook(hook->address);
                if (status != MH_OK) throw std::runtime_error("could not queue every native hook for stop");
                hook->detached = true;
            }
        }
        if (!runtime.initialized || !runtime.fatal.empty()) {
            if (!stopping) throw std::runtime_error(runtime.fatal.empty() ? "native runtime is not ready" : runtime.fatal);
            result = "{\"active\":0}";
        } else {
            result = dispatch_request(runtime, json);
        }
    } catch (...) { failure = std::current_exception(); }
    // No QuickJS lock while MinHook suspends threads: a game thread can be
    // blocked acquiring that lock after entering our thunk.
    if (MH_ApplyQueued() != MH_OK) throw std::runtime_error("could not apply native hook changes");
    if (stopping) {
        using Clock = std::chrono::steady_clock;
        const auto deadline = Clock::now() + std::chrono::seconds(2);
        auto quiet_since = Clock::now();
        auto generation = runtime.activity_generation.load();
        for (;;) {
            const auto current = runtime.activity_generation.load();
            const auto active = runtime.active.load();
            const auto now = Clock::now();
            if (active || current != generation) { quiet_since = now; generation = current; }
            if (!active && now - quiet_since >= std::chrono::milliseconds(25)) break;
            if (now >= deadline) throw std::runtime_error("native hooks disabled but callbacks did not drain within 2 seconds; active=" + std::to_string(active));
            // Original functions and their onLeave callbacks must be able to
            // acquire the JS mutex while stop waits for their full lifetime.
            Sleep(1);
        }
        std::lock_guard<std::recursive_mutex> lock(runtime.mutex);
        if (runtime.active.load() || runtime.activity_generation.load() != generation)
            throw std::runtime_error("native stop lost its quiet fence; active=" + std::to_string(runtime.active.load()));
        if (!runtime.fatal.empty()) throw std::runtime_error("native hooks disabled; active=" + std::to_string(runtime.active.load()) + "; fatal: " + runtime.fatal);
        if (!failure) {
            auto value = nlohmann::json::parse(result);
            value["active"] = runtime.active.load();
            value["native_quiet"] = true;
            value["native_activity_generation"] = std::to_string(runtime.activity_generation.load());
            result = value.dump();
        }
    }
    if (failure) {
        if (stopping) {
            try { std::rethrow_exception(failure); }
            catch (const std::exception& error) {
                throw std::runtime_error("native hooks disabled; active=" + std::to_string(runtime.active.load()) + "; stop failed: " + error.what());
            }
        }
        std::rethrow_exception(failure);
    }
    return result;
}
}

// Self-owned ABI fixtures. They do not inspect or modify a game process.
extern "C" __declspec(dllexport) __attribute__((noinline)) std::uint64_t ArkDpsTestInteger(
    std::uint64_t a,std::uint64_t b,std::uint64_t c,std::uint64_t d,std::uint64_t e,std::uint64_t f,
    std::uint64_t g,std::uint64_t h,std::uint64_t i,std::uint64_t j,std::uint64_t k,std::uint64_t l) {
    return a+2*b+3*c+4*d+5*e+6*f+7*g+8*h+9*i+10*j+11*k+12*l;
}
extern "C" __declspec(dllexport) __attribute__((noinline)) std::uint64_t ArkDpsTestFullArguments(
    std::uint64_t a,std::uint64_t b,std::uint64_t c,std::uint64_t d,std::uint64_t e,std::uint64_t f,
    std::uint64_t g,std::uint64_t h,std::uint64_t i,std::uint64_t j,std::uint64_t k,std::uint64_t l,
    std::uint64_t m,std::uint64_t n,std::uint64_t o,std::uint64_t p) {
    return a^b^c^d^e^f^g^h^i^j^k^l^m^n^o^p;
}
extern "C" __declspec(dllexport) __attribute__((noinline)) double ArkDpsTestFloat(double a,double b,double c,double d,double e) {
    return a+2*b+3*c+4*d+5*e;
}
struct TestLarge { std::uint64_t a,b,c,d; };
extern "C" __declspec(dllexport) __attribute__((noinline)) TestLarge ArkDpsTestSret(std::uint64_t a,std::uint64_t b) {
    return {a,b,a+b,a^b};
}
extern "C" __declspec(dllexport) __attribute__((noinline)) std::uint64_t ArkDpsTestNested(std::uint64_t a) {
    return ArkDpsTestInteger(a,2,3,4,5,6,7,8,9,10,11,12)+a;
}
extern "C" __declspec(dllexport) __attribute__((noinline)) std::uint64_t ArkDpsTestSlow(std::uint64_t milliseconds) {
    Sleep(static_cast<DWORD>(milliseconds > 1000 ? 1000 : milliseconds));
    return milliseconds + 123;
}
