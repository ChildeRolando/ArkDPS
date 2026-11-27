// Real queue publication is paused before its diagnostic mirror store.
#include "../native_pc/native_event_runtime/engine_capture_lane.h"
#include <chrono>
#include <iostream>
#include <thread>
using namespace arkdps::capture;
std::atomic<bool> paused{false}, resume_mirror{false};
std::uintptr_t guard_begin=0,guard_end=0;
LONG CALLBACK pause_mirror(EXCEPTION_POINTERS* fault) {
    if (fault->ExceptionRecord->ExceptionCode!=STATUS_GUARD_PAGE_VIOLATION) return EXCEPTION_CONTINUE_SEARCH;
    const auto address=fault->ExceptionRecord->ExceptionInformation[1];
    if (address<guard_begin || address>=guard_end) return EXCEPTION_CONTINUE_SEARCH;
    paused.store(true,std::memory_order_release);
    while (!resume_mirror.load(std::memory_order_acquire)) Sleep(0);
    return EXCEPTION_CONTINUE_EXECUTION;
}
void require(bool ok,const char* message) { if (!ok) { std::cerr<<message<<'\n'; std::exit(1); } }
bool owned_read(void*,std::uintptr_t address,void* out,std::size_t bytes) noexcept {
    std::memcpy(out,reinterpret_cast<const void*>(address),bytes); return true;
}
int main() {
    auto lane=std::make_unique<Lane>();
    SYSTEM_INFO info{}; GetSystemInfo(&info);
    guard_begin=reinterpret_cast<std::uintptr_t>(&lane->submitted.records)&~std::uintptr_t(info.dwPageSize-1);
    guard_end=guard_begin+info.dwPageSize;
    auto handler=AddVectoredExceptionHandler(1,pause_mirror); require(handler!=nullptr,"handler failed");
    DWORD old;
    require(VirtualProtect(reinterpret_cast<void*>(guard_begin),info.dwPageSize,PAGE_READWRITE|PAGE_GUARD,&old),"guard failed");
    std::atomic<unsigned> failure{0}; RecordHeader header{}; header.ticket=17;
    std::thread producer([&] { require(CaptureLanes::publish_to_lane(*lane,header,failure),"publish failed"); });
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while (!paused.load(std::memory_order_acquire) && std::chrono::steady_clock::now()<deadline) std::this_thread::yield();
    require(paused.load(),"producer did not reach submitted mirror");
    std::array<std::byte,sizeof(RecordHeader)> bytes{}; std::uint64_t ticket=0; std::size_t size=0;
    require(lane->queue.try_pop(ticket,bytes.data(),bytes.size(),size)==CaptureQueue::PopResult::popped,"published record not visible");
    require(ticket==17,"wrong ticket");
    lane->consumed.records.store(1,std::memory_order_release); lane->consumed.bytes.store(size,std::memory_order_release);
    const auto crossed=lane_metric_snapshot(*lane,true,100);
    require(!crossed.consistent && !crossed.resident_records && !crossed.resident_bytes,"inconsistent mirrors became known empty");
    require(!crossed.age_available && !crossed.last_age && !crossed.maximum_age,"unsampled lane became zero age");
    resume_mirror.store(true,std::memory_order_release); producer.join();
    RemoveVectoredExceptionHandler(handler);
    const auto caught_up=lane_metric_snapshot(*lane,true,100);
    require(caught_up.consistent && caught_up.resident_records==0,"valid empty mirrors rejected");
    observe_pending_head(*lane,17,10);
    observe_pending_head(*lane,18,100);
    require(lane_metric_snapshot(*lane,true,101).observed_pending_age==1,"new head inherited old age");
    observe_pending_head(*lane,std::nullopt,0);
    require(!lane_metric_snapshot(*lane,true,102).observed_pending_age,"pop did not clear head age");

    // Core can fail structurally before adding any failed FieldState. Header
    // rejection must still detect the duplicate append, not inspect fields only.
    auto plan=std::make_unique<HookCapturePlan>(); plan->max_bytes=64; plan->max_reads=16;
    plan->field_count=1; auto& field=plan->fields[0];
    std::uint32_t value=42; field.id=1; field.role=1; field.root=Root::absolute;
    field.absolute_address=reinterpret_cast<std::uintptr_t>(&value); field.bytes=4;
    require(validate_plan(*plan)==Error::ok,"owned plan invalid");
    auto snapshot=std::make_unique<Snapshot>(); Context context;
    capture_begin(*snapshot,Phase::entry,context,64,16);
    capture_append(*plan,Phase::entry,context,owned_read,nullptr,*snapshot);
    capture_append(*plan,Phase::entry,context,owned_read,nullptr,*snapshot);
    capture_finalize(*snapshot);
    require(snapshot->field_count==1 && snapshot->fields[0].error==Error::ok && structural_error(snapshot->error),
            "header-only structural error was accepted");
    require(!structural_error(Error::field_unreadable) && !structural_error(Error::changed_guard),"planned failure became a raw gap");
    std::cout<<"PASS native metrics: real crossed publication, no zero age, new head anchor, header-only structural rejection\n";
}
