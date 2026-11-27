// Self-owned executable: no DLL loading, hooks, game process or VM.
#include "../native_pc/native_event_runtime/capture_queue.h"
#include <array>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
#include <thread>
#include <vector>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

thread_local bool reject_allocation = false;
void* operator new(std::size_t size) {
    if (reject_allocation) std::abort();
    if (auto* result = std::malloc(size ? size : 1)) return result;
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete[](void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }
void operator delete[](void* ptr, std::size_t) noexcept { std::free(ptr); }

using Queue = arkdps::capture::CaptureQueue;
using Result = Queue::PopResult;
void require(bool condition, const char* reason) {
    if (!condition) { std::cerr << reason << '\n'; std::exit(1); }
}

void bounds_and_wrap() {
    bool rejected = false;
    try { Queue invalid({0, 10}); } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "zero capacity accepted");
    rejected = false;
    try { Queue invalid({1, 0}); } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected, "zero arena accepted");
    Queue q({3, 11});
    std::array<unsigned char, 11> output{};
    std::uint64_t ticket = 999;
    std::size_t size = 99;
    require(!q.head_ticket(), "initial head not empty");
    require(q.try_pop(ticket, output.data(), output.size(), size) == Result::empty && size == 0,
            "initial pop not empty");
    require(!q.push(1, nullptr, 1) && !q.push(1, output.data(), 12), "invalid input accepted");
    std::array<unsigned char, 7> source{{1,2,3,4,5,6,7}};
    require(q.push(0, source.data(), 7), "first push failed");
    source.fill(99); // Queue must own the original bytes after push returns.
    require(q.head_ticket() == 0, "zero ticket not retained");
    require(q.try_pop(ticket, output.data(), 6, size) == Result::destination_too_small && size == 7,
            "small destination consumed record");
    require(q.try_pop(ticket, nullptr, 11, size) == Result::destination_too_small,
            "null destination consumed record");
    require(q.head_ticket() == 0, "failed pop moved head");
    require(q.try_pop(ticket, output.data(), 11, size) == Result::popped && ticket == 0,
            "retry pop failed");
    for (unsigned i = 0; i < 7; ++i) require(output[i] == i+1, "payload not owned");

    // Byte wrap and metadata wrap have different periods; exact capacity fits.
    for (std::uint64_t round = 1; round < 101; ++round) {
        for (unsigned i = 0; i < 11; ++i) output[i] = static_cast<unsigned char>(round+i);
        const auto original = output;
        require(q.push(round, output.data(), 11), "wrapped exact byte capacity rejected");
        require(!q.push(999, output.data(), 1), "byte overflow overwrote queued data");
        output.fill(0);
        require(q.try_pop(ticket, output.data(), 11, size) == Result::popped && ticket == round
                && output == original, "byte/metadata wrap corrupted data");
    }
    for (unsigned i = 0; i < 3; ++i) require(q.push(i, nullptr, 0), "zero record rejected");
    require(!q.push(4, nullptr, 0), "metadata overflow accepted");
    for (unsigned i = 0; i < 3; ++i)
        require(q.try_pop(ticket, nullptr, 0, size) == Result::popped && ticket == i && size == 0,
                "zero record corrupted");
    require(!q.head_ticket(), "drained head not empty");
}

void full_does_not_wait_or_allocate() {
    Queue q({1, 8});
    std::uint64_t payload = 42;
    require(q.push(7, &payload, sizeof payload), "fill failed");
    const auto began = std::chrono::steady_clock::now();
    reject_allocation = true;
    for (unsigned i = 0; i < 100000; ++i)
        if (q.push(8, &payload, sizeof payload)) std::abort();
    reject_allocation = false;
    require(std::chrono::steady_clock::now()-began < std::chrono::seconds(2),
            "full queue waited for consumer");
    std::uint64_t ticket = 0, copy = 0;
    std::size_t size = 0;
    require(q.try_pop(ticket, &copy, sizeof copy, size) == Result::popped && ticket == 7 && copy == 42,
            "overflow changed original payload");
}

