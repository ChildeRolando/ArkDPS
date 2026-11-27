// Self-owned storage/control fixture. No VM, hooks, foreign process, or game.
#include "engine_capture_lane.h"
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
#include <thread>
#ifdef _WIN32
#include <psapi.h>
#include <malloc.h>
#endif
using namespace arkdps::capture;
thread_local volatile bool forbid_allocation=false;
void* operator new(std::size_t n){if(forbid_allocation)throw std::bad_alloc();if(auto* p=std::malloc(n?n:1))return p;throw std::bad_alloc();}
void* operator new[](std::size_t n){return ::operator new(n);}
void operator delete(void* p)noexcept{std::free(p);}void operator delete[](void* p)noexcept{std::free(p);}
void operator delete(void* p,std::size_t)noexcept{std::free(p);}void operator delete[](void* p,std::size_t)noexcept{std::free(p);}
void* operator new(std::size_t n,std::align_val_t alignment){
 if(forbid_allocation)throw std::bad_alloc();
 const auto a=static_cast<std::size_t>(alignment);
#ifdef _WIN32
 if(auto* p=_aligned_malloc(n?n:1,a))return p;
#else
 if(auto* p=std::aligned_alloc(a,((n?n:1)+a-1)/a*a))return p;
#endif
 throw std::bad_alloc();
}
void* operator new[](std::size_t n,std::align_val_t a){return ::operator new(n,a);}
void operator delete(void* p,std::align_val_t)noexcept{
#ifdef _WIN32
 _aligned_free(p);
#else
 std::free(p);
#endif
}
void operator delete[](void* p,std::align_val_t a)noexcept{::operator delete(p,a);}
void operator delete(void* p,std::size_t,std::align_val_t a)noexcept{::operator delete(p,a);}
void operator delete[](void* p,std::size_t,std::align_val_t a)noexcept{::operator delete(p,a);}
static std::array<std::byte,record_capacity> output;
static void initialize(Lane& lane,unsigned payload=128){lane.snapshot.byte_count=payload;lane.snapshot.segment_count=1;lane.snapshot.field_count=1;lane.snapshot.error=Error::ok;
 lane.snapshot.segments[0]={0x10000,1,1,0,payload,0,0};lane.snapshot.fields[0]=FieldState{};lane.snapshot.fields[0].id=1;lane.snapshot.fields[0].role=1;
 for(unsigned i=0;i<payload;++i)lane.snapshot.bytes[i]=std::byte(i);}
static RecordHeader header(Lane& lane,std::uint64_t ticket){RecordHeader h;h.ticket=ticket;h.invocation=ticket;h.lane=0;h.thread_birth=lane.birth.load();h.phase=ticket%2?Phase::entry:Phase::leave;return h;}
static void consume(CaptureLanes& lanes,std::uint64_t expected){const auto ready=lanes.next_ready(expected);assert(ready);std::size_t size=0;std::uint64_t ticket=0;Lane* owner=nullptr;
 assert(lanes.try_pop(*ready,ticket,output.data(),output.size(),size,owner)==CaptureQueue::PopResult::popped&&ticket==expected&&owner);
 RecordHeader h;std::memcpy(&h,output.data(),sizeof h);assert(h.ticket==expected&&size==sizeof h+sizeof(Segment)+sizeof(FieldState)+h.byte_count);
 const auto start=size-h.byte_count;for(unsigned i=0;i<h.byte_count;++i)assert(output[start+i]==std::byte(i));
 owner->consumed.records.fetch_add(1,std::memory_order_release);owner->consumed.bytes.fetch_add(size,std::memory_order_release);}
