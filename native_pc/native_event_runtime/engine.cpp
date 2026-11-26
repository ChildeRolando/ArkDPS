#include "engine.h"
#include "engine_worker.h"
#include "engine_capture_lane.h"
#include "engine_capture_plan.h"
#include "engine_capture_config.h"
#include "engine_capture_pipeline.h"
#include "engine_terminal_fence.h"
#include "engine_capture_timing.h"
#include <windows.h>
#include <psapi.h>
#include <MinHook.h>
extern "C" {
#include <quickjs.h>
}
#include "owned_replay_js.h"
#include "bridge_source.h"
#include "publication_source.h"
#include "cold_plan_source.h"
#include "json.hpp"
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
    unsigned timing_id = 0;
    std::string timing_name;
    void* address = nullptr;
    void* original = nullptr;
    void* thunk = nullptr;
    JSValue callbacks = JS_UNDEFINED;
    bool detached = false;
    arkdps::capture::CompiledPlan capture_plan;
};
struct Allocation { std::uintptr_t base; std::size_t size; };
struct Runtime {
    arkdps::EngineWorker worker;
    std::unique_ptr<arkdps::capture::CaptureLanes> lanes;
    std::atomic<arkdps::capture::CaptureLanes*> ingress{nullptr};
    arkdps::capture::CaptureConfigurations configurations;
    std::uint64_t processed_ticket = 0, closed_ticket = 0, delivered_sequence = 0, published_sequence = 0;
    bool replay_active = false;
    arkdps::capture::TerminalFence terminal_fence;
    arkdps::capture::CallbackTimingRegistry callback_timings;
    JSValue prefix_projection = JS_UNDEFINED;
    JSValue publication_publish = JS_UNDEFINED, publication_request = JS_UNDEFINED;
    JSValue mode_state = JS_UNDEFINED, mode_install = JS_UNDEFINED, graph_retire = JS_UNDEFINED;
    std::atomic<std::uint64_t> capture_mode_epoch{0};
    std::atomic<std::uint64_t> cold_publication_request{0};
    std::uint64_t published_mode_epoch = 0;
    int desired_mode = -1;
    std::string captured_publication;
    std::uint64_t published_ticket = 0;
    arkdps::capture::RecordHeader replay_header;
    JSValue replay_dispatch = JS_UNDEFINED;
    struct WorkerInvocation { JSValue receiver = JS_UNDEFINED; std::uint64_t id = 0; Hook* hook = nullptr; };
    std::array<std::array<WorkerInvocation, 256>, 16> receivers{};
    std::array<unsigned, 16> replay_depth{};
    std::array<std::byte, arkdps::capture::record_capacity> consume_buffer{};
    JSRuntime* runtime = nullptr;
    JSContext* context = nullptr;
    JSValue pointer = JS_UNDEFINED;
    JSValue dispatch = JS_UNDEFINED;
    std::vector<std::unique_ptr<Hook>> hooks;
    std::vector<Allocation> allocations;
    std::unordered_set<std::uintptr_t> exports;
    std::size_t allocated = 0;
    std::atomic<unsigned> active{0};
    std::atomic<std::uint64_t> activity_generation{0};
    std::atomic<bool> stopping{false};
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
// Diagnostics have no ownership of the VM lock. Every thread writes only its
// own fixed cells; snapshots use bounded seqlock reads outside the VM mutex.
// There is no hot-path allocation, log, global RMW, division, or clock when off.
constexpr unsigned timing_threads = 16, timing_cells = 1032;
constexpr unsigned timing_request = 1024, timing_transport_base = 1028;
struct TimingCell {
    std::atomic<std::uint64_t> revision{0}, calls{0}, samples{0}, ticks{0}, exclusive{0}, maximum{0};
    std::atomic<std::uint64_t> wait{0}, wait_max{0}, hold{0}, hold_max{0}, js{0}, reads{0}, bytes{0}, read_ticks{0}, read_spans{0};
};
struct TimingThread {
    std::atomic<DWORD> owner{0};
    std::array<TimingCell, timing_cells> cells;
};
struct TimingState {
    std::atomic<bool> enabled{false}, thread_overflow{false};
    std::array<TimingThread, timing_threads> threads;
};
TimingState timing_state;
std::uint64_t ticks_now() noexcept {
    LARGE_INTEGER value; QueryPerformanceCounter(&value); return value.QuadPart;
}
class CallbackTimer final {
public:
    explicit CallbackTimer(bool leave) noexcept : leave_(leave) {
        auto& runtime=state();
        if (!timing_state.enabled.load(std::memory_order_relaxed) || !runtime.lanes->enabled.load(std::memory_order_acquire)
            || runtime.lanes->failure.load(std::memory_order_relaxed)) return;
        const auto tid=GetCurrentThreadId();
        for (unsigned i=0;i<arkdps::capture::lane_count;++i) {
            if (runtime.lanes->lanes[i]->owner.load(std::memory_order_acquire)!=tid) continue;
            if(runtime.callback_timings.select(i)){index_=i;start_=ticks_now();}
            break;
        }
    }
    void bind(arkdps::capture::Lane& lane,unsigned hook) noexcept {
        if (!start_ || hook>=arkdps::capture::timing_hooks || state().lanes->lanes[index_].get()!=&lane) {start_=0;return;}
        hook_=hook;generation_=lane.generation.load(std::memory_order_acquire);bound_=true;
    }
    void stack_read() noexcept {++extra_reads_;extra_bytes_+=8;}
    void capture(const arkdps::capture::Snapshot& snapshot,unsigned parents,bool queued) noexcept {
        reads_=snapshot.read_count;bytes_=snapshot.read_bytes;dropped_=!queued;
        if(queued)enqueued_=sizeof(arkdps::capture::RecordHeader)+parents*sizeof(arkdps::capture::ParentCheckpoint)
            +snapshot.segment_count*sizeof(arkdps::capture::Segment)+snapshot.field_count*sizeof(arkdps::capture::FieldState)+snapshot.byte_count;
    }
    ~CallbackTimer(){
        if(!start_ || !bound_)return;
        const auto end=ticks_now();
        state().callback_timings.cell(index_,hook_,leave_).record(generation_,end>=start_?end-start_:0,
            reads_+extra_reads_,bytes_+extra_bytes_,enqueued_,dropped_);
    }
private:
    unsigned index_=0,hook_=0;bool leave_=false,bound_=false,dropped_=true;
    std::uint64_t start_=0,generation_=0,reads_=0,bytes_=0,enqueued_=0,extra_reads_=0,extra_bytes_=0;
};
TimingThread* timing_slot() noexcept {
    const auto id = GetCurrentThreadId();
    for (auto& slot : timing_state.threads)
        if (slot.owner.load(std::memory_order_relaxed)==id) return &slot;
    for (auto& slot : timing_state.threads) {
        DWORD empty=0;
        if (slot.owner.compare_exchange_strong(empty,id,std::memory_order_relaxed)) return &slot;
    }
    timing_state.thread_overflow.store(true,std::memory_order_relaxed);
    return nullptr;
}
std::uint32_t timing_random = 0x9e3779b9u; // Worker-only sampler.
void timing_add(std::atomic<std::uint64_t>& value, std::uint64_t delta) noexcept {
    value.store(value.load(std::memory_order_relaxed) + delta, std::memory_order_relaxed);
}
struct TimingSpan;
TimingSpan* timing_active = nullptr;
struct TimingSpan {
    TimingCell* cell = nullptr;
    TimingSpan* parent = nullptr;
    std::uint64_t start = 0, child = 0, wait = 0, hold = 0, js = 0;
    std::uint64_t reads = 0, bytes = 0, read_ticks = 0;
    bool sampled = false, detail_sampled = false;
    explicit TimingSpan(unsigned id, bool full = false) noexcept {
        if (!timing_state.enabled.load(std::memory_order_relaxed)) return;
        auto* slot = timing_slot();
        if (!slot || id >= timing_cells) return;
        cell = &slot->cells[id];
        timing_add(cell->calls, 1);
        timing_random ^= timing_random << 13;
        timing_random ^= timing_random >> 17;
        timing_random ^= timing_random << 5;
        const bool selected = (timing_random & 127u) == 0;
        sampled = full || timing_active || selected;
        if (sampled) {
            parent = timing_active;
            detail_sampled = selected || (parent && parent->detail_sampled);
            timing_active = this; start = ticks_now();
        }
    }
    ~TimingSpan() noexcept {
        if (!sampled) return;
        const auto elapsed = ticks_now() - start;
        timing_active = parent;
        if (parent) parent->child += elapsed;
        cell->revision.fetch_add(1, std::memory_order_acq_rel);
        timing_add(cell->samples, 1); timing_add(cell->ticks, elapsed);
        timing_add(cell->exclusive, elapsed > child ? elapsed-child : 0);
        cell->maximum.store((std::max)(cell->maximum.load(std::memory_order_relaxed), elapsed), std::memory_order_relaxed);
        timing_add(cell->wait, wait); timing_add(cell->hold, hold); timing_add(cell->js, js);
        cell->wait_max.store((std::max)(cell->wait_max.load(std::memory_order_relaxed), wait), std::memory_order_relaxed);
        cell->hold_max.store((std::max)(cell->hold_max.load(std::memory_order_relaxed), hold), std::memory_order_relaxed);
        timing_add(cell->reads, reads); timing_add(cell->bytes, bytes); timing_add(cell->read_ticks, read_ticks);
        timing_add(cell->read_spans, detail_sampled ? 1 : 0);
        cell->revision.fetch_add(1, std::memory_order_acq_rel);
    }
};
struct ReadTiming {
    TimingSpan* span = timing_active && timing_active->detail_sampled ? timing_active : nullptr;
    std::uint64_t start = span ? ticks_now() : 0;
    explicit ReadTiming(std::size_t bytes) noexcept { if (span) { ++span->reads; span->bytes += bytes; } }
    ~ReadTiming() { if (span) span->read_ticks += ticks_now()-start; }
};
nlohmann::json timing_snapshot() {
    const auto summary_started = ticks_now();
    using Json = nlohmann::json;
    LARGE_INTEGER frequency; QueryPerformanceFrequency(&frequency);
    Json rows = Json::array();
    unsigned skipped = 0, omitted = 0;
    for (const auto& thread : timing_state.threads) {
        const auto owner = thread.owner.load(std::memory_order_relaxed);
        if (!owner) continue;
        for (unsigned id=0; id<timing_cells; ++id) {
            const auto& c = thread.cells[id];
            if (!c.calls.load(std::memory_order_relaxed)) continue;
            if (rows.size() >= 128) { ++omitted; continue; }
            Json row;
            bool consistent = false;
            for (unsigned attempt=0; attempt<2 && !consistent; ++attempt) {
                const auto revision = c.revision.load(std::memory_order_acquire);
                if (revision & 1) continue;
                row = {{"thread_id", owner}, {"cell", id}, {"calls", c.calls.load(std::memory_order_relaxed)},
                    {"samples", c.samples.load(std::memory_order_relaxed)}, {"ticks", c.ticks.load(std::memory_order_relaxed)},
                    {"exclusive_ticks", c.exclusive.load(std::memory_order_relaxed)}, {"max_ticks", c.maximum.load(std::memory_order_relaxed)},
                    {"vm_wait_ticks", c.wait.load(std::memory_order_relaxed)}, {"vm_wait_max_ticks", c.wait_max.load(std::memory_order_relaxed)},
                    {"vm_hold_max_ticks", c.hold_max.load(std::memory_order_relaxed)},
                    {"vm_hold_ticks", c.hold.load(std::memory_order_relaxed)}, {"js_ticks", c.js.load(std::memory_order_relaxed)},
                    {"read_calls", c.reads.load(std::memory_order_relaxed)}, {"read_bytes", c.bytes.load(std::memory_order_relaxed)},
                    {"read_detail_spans", c.read_spans.load(std::memory_order_relaxed)},
                    {"read_ticks", c.read_ticks.load(std::memory_order_relaxed)}};
                std::atomic_thread_fence(std::memory_order_acquire);
                consistent = c.revision.load(std::memory_order_relaxed) == revision;
            }
            if (!consistent) { ++skipped; continue; }
            if (id < timing_request) {
                const auto index = id/2;
                row["kind"] = id%2 ? "hook_leave" : "hook_enter";
                if (index < state().hooks.size()) {
                    const auto& hook = state().hooks[index];
                    row["hook"] = hook->timing_name;
                    row["address"] = reinterpret_cast<std::uintptr_t>(hook->address);
                }
            } else {
                static const char* names[] = {"request_mark", "request_read", "request_ack", "request_other",
                    "transport_parse", "transport_encode", "transport_write", "transport_other"};
                row["kind"] = names[id-timing_request];
            }
            rows.push_back(std::move(row));
        }
    }
    FILETIME created{}, exited{}, kernel{}, user{};
    GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user);
    return {{"schema", 1}, {"enabled", timing_state.enabled.load(std::memory_order_relaxed)},
        {"pid", GetCurrentProcessId()}, {"qpc", ticks_now()}, {"qpc_frequency", frequency.QuadPart},
        {"process_created_filetime", (static_cast<std::uint64_t>(created.dwHighDateTime)<<32)|created.dwLowDateTime},
        {"summary_build_ticks", ticks_now()-summary_started},
        {"sampling", "hooks_random_1_in_128_nested_inherited_requests_all_reads_detail_1_in_128"}, {"cumulative", true},
        {"thread_capacity", timing_threads}, {"thread_overflow", timing_state.thread_overflow.load(std::memory_order_relaxed)},
        {"skipped_busy_cells", skipped}, {"omitted_cells", omitted}, {"rows", std::move(rows)}};
}

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
    if (state().replay_active) {
        state().lanes->poison(arkdps::capture::LaneFault::live_replay_access);
        throw std::runtime_error("live native read forbidden during captured replay");
    }
    ReadTiming timing(count);
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
        if (state().replay_active) { state().lanes->poison(arkdps::capture::LaneFault::live_replay_access); throw std::runtime_error("live native operation forbidden during captured replay"); }
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
        if (state().replay_active) { state().lanes->poison(arkdps::capture::LaneFault::live_replay_access); throw std::runtime_error("live native operation forbidden during captured replay"); }
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
        if (state().replay_active) { state().lanes->poison(arkdps::capture::LaneFault::live_replay_access); throw std::runtime_error("live native operation forbidden during captured replay"); }
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
// Deliberately held closed while sustained worker throughput is under review.
// Compiler support and source contracts are tested separately from readiness.
JSValue js_capture_capabilities(JSContext* c, JSValueConst, int, JSValueConst*) {
    const auto text=arkdps::capture::capture_capabilities(false).dump();
    return JS_ParseJSON(c,text.data(),text.size(),"native-capture-capabilities.json");
}
JSValue js_publish_scope(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c,[&] {
        if (count!=1) throw std::runtime_error("capture scope publication requires one graph");
        auto encoded=JS_JSONStringify(c,args[0],JS_UNDEFINED,JS_UNDEFINED);
        if (JS_IsException(encoded)) throw std::runtime_error(exception(c));
        std::string text;
        try { text=string(c,encoded); } catch (...) { JS_FreeValue(c,encoded); throw; }
        JS_FreeValue(c,encoded);
        state().configurations.publish(nlohmann::json::parse(text),false);
        return JS_UNDEFINED;
    });
}
JSValue js_publish_publication(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c,[&] {
        if (count!=1) throw std::runtime_error("capture publication requires one graph");
        auto encoded=JS_JSONStringify(c,args[0],JS_UNDEFINED,JS_UNDEFINED);
        if (JS_IsException(encoded)) throw std::runtime_error(exception(c));
        std::string text;
        try { text=string(c,encoded); } catch (...) { JS_FreeValue(c,encoded); throw; }
        JS_FreeValue(c,encoded);
        state().configurations.publish(nlohmann::json::parse(text),true);
        return JS_UNDEFINED;
    });
}
JSValue js_attach(JSContext* c, JSValueConst, int count, JSValueConst* args) {
    return guarded(c, [&] {
        if (count != 2 || !JS_IsObject(args[1])) throw std::runtime_error("hook requires address and callbacks");
        auto& runtime = state();
        if (runtime.hooks.size() >= 512 || runtime.initialized) throw std::runtime_error("hooks may only be registered during initialization");
        auto hook = std::make_unique<Hook>();
        hook->timing_id = static_cast<unsigned>(runtime.hooks.size());
        auto label = JS_GetPropertyStr(c, args[1], "arkdpsName");
        if (JS_IsString(label)) hook->timing_name = string(c, label).substr(0, 96);
        JS_FreeValue(c, label);
        hook->address = reinterpret_cast<void*>(integer(c, args[0]));
        auto contract=JS_GetPropertyStr(c,args[1],"arkdpsCaptureContract");
        auto encoded=JS_JSONStringify(c,contract,JS_UNDEFINED,JS_UNDEFINED);
        JS_FreeValue(c,contract);
        if (JS_IsException(encoded) || JS_IsUndefined(encoded)) {
            JS_FreeValue(c,encoded);
            throw std::runtime_error("native hook requires a complete capture contract");
        }
        std::string capture_json;
        try { capture_json=string(c,encoded); } catch (...) { JS_FreeValue(c,encoded); throw; }
        JS_FreeValue(c,encoded);
        hook->capture_plan=arkdps::capture::compile_plan(nlohmann::json::parse(capture_json), reinterpret_cast<std::uintptr_t>(hook->address));
        for (unsigned i=0;i<hook->capture_plan.plan->field_count;++i)
            if (hook->capture_plan.plan->fields[i].id>=arkdps::capture::scope_field_base
                || hook->capture_plan.plan->fields[i].guard_id>=arkdps::capture::scope_guard_base)
                throw std::runtime_error("base capture fields overlap graph namespaces");
        runtime.configurations.intern(hook->capture_plan);

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
JSValue js_active(JSContext* c, JSValueConst, int, JSValueConst*) {
    auto& runtime=state();
    return JS_NewUint32(c, runtime.replay_active ? runtime.replay_header.active : runtime.active.load());
}
JSValue js_replay_healthy(JSContext* c, JSValueConst, int, JSValueConst*) {
    auto& runtime=state();
    return JS_NewBool(c,runtime.replay_active && arkdps::owned_js::binding().active
        && runtime.lanes->failure.load(std::memory_order_acquire)==0);
}
JSValue js_pid(JSContext* c, JSValueConst, int, JSValueConst*) { return JS_NewUint32(c, GetCurrentProcessId()); }
void function(JSContext* c, JSValueConst global, const char* name, JSCFunction* callback, int arguments) {
    JS_SetPropertyStr(c, global, name, JS_NewCFunction(c, callback, name, arguments));
}
void evaluate(JSContext* c, const std::string& source, const char* filename) {
    auto value = JS_Eval(c, source.data(), source.size(), filename, JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(value)) throw std::runtime_error(exception(c));
    JS_FreeValue(c, value);
}

}


namespace {
nlohmann::json json_value(JSContext* c, JSValueConst value) {
    auto encoded=JS_JSONStringify(c,value,JS_UNDEFINED,JS_UNDEFINED);
    if (JS_IsException(encoded)) throw std::runtime_error(exception(c));
    std::string text;
    try { text=string(c,encoded); } catch (...) { JS_FreeValue(c,encoded); throw; }
    JS_FreeValue(c,encoded);
    return nlohmann::json::parse(text);
}
void install_mode_state(const nlohmann::json& mode) {
    auto& runtime=state();
    if (arkdps::capture::plan_uint(mode.at("version"),1)!=1
        || (mode.at("desired_mode")!="all" && mode.at("desired_mode")!="local"))
        throw std::runtime_error("invalid native capture mode state");
    const auto epoch=arkdps::capture::plan_uint(mode.at("mode_epoch"),9007199254740991ULL);
    runtime.desired_mode=mode.at("desired_mode")=="all" ? 1 : 0;
    // JS has already installed immutable history and graph configuration for
    // this epoch. No game thread waits for the worker or changes an old epoch.
    runtime.capture_mode_epoch.store(epoch,std::memory_order_release);
}
void configure_mode(const nlohmann::json& command) {
    if (!command.contains("autochess_enabled")) return;
    auto& runtime=state(); auto* c=runtime.context;
    if (!command.at("autochess_enabled").is_boolean()) throw std::runtime_error("autochess_enabled must be boolean");
    const int desired=command.at("autochess_enabled").get<bool>() ? 1 : 0;
    if (runtime.desired_mode==desired) return;
    if (!JS_IsFunction(c,runtime.mode_install) || runtime.desired_mode<0)
        throw std::runtime_error("native immutable capture mode control unavailable");
    const auto current=runtime.capture_mode_epoch.load(std::memory_order_acquire);
    if (current>=9007199254740991ULL) throw std::runtime_error("native capture mode epoch exhausted");
    auto barrier=JS_NewObject(c);
    JS_SetPropertyStr(c,barrier,"version",JS_NewInt32(c,1));
    JS_SetPropertyStr(c,barrier,"mode_epoch",JS_NewInt64(c,current+1));
    std::array<JSValue,2> arguments{{JS_NewBool(c,desired!=0),barrier}};
    auto answer=JS_Call(c,runtime.mode_install,JS_UNDEFINED,2,arguments.data());
    for (auto argument : arguments) JS_FreeValue(c,argument);
    if (JS_IsException(answer)) throw std::runtime_error(exception(c));
    nlohmann::json state;
    try { state=json_value(c,answer); } catch (...) { JS_FreeValue(c,answer); throw; }
    JS_FreeValue(c,answer);
    if (arkdps::capture::plan_uint(state.at("mode_epoch"),9007199254740991ULL)!=current+1
        || state.at("desired_mode")!=(desired ? "all" : "local"))
        throw std::runtime_error("native capture mode installation mismatch");
    install_mode_state(state);
}
void js_property(JSContext* c, JSValue object, const char* name, JSValue value) {
    if (JS_SetPropertyStr(c,object,name,value)<0) throw std::runtime_error(exception(c));
}
void replay_record(const std::byte* bytes, std::size_t length) {
    using namespace arkdps::capture;
    auto& runtime=state(); auto* c=runtime.context;
    RecordHeader header;
    if (length<sizeof header) throw std::runtime_error("short native capture header");
    std::memcpy(&header,bytes,sizeof header);
    const auto required=sizeof header+header.parent_checkpoint_count*sizeof(ParentCheckpoint)+header.segment_count*sizeof(Segment)+header.field_count*sizeof(FieldState)+header.byte_count;
    if (header.parent_checkpoint_count>invocation_depth || header.lane>=lane_count || !header.depth || header.depth>invocation_depth
        || header.hook_id>=runtime.hooks.size() || header.segment_count>max_segments
        || header.field_count>max_fields || header.byte_count>max_bytes || length!=required
        || header.ticket!=runtime.processed_ticket+1 || (header.phase!=Phase::entry && header.phase!=Phase::leave))
        throw std::runtime_error("invalid native capture record");
    if (structural_error(header.error)) throw std::runtime_error("native capture batch has a structural failure");
    auto* hook=runtime.hooks[header.hook_id].get();
    auto& depth=runtime.replay_depth[header.lane];
    auto& call=runtime.receivers[header.lane][header.depth-1];
    if (header.phase==Phase::entry) {
        if (header.depth!=depth+1 || call.id || header.parent!=(depth ? runtime.receivers[header.lane][depth-1].id : 0))
            throw std::runtime_error("native capture parent/entry topology mismatch");
        call.id=header.invocation; call.hook=hook; call.receiver=JS_NewObject(c); ++depth;
        js_property(c,call.receiver,"threadId",JS_NewUint32(c,header.thread_id));
        js_property(c,call.receiver,"returnAddress",pointer(c,header.return_address));
        js_property(c,call.receiver,"nativeCaptureInvocation",JS_NewString(c,std::to_string(header.invocation).c_str()));
        js_property(c,call.receiver,"nativeCaptureModeEpoch",JS_NewInt64(c,header.mode_epoch));
    } else if (header.depth!=depth || call.id!=header.invocation || call.hook!=hook
               || header.parent!=(depth>1 ? runtime.receivers[header.lane][depth-2].id : 0))
        throw std::runtime_error("native capture leave topology mismatch");
    // Copy a small worker-owned header once. The JS record decodes only the
    // properties a real callback reads; it never consults a later native lease.
    constexpr unsigned header_words=22;
    std::array<std::uint64_t,header_words+2*invocation_depth> packed{};
    const std::array<std::uint64_t,header_words> values{{header.ticket,header.invocation,header.parent,
        header.activity_begin,header.activity,header.thread_birth,header.publication_request,
        header.mode_epoch,header.scope_mode_epoch,header.scope_revision,
        header.publication_captured ? header.publication_revision : 0,header.cold_revision,
        header.hook_id,header.checkpoint,header.active,header.active_after,header.lane,header.thread_id,
        header.phase==Phase::entry ? 0u : 1u,header.closed_after ? 1u : 0u,
        header.scope_unavailable ? 1u : 0u,header.parent_checkpoint_count}};
    std::copy(values.begin(),values.end(),packed.begin());
    for (unsigned i=0;i<header.parent_checkpoint_count;++i) {
        ParentCheckpoint parent;
        std::memcpy(&parent,bytes+sizeof header+i*sizeof parent,sizeof parent);
        if (!parent.invocation || parent.invocation==header.invocation || parent.checkpoint!=i+1 || parent.reserved)
            throw std::runtime_error("invalid exact native parent checkpoint");
    }
    const auto parent_bytes=header.parent_checkpoint_count*sizeof(ParentCheckpoint);
    std::memcpy(packed.data()+header_words,bytes+sizeof header,parent_bytes);
    auto packed_bytes=JS_NewArrayBufferCopy(c,reinterpret_cast<const uint8_t*>(packed.data()),header_words*8+parent_bytes);
    if (JS_IsException(packed_bytes)) throw std::runtime_error(exception(c));
    const auto segment_start=sizeof header+header.parent_checkpoint_count*sizeof(ParentCheckpoint);
    const auto field_start=segment_start+header.segment_count*sizeof(Segment);
    const auto payload=bytes+field_start+header.field_count*sizeof(FieldState);
    const auto view_error=arkdps::owned_js::begin(bytes+segment_start,header.segment_count,
        bytes+field_start,header.field_count,payload,header.byte_count,
        runtime.configurations.roles,header.ticket,header.error);
    arkdps::owned_js::Lease view_lease;
    if (view_error!=Error::ok) {JS_FreeValue(c,packed_bytes);throw std::runtime_error("native owned view rejected structural capture");}
    runtime.replay_header=header; runtime.replay_active=true;
    auto method=JS_GetPropertyStr(c,hook->callbacks,header.phase==Phase::entry ? "onEnter" : "onLeave");
    auto callback_payload=header.phase==Phase::entry
        ? JS_NewArrayBufferCopy(c,reinterpret_cast<const uint8_t*>(header.args.data()),hook->capture_plan.plan->arg_count*8)
        : JS_NewBigUint64(c,header.return_value);
    std::array<JSValue,6> dispatch_args{{packed_bytes,method,call.receiver,callback_payload,
        JS_NewUint32(c,header.arg_mask),header.projection_candidate && header.closed_after ? runtime.prefix_projection : JS_UNDEFINED}};
    JSValue answer=JS_EXCEPTION;
    if (!JS_IsException(method) && !JS_IsException(callback_payload))
        answer=JS_Call(c,runtime.replay_dispatch,JS_UNDEFINED,dispatch_args.size(),dispatch_args.data());
    JS_FreeValue(c,packed_bytes);JS_FreeValue(c,method);JS_FreeValue(c,callback_payload);
    runtime.replay_active=false;
    if (const auto failure=runtime.lanes->failure.load(std::memory_order_acquire)) {
        if (JS_IsException(answer)) {auto ignored=JS_GetException(c);JS_FreeValue(c,ignored);}
        JS_FreeValue(c,answer);
        throw std::runtime_error("native capture failed: code="+std::to_string(failure));
    }
    if (JS_IsException(answer)) throw std::runtime_error(exception(c));
    if (JS_IsArray(c,answer)!=1) {JS_FreeValue(c,answer);throw std::runtime_error("invalid native replay dispatch result");}
    auto record=JS_GetPropertyUint32(c,answer,0);
    auto projection=JS_GetPropertyUint32(c,answer,1);
    auto complete=JS_GetPropertyUint32(c,answer,2);
    const bool valid=JS_IsBool(complete) && JS_ToBool(c,complete)==1;
    JS_FreeValue(c,complete);JS_FreeValue(c,answer);
    if (!valid || JS_IsException(record) || JS_IsException(projection)) {
        JS_FreeValue(c,record);JS_FreeValue(c,projection);
        throw std::runtime_error("native callback replay has structural gaps");
    }
    if (header.phase==Phase::leave) {
        JS_FreeValue(c,call.receiver); call={}; --depth;
    }
    runtime.processed_ticket=header.ticket;
    if (header.closed_after) {
        runtime.closed_ticket=header.ticket;
        if (!JS_IsUndefined(projection) && !JS_IsNull(projection)) {
            std::array<JSValue,2> arguments{{record,projection}};
            // The journal is pure even though ReplayEnd already cleared the JS
            // read view. Native primitives remain forbidden throughout publish.
            runtime.replay_active=true;
            auto published=JS_Call(c,runtime.publication_publish,JS_UNDEFINED,2,arguments.data());
            runtime.replay_active=false;
            if (JS_IsException(published) || runtime.lanes->failure.load(std::memory_order_acquire)) {
                JS_FreeValue(c,published); JS_FreeValue(c,record); JS_FreeValue(c,projection);
                throw std::runtime_error("native publication failed its owned boundary");
            }
            auto encoded=JS_JSONStringify(c,published,JS_UNDEFINED,JS_UNDEFINED);
            JS_FreeValue(c,published);
            if (JS_IsException(encoded)) { JS_FreeValue(c,record); JS_FreeValue(c,projection); throw std::runtime_error(exception(c)); }
            std::string text;
            try { text=string(c,encoded); } catch (...) { JS_FreeValue(c,encoded); JS_FreeValue(c,record); JS_FreeValue(c,projection); throw; }
            JS_FreeValue(c,encoded);
            const auto parsed=nlohmann::json::parse(text);
            if (!parsed.is_object() || parsed.value("native_publication_failure",false)
                || !parsed.value("error",nlohmann::json()).is_null()) {
                runtime.lanes->poison(LaneFault::publication_failure);
                throw std::runtime_error("native capture failed: code="+std::to_string(runtime.lanes->failure.load()));
            }

            if (parsed.is_object() && !parsed.value("retry",false) && parsed.value("error",nlohmann::json()).is_null()) {
                const auto& fence=parsed.at("native_publication");
                if (arkdps::capture::plan_uint(fence.at("version"),1)!=1
                    || fence.at("closed_ticket")!=std::to_string(header.ticket)
                    || !fence.at("token").is_string() || fence.at("token").get<std::string>().empty())
                    throw std::runtime_error("published mark does not bind native closed record");
                runtime.terminal_fence.clear();
                if (fence.value("terminal_verified",false) && hook->capture_plan.terminal_destroy
                    && header.phase==Phase::leave && header.closed_after && header.active_after==0) {
                    bool complete=true;
                    for (const auto id : hook->capture_plan.terminal_guard_fields) {
                        bool found=false;
                        for (unsigned i=0;i<header.field_count;++i) {
                            FieldState field;std::memcpy(&field,bytes+field_start+i*sizeof field,sizeof field);
                            if (field.id!=id || field.checkpoint) continue;
                            found=field.error==Error::ok;
                            if (!found) break;
                            unsigned copied=0;
                            for (unsigned j=0;j<header.segment_count;++j) {
                                Segment segment;std::memcpy(&segment,bytes+segment_start+j*sizeof segment,sizeof segment);
                                if (segment.field_id!=id || segment.role!=field.role || segment.checkpoint) continue;
                                if (!runtime.terminal_fence.add(segment.address,payload+segment.offset,segment.size)) {found=false;break;}
                                ++copied;
                            }
                            found=found && copied>0;break;
                        }
                        complete=complete && id && found;
                    }
                    runtime.terminal_fence.ticket=header.ticket;runtime.terminal_fence.activity=header.activity;
                    runtime.terminal_fence.verified=complete;
                }
                runtime.captured_publication=text;
                runtime.published_ticket=header.ticket;
                if (arkdps::capture::plan_uint(parsed.at("native_capture_scope_mode_epoch"),9007199254740991ULL)!=header.scope_mode_epoch)
                    throw std::runtime_error("published configuration epoch does not bind captured scope point");
                runtime.published_mode_epoch=header.scope_mode_epoch;
                runtime.published_sequence=arkdps::capture::plan_uint(fence.at("sequence"),9007199254740991ULL);
                auto pending=header.publication_request;
                if (pending && header.ticket>=runtime.lanes->minimum_publication_ticket.load(std::memory_order_acquire))
                    runtime.lanes->pending_publication.compare_exchange_strong(pending,0,std::memory_order_acq_rel);
            }
        }
    }
    if (header.publication_request) {
        // A stable but stale expected graph can fail only in JS, after native
        // guards succeeded. The next bounded candidate must capture actual
        // cold facts; never mutate old expected roots or retry every callback.
        if (runtime.published_ticket!=header.ticket)
            runtime.cold_publication_request.store(header.publication_request,std::memory_order_release);
        auto claimed=header.publication_request;
        runtime.lanes->publication_claim.compare_exchange_strong(claimed,0,std::memory_order_acq_rel);
    }
    JS_FreeValue(c,record); JS_FreeValue(c,projection);
}
bool drain_captures() noexcept {
    using namespace arkdps::capture;
    auto& runtime=state();
    if (!runtime.initialized || !runtime.fatal.empty()) return false;
    try {
        if (runtime.lanes->failure.load(std::memory_order_acquire))
            throw std::runtime_error("native capture failed: code="+std::to_string(runtime.lanes->failure.load()));
        // Bounded poll work keeps host jobs responsive under sustained producers.
        const auto poll_deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(2);
        for (unsigned budget=0;budget<512;++budget) {
            const auto ready=runtime.lanes->next_ready(runtime.processed_ticket+1);
            if (!ready) break; // A reserved but unpublished ticket is not skippable.
            Lane* found=nullptr;
            std::uint64_t ticket=0; std::size_t size=0;
            if (runtime.lanes->try_pop(*ready,ticket,runtime.consume_buffer.data(),runtime.consume_buffer.size(),size,found)!=CaptureQueue::PopResult::popped
                || !found || runtime.lanes->failure.load(std::memory_order_acquire))
                throw std::runtime_error("native capture failed: code="+std::to_string(runtime.lanes->failure.load(std::memory_order_acquire)));
            observe_pending_head(*found,std::nullopt,0);
            found->consumed.records.store(found->consumed.records.load(std::memory_order_relaxed)+1,std::memory_order_release);
            found->consumed.bytes.store(found->consumed.bytes.load(std::memory_order_relaxed)+size,std::memory_order_release);
            RecordHeader timing_header;
            std::memcpy(&timing_header,runtime.consume_buffer.data(),sizeof timing_header);
            if (timing_header.captured_qpc) {
                const auto now=ticks_now();
                found->consumed.last_age=now>=timing_header.captured_qpc ? now-timing_header.captured_qpc : 0;
                found->consumed.maximum_age=(std::max)(found->consumed.maximum_age,found->consumed.last_age);
                ++found->consumed.age_samples;
            }
            JS_UpdateStackTop(runtime.runtime);
            replay_record(runtime.consume_buffer.data(),size);
            if (runtime.lanes->failure.load(std::memory_order_acquire))
                throw std::runtime_error("native capture failed during replay");
            if (std::chrono::steady_clock::now()>=poll_deadline) break;
        }
        for (unsigned index=0;index<lane_count;++index)
            runtime.lanes->reclaim_terminated(index,runtime.replay_depth[index]==0);
        if (runtime.lanes->next_ready(runtime.processed_ticket+1)) return true;
        std::atomic_thread_fence(std::memory_order_seq_cst);
        if (!runtime.replay_active && runtime.active.load(std::memory_order_acquire)==0
            && runtime.processed_ticket==runtime.lanes->reserved.load(std::memory_order_acquire)) {
            const auto bounds=runtime.configurations.collect_quiescent(runtime.capture_mode_epoch.load(std::memory_order_acquire));
            if (!bounds.is_null()) {
                if (!JS_IsFunction(runtime.context,runtime.graph_retire)) throw std::runtime_error("capture graph retirement unavailable");
                const auto text=bounds.dump();
                auto argument=JS_ParseJSON(runtime.context,text.data(),text.size(),"capture-retirement.json");
                runtime.replay_active=true;
                auto result=JS_Call(runtime.context,runtime.graph_retire,JS_UNDEFINED,1,&argument);
                runtime.replay_active=false;
                JS_FreeValue(runtime.context,argument);
                const bool failed=JS_IsException(result);JS_FreeValue(runtime.context,result);
                if (failed || runtime.lanes->failure.load(std::memory_order_acquire))
                    throw std::runtime_error("capture graph retirement violated its owned boundary");
            }
        }
        return false;
    } catch (const std::exception& error) {
        runtime.captured_publication.clear(); runtime.published_ticket=0;
        runtime.lanes->poison(LaneFault::replay_structure);
        runtime.lanes->enabled.store(false,std::memory_order_release);
        fail(error.what());
    } catch (...) {
        runtime.captured_publication.clear(); runtime.published_ticket=0;
        runtime.lanes->poison(LaneFault::replay_structure);
        runtime.lanes->enabled.store(false,std::memory_order_release);
        fail("native capture replay failed");
    }
    return false;
}
}

// Callback paths never enter QuickJS or touch the host mailbox.
extern "C" void* arkdps_on_enter(Hook* hook, EntryFrame* frame) noexcept {
    using namespace arkdps::capture;
    auto& runtime=state();
    const auto pinned_mode=pin_mode_entry(runtime.active,runtime.activity_generation,runtime.capture_mode_epoch);
    const auto scope_mode=runtime.capture_mode_epoch.load(std::memory_order_acquire);
    frame->return_target=2;
    CallbackTimer callback_timer(false);
    auto& lanes=*runtime.lanes;
    if (!lanes.enabled.load(std::memory_order_acquire) || lanes.failure.load(std::memory_order_relaxed)) return hook->original;
    auto* lane=lanes.current();
    if (!lane) return hook->original;
    callback_timer.bind(*lane,hook->timing_id);
    if (lane->depth>=invocation_depth) { lanes.poison(LaneFault::depth_full); return hook->original; }
    auto& call=lane->calls[lane->depth];
    call.hook=hook; call.mode_epoch=pinned_mode;
    call.apply_modifier=hook->capture_plan.apply_modifier; call.buff_damage=hook->capture_plan.buff_damage;
    call.configuration=runtime.configurations.pin(pinned_mode);
    callback_timer.stack_read();
    if (!capture_read(nullptr,frame->stack,&call.return_address,sizeof call.return_address)) { lanes.poison(LaneFault::invalid_stack); return hook->original; }
    call.id=lanes.reserved.fetch_add(1,std::memory_order_acq_rel)+1;
    call.parent=lane->depth ? lane->calls[lane->depth-1].id : 0;
    auto& context=call.context;
    context={}; context.invocation_id=call.id; context.parent_invocation_id=call.parent;
    context.globals=hook->capture_plan.globals; context.globals_valid=hook->capture_plan.globals_valid;
    const std::array<std::uint64_t,4> gp{{frame->rcx,frame->rdx,frame->r8,frame->r9}};
    for (unsigned index=0;index<hook->capture_plan.plan->arg_count;++index) {
        if (!(hook->capture_plan.plan->arg_mask&(1u<<index))) continue;
        if (index<4) context.args[index]=gp[index];
        else {
            callback_timer.stack_read();
            if (!capture_read(nullptr,frame->stack+40+(index-4)*8,&context.args[index],8)) {
                lanes.poison(LaneFault::invalid_stack); return hook->original;
            }
        }
        context.args_valid|=1u<<index;
    }
    context.entry_refs=context.args; context.entry_valid=context.args_valid;
    for (unsigned i=lane->depth;i>0;--i) {
        const auto& parent=lane->calls[i-1];
        if (parent.buff_damage && (parent.context.args_valid&1u)) {
            context.parent_refs[0]=parent.context.args[0]; context.parent_valid|=1u; break;
        }
    }
    ++lane->depth; frame->return_target=1;
    RecordHeader header{};
    header.ticket=call.id; header.invocation=call.id; header.parent=call.parent; header.mode_epoch=call.mode_epoch;
    header.captured_qpc=timing_state.enabled.load(std::memory_order_relaxed) ? ticks_now() : 0;
    header.activity_begin=runtime.activity_generation.load(std::memory_order_acquire);
    header.active=runtime.active.load(std::memory_order_acquire);
    header.activity=header.activity_begin; header.args=context.args; header.return_address=call.return_address; header.scope_mode_epoch=scope_mode;
    header.hook_id=hook->timing_id; header.thread_id=GetCurrentThreadId(); header.thread_birth=lane->birth;
    for (unsigned i=0;i<lane_count;++i) if (lanes.lanes[i].get()==lane) header.lane=i;
    header.depth=lane->depth; header.phase=Phase::entry; header.arg_mask=context.args_valid;
    capture_scope_point(hook->capture_plan,Phase::entry,context,call.configuration,
                        capture_read,nullptr,lane->snapshot,header,lane,
                        header.publication_request && runtime.cold_publication_request.load(std::memory_order_acquire)==header.publication_request);
    save_entry_roots(hook->capture_plan,lane->snapshot,context);
    const bool queued=lanes.publish(*lane,header);
    callback_timer.capture(lane->snapshot,header.parent_checkpoint_count,queued);
    return hook->original;
}
extern "C" void* arkdps_on_leave(ReturnFrame* frame, std::uint64_t mode) noexcept {
    using namespace arkdps::capture;
    auto& runtime=state();
    CallbackTimer callback_timer(true);
    auto& lanes=*runtime.lanes;
    if (mode==2) {
        runtime.activity_generation.fetch_add(1,std::memory_order_acq_rel);
        runtime.active.fetch_sub(1,std::memory_order_acq_rel);
        return nullptr;
    }
    auto* lane=lanes.current();
    if (!lane || !lane->depth) {
        lanes.poison(LaneFault::invalid_stack);
        runtime.activity_generation.fetch_add(1,std::memory_order_acq_rel);
        runtime.active.fetch_sub(1,std::memory_order_acq_rel);
        return nullptr;
    }
    auto& call=lane->calls[lane->depth-1];
    auto* hook=static_cast<Hook*>(call.hook);
    callback_timer.bind(*lane,hook->timing_id);
    RecordHeader header{};
    header.ticket=lanes.reserved.fetch_add(1,std::memory_order_acq_rel)+1;
    header.invocation=call.id; header.parent=call.parent; header.mode_epoch=call.mode_epoch;
    header.hook_id=hook->timing_id; header.thread_id=GetCurrentThreadId(); header.thread_birth=lane->birth;
    for (unsigned i=0;i<lane_count;++i) if (lanes.lanes[i].get()==lane) header.lane=i;
    header.depth=lane->depth; header.phase=Phase::leave;
    header.args=call.context.args; header.arg_mask=call.context.args_valid;
    header.return_value=frame->rax; header.return_address=call.return_address;
    header.scope_mode_epoch=runtime.capture_mode_epoch.load(std::memory_order_acquire);
    const auto requested=lanes.pending_publication.load(std::memory_order_acquire);
    std::uint64_t unclaimed=0;
    if (requested && !call.hp_ignored && lanes.publication_attempts.load(std::memory_order_acquire)<2
        && runtime.active.load(std::memory_order_acquire)==1
        && lanes.publication_claim.compare_exchange_strong(unclaimed,requested,std::memory_order_acq_rel)) {
        lanes.publication_attempts.fetch_add(1,std::memory_order_relaxed);
        header.publication_request=requested;
    }
    header.captured_qpc=timing_state.enabled.load(std::memory_order_relaxed) ? ticks_now() : 0;
    header.activity_begin=runtime.activity_generation.load(std::memory_order_acquire);
    header.active=runtime.active.load(std::memory_order_acquire);
    call.context.return_value=frame->rax; call.context.return_valid=true;
    capture_scope_point(hook->capture_plan,Phase::leave,call.context,call.configuration,
                        capture_read,nullptr,lane->snapshot,header,lane,
                        header.publication_request && runtime.cold_publication_request.load(std::memory_order_acquire)==header.publication_request);
    header.activity=runtime.activity_generation.fetch_add(1,std::memory_order_acq_rel)+1;
    header.active_after=runtime.active.fetch_sub(1,std::memory_order_acq_rel)-1;
    header.closed_after=header.active_after==0 && header.activity==header.activity_begin+1;
    const bool queued=lanes.publish(*lane,header);
    callback_timer.capture(lane->snapshot,header.parent_checkpoint_count,queued);
    --lane->depth;
    return nullptr;
}

namespace arkdps {
void initialize_on_worker(const std::string& source) {
    auto& runtime = state();
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
    function(c,global,"__nativeCaptureCapabilities",js_capture_capabilities,0);
    function(c,global,"__publishCaptureScope",js_publish_scope,1);
    function(c,global,"__publishCapturePublication",js_publish_publication,1);
    function(c,global,"__nativeReplayHealthy",js_replay_healthy,0);
    function(c,global,"__send",js_send,1);function(c,global,"__nativeActive",js_active,0);function(c,global,"__pid",js_pid,0);
    try {
        arkdps::owned_js::install(c,[]() noexcept {
            state().lanes->poison(arkdps::capture::LaneFault::replay_structure);
        });
        evaluate(c, arkdps_bridge_source, "native-bridge.js");
        evaluate(c, arkdps_publication_source, "native-publication.js");
        evaluate(c, arkdps_cold_plan_source, "native-cold-plan.js");
        evaluate(c, "globalThis.arkdpsNativeAsyncCapture=true;", "native-async.js");
        runtime.replay_dispatch=JS_GetPropertyStr(c,global,"__arkdpsReplayDispatch");
        if (!JS_IsFunction(c,runtime.replay_dispatch)) throw std::runtime_error("native replay dispatch unavailable");
        runtime.pointer = JS_GetPropertyStr(c, global, "__arkdpsPointer");
        runtime.dispatch = JS_GetPropertyStr(c, global, "__arkdpsRequest");
        evaluate(c, source, "event-probe.js");
        runtime.prefix_projection=JS_GetPropertyStr(c,global,"__arkdpsOnClosedRecord");
        runtime.publication_publish=JS_GetPropertyStr(c,global,"__arkdpsPublishClosedRecord");
        runtime.publication_request=JS_GetPropertyStr(c,global,"__arkdpsPublicationRequest");
        runtime.mode_state=JS_GetPropertyStr(c,global,"__arkdpsCaptureModeState");
        runtime.mode_install=JS_GetPropertyStr(c,global,"__arkdpsInstallCaptureMode");
        runtime.graph_retire=JS_GetPropertyStr(c,global,"__arkdpsRetireCaptureGraphs");
        if (JS_IsFunction(c,runtime.mode_state)) {
            auto mode=JS_Call(c,runtime.mode_state,JS_UNDEFINED,0,nullptr);
            if (JS_IsException(mode)) throw std::runtime_error(exception(c));
            try { install_mode_state(json_value(c,mode)); } catch (...) { JS_FreeValue(c,mode); throw; }
            JS_FreeValue(c,mode);
        }
        if (!JS_IsFunction(c,runtime.publication_publish) || !JS_IsFunction(c,runtime.publication_request))
            throw std::runtime_error("native async publication journal unavailable");
        if (runtime.ready.empty()) throw std::runtime_error("probe did not publish readiness");
        for (const auto& hook : runtime.hooks) {
            if (hook->detached) continue;
            if (MH_QueueEnableHook(hook->address) != MH_OK) throw std::runtime_error("could not queue native hooks");
        }
        // All JS definitions and retained function handles now exist before the
        // first target thread can execute one of the enabled callbacks.
        runtime.initialized = true;
        runtime.lanes->enabled.store(true,std::memory_order_release);
        if (MH_ApplyQueued() != MH_OK) throw std::runtime_error("could not enable native hooks");
        JS_FreeValue(c, global);
    } catch (...) {
        JS_FreeValue(c, global);
        fail("native probe initialization failed");
        disable_hooks();
        throw;
    }
}
std::string readiness_on_worker() {
    auto& runtime = state();
    if (!runtime.initialized || !runtime.fatal.empty()) throw std::runtime_error(runtime.fatal.empty() ? "native runtime is not ready" : runtime.fatal);
    return runtime.ready;
}
std::string request_on_worker(const std::string& json, std::uint64_t minimum_ticket) {
    auto& runtime=state();
    if (json.size()>8*max_read) throw std::runtime_error("request too large");
    const auto command=nlohmann::json::parse(json,[](int depth,nlohmann::json::parse_event_t,nlohmann::json&) {
        if (depth>64) throw std::runtime_error("request nesting exceeds limit");
        return true;
    });
    const auto operation=command.value("operation",std::string());
    const bool stopping=operation=="stop";
    TimingSpan timing(timing_request,true);
    bool diagnostic=false, diagnostic_snapshot=false;
    if (operation=="mark" && command.contains("native_timing")) {
        const auto& control=command.at("native_timing");
        if (control.is_object() && control.value("version",nlohmann::json())==1
            && control.contains("enabled") && control.at("enabled").is_boolean()) {
            timing_state.enabled.store(control.at("enabled").get<bool>(),std::memory_order_relaxed);
            diagnostic=true; diagnostic_snapshot=control.value("snapshot",false);
        }
    }
    if (stopping) {
        runtime.stopping.store(true,std::memory_order_release);
        runtime.lanes->enabled.store(false,std::memory_order_release);
        for (auto& hook : runtime.hooks) {
            if (MH_QueueDisableHook(hook->address)!=MH_OK) throw std::runtime_error("could not disable native hooks");
            hook->detached=true;
        }
        if (MH_ApplyQueued()!=MH_OK) throw std::runtime_error("could not apply native stop");
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
        auto quiet_since=std::chrono::steady_clock::now();
        auto previous=runtime.activity_generation.load(std::memory_order_acquire);
        for (;;) {
            drain_captures();
            const auto generation=runtime.activity_generation.load(std::memory_order_acquire);
            const auto active=runtime.active.load(std::memory_order_acquire);
            const auto reserved=runtime.lanes->reserved.load(std::memory_order_acquire);
            const auto now=std::chrono::steady_clock::now();
            if (generation!=previous || active || runtime.processed_ticket!=reserved) quiet_since=now;
            previous=generation;
            if (!runtime.fatal.empty()) throw std::runtime_error("hooks disabled; capture drain failed: "+runtime.fatal);
            if (!active && runtime.processed_ticket==reserved && now-quiet_since>=std::chrono::milliseconds(25)) break;
            if (now>=deadline) throw std::runtime_error("hooks disabled; native calls/capture prefix did not drain");
            Sleep(1); // Worker/host only; game original functions never wait here.
        }
    } else {
        drain_captures();
        if (!runtime.initialized || !runtime.fatal.empty()) throw std::runtime_error(runtime.fatal.empty() ? "native runtime not ready" : runtime.fatal);
    }
    if (!stopping) configure_mode(command);
    auto async_state=[&] {
        const auto reserved=runtime.lanes->reserved.load(std::memory_order_acquire);
        nlohmann::json state{{"version",1},{"reserved_ticket",reserved},
            {"processed_ticket",runtime.processed_ticket},{"closed_ticket",runtime.closed_ticket},
            {"published_ticket",runtime.published_ticket},
            {"desired_mode_epoch",runtime.capture_mode_epoch.load(std::memory_order_acquire)},
            {"published_mode_epoch",runtime.published_mode_epoch},
            {"publication_ready",!runtime.captured_publication.empty() && runtime.published_mode_epoch==runtime.capture_mode_epoch.load(std::memory_order_acquire)},
            {"activity_generation",runtime.activity_generation.load(std::memory_order_acquire)},
            {"active",runtime.active.load(std::memory_order_acquire)},
            {"failure_code",runtime.lanes->failure.load(std::memory_order_acquire)},
            {"captured_prefix_lag_records",reserved-runtime.processed_ticket},
            {"publication_lag_records",runtime.processed_ticket-runtime.published_ticket},
            {"published_event_sequence",runtime.published_sequence},{"delivered_event_sequence",runtime.delivered_sequence},
            {"read_prefix_lag_events",runtime.published_sequence>=runtime.delivered_sequence ? runtime.published_sequence-runtime.delivered_sequence : 0}};
        const bool age=timing_state.enabled.load(std::memory_order_relaxed);
        LARGE_INTEGER frequency{}; QueryPerformanceFrequency(&frequency);
        state["queue_age_available"]=age;
        state["queue_age_qpc_frequency"]=frequency.QuadPart;
        state["lane_metrics"]=nlohmann::json::array();
        state["callback_timing"]={{"enabled",age},{"selection","xorshift_approximately_1_in_128"},
            {"includes_first_touch",false},{"includes_original_function",false},
            {"scope","after_sampling_decision_through_capture_enqueue"},
            {"includes_identity_lookup",true},{"includes_metrics_recording",false},
            {"sample_sums_are_not_extrapolated",true},{"qpc_frequency",frequency.QuadPart},
            {"samples",nlohmann::json::array()}};
        for (unsigned index=0;index<capture::lane_count;++index) {
            auto& lane=*runtime.lanes->lanes[index];
            const auto owner=lane.owner.load(std::memory_order_acquire);
            if (!owner || owner==MAXDWORD) continue;
            const auto snapshot=capture::lane_metric_snapshot(lane,age,ticks_now());
            auto optional=[](std::optional<std::uint64_t> value) { return value ? nlohmann::json(*value) : nlohmann::json(); };
            nlohmann::json row{{"lane",index},{"captured_records",snapshot.submitted},{"consumed_records",snapshot.consumed},
                {"captured_bytes",snapshot.submitted_bytes},{"consumed_bytes",snapshot.consumed_bytes},
                {"counter_snapshot_consistent",snapshot.consistent},
                {"resident_records_approx",optional(snapshot.resident_records)},
                {"resident_bytes_approx",optional(snapshot.resident_bytes)},
                {"thread_id",snapshot.thread_id},{"thread_birth",std::to_string(snapshot.thread_birth)},
                {"lane_generation",snapshot.generation},{"peak_resident_records",snapshot.peak_records},{"peak_resident_bytes",snapshot.peak_bytes},
                {"queue_age_available",snapshot.age_available},{"age_sample_count",snapshot.age_samples},
                {"last_capture_to_replay_qpc",optional(snapshot.last_age)},
                {"maximum_capture_to_replay_qpc",optional(snapshot.maximum_age)},
                {"observed_pending_age_lower_bound_qpc",optional(snapshot.observed_pending_age)}};
            for(unsigned hook=0;hook<runtime.hooks.size() && hook<capture::timing_hooks;++hook)
                for(unsigned edge=0;edge<2;++edge) {
                    const auto sample=runtime.callback_timings.cell(index,hook,edge!=0).snapshot(snapshot.generation);
                    if(!sample)continue;
                    state["callback_timing"]["samples"].push_back({{"lane",index},{"lane_generation",sample->generation},
                        {"hook_id",hook},{"hook",runtime.hooks[hook]->timing_name},{"edge",edge?"leave":"entry"},
                        {"sample_count",sample->samples},{"sample_sum_qpc",sample->ticks},{"sample_max_qpc",sample->maximum},
                        {"sample_native_reads",sample->reads},{"sample_native_read_bytes",sample->read_bytes},
                        {"sample_enqueued_bytes",sample->enqueued_bytes},{"sample_drops",sample->drops}});
                }
            state["lane_metrics"].push_back(std::move(row));
        }
        return state;
    };
    if (operation=="ack") {
        const auto sequence=arkdps::capture::plan_uint(command.at("sequence"),UINT64_MAX);
        if (sequence>runtime.delivered_sequence) throw std::runtime_error("native ACK exceeds delivered event prefix");
    }
    const bool publication_operation=operation=="mark" || operation=="read" || operation=="terminal_read" || operation=="ack";
    if (operation=="mark") {
        const auto terminal_ready=[&]() noexcept {
            if (runtime.published_mode_epoch!=runtime.capture_mode_epoch.load(std::memory_order_acquire)) return false;
            const auto active=runtime.active.load(std::memory_order_acquire);
            const auto reserved=runtime.lanes->reserved.load(std::memory_order_acquire);
            const auto activity=runtime.activity_generation.load(std::memory_order_acquire);
            if (!runtime.terminal_fence.accepts(active,reserved,activity,arkdps::capture::capture_read,nullptr)) return false;
            std::atomic_thread_fence(std::memory_order_seq_cst);
            return runtime.active.load(std::memory_order_acquire)==0
                && runtime.lanes->reserved.load(std::memory_order_acquire)==reserved
                && runtime.activity_generation.load(std::memory_order_acquire)==activity;
        };
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(100);
        while (!terminal_ready() && (runtime.published_ticket<minimum_ticket || runtime.published_mode_epoch!=runtime.capture_mode_epoch.load(std::memory_order_acquire)) && std::chrono::steady_clock::now()<deadline) {
            const bool ready_more=drain_captures();
            if (!runtime.fatal.empty()) throw std::runtime_error(runtime.fatal);
            if (!ready_more && (runtime.published_ticket<minimum_ticket || runtime.published_mode_epoch!=runtime.capture_mode_epoch.load(std::memory_order_acquire))) Sleep(1);
        }
        if (!terminal_ready() && (runtime.published_ticket<minimum_ticket || runtime.published_mode_epoch!=runtime.capture_mode_epoch.load(std::memory_order_acquire)))
            return nlohmann::json{{"retry",true},{"error",nullptr},{"stopped",false},
                {"events",nlohmann::json::array()},{"hp_context",nlohmann::json::array()},
                {"mark_retry_reason","native_fresh_publication_not_ready"},{"native_async_capture",async_state()}}.dump();
    }
    JS_UpdateStackTop(runtime.runtime);
    JSValue argument=publication_operation ? JS_ParseJSON(runtime.context,json.data(),json.size(),"native-command.json")
                                          : JS_NewStringLen(runtime.context,json.data(),json.size());
    if (JS_IsException(argument)) throw std::runtime_error(exception(runtime.context));
    // Publication requests are pure journal operations. C++ primitives must
    // reject live access even when a script catches the resulting exception.
    runtime.replay_active=publication_operation;
    auto value=JS_Call(runtime.context,publication_operation ? runtime.publication_request : runtime.dispatch,JS_UNDEFINED,1,&argument);
    runtime.replay_active=false;
    JS_FreeValue(runtime.context,argument);
    if (JS_IsException(value)) throw std::runtime_error(exception(runtime.context));
    if (runtime.lanes->failure.load(std::memory_order_acquire)) {
        JS_FreeValue(runtime.context,value); drain_captures();
        throw std::runtime_error("native journal violated owned memory boundary");
    }
    JSValue serialized=publication_operation ? JS_JSONStringify(runtime.context,value,JS_UNDEFINED,JS_UNDEFINED) : JS_DupValue(runtime.context,value);
    JS_FreeValue(runtime.context,value);
    if (JS_IsException(serialized)) throw std::runtime_error(exception(runtime.context));
    std::string encoded;
    try { encoded=string(runtime.context,serialized); } catch (...) { JS_FreeValue(runtime.context,serialized); throw; }
    JS_FreeValue(runtime.context,serialized);
    auto response=nlohmann::json::parse(encoded);
    if (publication_operation && response.is_object() && (response.value("native_publication_failure",false)
        || !response.value("error",nlohmann::json()).is_null())) {
        runtime.lanes->poison(capture::LaneFault::publication_failure);
        drain_captures();
        throw std::runtime_error("native capture failed: code="+std::to_string(runtime.lanes->failure.load()));
    }

    if (!response.is_object()) throw std::runtime_error("native worker reply must be an object");
    response.erase("native_quiet_boundary_version"); response.erase("native_deferred_callbacks");
    response["native_async_capture"]=async_state();
    if ((operation=="read" || operation=="terminal_read") && response.value("error",nlohmann::json()).is_null()
               && !response.value("retry",false) && response.contains("events") && response.at("events").is_array()) {
        const auto after=arkdps::capture::plan_uint(command.at("after"),UINT64_MAX);
        const auto through=arkdps::capture::plan_uint(command.at("through"),UINT64_MAX);
        bool contiguous=through>=after && through-after==response.at("events").size();
        std::uint64_t sequence=after;
        for (const auto& event : response.at("events")) {
            if (!event.is_object() || !event.contains("sequence") || arkdps::capture::plan_uint(event.at("sequence"),UINT64_MAX)!=++sequence) contiguous=false;
        }
        if (contiguous) runtime.delivered_sequence=(std::max)(runtime.delivered_sequence,through);
    }
    if (stopping) { response["native_quiet"]=true; response["active"]=0; }
    if (diagnostic) {
        response["native_timing_control"]={{"version",1},{"enabled",timing_state.enabled.load()},
            {"callback_sampling",0},{"capture_timing_available",false}};
        if (diagnostic_snapshot) response["native_timing"]=timing_snapshot();
    }
    return response.dump();
}
void initialize(const std::string& source) {
    auto& runtime=state();
    runtime.worker.start([source] {
        capture::CaptureLanes::Config lane_config;
        lane_config.lane_queue={2048,2*1024*1024};
        state().lanes=std::make_unique<capture::CaptureLanes>(lane_config);
        state().ingress.store(state().lanes.get(),std::memory_order_release);
        initialize_on_worker(source);
    },drain_captures);
    runtime.worker.wait_initialized();
}
std::string readiness() {
    return state().worker.submit([] { return readiness_on_worker(); });
}
std::string request(const std::string& json) {
    if (json.size()>8*max_read) throw std::runtime_error("request too large");
    const auto command=nlohmann::json::parse(json,[](int depth,nlohmann::json::parse_event_t,nlohmann::json&) {
        if (depth>64) throw std::runtime_error("request nesting exceeds limit");
        return true;
    });
    std::uint64_t minimum=0;
    if (command.value("operation",std::string())=="mark") {
        auto* lanes=state().ingress.load(std::memory_order_acquire);
        if (!lanes) throw std::runtime_error("native capture not initialized");
        const auto reserved=lanes->reserved.load(std::memory_order_acquire);
        if (reserved==UINT64_MAX) throw std::runtime_error("native capture ticket exhausted");
        minimum=reserved+1;
        lanes->minimum_publication_ticket.store(minimum,std::memory_order_release);
        const auto revision=lanes->request_serial.fetch_add(1,std::memory_order_acq_rel)+1;
        lanes->publication_attempts.store(0,std::memory_order_release);
        lanes->pending_publication.store(revision,std::memory_order_release);
    }
    return state().worker.submit([json,minimum] { return request_on_worker(json,minimum); });
}
std::string captured_prefix_publication() {
    return state().worker.submit([] { if (!state().fatal.empty()) throw std::runtime_error(state().fatal); return state().captured_publication; });
}
std::uint64_t timing_begin() noexcept {
    return timing_state.enabled.load(std::memory_order_relaxed) ? ticks_now() : 0;
}
void timing_transport(unsigned phase, std::uint64_t start, std::size_t bytes) noexcept {
    if (!start || phase > 3) return;
    auto* thread = timing_slot();
    if (!thread) return;
    auto& c = thread->cells[timing_transport_base+phase];
    const auto elapsed = ticks_now()-start;
    c.revision.fetch_add(1, std::memory_order_acq_rel);
    timing_add(c.calls, 1); timing_add(c.samples, 1); timing_add(c.ticks, elapsed);
    timing_add(c.exclusive, elapsed); timing_add(c.bytes, bytes);
    c.maximum.store((std::max)(c.maximum.load(std::memory_order_relaxed),elapsed),std::memory_order_relaxed);
    c.revision.fetch_add(1, std::memory_order_acq_rel);
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
