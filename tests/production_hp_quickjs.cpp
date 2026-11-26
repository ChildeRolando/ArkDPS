extern "C" {
#include "quickjs.h"
}
#include "engine_capture_pipeline.h"
#include "owned_replay_js.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
using namespace arkdps::capture;
using J = nlohmann::json;
struct Region {
  std::uintptr_t address;
  std::vector<std::byte> data;
};
static std::vector<Region> regions;
static std::unique_ptr<CompiledPlan> plan;
static Snapshot snapshot;
static Context input;
static RecordHeader last_header;
static std::unique_ptr<CaptureConfigurations> configurations;
static std::unique_ptr<Lane> capture_lane;
static Region *region(std::uintptr_t a, std::size_t n) {
  auto it = std::upper_bound(
      regions.begin(), regions.end(), a,
      [](auto address, const Region &r) { return address < r.address; });
  if (it == regions.begin())
    return nullptr;
  --it;
  return a - it->address <= it->data.size() &&
                 n <= it->data.size() - (a - it->address)
             ? &*it
             : nullptr;
}
static bool read_memory(void *, std::uintptr_t a, void *out,
                        std::size_t n) noexcept {
  auto *r = region(a, n);
  if (!r)
    return false;
  std::memcpy(out, r->data.data() + a - r->address, n);
  return true;
}
static JSValue configure(JSContext *c, JSValueConst, int argc,
                         JSValueConst *argv) {
  try {
    if (argc != 1)
      throw std::runtime_error("config count");
    const char *text = JS_ToCString(c, argv[0]);
    auto j = J::parse(text);
    JS_FreeCString(c, text);
    plan = std::make_unique<CompiledPlan>(compile_plan(j.at("plan"), 0x9000));
    configurations = std::make_unique<CaptureConfigurations>();
    configurations->intern(*plan);
    if (j.contains("scope")) {
      configurations->publish(j.at("scope"), false);
      configurations->publish(j.at("publication"), true);
      capture_lane = std::make_unique<Lane>(CaptureQueue::Config{2, 4096}, 1);
      capture_lane->depth = 1;
    }
    for (const auto &r : j.at("ranges")) {
      Region range;
      range.address = plan_address(r.at("address"));
      for (auto b : r.at("bytes"))
        range.data.push_back(static_cast<std::byte>(b.get<unsigned>()));
      regions.push_back(std::move(range));
    }
    input.args[0] = plan_address(j.at("enemy"));
    input.entry_refs[0] = input.args[0];
    input.args_valid = input.entry_valid = 1;
    input.globals = plan->globals;
    input.globals_valid = plan->globals_valid;
    return JS_UNDEFINED;
  } catch (const std::exception &e) {
    return JS_ThrowInternalError(c, "%s", e.what());
  }
}
static JSValue put(JSContext *c, JSValueConst, int, JSValueConst *argv) {
  std::int64_t at = 0, bits = 0;
  JS_ToInt64(c, &at, argv[0]);
  JS_ToBigInt64(c, &bits, argv[1]);
  auto *r = region(at, 8);
  if (!r)
    return JS_ThrowInternalError(c, "put outside owned memory");
  std::memcpy(r->data.data() + at - r->address, &bits, 8);
  return JS_UNDEFINED;
}
static std::uint64_t serial = 0;
static bool poisoned = false;
struct Profile {
  std::uint64_t scalar = 0, bytes = 0, point = 0, edges = 0, read_count = 0,
                read_bytes = 0;
  double capture_ms = 0, view_ms = 0;
};
struct FunctionTiming {
  std::uint64_t calls = 0, samples = 0;
  double sampled_us = 0, max_us = 0;
};
static FunctionTiming hook_timing, graph_timing;
static bool sample_functions = false;
static bool native_wrappers = true;
static std::uint64_t prepared_attempts = 0, prepared_packets = 0;
static bool sealed_owner = true;
struct SampleTimer {
  FunctionTiming &t;
  bool sampled;
  std::chrono::steady_clock::time_point start;
  SampleTimer(FunctionTiming &value)
      : t(value), sampled(sample_functions && ((t.calls++ & 127) == 0)) {
    if (sampled)
      start = std::chrono::steady_clock::now();
  }
  ~SampleTimer() {
    if (sampled) {
      const auto us = std::chrono::duration<double, std::micro>(
                          std::chrono::steady_clock::now() - start)
                          .count();
      ++t.samples;
      t.sampled_us += us;
      t.max_us = std::max(t.max_us, us);
    }
  }
};
static Profile profile;
static std::uint64_t hp_observation_calls = 0, hp_failure_calls = 0;
#ifdef ARKDPS_CAPTURE_HP_OBSERVATION
static JSValue owned_hp_observation(JSContext *c, JSValueConst self, int n,
                                    JSValueConst *args) {
  ++hp_observation_calls;
  return arkdps::owned_js::hp_observation(c, self, n, args);
}
static JSValue owned_hp_failure(JSContext *c, JSValueConst self, int n,
                                JSValueConst *args) {
  ++hp_failure_calls;
  return arkdps::owned_js::consume_hp_failure(c, self, n, args);
}
#endif
static std::uint64_t hook_point_calls = 0, missing_hook_calls = 0;
#ifdef ARKDPS_CAPTURE_HOOK_POINT
static JSValue owned_hook_point(JSContext *c, JSValueConst self, int n,
                                JSValueConst *args) {
  SampleTimer timer(hook_timing);
  ++hook_point_calls;
  return arkdps::owned_js::hook_point(c, self, n, args);
}
static JSValue owned_missing_hook(JSContext *c, JSValueConst self, int n,
                                  JSValueConst *args) {
  ++missing_hook_calls;
  return arkdps::owned_js::missing_hook_slot(c, self, n, args);
}
#endif
static std::uint64_t batch_calls = 0, graph_calls = 0, graph_check_calls = 0,
                     batch_output_bytes = 0, graph_output_bytes = 0,
                     selected_slots = 0;
