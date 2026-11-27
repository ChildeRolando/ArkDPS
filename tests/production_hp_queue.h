// Standalone owned producer/consumer pressure. No DLL, hooks or external
// process.
#include <atomic>
#include <thread>

static JSValue queue_run(JSContext *c, JSValueConst, int argc,
                         JSValueConst *argv) {
  if (argc != 2 || !JS_IsFunction(c, argv[1]) || !configurations)
    return JS_ThrowInternalError(
        c, "queue fixture requires full prepared scope and consumer");
  const char *text = JS_ToCString(c, argv[0]);
  J options;
  try {
    options = J::parse(text);
  } catch (...) {
    JS_FreeCString(c, text);
    return JS_ThrowInternalError(c, "invalid queue options");
  }
  JS_FreeCString(c, text);
  const unsigned count = options.value("count", 30000u),
                 rate = options.value("rate", 30000u),
                 pause = options.value("pause_ms", 0u),
                 pub_every = options.value("pub_every", 8571u);
  if (!count || count > 300000 || !rate || rate > 60000 || pause > 500 ||
      !pub_every)
    return JS_ThrowInternalError(c, "queue options exceed owned test bound");
  CaptureLanes::Config capacity;
  capacity.lane_queue = {2048, 2 * 1024 * 1024};
  capacity.spill = {32768, 32 * 1024 * 1024};
  auto queues = std::make_unique<CaptureLanes>(capacity);
  auto destination = std::make_unique<std::byte[]>(record_capacity);
  std::atomic<bool> ready{false}, done{false}, stop{false};
  std::atomic<unsigned> sent{0};
  std::atomic<std::uint64_t> reserved{0};
  double producer_ms = 0;
  std::array<std::size_t, 2> payload_min{{record_capacity, record_capacity}},
      payload_max{};
  Lane *produced_lane = nullptr;
  LARGE_INTEGER frequency{};
  QueryPerformanceFrequency(&frequency);
  const auto start = std::chrono::steady_clock::now();
  std::thread producer([&] {
    auto *lane = queues->current();
    produced_lane = lane;
    if (!lane) {
      done.store(true, std::memory_order_release);
      return;
    }
    lane->depth = 1;
    ready.store(true, std::memory_order_release);
    const auto began = std::chrono::steady_clock::now();
    auto put_value = [&](std::uintptr_t address, auto value) {
      auto *r = region(address, sizeof value);
      if (!r) {
        queues->poison(LaneFault::invalid_record);
        return;
      }
      std::memcpy(r->data.data() + address - r->address, &value, sizeof value);
    };
    for (unsigned i = 0; i < count && !stop.load(std::memory_order_relaxed) &&
                         !queues->failure.load(std::memory_order_relaxed);
         ++i) {
      const auto due = began + std::chrono::nanoseconds(std::uint64_t(i) *
                                                        1000000000ULL / rate);
      std::this_thread::sleep_until(due);
      const auto frame = 30u + i / 1000;
      put_value(input.globals[0], frame);
      put_value(input.globals[0] + 4, (std::uint64_t(frame) << 32) / 30);
      for (unsigned edge = 0; edge < 2; ++edge) {
        put_value(input.args[0] + 0x40,
                  (std::uint64_t(100000 + i + edge) << 32));
        RecordHeader h;
        h.ticket = reserved.fetch_add(1, std::memory_order_relaxed) + 1;
        h.invocation = i + 1;
        h.phase = edge ? Phase::leave : Phase::entry;
        h.lane = 0;
        h.depth = 1;
        h.thread_id = lane->owner.load(std::memory_order_acquire);
        h.thread_birth = lane->birth.load(std::memory_order_acquire);
        h.args[0] = input.args[0];
        h.arg_mask = 1;
        h.active = edge ? 0 : 1;
        h.active_after = edge ? 0 : 1;
        h.closed_after = edge != 0;
        h.activity_begin = h.ticket;
        h.activity = edge ? h.ticket + 1 : h.ticket;
        h.return_address = 0x1234;
        if (edge && ((i + 1) % pub_every == 0 || i + 1 == count))
          h.publication_request = h.ticket;
        lane->calls[0].id = h.invocation;
        LARGE_INTEGER now{};
        QueryPerformanceCounter(&now);
        h.captured_qpc = now.QuadPart;
        capture_scope_point(*plan, h.phase, input, configurations->pin(0),
                            read_memory, nullptr, lane->snapshot, h, lane);
        if (lane->snapshot.error != Error::ok) {
          queues->poison(LaneFault::invalid_record);
          break;
        }
        const auto compact =
            sizeof h + h.parent_checkpoint_count * sizeof(ParentCheckpoint) +
            lane->snapshot.segment_count * sizeof(Segment) +
            lane->snapshot.field_count * sizeof(FieldState) +
            lane->snapshot.byte_count;
        payload_min[edge] = std::min(payload_min[edge], compact);
        payload_max[edge] = std::max(payload_max[edge], compact);
        if (!queues->publish(*lane, h))
          break;
      }
      if (!queues->failure.load(std::memory_order_relaxed))
        sent.store(i + 1, std::memory_order_release);
    }
    producer_ms = std::chrono::duration<double, std::milli>(
                      std::chrono::steady_clock::now() - began)
                      .count();
    lane->depth = 0;
    done.store(true, std::memory_order_release);
  });
  while (!ready.load(std::memory_order_acquire) &&
         !done.load(std::memory_order_acquire))
    std::this_thread::yield();
  if (pause)
    std::this_thread::sleep_for(std::chrono::milliseconds(pause));
  struct Trend {
    double ms = 0;
    std::uint64_t produced = 0, consumed = 0;
  };
  std::array<Trend, 128> trend{};
  unsigned trend_count = 0;
  std::uint64_t expected = 1, consumed = 0, consumed_bytes = 0;
  double max_age_ms = 0, last_sample = -100, consumer_busy_ms = 0;
  bool semantic_failure = false;
  std::string semantic_error;
  while (!queues->failure.load(std::memory_order_acquire)) {
    const auto elapsed = std::chrono::duration<double, std::milli>(
                             std::chrono::steady_clock::now() - start)
                             .count();
    if (elapsed - last_sample >= 100 && trend_count < trend.size()) {
      trend[trend_count++] = {elapsed, reserved.load(std::memory_order_acquire),
                              consumed};
      last_sample = elapsed;
    }
    const auto next = queues->next_ready(expected);
    if (!next) {
      if (done.load(std::memory_order_acquire) &&
          expected > reserved.load(std::memory_order_acquire))
        break;
      std::this_thread::yield();
      continue;
    }
    const auto busy_start = std::chrono::steady_clock::now();
    std::uint64_t ticket = 0;
    std::size_t size = 0;
    Lane *owner = nullptr;
    if (queues->try_pop(*next, ticket, destination.get(), record_capacity, size,
                        owner) != CaptureQueue::PopResult::popped ||
        !owner || ticket != expected) {
      queues->poison(LaneFault::invalid_record);
      break;
    }
    RecordHeader h;
    std::memcpy(&h, destination.get(), sizeof h);
    const auto prefix =
        sizeof h + h.parent_checkpoint_count * sizeof(ParentCheckpoint);
    const auto segment_bytes = h.segment_count * sizeof(Segment),
               field_bytes = h.field_count * sizeof(FieldState);
    if (h.parent_checkpoint_count ||
        prefix + segment_bytes + field_bytes + h.byte_count != size) {
      queues->poison(LaneFault::invalid_record);
      break;
    }
    const auto err = arkdps::owned_js::begin(
        destination.get() + prefix, h.segment_count,
        destination.get() + prefix + segment_bytes, h.field_count,
        destination.get() + prefix + segment_bytes + field_bytes, h.byte_count,
        configurations->roles, h.ticket, h.error);
    if (err != Error::ok) {
      queues->poison(LaneFault::replay_structure);
      break;
    }
#ifdef ARKDPS_CAPTURE_GRAPH_BATCH
    const auto *exact = configurations->pin(0);
    if (exact->id != h.configuration_id)
      exact = nullptr;
    arkdps::owned_js::bind_graphs(exact, h.scope_revision,
                                  h.publication_revision, h.scope_mode_epoch,
                                  h.checkpoint);
#endif
#ifdef ARKDPS_CAPTURE_HP_OBSERVATION
    arkdps::owned_js::bind_hp_observation(plan.get(), h.phase, h.args[0],
                                          h.checkpoint, h.mode_epoch);
#endif
#ifdef ARKDPS_CAPTURE_HOOK_POINT
    arkdps::owned_js::bind_hook(plan.get(), h.phase, h.args[0], h.checkpoint);
#endif
    std::array<std::uint64_t, 22> packed{
        {h.ticket,
         h.invocation,
         h.parent,
         h.activity_begin,
         h.activity,
         h.thread_birth,
         h.publication_request,
         h.mode_epoch,
         h.scope_mode_epoch,
         h.scope_revision,
         h.publication_revision,
         h.cold_revision,
         h.hook_id,
         h.checkpoint,
         h.active,
         h.active_after,
         h.lane,
         h.thread_id,
         h.phase == Phase::entry ? 0u : 1u,
         h.closed_after ? 1u : 0u,
         (h.scope_unavailable ? 1u : 0u) | (h.capture_concurrent ? 2u : 0u),
         0}};
    auto header = JS_NewArrayBufferCopy(
        c, reinterpret_cast<const std::uint8_t *>(packed.data()),
        sizeof packed);
    auto answer = JS_Call(c, argv[1], JS_UNDEFINED, 1, &header);
    JS_FreeValue(c, header);
    if (JS_IsException(answer) || !JS_IsBool(answer) ||
        JS_ToBool(c, answer) != 1) {
      semantic_failure = true;
      auto exception =
          JS_IsException(answer) ? JS_GetException(c) : JS_UNDEFINED;
      const char *message = JS_ToCString(c, exception);
      semantic_error = message ? message : "consumer failed";
      JS_FreeCString(c, message);
      JS_FreeValue(c, exception);
      JS_FreeValue(c, answer);
      stop.store(true, std::memory_order_release);
      break;
    }
    JS_FreeValue(c, answer);
    arkdps::owned_js::end();
    owner->consumed.records.fetch_add(1, std::memory_order_release);
    owner->consumed.bytes.fetch_add(size, std::memory_order_release);
    ++consumed;
    consumed_bytes += size;
    ++expected;
    consumer_busy_ms += std::chrono::duration<double, std::milli>(
                            std::chrono::steady_clock::now() - busy_start)
                            .count();
    LARGE_INTEGER now{};
    QueryPerformanceCounter(&now);
    if (now.QuadPart >= static_cast<LONGLONG>(h.captured_qpc))
      max_age_ms = std::max(max_age_ms, (now.QuadPart - h.captured_qpc) *
                                            1000.0 / frequency.QuadPart);
  }
  stop.store(true, std::memory_order_release);
  producer.join();
  const auto elapsed = std::chrono::duration<double, std::milli>(
                           std::chrono::steady_clock::now() - start)
                           .count();
  if (semantic_failure)
    return JS_ThrowInternalError(c, "owned queued consumer: %s",
                                 semantic_error.c_str());
  J points = J::array();
  for (unsigned i = 0; i < trend_count; i++)
    points.push_back({{"ms", trend[i].ms},
                      {"reserved", trend[i].produced},
                      {"consumed", trend[i].consumed},
                      {"backlog", trend[i].produced - trend[i].consumed}});
  const auto failure = queues->failure.load(std::memory_order_acquire);
  J report = {
      {"requested_producer_ms", count * 1000.0 / rate},
      {"entry_compact_min", payload_min[0]},
      {"entry_compact_max", payload_max[0]},
      {"leave_compact_min", payload_min[1]},
      {"leave_compact_max", payload_max[1]},
      {"failure_code", failure},
      {"target_setters_per_second", rate},
      {"requested_setters", count},
      {"submitted_setters", sent.load()},
      {"reserved_records", reserved.load()},
      {"consumed_records", consumed},
      {"consumed_bytes", consumed_bytes},
      {"producer_ms", producer_ms},
      {"elapsed_ms", elapsed},
      {"pause_ms", pause},
      {"max_queue_age_ms", max_age_ms},
      {"actual_producer_setters_per_second",
       sent.load() * 1000.0 / producer_ms},
      {"consumer_busy_ms", consumer_busy_ms},
      {"consumer_service_records_per_second",
       consumed * 1000.0 / std::max(0.001, consumer_busy_ms)},
      {"peak_resident_bytes",
       produced_lane ? produced_lane->submitted.peak_bytes.load() : 0},
      {"trend", points},
      {"consumed_prefix_contiguous", true},
      {"raw_prefix_contiguous", failure == 0 && consumed == reserved.load()},
      {"capacity_pass",
       failure == 0 && consumed == count * 2 &&
           consumed * 1000.0 / std::max(0.001, consumer_busy_ms) >= rate * 2}};
  const auto output = report.dump();
  return JS_NewStringLen(c, output.data(), output.size());
}
