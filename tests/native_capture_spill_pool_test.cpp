#include "capture_spill_pool.h"
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <new>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#endif
using namespace arkdps::capture;
static std::atomic<std::size_t> allocations{0};
void* operator new(std::size_t n) { allocations.fetch_add(1); if(auto p=std::malloc(n)) return p; throw std::bad_alloc(); }
void operator delete(void* p) noexcept {std::free(p);}
void operator delete(void* p,std::size_t) noexcept {std::free(p);}
void* operator new[](std::size_t n) {return ::operator new(n);}
void operator delete[](void* p) noexcept {std::free(p);}
void operator delete[](void* p,std::size_t) noexcept {std::free(p);}
struct Payload {std::uint64_t owner,ticket,checksum;};
static bool pop_first(CaptureSpillPool& p, Payload& payload, std::uint64_t& ticket) {
 std::size_t selected=CaptureSpillPool::bank_count;
 for(std::size_t i=0;i<CaptureSpillPool::bank_count;++i){auto h=p.head_ticket(i);if(h&&(selected==CaptureSpillPool::bank_count||*h<ticket)){selected=i;ticket=*h;}}
 if(selected==CaptureSpillPool::bank_count)return false;
 std::size_t size=0;assert(p.try_pop(selected,ticket,&payload,sizeof payload,size)==CaptureQueue::PopResult::popped);
 assert(size==sizeof payload);p.reap_empty(selected);return true;
}
#ifdef _WIN32
static std::atomic<unsigned> guard_stage{0};
static std::uintptr_t guard_source=0;
static LONG CALLBACK guard_handler(EXCEPTION_POINTERS* e) {
 if(e->ExceptionRecord->ExceptionCode!=STATUS_GUARD_PAGE_VIOLATION||e->ExceptionRecord->NumberParameters<2
    ||e->ExceptionRecord->ExceptionInformation[1]<guard_source||e->ExceptionRecord->ExceptionInformation[1]>=guard_source+4096)return EXCEPTION_CONTINUE_SEARCH;
 guard_stage.store(1,std::memory_order_release);
 const auto end=GetTickCount64()+3000;
 while(guard_stage.load(std::memory_order_acquire)!=2){if(GetTickCount64()>end)return EXCEPTION_CONTINUE_SEARCH;YieldProcessor();}
 return EXCEPTION_CONTINUE_EXECUTION;
}
#endif
int main(){
 bool rejected=false;try{CaptureSpillPool bad({0,64});}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
 CaptureSpillPool hot({2,2*sizeof(Payload)});
 const auto initial=allocations.load();
 assert(!hot.try_push(0,0,nullptr,0)&&!hot.try_push(CaptureSpillPool::busy,0,nullptr,0));
 assert(!hot.try_push(1,0,nullptr,1)&&!hot.try_push(1,0,"x",1000));
 // One hot producer fills ALL banks rather than a permanent per-lane quota.
 for(std::uint64_t i=0;i<8;++i){Payload value{7,i,i^7};assert(hot.try_push(7,i,&value,sizeof value));}
 Payload extra{7,9,14};assert(!hot.try_push(7,9,&extra,sizeof extra));
 for(unsigned i=0;i<4;++i){assert(hot.bank_owner(i)==7);assert(!hot.reap_empty(i));}
 assert(hot.has_owner_pending(7));
 std::uint64_t small_ticket=UINT64_MAX,small_value=0;std::size_t required=0;
 assert(hot.try_pop(0,small_ticket,&small_value,8,required)==CaptureQueue::PopResult::destination_too_small);
 assert(required==sizeof(Payload)&&hot.head_ticket(0)==0&&!hot.reap_empty(0));
 for(std::uint64_t expected=0;expected<8;++expected){Payload value{};std::uint64_t ticket=UINT64_MAX;assert(pop_first(hot,value,ticket));assert(ticket==expected&&value.ticket==expected&&value.checksum==(expected^7));}
 assert(!hot.has_owner_pending(7));assert(allocations.load()==initial);
 // Each independent resource is a hard boundary, not an implicit resize.
 CaptureSpillPool byte_bound({8,sizeof(Payload)}),meta_bound({1,8*sizeof(Payload)});
 const auto resource_before=allocations.load();
 for(std::uint64_t i=0;i<4;++i){Payload a{9,i,i};assert(byte_bound.try_push(9,i,&a,sizeof a));assert(meta_bound.try_push(9,i,&a,sizeof a));}
 assert(!byte_bound.try_push(9,9,&extra,sizeof extra)&&!meta_bound.try_push(9,9,&extra,sizeof extra));
 assert(allocations.load()==resource_before);
 // Poisoned producer buffer and a held 500ms backlog remain owned by queue.
 Payload value{11,100,111};assert(hot.try_push(11,100,&value,sizeof value));value={0,0,0};
 std::this_thread::sleep_for(std::chrono::milliseconds(500));
 std::uint64_t ticket=UINT64_MAX;assert(pop_first(hot,value,ticket)&&value.owner==11&&value.ticket==100&&value.checksum==111);
 assert(allocations.load()==resource_before);
 // Repeated borrower changes exercise SPSC publication across owner handoff.
 for(std::uint64_t tag=1;tag<=256;++tag){Payload a{tag,tag,tag*17};assert(hot.try_push(static_cast<std::uint32_t>(tag),tag,&a,sizeof a));ticket=UINT64_MAX;Payload b{};assert(pop_first(hot,b,ticket)&&b.owner==tag&&b.checksum==tag*17);assert(!hot.has_owner_pending(static_cast<std::uint32_t>(tag)));}
 assert(allocations.load()==resource_before);
 // Sixteen sparse producers contest four exclusive banks, without waiting or
 // silently sharing a bank with a different tag. Failure is a real bound.
 CaptureSpillPool sparse({2,2*sizeof(Payload)});
 std::array<std::thread,16> producers;std::array<std::atomic<bool>,16> success{};
 std::atomic<bool> start{false};
 for(unsigned i=0;i<16;++i)producers[i]=std::thread([&,i]{while(!start.load(std::memory_order_acquire))std::this_thread::yield();Payload a{i+1,i+1,(i+1)*17};success[i].store(sparse.try_push(i+1,i+1,&a,sizeof a));});
 const auto concurrent_before=allocations.load();start.store(true,std::memory_order_release);
 for(auto& thread:producers)thread.join();
 unsigned accepted=0,popped=0;for(auto& s:success)if(s.load())++accepted;
 assert(accepted>0&&accepted<=4);
 for(;;){Payload a{};ticket=UINT64_MAX;if(!pop_first(sparse,a,ticket))break;assert(a.owner>=1&&a.owner<=16&&success[a.owner-1].load()&&a.checksum==a.owner*17);success[a.owner-1].store(false);++popped;}
 assert(popped==accepted&&allocations.load()==concurrent_before);
#ifdef _WIN32
 // Pause a real memcpy via a source guard page AFTER owner->Busy but BEFORE
 // queue publication. Reaping must reject Busy even though queue is empty.
 CaptureSpillPool guarded({2,64});
 void* memory=VirtualAlloc(nullptr,4096,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);assert(memory);
 const std::uint64_t magic=0xabcdef1234567890ULL;std::memcpy(memory,&magic,8);
 guard_source=reinterpret_cast<std::uintptr_t>(memory);auto handler=AddVectoredExceptionHandler(1,guard_handler);assert(handler);
 DWORD previous=0;assert(VirtualProtect(memory,4096,PAGE_READWRITE|PAGE_GUARD,&previous));
 std::atomic<bool> launch{false},pushed{false};std::thread writer([&]{while(!launch.load(std::memory_order_acquire))std::this_thread::yield();pushed.store(guarded.try_push(77,77,memory,8));});
 const auto guarded_before=allocations.load();launch.store(true,std::memory_order_release);
 const auto deadline=GetTickCount64()+3000;while(guard_stage.load(std::memory_order_acquire)!=1){assert(GetTickCount64()<deadline);std::this_thread::yield();}
 assert(guarded.owner_state(0)==(77|CaptureSpillPool::busy));
 assert(!guarded.head_ticket(0)&&!guarded.reap_empty(0)&&guarded.has_owner_pending(77));
 guard_stage.store(2,std::memory_order_release);writer.join();assert(pushed.load());
 std::uint64_t copied=0;std::size_t size=0;assert(guarded.try_pop(0,ticket,&copied,8,size)==CaptureQueue::PopResult::popped&&copied==magic);
 assert(guarded.reap_empty(0)&&!guarded.has_owner_pending(77));assert(allocations.load()==guarded_before);
 RemoveVectoredExceptionHandler(handler);VirtualFree(memory,0,MEM_RELEASE);
#endif
 std::cout<<"spill pool: hot8/sparse16/backlog500ms/owned/noheap/ownerhandoff/guardBusy passed accepted_sparse="<<accepted<<"\n";
}
