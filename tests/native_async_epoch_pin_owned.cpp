// Own-process guard page pauses the actual production entry helper at its epoch
// read. A retirement grace check must already see that producer as active.
#include "../native_pc/native_event_runtime/engine_capture_lane.h"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
#include <thread>
using namespace arkdps::capture;
std::atomic<bool> entered{false}, resume{false};
std::uintptr_t guarded_begin=0, guarded_end=0;
LONG CALLBACK pause_epoch(EXCEPTION_POINTERS* fault) {
    if (fault->ExceptionRecord->ExceptionCode!=STATUS_GUARD_PAGE_VIOLATION)
        return EXCEPTION_CONTINUE_SEARCH;
    const auto address=fault->ExceptionRecord->ExceptionInformation[1];
    if (address<guarded_begin || address>=guarded_end) return EXCEPTION_CONTINUE_SEARCH;
    entered.store(true,std::memory_order_release);
    while (!resume.load(std::memory_order_acquire)) Sleep(0); // Test-only pause.
    return EXCEPTION_CONTINUE_EXECUTION;
}
void require(bool condition,const char* message) {
    if (!condition) { std::cerr<<message<<'\n'; std::exit(1); }
}
int main() {
    SYSTEM_INFO info{}; GetSystemInfo(&info);
    auto* memory=VirtualAlloc(nullptr,info.dwPageSize,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    require(memory!=nullptr,"epoch page allocation failed");
    auto* epoch=new(memory) std::atomic<std::uint64_t>(0);
    guarded_begin=reinterpret_cast<std::uintptr_t>(memory); guarded_end=guarded_begin+info.dwPageSize;
    auto handler=AddVectoredExceptionHandler(1,pause_epoch);
    require(handler!=nullptr,"guard handler failed");
    DWORD prior;
    require(VirtualProtect(memory,info.dwPageSize,PAGE_READWRITE|PAGE_GUARD,&prior),"epoch page guard failed");
    std::atomic<unsigned> active{0}; std::atomic<std::uint64_t> activity{0};
    std::uint64_t pinned=99;
    std::thread producer([&] { pinned=pin_mode_entry(active,activity,*epoch); });
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while (!entered.load(std::memory_order_acquire) && std::chrono::steady_clock::now()<deadline)
        std::this_thread::yield();
    require(entered.load(),"producer never reached epoch read");
    require(active.load(std::memory_order_acquire)==1 && activity.load(std::memory_order_acquire)==1,
            "epoch was read before entry became visible to retirement");
    epoch->store(1,std::memory_order_release);
    std::atomic_thread_fence(std::memory_order_seq_cst);
    require(active.load(std::memory_order_acquire)!=0,"old configuration could be reclaimed while pin is paused");
    resume.store(true,std::memory_order_release);
    producer.join();
    require(pinned==1,"producer did not observe published epoch after pause");
    activity.fetch_add(1,std::memory_order_acq_rel); active.fetch_sub(1,std::memory_order_acq_rel);
    require(active.load()==0 && activity.load()==2,"entry lifetime did not retire");
    RemoveVectoredExceptionHandler(handler); epoch->~atomic(); VirtualFree(memory,0,MEM_RELEASE);
    std::cout<<"PASS native epoch pin: paused epoch read remains visible to retirement grace\n";
}
