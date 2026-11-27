#include "engine_capture_pipeline.h"
#include "owned_replay_view.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <vector>
#include <atomic>
#include <chrono>
#include <thread>
#include <new>
#include <cstdlib>
using namespace arkdps::capture;using Json=nlohmann::json;
static std::atomic<std::size_t> allocations{0};
[[gnu::noinline]] void* operator new(std::size_t n){allocations.fetch_add(1);if(auto p=std::malloc(n))return p;throw std::bad_alloc();}
[[gnu::noinline]] void operator delete(void* p) noexcept {std::free(p);}
[[gnu::noinline]] void operator delete(void* p,std::size_t) noexcept {std::free(p);}
[[gnu::noinline]] void* operator new[](std::size_t n){return ::operator new(n);}
[[gnu::noinline]] void operator delete[](void* p) noexcept {std::free(p);}
[[gnu::noinline]] void operator delete[](void* p,std::size_t) noexcept {std::free(p);}
struct Region {std::uintptr_t address;std::vector<std::byte> bytes;};
struct Memory {
 std::vector<Region> regions;std::uintptr_t clock=0,round=0;unsigned clock_reads=0,round_reads=0;bool change_clock=false,change_round=false;
 Region* at(std::uintptr_t a,std::size_t n){auto it=std::upper_bound(regions.begin(),regions.end(),a,[](auto address,const auto& r){return address<r.address;});if(it==regions.begin())return nullptr;--it;return a-it->address<=it->bytes.size()&&n<=it->bytes.size()-(a-it->address)?&*it:nullptr;}
 template<class T>void put(std::uintptr_t a,T v){auto* r=at(a,sizeof v);assert(r);std::memcpy(r->bytes.data()+a-r->address,&v,sizeof v);}
 static bool read(void* p,std::uintptr_t a,void* out,std::size_t n) noexcept {auto& m=*static_cast<Memory*>(p);
  if(a==m.clock&&n==12&&++m.clock_reads==2&&m.change_clock)m.put<std::uint32_t>(a,31);
  if(a==m.round&&n==4&&++m.round_reads==2&&m.change_round)m.put<std::uint32_t>(a,2);
  auto* r=m.at(a,n);if(!r)return false;std::memcpy(out,r->bytes.data()+a-r->address,n);return true;
 }
};
static void valid(const Snapshot& s){assert(s.error==Error::ok);for(unsigned i=0;i<s.field_count;++i)assert(s.fields[i].error==Error::ok);}
static std::size_t compact(const Snapshot& s,const RecordHeader& h){return sizeof h+h.parent_checkpoint_count*sizeof(ParentCheckpoint)+s.segment_count*sizeof(Segment)+s.field_count*sizeof(FieldState)+s.byte_count;}
static Json measure(const Snapshot& s,const RecordHeader& h){return Json{{"read_count",s.read_count},{"read_bytes",s.read_bytes},{"owned_bytes",s.byte_count},{"segments",s.segment_count},{"fields",s.field_count},{"compact_bytes",compact(s,h)},{"scope_revision",h.scope_revision},{"publication_revision",h.publication_revision}};}
static const std::byte* field_bytes(const Snapshot& s,unsigned id,std::size_t n){for(unsigned i=0;i<s.field_count;++i)if(s.fields[i].id==id&&s.fields[i].checkpoint==0){const auto& f=s.fields[i];assert(f.error==Error::ok&&f.final_segment<s.segment_count);const auto& segment=s.segments[f.final_segment];assert(segment.size>=n);return s.bytes.data()+segment.offset;}return nullptr;}
static void copy_used(const Snapshot& from,Snapshot& to){
 to.segment_count=from.segment_count;to.field_count=from.field_count;to.byte_count=from.byte_count;
 to.error=from.error;to.checkpoint=from.checkpoint;to.phase=from.phase;
 to.read_count=from.read_count;to.read_bytes=from.read_bytes;
 std::copy_n(from.segments.data(),from.segment_count,to.segments.data());
 std::copy_n(from.fields.data(),from.field_count,to.fields.data());
 std::copy_n(from.bytes.data(),from.byte_count,to.bytes.data());
}
static Json spill_pressure(const Snapshot& entry,const Snapshot& leave,const Snapshot& publication,unsigned bank_mib){
 // A transport-capacity workload reuses real captured immutable layouts, not
 // invented 128B records. It does not replay repeated fixture HP facts as stats.
 constexpr unsigned edges=30000; // 30k setters/s x two edges x 500ms.
 CaptureLanes manager({{2048,2*1024*1024},{32768,bank_mib*1024u*1024u}});
 auto buffer=std::make_unique<std::byte[]>(record_capacity);
 std::atomic<bool> launch{false},complete{false};std::atomic<unsigned> produced{0};
 std::uint64_t producer_us=0;
 const auto began=std::chrono::steady_clock::now();
 std::thread producer([&]{while(!launch.load(std::memory_order_acquire))std::this_thread::yield();
  const auto start=std::chrono::steady_clock::now();auto* lane=manager.current();assert(lane==manager.lanes[0].get());
  for(unsigned i=1;i<=edges;++i){const auto& source=i%2?entry:((i==2||i==edges)?publication:leave);copy_used(source,lane->snapshot);
   RecordHeader header;header.ticket=i;header.invocation=((i-1)/2)*2+1;header.lane=0;header.depth=1;
   header.thread_birth=lane->birth;header.phase=i%2?Phase::entry:Phase::leave;
   header.publication_captured=i==2||i==edges;
   if(!manager.publish(*lane,header))break;
   produced.store(i,std::memory_order_release);
  }
  producer_us=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-start).count();
  complete.store(true,std::memory_order_release);
 });
 std::thread consumer([&]{while(!launch.load(std::memory_order_acquire))std::this_thread::yield();
  std::this_thread::sleep_until(began+std::chrono::milliseconds(500));
  while(!complete.load(std::memory_order_acquire))std::this_thread::yield();
  for(unsigned i=1;i<=produced.load(std::memory_order_acquire);++i){const auto ready=manager.next_ready(i);assert(ready);
   std::uint64_t ticket=0;std::size_t size=0;Lane* owner=nullptr;
   assert(manager.try_pop(*ready,ticket,buffer.get(),record_capacity,size,owner)==CaptureQueue::PopResult::popped&&owner&&ticket==i);
   RecordHeader h;std::memcpy(&h,buffer.get(),sizeof h);const auto& source=i%2?entry:((i==2||i==edges)?publication:leave);
   assert(size==compact(source,h)&&h.segment_count==source.segment_count&&h.field_count==source.field_count&&h.byte_count==source.byte_count);
   owner->consumed.records.fetch_add(1,std::memory_order_release);owner->consumed.bytes.fetch_add(size,std::memory_order_release);
  }
 });
 const auto before=allocations.load();launch.store(true,std::memory_order_release);producer.join();consumer.join();
 const auto hot_allocations=allocations.load()-before;assert(hot_allocations==0);
 auto& lane=*manager.lanes[0];const auto metric=lane_metric_snapshot(lane,false,0);
 assert(metric.consistent&&metric.resident_records==0&&metric.submitted==produced.load()&&metric.consumed==produced.load());
 if(bank_mib==32){assert(produced==edges&&manager.failure==0);assert(metric.peak_bytes==15000*(compact(entry,RecordHeader{})+compact(leave,RecordHeader{}))+2*(compact(publication,RecordHeader{})-compact(leave,RecordHeader{})));}
 else assert(produced<edges&&manager.failure==static_cast<unsigned>(LaneFault::queue_full));
 return Json{{"bank_mib",bank_mib},{"offered_edges",edges},{"offered_setters_per_second_equivalent",30000},
  {"consumer_pause_ms_at_least",500},{"accepted_edges",produced.load()},{"failure_code",manager.failure.load()},
  {"peak_records",metric.peak_records},{"peak_serialized_bytes",metric.peak_bytes},{"producer_elapsed_us",producer_us},
  {"producer_met_500ms_offering_window",producer_us<=500000},{"hot_cpp_allocations",hot_allocations},
  {"transport_shape_only",true},{"host_claim_bursts",2},{"fifo_complete_for_accepted_prefix",true}};
}
int main(int argc,char** argv){assert(argc==2||argc==3);std::ifstream stream(argv[1]);Json input;stream>>input;
 assert(input["fixture_only"]==true&&input["not_runtime_readiness"]==true&&input["roster_members"]==41);
 Memory memory;for(const auto& r:input["ranges"]){Region value{plan_address(r["address"]),{}};for(const auto& b:r["bytes"])value.bytes.push_back(static_cast<std::byte>(b.get<unsigned>()));memory.regions.push_back(std::move(value));}
 memory.clock=plan_address(input["clock"]);memory.round=plan_address(input["scope_round"]);
 const auto enemy=plan_address(input["enemy"]),hp=plan_address(input["hp_address"]),maxhp=plan_address(input["maxhp_address"]);
 const auto key=std::stoull(input["maxhp_key"].get<std::string>());
 auto configs=std::make_unique<CaptureConfigurations>();
 auto plan=std::make_unique<CompiledPlan>(compile_plan(input["plan"],0x9000));configs->intern(*plan);
 auto dirty=std::make_unique<CompiledPlan>(compile_plan(input["dirty"],0x9100));configs->intern(*dirty);
 configs->publish(input["scope"],false);configs->publish(input["publication"],true);auto* pinned=configs->pin(0);assert(pinned&&plan->hp_normal&&plan->hp_defer_scope&&dirty->hp_dirty);
 auto entry_template=std::make_unique<Snapshot>(),heal_template=std::make_unique<Snapshot>(),publication_template=std::make_unique<Snapshot>();
 auto lane=std::make_unique<Lane>(CaptureQueue::Config{2,4096},1);lane->depth=1;lane->calls[0].id=10;
 Context context;context.args[0]=enemy;context.args_valid=1;context.entry_refs=context.args;context.entry_valid=1;context.globals=plan->globals;context.globals_valid=plan->globals_valid;
 unsigned attr_id=0;for(const auto& f:input["plan"]["fields"])if(f["name"]=="attributes.slot0")attr_id=f["field_id"];
 assert(attr_id);Json receipt{{"fixture_only",true},{"roster_members",input["roster_members"]},{"publication_guards",input["publication_guards"]},{"header_bytes",sizeof(RecordHeader)},{"branches",Json::object()}};
 auto entry=[&](){RecordHeader h;h.invocation=10;capture_scope_point(*plan,Phase::entry,context,pinned,Memory::read,&memory,lane->snapshot,h,lane.get());valid(lane->snapshot);assert(lane->calls[0].hp_before_valid&&lane->calls[0].hp_exact_enemy);assert(h.scope_unavailable);copy_used(lane->snapshot,*entry_template);return measure(lane->snapshot,h);};
 auto leave=[&](bool claim){RecordHeader h;h.invocation=10;h.publication_request=claim?1:0;capture_scope_point(*plan,Phase::leave,context,pinned,Memory::read,&memory,lane->snapshot,h,lane.get());valid(lane->snapshot);return std::make_pair(measure(lane->snapshot,h),h);};
 const auto opening=std::uint64_t(100)<<32;
 memory.put(hp,opening);receipt["branches"]["unchanged"]["entry"]=entry();auto nochange=leave(false);assert(nochange.second.scope_unavailable&&!field_bytes(lane->snapshot,attr_id,20));receipt["branches"]["unchanged"]["leave"]=nochange.first;
 memory.put(hp,opening);receipt["branches"]["heal"]["entry"]=entry();memory.put(hp,std::uint64_t(120)<<32);auto healed=leave(false);assert(!healed.second.scope_unavailable&&!healed.second.publication_captured);
 const auto* attr=field_bytes(lane->snapshot,attr_id,20);assert(attr);std::uint64_t a=0,b=0;std::memcpy(&a,attr,8);std::memcpy(&b,attr+8,8);assert((a^b)==(std::uint64_t(200)<<32)&&attr[16]==std::byte{1});receipt["branches"]["heal"]["first_max_hp"]=200;receipt["branches"]["heal"]["leave"]=healed.first;copy_used(lane->snapshot,*heal_template);
 memory.put(hp,opening);receipt["branches"]["nested_maxhp_dirty_hostclaim"]["entry"]=entry();
 lane->depth=2;lane->calls[1].id=11;Context dc=context;dc.args[1]=0;dc.args_valid=3;dc.entry_refs=dc.args;dc.entry_valid=3;
 RecordHeader dh;dh.invocation=11;capture_scope_point(*dirty,Phase::entry,dc,pinned,Memory::read,&memory,lane->snapshot,dh,lane.get());valid(lane->snapshot);assert(lane->calls[0].hp_nested_dirty);receipt["nested_dirty_entry"]=measure(lane->snapshot,dh);
 memory.put(maxhp,key^(std::uint64_t(240)<<32));dh={};dh.invocation=11;capture_scope_point(*dirty,Phase::leave,dc,pinned,Memory::read,&memory,lane->snapshot,dh,lane.get());valid(lane->snapshot);receipt["nested_dirty_leave"]=measure(lane->snapshot,dh);
 lane->depth=1;auto nested=leave(true);assert(!nested.second.scope_unavailable&&nested.second.publication_captured);attr=field_bytes(lane->snapshot,attr_id,20);assert(attr);std::memcpy(&a,attr,8);std::memcpy(&b,attr+8,8);assert((a^b)==(std::uint64_t(240)<<32));receipt["branches"]["nested_maxhp_dirty_hostclaim"]["leave"]=nested.first;receipt["branches"]["nested_maxhp_dirty_hostclaim"]["fresh_max_hp"]=240;copy_used(lane->snapshot,*publication_template);
 // Negative identities/clocks/scopes never inherit the no-change shortcut.
 memory.put(hp,opening);entry();memory.put<std::uint32_t>(enemy+0x18,4194366);auto rebound=leave(false);assert(!rebound.second.scope_unavailable&&field_bytes(lane->snapshot,attr_id,20));memory.put<std::uint32_t>(enemy+0x18,4194365);
 memory.clock_reads=0;memory.change_clock=true;RecordHeader negative;capture_scope_point(*plan,Phase::entry,context,pinned,Memory::read,&memory,lane->snapshot,negative,lane.get());assert(lane->snapshot.error==Error::changed_guard);memory.change_clock=false;memory.put<std::uint32_t>(memory.clock,30);
 memory.put(hp,opening);entry();memory.put(hp,std::uint64_t(120)<<32);memory.round_reads=0;memory.change_round=true;negative={};capture_scope_point(*plan,Phase::leave,context,pinned,Memory::read,&memory,lane->snapshot,negative,lane.get());assert(lane->snapshot.error==Error::changed_guard);
 receipt["negative_checks"]={"uid_change_disables_nochange","clock_changed_guard","scope_changed_guard"};
 if(argc==3&&std::string(argv[2])=="--view-initialize"){
  auto view=std::make_unique<OwnedReplayView>();receipt["view_initialize"]=Json::object();
  for(const auto& branch:std::array<std::pair<const char*,const Snapshot*>,3>{{{"entry",entry_template.get()},{"heal",heal_template.get()},{"publication",publication_template.get()}}}){
   const auto& snapshot=*branch.second;const unsigned repeats=std::string(branch.first)=="publication"?10:400;
   const auto before=allocations.load();const auto start=std::chrono::steady_clock::now();
   for(unsigned i=0;i<repeats;++i){
    assert(view->initialize(snapshot.segments.data(),snapshot.segment_count,snapshot.fields.data(),snapshot.field_count,
      snapshot.bytes.data(),snapshot.byte_count,static_cast<std::uint16_t>(configs->roles.size()),snapshot.error)==Error::ok);
    // Exercise the indexed point and exact field read from the real captured
    // snapshot; timing remains the same bounded operations before/after.
    const auto& f=snapshot.fields[0];const auto point=view->point_info(f.role,f.checkpoint);assert(point.field_count>0);
    if(f.final_segment!=0xffff){const auto& seg=snapshot.segments[f.final_segment];
     const auto value=view->read(f.role,f.checkpoint,f.id,seg.address,seg.size);assert(value.error==Error::ok);
     assert(!seg.size||std::memcmp(value.data,snapshot.bytes.data()+seg.offset,seg.size)==0);}
   }
   const auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
   const auto hot_allocations=allocations.load()-before;assert(hot_allocations==0);
   receipt["view_initialize"][branch.first]={{"repeats",repeats},{"total_ns",ns},{"fields",snapshot.field_count},
    {"segments",snapshot.segment_count},{"payload_bytes",snapshot.byte_count},{"allocations",hot_allocations}};
  }
 }else if(argc==3){assert(std::string(argv[2])=="--spill-pressure");receipt["spill_pressure"]={spill_pressure(*entry_template,*heal_template,*publication_template,24),spill_pressure(*entry_template,*heal_template,*publication_template,32)};}
 std::cout<<receipt.dump()<<'\n';
}