static void geometry(){
 static_assert(CaptureQueue::metadata_record_size()==sizeof(std::uint64_t)+2*sizeof(std::size_t));
 for(auto value:{511u,2049u}){bool rejected=false;try{CaptureLanes::budget_config(value);}catch(const std::invalid_argument&){rejected=true;}assert(rejected);}
 for(auto mib:{512u,1024u,2048u}){CaptureLanes lanes(CaptureLanes::budget_config(mib));auto s=lanes.memory_status();
  assert(s.enabled&&s.requested_budget_mib==mib&&s.budget_bytes==std::uint64_t(mib)*1024*1024&&s.maximum_storage_bytes<=s.budget_bytes);
  assert(s.prepared_banks==4&&s.free_banks==4&&s.maximum_banks<=128&&s.prepared_storage_bytes<s.maximum_storage_bytes);
  assert(s.slot_capacity==16*2048ull+s.maximum_banks*65536ull&&s.arena_capacity==16*2*1024*1024ull+s.maximum_banks*16*1024*1024ull);
  const auto excluded=sizeof(decltype(Lane::calls))+sizeof(decltype(Lane::snapshot))+sizeof(decltype(Lane::parent_checkpoints))+sizeof(decltype(Lane::scratch));
  assert(s.fixed_capture_working_bytes==16*excluded);
  std::cout<<"{\"kind\":\"geometry\",\"mib\":"<<mib<<",\"banks\":"<<s.maximum_banks<<",\"maximum\":"<<s.maximum_storage_bytes<<",\"prepared\":"<<s.prepared_storage_bytes<<",\"fixed_working\":"<<s.fixed_capture_working_bytes<<",\"slots\":"<<s.slot_capacity<<"}\n";
 }
}
static void bounds(){
 const auto packed=sizeof(RecordHeader)+sizeof(Segment)+sizeof(FieldState)+128;
 for(bool byte_bound:{false,true}){CaptureLanes::Config c;c.lane_queue={byte_bound?8u:1u,byte_bound?packed:4096};c.spill.metadata_slots=byte_bound?8:1;c.spill.arena_bytes=byte_bound?packed:4096;
  CaptureLanes lanes(c);auto* lane=lanes.current();assert(lane==lanes.lanes[0].get());initialize(*lane);
  forbid_allocation=true;for(unsigned i=1;i<=5;++i)assert(lanes.publish(*lane,header(*lane,i)));assert(!lanes.publish(*lane,header(*lane,6)));forbid_allocation=false;
  auto f=lanes.queue_failure_snapshot();assert(f.available&&f.ticket==6&&f.lane==0&&f.attempted_bytes==packed&&lanes.failure==4);
  assert(f.private_queue.used_slots==1&&f.private_queue.used_bytes==packed);
  assert(byte_bound?f.spill.byte_rejections==4:f.spill.slot_rejections==4);
  assert(byte_bound?f.spill.slot_rejections==0:f.spill.byte_rejections==0);
  lanes.poison(LaneFault::publication_failure);assert(lanes.failure==4&&lanes.queue_failure_snapshot().ticket==6);
  for(unsigned i=1;i<=5;++i)consume(lanes,i);
 }
 CaptureLanes::Config c;c.lane_queue={1,4096};c.spill={1,4096,4,true,1,1,0};CaptureLanes lanes(c);auto* lane=lanes.current();initialize(*lane);
 forbid_allocation=true;assert(lanes.publish(*lane,header(*lane,1))&&lanes.publish(*lane,header(*lane,2))&&!lanes.publish(*lane,header(*lane,3)));forbid_allocation=false;
 auto f=lanes.queue_failure_snapshot();assert(f.available&&f.spill.unprepared_banks==3&&f.spill.prepared_banks==1&&f.spill.owned_banks==1&&lanes.failure==4);
 lanes.maintain_spill();assert(lanes.memory_status().prepared_banks==2&&lanes.failure==4&&lanes.queue_failure_snapshot().ticket==3);
}
static void partial_bank_revisit(){
 CaptureSpillPool pool({2,64,2,true,2,2,0});CaptureSpillPool::ProducerCursor cursor;std::uint64_t value=9,ticket=0;std::size_t size=0;
 assert(pool.try_push(1,1,&value,8,&cursor)&&pool.try_push(1,2,&value,8,&cursor)&&pool.try_push(1,3,&value,8,&cursor));
 assert(pool.try_pop(0,ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==1);
 assert(pool.try_push(1,4,&value,8,&cursor)&&pool.try_push(1,5,&value,8,&cursor));
 assert(!pool.try_push(1,6,&value,8,&cursor)); // All four slots now genuinely occupied.
 for(auto expected:{2ull,3ull,4ull,5ull}){std::size_t bank=128;for(std::size_t i=0;i<2;++i)if(pool.head_ticket(i)==expected)bank=i;assert(bank<2);assert(pool.try_pop(bank,ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==expected);pool.reap_empty(bank);}
 assert(pool.free_banks()==2);
}
static void preparation_backoff(){
 CaptureSpillPool pool({1,64,4,true,1,1,0});CaptureSpillPool::ProducerCursor cursor;std::uint64_t value=1;assert(pool.try_push(1,1,&value,8,&cursor));
 forbid_allocation=true;for(unsigned i=0;i<500;++i)pool.maintain();forbid_allocation=false;
 std::cout<<"backoff failures="<<pool.prepare_failures()<<" prepared="<<pool.prepared_banks()<<std::endl;
 assert(pool.prepare_failures()==1&&pool.prepared_banks()==1);
 std::this_thread::sleep_for(std::chrono::milliseconds(1010));pool.maintain();assert(pool.prepare_failures()==1&&pool.prepared_banks()==2);
}
#ifdef _WIN32
static std::atomic<unsigned> guarded{0};static std::uintptr_t guard_address=0;
static LONG CALLBACK handler(EXCEPTION_POINTERS* e){if(e->ExceptionRecord->ExceptionCode!=STATUS_GUARD_PAGE_VIOLATION||e->ExceptionRecord->NumberParameters<2||e->ExceptionRecord->ExceptionInformation[1]<guard_address||e->ExceptionRecord->ExceptionInformation[1]>=guard_address+4096)return EXCEPTION_CONTINUE_SEARCH;
 guarded.store(1);const auto deadline=GetTickCount64()+3000;while(guarded.load()!=2){if(GetTickCount64()>deadline)return EXCEPTION_CONTINUE_SEARCH;YieldProcessor();}return EXCEPTION_CONTINUE_EXECUTION;}
static void writer_excludes_trim(){
 CaptureSpillPool pool({1,4096,4,true,1,1,0});CaptureSpillPool::ProducerCursor cursor;
 void* memory=VirtualAlloc(nullptr,4096,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);assert(memory);const std::uint64_t magic=77;std::memcpy(memory,&magic,8);
 guard_address=reinterpret_cast<std::uintptr_t>(memory);auto h=AddVectoredExceptionHandler(1,handler);assert(h);DWORD previous=0;assert(VirtualProtect(memory,4096,PAGE_READWRITE|PAGE_GUARD,&previous));
 std::thread writer([&]{forbid_allocation=true;assert(pool.try_push(1,1,memory,8,&cursor));forbid_allocation=false;});
 const auto deadline=GetTickCount64()+3000;while(guarded.load()!=1){assert(GetTickCount64()<deadline);std::this_thread::yield();}
 for(unsigned i=0;i<50;++i){pool.maintain();}
 assert(pool.owner_state(0)==(1|CaptureSpillPool::busy)&&pool.trimmed()==0);
 auto f=pool.failure_snapshot(2,8);assert(f.busy_banks==1);guarded.store(2);writer.join();
 std::uint64_t ticket=0,copied=0;std::size_t size=0;assert(pool.try_pop(0,ticket,&copied,8,size)==CaptureQueue::PopResult::popped&&copied==magic);assert(pool.reap_empty(0));pool.maintain();assert(pool.prepared_banks()==1&&pool.trimmed()==1);
 RemoveVectoredExceptionHandler(h);VirtualFree(memory,0,MEM_RELEASE);
}
static std::size_t commitment(){PROCESS_MEMORY_COUNTERS_EX info{};assert(GetProcessMemoryInfo(GetCurrentProcess(),reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&info),sizeof info));return info.PrivateUsage;}
#endif
static void growth_and_trim(){
 CaptureLanes::Config c=CaptureLanes::budget_config(512);c.spill.idle_milliseconds=0;CaptureLanes lanes(c);auto* lane=lanes.current();initialize(*lane,32768);
#ifdef _WIN32
 const auto before_commit=commitment();
#endif
 const auto before=lanes.memory_status();std::uint32_t peak=before.prepared_banks;
 for(unsigned i=1;i<=2400;++i){forbid_allocation=true;assert(lanes.publish(*lane,header(*lane,i)));forbid_allocation=false;
  if(i%20==0){lanes.maintain_spill();peak=(std::max)(peak,lanes.memory_status().prepared_banks);}}
 const auto grown=lanes.memory_status();assert(peak>4&&grown.maximum_storage_bytes<=grown.budget_bytes&&grown.prepared_storage_bytes<=grown.maximum_storage_bytes);
#ifdef _WIN32
 const auto grown_commit=commitment();assert(grown_commit>before_commit+16*1024*1024);
#endif
 for(unsigned i=1;i<=2400;++i)consume(lanes,i);
 for(unsigned i=0;i<256;++i){lanes.maintain_spill();}
 const auto after=lanes.memory_status();assert(after.prepared_banks==4&&after.free_banks==4&&after.trimmed>0&&after.prepared_storage_bytes==before.prepared_storage_bytes);
#ifdef _WIN32
 const auto after_commit=commitment();assert(grown_commit>after_commit+16*1024*1024);
#endif
 // Re-growth after retirement cannot use a stale queue pointer or old bytes.
 initialize(*lane,128);for(unsigned i=2401;i<2501;++i){forbid_allocation=true;assert(lanes.publish(*lane,header(*lane,i)));forbid_allocation=false;}for(unsigned i=2401;i<2501;++i)consume(lanes,i);assert(lanes.failure==0);
 std::cout<<"{\"kind\":\"growth_trim\",\"peak_banks\":"<<peak<<",\"trimmed\":"<<after.trimmed<<",\"final_prepared\":"<<after.prepared_storage_bytes<<"}\n";
}
int main(){geometry();bounds();partial_bank_revisit();preparation_backoff();
#ifdef _WIN32
 writer_excludes_trim();
#endif
 growth_and_trim();std::cout<<"PASS capture budget: geometry512/1024/2048/slot+byte/unprepared-fatal4/owned-revisit/backoff/busy-exclusion/grow-trim\n";return 0;
}