void concurrent_lanes() {
    constexpr unsigned lanes = 4, rounds = 10000;
    std::array<std::unique_ptr<Queue>, lanes> queues;
    for (auto& queue : queues) queue = std::make_unique<Queue>(Queue::Config{7, 997});
    std::atomic<bool> start{false};
    std::atomic<unsigned> finished{0};
    std::array<std::thread, lanes> producers;
    for (unsigned lane = 0; lane < lanes; ++lane) producers[lane] = std::thread([&, lane] {
        std::array<unsigned char, 251> payload{};
        while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
        reject_allocation = true;
        for (std::uint64_t n = 1; n <= rounds; ++n) {
            const auto size = 1 + (n*13 + lane) % payload.size();
            for (unsigned i = 0; i < size; ++i) payload[i] = static_cast<unsigned char>(n+lane*19+i);
            while (!queues[lane]->push(n, payload.data(), size)) std::this_thread::yield();
            payload.fill(0); // Cannot mutate the published record.
        }
        reject_allocation = false;
        finished.fetch_add(1, std::memory_order_release);
    });
    std::array<std::uint64_t, lanes> expected{{1,1,1,1}};
    std::array<unsigned char, 251> copy{};
    start.store(true, std::memory_order_release);
    unsigned consumed = 0;
    const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(5);
    while (consumed < lanes*rounds) {
        require(std::chrono::steady_clock::now() < deadline, "concurrent lanes failed to progress");
        for (unsigned lane = 0; lane < lanes; ++lane) {
            auto& queue = *queues[lane];
            const auto head = queue.head_ticket();
            if (!head) continue;
            require(*head == expected[lane], "lane head out of order");
            std::uint64_t ticket;
            std::size_t size;
            require(queue.try_pop(ticket, copy.data(), copy.size(), size) == Result::popped,
                    "published record disappeared");
            require(ticket == expected[lane] && size == 1+(ticket*13+lane)%copy.size(),
                    "record metadata corrupted");
            for (unsigned i = 0; i < size; ++i)
                require(copy[i] == static_cast<unsigned char>(ticket+lane*19+i), "partial or overwritten payload");
            ++expected[lane]; ++consumed;
        }
    }
    for (auto& producer : producers) producer.join();
    require(finished.load() == lanes, "producer did not finish");
    for (auto& queue : queues) require(!queue->head_ticket(), "lane not drained");
}

#ifdef _WIN32
std::atomic<bool> guard_entered{false}, resume_copy{false};
std::uintptr_t guarded_begin = 0, guarded_end = 0;
LONG CALLBACK pause_copy(EXCEPTION_POINTERS* exception) {
    if (exception->ExceptionRecord->ExceptionCode != STATUS_GUARD_PAGE_VIOLATION)
        return EXCEPTION_CONTINUE_SEARCH;
    const auto address = exception->ExceptionRecord->ExceptionInformation[1];
    if (address < guarded_begin || address >= guarded_end) return EXCEPTION_CONTINUE_SEARCH;
    guard_entered.store(true, std::memory_order_release);
    while (!resume_copy.load(std::memory_order_acquire)) Sleep(0); // Test-only deliberate pause.
    return EXCEPTION_CONTINUE_EXECUTION;
}

void unpublished_copy_is_invisible() {
    SYSTEM_INFO info{}; GetSystemInfo(&info);
    const auto bytes = static_cast<std::size_t>(info.dwPageSize)*2;
    auto* source = static_cast<unsigned char*>(VirtualAlloc(nullptr, bytes, MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE));
    require(source != nullptr, "VirtualAlloc failed");
    for (std::size_t i = 0; i < bytes; ++i) source[i] = static_cast<unsigned char>(i);
    Queue queue({2, bytes});
    std::vector<unsigned char> output(bytes);
    auto handler = AddVectoredExceptionHandler(1, pause_copy);
    require(handler != nullptr, "guard handler registration failed");
    guarded_begin = reinterpret_cast<std::uintptr_t>(source + info.dwPageSize);
    guarded_end = reinterpret_cast<std::uintptr_t>(source + bytes);
    DWORD old;
    require(VirtualProtect(source + info.dwPageSize, info.dwPageSize, PAGE_READWRITE|PAGE_GUARD, &old),
            "guard protection failed");
    std::thread producer([&] { require(queue.push(45, source, bytes), "guarded push failed"); });
    const auto deadline = std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while (!guard_entered.load(std::memory_order_acquire) && std::chrono::steady_clock::now()<deadline)
        std::this_thread::yield();
    require(guard_entered.load(), "copy did not reach guarded source");
    std::uint64_t ticket = 0; std::size_t size = 0;
    require(!queue.head_ticket() && queue.try_pop(ticket, output.data(), bytes, size) == Result::empty,
            "partly copied record published early");
    resume_copy.store(true, std::memory_order_release);
    producer.join();
    require(queue.try_pop(ticket, output.data(), bytes, size) == Result::popped && ticket == 45 && size == bytes,
            "completed copy not published");
    for (std::size_t i = 0; i < bytes; ++i) require(output[i] == static_cast<unsigned char>(i), "guarded copy corrupted");
    RemoveVectoredExceptionHandler(handler);
    VirtualFree(source, 0, MEM_RELEASE);
}
#endif

int main() {
    bounds_and_wrap();
    full_does_not_wait_or_allocate();
    concurrent_lanes();
#ifdef _WIN32
    unpublished_copy_is_invisible();
    std::cout << "PASS capture_queue: bounds, owned payload, two wraps, full/no allocation, 4x10000 concurrent records, paused publication\n";
#else
    std::cout << "PASS capture_queue: portable subset (guard-page publication test requires Windows)\n";
#endif
}