static void batch_size(JSContext *c, JSValueConst value, std::uint64_t &bytes) {
  if (!JS_IsObject(value))
    return;
  std::size_t size = 0;
  auto *data = JS_GetArrayBuffer(c, &size, value);
  if (!data)
    return;
  bytes += size;
  if (size >= 16) {
    std::uint32_t n = 0;
    std::memcpy(&n, data + 4, 4);
    if (n <= 32 && size >= 16 + 16 * n)
      for (unsigned i = 0; i < n; i++) {
        std::uint16_t status = 0;
        std::memcpy(&status, data + 16 + i * 16, 2);
        if (status != 0xffff)
          ++selected_slots;
      }
  }
}
#ifdef ARKDPS_CAPTURE_GRAPH_BATCH
static JSValue owned_graph(JSContext *c, JSValueConst self, int n,
                           JSValueConst *args) {
  ++graph_calls;
  auto result = arkdps::owned_js::graph_read_batch(c, self, n, args);
  batch_size(c, result, graph_output_bytes);
  return result;
}
#ifdef ARKDPS_CAPTURE_GRAPH_CHECK
static JSValue owned_graph_check(JSContext *c, JSValueConst self, int n,
                                 JSValueConst *args) {
  SampleTimer timer(graph_timing);
  ++graph_check_calls;
  return arkdps::owned_js::graph_check(c, self, n, args);
}
#endif
#endif
#ifdef ARKDPS_CAPTURE_READ_BATCH
static JSValue owned_batch(JSContext *c, JSValueConst self, int n,
                           JSValueConst *args) {
  ++batch_calls;
  auto result = arkdps::owned_js::read_batch(c, self, n, args);
  batch_size(c, result, batch_output_bytes);
  return result;
}
#endif
#ifdef ARKDPS_DIRECT_HP
static JSValue direct_reason(JSContext *c, JSValueConst, int n,
                             JSValueConst *args) {
  const auto reason =
      direct_hp_reason(*plan, last_header, n == 1 && JS_ToBool(c, args[0]));
  return JS_NewUint32(c, static_cast<unsigned>(reason));
}
static JSValue prepare_hp(JSContext *c, JSValueConst, int, JSValueConst *) {
  ++prepared_attempts;
  auto result = arkdps::owned_js::prepare_hp_observation(c);
  if (JS_IsObject(result))
    ++prepared_packets;
  return result;
}
#endif
static JSValue seal_plans(JSContext *, JSValueConst, int, JSValueConst *) {
#ifdef ARKDPS_CAPTURE_READ_BATCH
  arkdps::owned_js::seal_read_plans();
#endif
  return JS_UNDEFINED;
}

