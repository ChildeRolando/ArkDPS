#include "engine_terminal_fence.h"
#include <cassert>
#include <iostream>
#include <memory>
using namespace arkdps::capture;
struct Fixture {
 alignas(8) std::array<std::byte,0x230> controller{};
 alignas(8) std::array<std::byte,0x38> logger{},native{};
 alignas(8) std::array<std::byte,0x20> level{};
 std::uint64_t stats=0x5555;std::uintptr_t singleton=0;
 struct String {std::uint64_t klass=0x3000,monitor=0;std::int32_t length=200;char16_t text[200];} stage;
 std::array<std::byte,12> clock{};bool unreadable=false;
 Fixture(){put(controller.data(),0x1111);put(controller.data()+0x10,reinterpret_cast<std::uintptr_t>(native.data()));put(controller.data()+0xe0,reinterpret_cast<std::uintptr_t>(logger.data()));put(controller.data()+0x160,reinterpret_cast<std::uintptr_t>(level.data()));put(logger.data(),0x2222);put(logger.data()+0x18,reinterpret_cast<std::uintptr_t>(controller.data()));put(logger.data()+0x30,reinterpret_cast<std::uintptr_t>(&stats));put(native.data()+0x28,reinterpret_cast<std::uintptr_t>(controller.data()));put(level.data(),0x4444);put(level.data()+0x18,reinterpret_cast<std::uintptr_t>(&stage));singleton=reinterpret_cast<std::uintptr_t>(controller.data());for(auto& c:stage.text)c=u'A';}
 static void put(std::byte* p,std::uintptr_t value){std::memcpy(p,&value,8);}
 static bool read(void* p,std::uintptr_t address,void* out,std::size_t size) noexcept {if(static_cast<Fixture*>(p)->unreadable)return false;std::memcpy(out,reinterpret_cast<void*>(address),size);return true;}
};
int main(){
 Fixture f;auto plan=std::make_unique<HookCapturePlan>();plan->schema_version=1;plan->max_reads=4096;plan->max_bytes=65536;
 const auto add=[&](unsigned id,const void* address,unsigned bytes,Kind kind=Kind::bytes){auto& field=plan->fields[plan->field_count++];field.id=id;field.role=1;field.phase=Phase::leave;field.root=Root::absolute;field.absolute_address=reinterpret_cast<std::uintptr_t>(address);field.bytes=bytes;field.kind=kind;if(kind==Kind::utf16){field.expected_class=0x3000;field.max_units=200;}};
 add(1,&f.singleton,8);add(2,f.clock.data(),12);
 const std::array<const void*,14> addresses{{f.controller.data(),f.controller.data()+0x228,f.controller.data()+0x10,f.native.data()+0x28,f.controller.data()+0xe0,f.logger.data()+0x18,f.logger.data()+0x30,f.controller.data()+0x160,f.logger.data(),f.level.data(),&f.stats,f.logger.data()+0x30,f.level.data()+0x18,&f.stage}};
 const std::array<unsigned,14> sizes{{24,4,8,8,8,8,8,8,56,8,8,8,8,0}};
 for(unsigned i=0;i<14;++i)add(100+i,addresses[i],sizes[i],i==13?Kind::utf16:Kind::bytes);
 assert(validate_plan(*plan)==Error::ok);auto snapshot=std::make_unique<Snapshot>();Context ctx{};
 assert(capture(*plan,Phase::leave,ctx,Fixture::read,&f,*snapshot)==Error::ok);
 const auto build=[&](TerminalFence& fence){
  fence.clear();bool complete=terminal_field_witnesses(fence,snapshot->segments.data(),snapshot->segment_count,snapshot->fields.data(),snapshot->field_count,snapshot->bytes.data(),snapshot->byte_count,1)
   &&terminal_field_witnesses(fence,snapshot->segments.data(),snapshot->segment_count,snapshot->fields.data(),snapshot->field_count,snapshot->bytes.data(),snapshot->byte_count,2);
  const auto& singleton=snapshot->segments[snapshot->fields[0].final_segment];complete=fence.certify_singleton(snapshot->bytes.data()+singleton.offset,singleton.size)&&complete;
  bool retained=true;
  for(unsigned i=0;i<14;++i)retained=terminal_field_witnesses(fence,snapshot->segments.data(),snapshot->segment_count,snapshot->fields.data(),snapshot->field_count,snapshot->bytes.data(),snapshot->byte_count,100+i,i==0?TerminalFieldSlice::controller:i==8?TerminalFieldSlice::logger:TerminalFieldSlice::all)&&retained;
  fence.retained_complete=retained;fence.verified=complete&&retained;fence.ticket=101;fence.activity=202;
 };
 TerminalFence fence;build(fence);assert(!fence.singleton_null&&fence.retained_complete&&fence.count<=64&&fence.accepts(0,101,202,Fixture::read,&f));
 const std::array<std::byte*,11> mutate{{f.controller.data(),f.controller.data()+0x10,f.native.data()+0x28,f.controller.data()+0xe0,f.logger.data()+0x18,f.logger.data()+0x30,f.controller.data()+0x160,f.level.data(),reinterpret_cast<std::byte*>(&f.stats),f.level.data()+0x18,reinterpret_cast<std::byte*>(f.stage.text)}};
 for(auto* address:mutate){const auto before=*address;*address^=std::byte{1};assert(!fence.accepts(0,101,202,Fixture::read,&f));*address=before;assert(fence.accepts(0,101,202,Fixture::read,&f));}
 f.controller[8]^=std::byte{1};f.logger[8]^=std::byte{1};assert(fence.accepts(0,101,202,Fixture::read,&f)); // Monitor/padding is outside the source's identity veto.
 f.unreadable=true;assert(!fence.accepts(0,101,202,Fixture::read,&f));f.unreadable=false;
 snapshot->fields[14].error=Error::field_unreadable;TerminalFence partial;build(partial);assert(!partial.retained_complete&&!partial.accepts(0,101,202,Fixture::read,&f));
 std::cout<<"PASS retained terminal: 200-char stage within64 witnesses, 11 independent same-clock mutations rejected, unreadable/partial refused\n";
}
