extern "C" {
#include "quickjs.h"
}
#include "engine_capture_plan.h"
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
static Region *region(std::uintptr_t a, std::size_t n) {
  for (auto &r : regions)
    if (a >= r.address && a - r.address <= r.data.size() &&
        n <= r.data.size() - (a - r.address))
      return &r;
  return nullptr;
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
static Profile profile;

static JSValue edge(JSContext *c, JSValueConst, int, JSValueConst *argv) {
  std::int32_t phase = 0;
  JS_ToInt32(c, &phase, argv[0]);
  const auto start = std::chrono::steady_clock::now();
  const auto error = capture(*plan->plan, phase ? Phase::leave : Phase::entry,
                             input, read_memory, nullptr, snapshot);
  if (error != Error::ok)
    return JS_ThrowInternalError(c, "capture error %u",
                                 static_cast<unsigned>(error));
  const auto captured = std::chrono::steady_clock::now();
  profile.capture_ms +=
      std::chrono::duration<double, std::milli>(captured - start).count();
  profile.read_count += snapshot.read_count;
  profile.read_bytes += snapshot.read_bytes;
  ++profile.edges;
  const auto result = arkdps::owned_js::begin(
      snapshot.segments.data(), snapshot.segment_count, snapshot.fields.data(),
      snapshot.field_count, snapshot.bytes.data(), snapshot.byte_count,
      plan->roles, ++serial, Error::ok);
  profile.view_ms += std::chrono::duration<double, std::milli>(
                         std::chrono::steady_clock::now() - captured)
                         .count();
  if (result != Error::ok)
    return JS_ThrowInternalError(c, "view error %u",
                                 static_cast<unsigned>(result));
  std::array<std::uint64_t, 22> header{};
  header[0] = serial;
  header[1] = (serial + 1) / 2;
  header[3] = serial;
  header[4] = serial + 1;
  header[5] = 1;
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
int main(int argc, char **argv) {
  if (argc != 2)
    return 2;
  std::ifstream stream(argv[1]);
  std::string source((std::istreambuf_iterator<char>(stream)), {});
  arkdps::owned_js::binding().poison = []() noexcept { poisoned = true; };
  auto *rt = JS_NewRuntime();
  auto *c = JS_NewContext(rt);
  JS_SetMemoryLimit(rt, 512 * 1024 * 1024);
  auto g = JS_GetGlobalObject(c);
  JS_SetPropertyStr(c, g, "print", JS_NewCFunction(c, print, "print", 1));
  JS_SetPropertyStr(c, g, "__monotonicMs",
                    JS_NewCFunction(c, timer, "__monotonicMs", 0));
  JS_SetPropertyStr(c, g, "__ownedConfigure",
                    JS_NewCFunction(c, configure, "__ownedConfigure", 1));
  JS_SetPropertyStr(c, g, "__ownedPut",
                    JS_NewCFunction(c, put, "__ownedPut", 2));
  JS_SetPropertyStr(c, g, "__ownedEdge",
                    JS_NewCFunction(c, edge, "__ownedEdge", 1));
  JS_SetPropertyStr(c, g, "__nativeOwnedRead",
                    JS_NewCFunction(c, owned_bytes, "__nativeOwnedRead", 5));
  JS_SetPropertyStr(c, g, "__nativeOwnedScalar",
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
  JS_SetPropertyStr(c, g, "__nativeReplayHealthy",
                    JS_NewCFunction(c, healthy, "__nativeReplayHealthy", 0));
  JS_SetPropertyStr(c, g, "__ownedProfile",
                    JS_NewCFunction(c, report, "__ownedProfile", 1));
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
  JS_FreeContext(c);
  JS_FreeRuntime(rt);
  return status;
}