static JSValue edge(JSContext *c, JSValueConst, int argc, JSValueConst *argv) {
  std::int32_t phase = 0;
  JS_ToInt32(c, &phase, argv[0]);
  const auto start = sample_functions ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
  RecordHeader raw_header;
  raw_header.phase = phase ? Phase::leave : Phase::entry;
  raw_header.args = input.args;
  raw_header.arg_mask = input.args_valid;
  raw_header.depth = 1;
  raw_header.invocation = (serial + 2) / 2;
  if (argc > 1 && JS_ToBool(c, argv[1]))
    raw_header.publication_request = serial + 1;
  Error error = Error::ok;
  if (capture_lane) {
    capture_lane->calls[0].id = raw_header.invocation;
    capture_scope_point(*plan, raw_header.phase, input, configurations->pin(0),
                        read_memory, nullptr, snapshot, raw_header,
                        capture_lane.get());
    error = snapshot.error;
  } else
    error = capture(*plan->plan, raw_header.phase, input, read_memory, nullptr,
                    snapshot);
  if (error != Error::ok)
    return JS_ThrowInternalError(c, "capture error %u",
                                 static_cast<unsigned>(error));
  const auto captured = sample_functions ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
  if(sample_functions) profile.capture_ms +=
      std::chrono::duration<double, std::milli>(captured - start).count();
  profile.read_count += snapshot.read_count;
  profile.read_bytes += snapshot.read_bytes;
  ++profile.edges;
  const auto result = arkdps::owned_js::begin(
      snapshot.segments.data(), snapshot.segment_count, snapshot.fields.data(),
      snapshot.field_count, snapshot.bytes.data(), snapshot.byte_count,
      configurations ? configurations->roles : plan->roles, ++serial,
      Error::ok);
#ifdef ARKDPS_CAPTURE_GRAPH_BATCH
  const auto *exact = configurations ? configurations->pin(0) : nullptr;
  if (exact && exact->id != raw_header.configuration_id)
    exact = nullptr;
  arkdps::owned_js::bind_graphs(exact, raw_header.scope_revision,
                                raw_header.publication_revision, 0);
#endif
#ifdef ARKDPS_CAPTURE_HP_OBSERVATION
  arkdps::owned_js::bind_hp_observation(plan.get(), raw_header.phase,
                                        input.args[0], raw_header.checkpoint,
                                        raw_header.mode_epoch);
#endif
#ifdef ARKDPS_CAPTURE_HOOK_POINT
  arkdps::owned_js::bind_hook(plan.get(), raw_header.phase, input.args[0],
                              raw_header.checkpoint);
#endif
  if(sample_functions) profile.view_ms += std::chrono::duration<double, std::milli>(
                         std::chrono::steady_clock::now() - captured)
                         .count();
  if (result != Error::ok)
    return JS_ThrowInternalError(c, "view error %u",
                                 static_cast<unsigned>(result));
  last_header = raw_header;
  std::array<std::uint64_t, 22> header{};
  header[0] = serial;
  header[1] = (serial + 1) / 2;
  header[3] = serial;
  header[4] = serial + 1;
  header[5] = 1;
  header[6] = raw_header.publication_request;
  header[9] = raw_header.scope_revision;
  header[10] = raw_header.publication_revision;
  header[11] = raw_header.cold_revision;
  header[20] = raw_header.scope_unavailable ? 1 : 0;
  header[14] = phase ? 0 : 1;
  header[15] = phase ? 0 : 1;
  header[17] = 1;
  header[18] = phase ? 1 : 0;
  header[19] = phase ? 1 : 0;
  return JS_NewArrayBufferCopy(
      c, reinterpret_cast<const std::uint8_t *>(header.data()), sizeof header);
}
static JSValue report(JSContext *c, JSValueConst, int n, JSValueConst *args) {
  if (n && JS_ToBool(c, args[0])) {
    profile = {};
    prepared_attempts = prepared_packets = 0;
    hp_observation_calls = hp_failure_calls = 0;
    hook_timing = {};
    graph_timing = {};
    hook_point_calls = missing_hook_calls = 0;
    batch_calls = graph_calls = graph_check_calls = batch_output_bytes =
        graph_output_bytes = selected_slots = 0;
    return JS_UNDEFINED;
  }
  J j = {{"scalar_calls", profile.scalar},
         {"byte_read_calls", profile.bytes},
         {"point_calls", profile.point},
         {"edges", profile.edges},
         {"capture_read_count", profile.read_count},
         {"capture_read_bytes", profile.read_bytes},
         {"capture_ms", profile.capture_ms},
         {"view_initialize_ms", profile.view_ms}};
  j["cpp_io_counters_available"] = native_wrappers;
#ifdef ARKDPS_SEALED_PROVIDER
  j["sealed_owner"] = sealed_owner;
#else
  j["sealed_owner"] = nullptr;
#endif
  j["batch_calls"] = batch_calls;
  for (const auto &pair : {std::pair<const char *, FunctionTiming *>{
                               "hook_point_timing", &hook_timing},
                           {"graph_check_timing", &graph_timing}}) {
    const auto &t = *pair.second;
    j[pair.first] = {{"calls", t.calls},
                     {"samples", t.samples},
                     {"sampled_us", t.sampled_us},
                     {"max_us", t.max_us}};
  }
  j["cpp_timing_enabled"] = sample_functions;
  j["prepared_issue_attempts"] = prepared_attempts;
  j["prepared_packets"] = prepared_packets;
  j["hp_observation_calls"] = hp_observation_calls;
  j["hp_failure_calls"] = hp_failure_calls;
  j["hook_point_calls"] = hook_point_calls;
  j["missing_hook_calls"] = missing_hook_calls;
  j["graph_calls"] = graph_calls;
  j["graph_check_calls"] = graph_check_calls;
  j["batch_output_bytes"] = batch_output_bytes;
  j["graph_output_bytes"] = graph_output_bytes;
  j["selected_field_slots"] = selected_slots;
  j["owned_view_internal_read_calls"] = "not_measured";
#ifdef ARKDPS_CAPTURE_READ_BATCH
  j["registered_plans"] = arkdps::owned_js::binding().read_plans.size();
  j["plans_sealed"] = !arkdps::owned_js::binding().read_plans_open;
#else
  j["registered_plans"] = 0;
  j["plans_sealed"] = false;
#endif
  if (!native_wrappers)
    for (const auto *key :
         {"scalar_calls", "byte_read_calls", "point_calls", "batch_calls",
          "batch_output_bytes", "graph_calls", "graph_check_calls",
          "graph_output_bytes", "selected_field_slots", "hook_point_calls",
          "missing_hook_calls", "hp_observation_calls", "hp_failure_calls"})
      j[key] = nullptr;
  const auto text = j.dump();
  return JS_NewStringLen(c, text.data(), text.size());
}
static JSValue owned_point(JSContext *c, JSValueConst self, int n,
                           JSValueConst *args) {
  ++profile.point;
  return arkdps::owned_js::point(c, self, n, args);
}
static unsigned misses = 0;
static JSValue owned_bytes(JSContext *c, JSValueConst self, int n,
                           JSValueConst *args) {
  ++profile.bytes;
  auto result = arkdps::owned_js::bytes(c, self, n, args);
  if (arkdps::owned_js::binding().error != Error::ok && misses++ < 6) {
    const char *role = JS_ToCString(c, args[0]);
    const char *address = JS_ToCString(c, args[3]);
    std::cerr << "owned read miss "
              << static_cast<unsigned>(arkdps::owned_js::binding().error)
              << " role=" << role << " at=" << address << "\n";
    JS_FreeCString(c, role);
    JS_FreeCString(c, address);
  }
  return result;
}
static JSValue owned_scalar(JSContext *c, JSValueConst self, int n,
                            JSValueConst *args) {
  ++profile.scalar;
  auto result = arkdps::owned_js::scalar(c, self, n, args);
  if (arkdps::owned_js::binding().error != Error::ok && misses++ < 6) {
    const char *role = JS_ToCString(c, args[0]);
    const char *address = JS_ToCString(c, args[3]);
    std::cerr << "owned scalar miss "
              << static_cast<unsigned>(arkdps::owned_js::binding().error)
              << " role=" << role << " at=" << address << "\n";
    JS_FreeCString(c, role);
    JS_FreeCString(c, address);
  }
  return result;
}
static JSValue healthy(JSContext *c, JSValueConst, int, JSValueConst *) {
  return JS_NewBool(c, !poisoned);
}
static JSValue print(JSContext *c, JSValueConst, int argc, JSValueConst *argv) {
  for (int i = 0; i < argc; i++) {
    const char *s = JS_ToCString(c, argv[i]);
    if (s) {
      std::cout << s;
      JS_FreeCString(c, s);
    }
  }
  std::cout << '\n';
  return JS_UNDEFINED;
}
static JSValue timer(JSContext *c, JSValueConst, int, JSValueConst *) {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return JS_NewFloat64(c,
                       std::chrono::duration<double, std::milli>(now).count());
}
#include "production_hp_queue.h"

