// Counting-only actual capture pipeline over a frozen owned heap.
#include "engine_capture_pipeline.h"
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
using Json=nlohmann::json;
using namespace arkdps::capture;
struct Label {unsigned field,role,checkpoint;};
static std::vector<Label> labels;
void audit_enter(unsigned field,unsigned role,unsigned checkpoint){labels.push_back({field,role,checkpoint});}
void audit_leave(){labels.pop_back();}
static std::string hex(std::uintptr_t value){std::ostringstream s;s<<"0x"<<std::hex<<value;return s.str();}
static std::string category(unsigned field,unsigned checkpoint){return checkpoint?"parent":field>=32768?"cold_dynamic":field>=cold_field_base?"cold":field>=publication_field_base?"publication":field>=scope_field_base?"scope":field?"base":"abi";}
struct Reader {
 std::uintptr_t base=0;std::vector<char> heap;Json reads=Json::array();
 static bool read(void* self,std::uintptr_t address,void* out,std::size_t size)noexcept{
  auto& r=*static_cast<Reader*>(self);const auto label=labels.empty()?Label{}:labels.back();
  const bool stack=address>=0x7000&&address<0x7100;
  const bool valid=stack||(address>=r.base&&address-r.base<=r.heap.size()&&size<=r.heap.size()-(address-r.base));
  r.reads.push_back({{"order",r.reads.size()+1},{"field_id",label.field},{"role_id",label.role},{"checkpoint",label.checkpoint},{"category",category(label.field,label.checkpoint)},{"address",hex(address)},{"bytes",size},{"success",valid}});
  if(valid){if(stack)std::memset(out,0,size);else std::memcpy(out,r.heap.data()+address-r.base,size);}return valid;
 }
};
int main(int argc,char** argv){try{
 if(argc!=3)throw std::runtime_error("world.json heap.bin required");
 Json world;std::ifstream(argv[1])>>world;Reader reader;reader.base=world.at("heap").at("base").get<std::uintptr_t>();
 std::ifstream heap(argv[2],std::ios::binary);reader.heap.assign(std::istreambuf_iterator<char>(heap),{});
 if(reader.heap.size()!=world.at("heap").at("bytes").get<std::size_t>())throw std::runtime_error("owned heap length mismatch");
 Json cases=Json::array();
 for(const auto& hook:world.at("hooks"))for(const std::string scenario:{"ordinary","publication","cold","nested_parent"}){
  auto base=compile_plan(hook.at("plan"),plan_address(hook.at("plan").at("address")));CaptureConfigurations registry;registry.intern(base);
  registry.publish(world.at("scope"),false);registry.publish(world.at("publication"),true);
  const auto* config=registry.pin(world.at("scope").at("mode_epoch").get<std::uint64_t>());
  if(!config||!config->scope||!config->publication)throw std::runtime_error("graph config not pinned");
  Context context{};for(unsigned i=0;i<hook.at("args").size();++i)context.args[i]=plan_address(hook.at("args")[i]);
  context.args_valid=base.plan->arg_mask;context.entry_refs=context.args;context.entry_valid=context.args_valid;
  context.globals=base.globals;context.globals_valid=base.globals_valid;
  for(unsigned i=0;i<hook.at("parent_refs").size();++i){context.parent_refs[i]=plan_address(hook.at("parent_refs")[i]);context.parent_valid|=1u<<i;}
  context.return_value=plan_address(world.at("labels").at("modifier"));context.return_valid=true;
  auto lane=std::make_unique<Lane>();lane->depth=scenario=="nested_parent"?2:1;
  if(lane->depth==2){lane->calls[0].id=99;lane->calls[0].apply_modifier=true;lane->calls[0].context=context;lane->calls[0].context.args[1]=plan_address(world.at("labels").at("modifier"));lane->calls[0].context.args_valid|=2;}
  lane->calls[lane->depth-1].id=100;
  std::map<unsigned,std::string> names{{0,"abi.return_address"}};
  for(const auto& field:hook.at("plan").at("fields"))names[field.at("field_id").get<unsigned>()]=field.value("name",field.at("role").get<std::string>());
  auto graph_names=[&](const Json& fields,unsigned begin){for(const auto& f:fields)names[begin++]=f.value("name",f.at("role").get<std::string>());};
  graph_names(world.at("scope").at("fields"),scope_field_base);
  unsigned pub=publication_field_base;for(const auto& chunk:world.at("publication").at("chunks")){graph_names(chunk.at("fields"),pub);pub+=chunk.at("fields").size();}
  if(hook.at("plan").contains("cold_plan"))graph_names(hook.at("plan").at("cold_plan").at("fields"),cold_field_base);
  for(const auto phase:{Phase::entry,Phase::leave}){
   reader.reads=Json::array();labels.clear();
   RecordHeader header{};header.phase=phase;header.active=lane->depth;header.depth=lane->depth;header.invocation=100;
   if(scenario=="publication"&&phase==Phase::leave)header.publication_request=1;
   // Native entry also reads the return address before capture_scope_point.
   if(phase==Phase::entry){std::uint64_t ignored;Reader::read(&reader,0x7000,&ignored,8);for(unsigned i=4;i<base.plan->arg_count;++i)if(base.plan->arg_mask&(1u<<i))Reader::read(&reader,0x7000+40+(i-4)*8,&ignored,8);}
   capture_scope_point(base,phase,context,config,Reader::read,&reader,lane->snapshot,header,lane.get(),scenario=="cold");
   auto& snap=lane->snapshot;if(phase==Phase::entry)save_entry_roots(base,snap,context);
   Json buckets=Json::object(),fields=Json::array();
   for(auto& read:reader.reads){const auto id=read.at("field_id").get<unsigned>();read["field_name"]=names.count(id)?names.at(id):"projection.dynamic";if(!id&&read.at("address")!="0x7000")read["field_name"]="abi.stack_argument";
    const auto role=read.at("role_id").get<unsigned>();read["role"]=role?registry.roles.at(role-1):"abi";
    auto& b=buckets[read.at("category").get<std::string>()];if(b.is_null())b={{"reads",0},{"read_bytes",0},{"payload_bytes",0},{"fields",0},{"segments",0}};
    b["reads"]=b["reads"].get<unsigned>()+1;b["read_bytes"]=b["read_bytes"].get<unsigned>()+read.at("bytes").get<unsigned>();
   }
   auto bucket=[&](unsigned id,unsigned checkpoint)->Json&{auto& b=buckets[category(id,checkpoint)];if(b.is_null())b={{"reads",0},{"read_bytes",0},{"payload_bytes",0},{"fields",0},{"segments",0}};return b;};
   for(unsigned i=0;i<snap.field_count;++i){const auto& f=snap.fields[i];auto& b=bucket(f.id,f.checkpoint);b["fields"]=b["fields"].get<unsigned>()+1;fields.push_back({{"field_id",f.id},{"checkpoint",f.checkpoint},{"error",unsigned(f.error)},{"field_name",names.count(f.id)?names.at(f.id):"projection.dynamic"}});}
   for(unsigned i=0;i<snap.segment_count;++i){const auto& s=snap.segments[i];auto& b=bucket(s.field_id,s.checkpoint);b["segments"]=b["segments"].get<unsigned>()+1;b["payload_bytes"]=b["payload_bytes"].get<unsigned>()+s.size;}
   for(auto& b:buckets){b["field_state_bytes"]=b["fields"].get<unsigned>()*sizeof(FieldState);b["segment_bytes"]=b["segments"].get<unsigned>()*sizeof(Segment);b["packed_bytes"]=b["field_state_bytes"].get<unsigned>()+b["segment_bytes"].get<unsigned>()+b["payload_bytes"].get<unsigned>();}
   const auto bytes=sizeof(header)+header.parent_checkpoint_count*sizeof(ParentCheckpoint)+snap.field_count*sizeof(FieldState)+snap.segment_count*sizeof(Segment)+snap.byte_count;
   header.ticket=phase==Phase::entry?1:2;std::atomic<unsigned> failure{0};
   if(!CaptureLanes::publish_to_lane(*lane,header,failure))throw std::runtime_error("actual queue serialization failed");
   std::uint64_t queued_ticket=0;std::size_t queued_bytes=0;
   if(lane->queue.try_pop(queued_ticket,lane->scratch.data(),lane->scratch.size(),queued_bytes)!=CaptureQueue::PopResult::popped||queued_bytes!=bytes||queued_ticket!=header.ticket)throw std::runtime_error("queue bytes differ from ledger");
   cases.push_back({{"name",hook.at("name")},{"overload",hook.at("overload")},{"scenario",scenario},{"edge",phase==Phase::entry?"enter":"leave"},{"capture_error",unsigned(snap.error)},{"capture_reads",snap.read_count},{"capture_read_bytes",snap.read_bytes},{"native_reads_with_abi",reader.reads.size()},{"payload_bytes",snap.byte_count},{"fields",snap.field_count},{"segments",snap.segment_count},{"record_header_bytes",sizeof(header)},{"parent_checkpoint_bytes",header.parent_checkpoint_count*sizeof(ParentCheckpoint)},{"packed_enqueue_bytes",bytes},{"actual_queued_bytes",queued_bytes},{"scope_revision",header.scope_revision},{"cold_revision",header.cold_revision},{"publication_revision",header.publication_revision},{"buckets",buckets},{"field_states",fields},{"reads",reader.reads}});
  }
 }
 std::cout<<Json{{"schema",1},{"counting_only",true},{"backend_owned_reads_measured",false},{"sizeof",{{"RecordHeader",sizeof(RecordHeader)},{"FieldState",sizeof(FieldState)},{"Segment",sizeof(Segment)},{"ParentCheckpoint",sizeof(ParentCheckpoint)}}},{"cases",cases}}.dump(2)<<'\n';
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
