#include "owned_replay_view.h"
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
using namespace arkdps::capture;
static std::size_t allocations=0;
void* operator new(std::size_t n){++allocations;if(auto p=std::malloc(n))return p;throw std::bad_alloc();}
void operator delete(void* p) noexcept {std::free(p);}
void operator delete(void* p,std::size_t) noexcept {std::free(p);}
void* operator new[](std::size_t n){return ::operator new(n);}
void operator delete[](void* p) noexcept {std::free(p);}
void operator delete[](void* p,std::size_t) noexcept {std::free(p);}
struct Fixture {
 std::array<Segment,max_segments> segments{};std::array<FieldState,max_fields> fields{};
 std::array<std::byte,max_bytes> payload{};std::size_t ns=0,nf=0,nb=0;
 void reset(){ns=nf=nb=0;}
 FieldState& field(std::uint16_t id,std::uint16_t role,std::uint32_t cp,Error error=Error::ok,std::uint16_t guard=0,std::uintptr_t attempt=0,std::uint32_t size=0){
  auto& f=fields[nf++];f=FieldState{};f.id=id;f.role=role;f.checkpoint=cp;f.error=error;f.guard_id=guard;f.guard=guard?Guard::member:Guard::none;f.attempted_address=attempt;f.attempted_size=size;return f;
 }
 void segment(std::uint16_t id,std::uint16_t role,std::uint32_t cp,std::uintptr_t address,const void* bytes,std::uint32_t size,std::uint16_t guard=0){
  assert(ns<max_segments&&size<=max_bytes-nb);segments[ns++]={address,id,role,static_cast<std::uint32_t>(nb),size,guard,cp};
  if(size)std::memcpy(payload.data()+nb,bytes,size);
  nb+=size;
 }
 Error init(OwnedReplayView& view,Error header=Error::ok){return view.initialize(segments.data(),ns,fields.data(),nf,payload.data(),nb,1000,header);}
};
static Fixture f;static OwnedReplayView view;
static std::uint64_t number(OwnedReplayView::ReadResult result,std::size_t size){assert(result.error==Error::ok);std::uint64_t value=0;std::memcpy(&value,result.data,size);return value;}
int main(){
 const auto before=allocations;
 std::uint64_t original=0x12345678abcdef00ULL;
 f.field(1,1,0);f.segment(1,1,0,0x1000,&original,8);
 const auto second=original^123;f.field(32768,1,1);f.segment(32768,1,1,0x1000,&second,8);
 f.field(2,100,0);f.segment(2,100,0,0x8000,&original,8);
 assert(f.init(view)==Error::ok);original=0; // Game/original memory is no longer relevant.
 assert(number(view.read(1,0,1,0x1000,8),8)==0x12345678abcdef00ULL);
 assert(number(view.read(1,1,32768,0x1000,8),8)==second);
 assert(view.read(1,2,std::nullopt,0x1000,8).error==Error::missing_plan_field);
 assert(view.read(2,0,std::nullopt,0x1000,8).error==Error::missing_plan_field);
 assert(view.read(1,0,32768,0x1000,8).error==Error::missing_plan_field);
 assert(view.read(100,0,std::nullopt,0x8000,8).data==f.payload.data()+16); // Borrowed owner span, no copy.
 auto info=view.point_info(1,1);assert(info.error==Error::ok&&info.field_count==1&&info.single_field_id==32768&&info.single_field_error==Error::ok);
 // Serialized metadata can be unaligned; the payload still belongs to worker.
 static std::array<std::byte,3*sizeof(Segment)+1> unaligned_segments;
 static std::array<std::byte,3*sizeof(FieldState)+1> unaligned_fields;
 std::memcpy(unaligned_segments.data()+1,f.segments.data(),f.ns*sizeof(Segment));
 std::memcpy(unaligned_fields.data()+1,f.fields.data(),f.nf*sizeof(FieldState));
 assert(view.initialize(unaligned_segments.data()+1,f.ns,unaligned_fields.data()+1,f.nf,f.payload.data(),f.nb,100)==Error::ok);
 assert(number(view.read(1,0,std::nullopt,0x1000,4),4)==0xabcdef00);
 // Failed prefixes cannot lend successful bytes. An independent successful
 // field at the same point is still usable in auto-ID mode.
 f.reset();std::uint64_t bytes=42,other=99;
 f.field(10,2,3,Error::field_unreadable,0,0x9000,8);f.segment(10,2,3,0x2000,&bytes,8);
 f.field(11,2,3);f.segment(11,2,3,0x2000,&other,8);
 f.field(12,3,0,Error::field_unreadable,0,0xa000,8);
 f.field(13,4,0,Error::changed_guard);f.segment(13,4,0,0x4000,&bytes,8);
 assert(f.init(view)==Error::ok);
 assert(view.read(2,3,10,0x2000,8).error==Error::field_unreadable);
 assert(number(view.read(2,3,std::nullopt,0x2000,8),8)==99);
 assert(view.read(2,3,10,0x9004,4).error==Error::field_unreadable);
 assert(view.read(2,3,10,0x9004,8).error==Error::missing_plan_field);
 assert(view.read(2,3,10,0xb000,4).error==Error::missing_plan_field); // Explicit IDs do not bypass geometry.
 assert(view.read(3,0,std::nullopt,0xa000,8).error==Error::field_unreadable);
 assert(view.read(4,0,std::nullopt,0x4000,8).error==Error::changed_guard);
 assert(!OwnedReplayView::structural(Error::changed_guard));
 info=view.point_info(3,0);assert(info.field_count==1&&info.single_field_error==Error::field_unreadable); // Optional may return unavailable without reading.
 info=view.point_info(2,3);assert(info.field_count==2&&info.single_field_id==0);
 f.field(14,3,0,Error::class_mismatch,0,0xa000,8);assert(f.init(view)==Error::ok);
 assert(view.read(3,0,std::nullopt,0xa000,8).error==Error::missing_plan_field); // Ambiguous failed attempt.
 // Exact same-field overlapping equal data is legal. Conflicting bytes or
 // ambiguous auto guarded witnesses are structural gaps, not unknown values.
 f.reset();bytes=0x1122334455667788ULL;const std::uint32_t upper=0x11223344;
 f.field(20,5,0);f.segment(20,5,0,0x5000,&bytes,8);f.segment(20,5,0,0x5004,&upper,4);
 assert(f.init(view)==Error::ok&&number(view.read(5,0,20,0x5004,4),4)==upper);
 assert(number(view.read(5,0,std::nullopt,0x5004,4),4)==upper);
 f.fields[0].guard_id=1;f.fields[0].guard=Guard::member;f.segments[0].guard_id=f.segments[1].guard_id=1;
 assert(f.init(view)==Error::ok&&view.read(5,0,20,0x5004,4).error==Error::ok);
 assert(view.read(5,0,std::nullopt,0x5004,4).error==Error::ambiguous_read);
 f.field(21,5,0);f.segment(21,5,0,0x5004,&upper,4);
 assert(f.init(view)==Error::ok&&view.read(5,0,std::nullopt,0x5004,4).error==Error::ambiguous_read);
 f.nf=1;f.ns=2;f.nb=12;
 f.fields[0].guard_id=0;f.fields[0].guard=Guard::none;f.segments[0].guard_id=f.segments[1].guard_id=0;
 std::uint32_t conflict=7;std::memcpy(f.payload.data()+8,&conflict,4);
 assert(f.init(view)==Error::ok&&view.read(5,0,20,0x5004,4).error==Error::ambiguous_read);
 f.fields[0].guard_id=1;assert(f.init(view)==Error::invalid_plan);f.fields[0].guard_id=0;
 // All structural errors are admitted at the worker boundary, not softened
 // to a failed optional field. Failed initialize cannot leak an older index.
 assert(f.init(view,Error::bounds)==Error::bounds&&view.read(5,0,20,0x5000,4).error==Error::bounds);
 for(auto error:{Error::invalid_plan,Error::invalid_phase,Error::bounds,Error::missing_plan_field,Error::ambiguous_read}){
  f.fields[0].error=error;assert(f.init(view)==error);
 }
 f.fields[0].error=Error::ok;f.fields[1]=f.fields[0];f.nf=2;assert(f.init(view)==Error::invalid_plan);
 f.nf=1;f.fields[0].id=0;assert(f.init(view)==Error::invalid_plan);f.fields[0].id=20;
 f.segments[0].field_id=21;assert(f.init(view)==Error::missing_plan_field);f.segments[0].field_id=20;
 f.fields[0].guard_id=1;f.fields[0].guard=Guard::none;assert(f.init(view)==Error::invalid_plan);
 f.fields[0].guard_id=0;f.fields[0].final_segment=0;assert(f.init(view)==Error::invalid_plan);
 f.fields[0].final_segment=0xffff;
 f.segments[0].offset=UINT32_MAX;assert(f.init(view)==Error::invalid_plan);
 f.reset();std::uint8_t last=7;f.field(1,1,0);f.segment(1,1,0,UINTPTR_MAX,&last,1);
 assert(f.init(view)==Error::ok&&number(view.read(1,0,1,UINTPTR_MAX,1),1)==7);
 assert(view.read(1,0,1,UINTPTR_MAX,2).error==Error::bounds);
 assert(view.read(1,0,1,0,max_bytes+1).error==Error::bounds);
 f.segments[0].size=2;f.nb=2;assert(f.init(view)==Error::invalid_plan);
 // A larger multi-point index: 100 roles x 50 scalar reads, no VM objects,
 // BigInt parsing, ArrayBuffer copy or per-read allocation.
 f.reset();
 for(std::uint16_t role=1;role<=100;++role)for(std::uint16_t id=1;id<=50;++id){
  const auto value=(std::uint64_t(role)<<32)|id;f.field(id,role,role%3);f.segment(id,role,role%3,0x100000+role*4096+id*16,&value,8);
 }
 const auto start=std::chrono::steady_clock::now();assert(f.init(view)==Error::ok);
 const auto indexed=std::chrono::steady_clock::now();std::uint64_t checksum=0;
 for(std::uint16_t role=1;role<=100;++role)for(std::uint16_t id=1;id<=50;++id){
  const auto result=view.read(role,role%3,std::nullopt,0x100000+role*4096+id*16,8);
  const auto value=number(result,8);assert(value==((std::uint64_t(role)<<32)|id));checksum+=value;
 }
 const auto end=std::chrono::steady_clock::now();assert(allocations==before);
 std::cout<<"owned replay view: semantics/unaligned/poison/dynamic/guards/geometry/noheap passed roles100_reads5000="
  <<std::chrono::duration_cast<std::chrono::microseconds>(end-indexed).count()<<"us init="
  <<std::chrono::duration_cast<std::chrono::microseconds>(indexed-start).count()<<"us checksum="<<checksum<<"\n";
}