int main(int argc, char **argv) {
  if (argc < 2 || argc > 5)
    return 2;
  std::ifstream stream(argv[1]);
  std::string source((std::istreambuf_iterator<char>(stream)), {});
  arkdps::owned_js::binding().poison = []() noexcept { poisoned = true; };
  auto *rt = JS_NewRuntime();
  auto *c = JS_NewContext(rt);
#ifdef ARKDPS_SEALED_PROVIDER
  native_wrappers = false;

  for (int i = 2; i < argc; i++)
    if (std::string(argv[i]) == "--unsealed-provider")
      sealed_owner = false;
  arkdps::owned_js::install(
      c, []() noexcept { poisoned = true; }, sealed_owner);
#else
  arkdps::owned_js::install(c, []() noexcept { poisoned = true; });
#endif
  JS_SetMemoryLimit(rt, 512 * 1024 * 1024);
  auto g = JS_GetGlobalObject(c);
#ifdef ARKDPS_DIRECT_HP
  JS_SetPropertyStr(c, g, "__ownedPrepareHp",
                    JS_NewCFunction(c, prepare_hp, "__ownedPrepareHp", 0));
  JS_SetPropertyStr(
      c, g, "__ownedDirectReason",
      JS_NewCFunction(c, direct_reason, "__ownedDirectReason", 1));
#endif
  JS_SetPropertyStr(c, g, "print", JS_NewCFunction(c, print, "print", 1));
  JS_SetPropertyStr(c, g, "__monotonicMs",
                    JS_NewCFunction(c, timer, "__monotonicMs", 0));
  JS_SetPropertyStr(c, g, "__ownedQueue",
                    JS_NewCFunction(c, queue_run, "__ownedQueue", 2));
  JS_SetPropertyStr(c, g, "__ownedConfigure",
                    JS_NewCFunction(c, configure, "__ownedConfigure", 1));
  JS_SetPropertyStr(c, g, "__ownedPut",
                    JS_NewCFunction(c, put, "__ownedPut", 2));
  JS_SetPropertyStr(c, g, "__ownedEdge",
                    JS_NewCFunction(c, edge, "__ownedEdge", 2));
  if (native_wrappers) {
    JS_SetPropertyStr(c, g, "__nativeOwnedRead",
                      JS_NewCFunction(c, owned_bytes, "__nativeOwnedRead", 5));
    JS_SetPropertyStr(
        c, g, "__nativeOwnedScalar",
        JS_NewCFunction(c, owned_scalar, "__nativeOwnedScalar", 5));
    JS_SetPropertyStr(c, g, "__nativeOwnedPoint",
                      JS_NewCFunction(c, owned_point, "__nativeOwnedPoint", 2));
    JS_SetPropertyStr(
        c, g, "__nativeOwnedStatus",
        JS_NewCFunction(c, arkdps::owned_js::status, "__nativeOwnedStatus", 0));
    JS_SetPropertyStr(
        c, g, "__nativeOwnedLease",
        JS_NewCFunction(c, arkdps::owned_js::lease, "__nativeOwnedLease", 1));
    JS_SetPropertyStr(c, g, "__nativeOwnedNumericAddress", JS_TRUE);
  }
  JS_SetPropertyStr(c, g, "__nativeReplayHealthy",
                    JS_NewCFunction(c, healthy, "__nativeReplayHealthy", 0));
  JS_SetPropertyStr(c, g, "__ownedProfile",
                    JS_NewCFunction(c, report, "__ownedProfile", 1));
  JS_SetPropertyStr(c, g, "__sealReadonlyPlans",
                    JS_NewCFunction(c, seal_plans, "__sealReadonlyPlans", 0));
  if (native_wrappers) {
#ifdef ARKDPS_CAPTURE_GRAPH_BATCH
    JS_SetPropertyStr(
        c, g, "__nativeOwnedGraphReadBatch",
        JS_NewCFunction(c, owned_graph, "__nativeOwnedGraphReadBatch", 4));
#ifdef ARKDPS_CAPTURE_GRAPH_CHECK
    JS_SetPropertyStr(
        c, g, "__nativeOwnedGraphCheck",
        JS_NewCFunction(c, owned_graph_check, "__nativeOwnedGraphCheck", 3));
#endif
#endif
#ifdef ARKDPS_CAPTURE_READ_BATCH
    JS_SetPropertyStr(
        c, g, "__nativeOwnedReadBatch",
        JS_NewCFunction(c, owned_batch, "__nativeOwnedReadBatch", 5));
#endif
#ifdef ARKDPS_CAPTURE_HOOK_POINT
    JS_SetPropertyStr(
        c, g, "__nativeOwnedHookPoint",
        JS_NewCFunction(c, owned_hook_point, "__nativeOwnedHookPoint", 2));
    JS_SetPropertyStr(c, g, "__nativeOwnedMissingHookSlot",
                      JS_NewCFunction(c, owned_missing_hook,
                                      "__nativeOwnedMissingHookSlot", 1));
#endif
#ifdef ARKDPS_CAPTURE_HP_OBSERVATION
    JS_SetPropertyStr(c, g, "__nativeOwnedHpObservation",
                      JS_NewCFunction(c, owned_hp_observation,
                                      "__nativeOwnedHpObservation", 2));
    JS_SetPropertyStr(c, g, "__nativeOwnedConsumeHpFailure",
                      JS_NewCFunction(c, owned_hp_failure,
                                      "__nativeOwnedConsumeHpFailure", 1));
#endif
  }
  for (int i = 2; i < argc; i++) {
    if (std::string(argv[i]) == "--sample-functions")
      sample_functions = true;
    if (std::string(argv[i]) == "--disable-ascii")
      JS_SetPropertyStr(c, g, "__nativeOwnedUtf16Ascii", JS_UNDEFINED);
    if (std::string(argv[i]) == "--disable-graph")
      JS_SetPropertyStr(c, g, "__nativeOwnedGraphReadBatch", JS_UNDEFINED);
    if (std::string(argv[i]) == "--disable-hp-observation") {
      JS_SetPropertyStr(c, g, "__nativeOwnedHpObservation", JS_UNDEFINED);
      JS_SetPropertyStr(c, g, "__nativeOwnedConsumeHpFailure", JS_UNDEFINED);
    }
    if (std::string(argv[i]) == "--disable-batch") {
      JS_SetPropertyStr(c, g, "__nativeOwnedReadBatch", JS_UNDEFINED);
      JS_SetPropertyStr(c, g, "__nativeOwnedReadPlanRegister", JS_UNDEFINED);
    }
  }
  bool direct_enabled = false;
  for (int i = 2; i < argc; i++)
    if (std::string(argv[i]) == "--direct-hp")
      direct_enabled = true;
  bool disable_scope_primitives = false;
  for(int i=2;i<argc;i++) if(std::string(argv[i])=="--disable-scope-primitives") disable_scope_primitives=true;
  JS_SetPropertyStr(c,g,"__ownedDisableScopePrimitives",JS_NewBool(c,disable_scope_primitives));
  JS_SetPropertyStr(c, g, "__ownedDirectEnabled",
                    JS_NewBool(c, direct_enabled));
  JS_FreeValue(c, g);
  auto result = JS_Eval(c, source.data(), source.size(), "owned-hp-bench.js",
                        JS_EVAL_TYPE_GLOBAL);
  int status = 0;
  if (JS_IsException(result)) {
    auto e = JS_GetException(c);
    const char *s = JS_ToCString(c, e);
    std::cerr << (s ? s : "exception") << '\n';
    JS_FreeCString(c, s);
    auto stack = JS_GetPropertyStr(c, e, "stack");
    s = JS_ToCString(c, stack);
    std::cerr << (s ? s : "") << '\n';
    JS_FreeCString(c, s);
    JS_FreeValue(c, stack);
    JS_FreeValue(c, e);
    status = 1;
  }
  JS_FreeValue(c, result);
  arkdps::owned_js::end();
  JS_FreeContext(c);
  JS_FreeRuntime(rt);
  return status;
}
