#include "engine_capture_pipeline.h"
#include "owned_replay_view.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;
using Json=nlohmann::json;
struct OwnedMemory {std::uint64_t word=42;unsigned reads=0;bool mutate=false;};
bool read(void* state,std::uintptr_t address,void* out,std::size_t size) noexcept {
 auto& m=*static_cast<OwnedMemory*>(state);++m.reads;
 if(m.mutate&&m.reads==2)++m.word;
 if(address!=reinterpret_cast<std::uintptr_t>(&m.word)||size!=8)return false;
 std::memcpy(out,&m.word,8);return true;
}
int main(){
 OwnedMemory memory;const auto address=std::to_string(reinterpret_cast<std::uintptr_t>(&memory.word));
 auto field=[&](unsigned id,const char* role,const char* phase,const char* side="none",unsigned guard=0){return Json{
  {"field_id",id},{"role",role},{"phase",phase},{"root","absolute"},{"root_index",0},{"absolute_address",address},
  {"pointer_offsets",Json::array()},{"offset",0},{"bytes",8},{"kind","bytes"},{"guard_id",guard},{"guard_side",side},{"required",true}};};
 Json cold{{"version",1},{"max_bytes",1024},{"max_reads",32},{"fields",Json::array({field(1,"cold.root_open","checkpoint","before",1),field(2,"cold.body","checkpoint"),field(3,"cold.root_close","checkpoint","after",1)})}};
 Json raw{{"schema_version",1},{"complete",true},{"profile","owned"},{"name","cold-retry"},{"address","1"},{"abi","win64-gp"},{"arg_count",0},{"arg_mask",0},{"globals",Json::array()},{"max_bytes",1024},{"max_reads",32},{"required_projections",Json::array()},{"fields",Json::array()},{"cold_plan",cold}};
 auto plan=std::make_unique<CompiledPlan>(compile_plan(raw,1));auto configs=std::make_unique<CaptureConfigurations>();configs->intern(*plan);
 Json scope{{"version",1},{"revision",1},{"mode_epoch",0},{"raw_collection","all"},{"available",true},{"body_insertion_index",1},{"fields",Json::array({field(1,"scope.open","checkpoint","before",1),field(2,"scope.close","checkpoint","after",1)})}};
 configs->publish(scope,false);auto snapshot=std::make_unique<Snapshot>();auto view=std::make_unique<OwnedReplayView>();Context context;
 std::atomic<std::uint64_t> required{0};
 const std::uint64_t already_posted_request=12; // New host deadline before old replay failure.
 require_cold_publication(required,10);
 assert(cold_publication_required(already_posted_request,required.load()));
 // Host request11/12 already exists when the failed request10 replays.
 // Both must capture actual cold roots/body; changing requests proves nothing.
 for(auto request:{11u,12u}){
  RecordHeader h;h.publication_request=request;h.ticket=100+request;
  memory.word=request;memory.reads=0;
  capture_scope_point(*plan,Phase::leave,context,configs->pin(0),read,&memory,*snapshot,h,nullptr,
    cold_publication_required(request,required.load()));
  assert(snapshot->error==Error::ok&&h.cold_revision==1&&snapshot->field_count==3);
  assert(view->initialize(snapshot->segments.data(),snapshot->segment_count,snapshot->fields.data(),snapshot->field_count,
    snapshot->bytes.data(),snapshot->byte_count,static_cast<std::uint16_t>(configs->roles.size()))==Error::ok);
  for(unsigned i=0;i<snapshot->field_count;++i){const auto& f=snapshot->fields[i];const auto& segment=snapshot->segments[f.final_segment];
   const auto value=view->read(f.role,f.checkpoint,f.id,segment.address,8);assert(value.error==Error::ok);
   std::uint64_t actual=0;std::memcpy(&actual,value.data,8);assert(actual==request);}
 }
 require_cold_publication(required,12);require_cold_publication(required,10);assert(required==12);
 assert(!clear_cold_publication(required,11,200,100,0,0,true)); // Older successful request.
 assert(!clear_cold_publication(required,12,99,100,0,0,true)); // Not fresh enough for host mark.
 assert(!clear_cold_publication(required,12,200,100,0,1,true)); // UI mode changed.
 assert(!clear_cold_publication(required,12,200,100,0,0,false)); // Not a verified closed prefix.
 assert(required==12);
 memory.reads=0;memory.mutate=true;RecordHeader failed;failed.publication_request=13;
 capture_scope_point(*plan,Phase::leave,context,configs->pin(0),read,&memory,*snapshot,failed,nullptr,true);
 assert(snapshot->error==Error::changed_guard);require_cold_publication(required,13);assert(required==13);
 assert(!clear_cold_publication(required,12,200,100,0,0,true));
 assert(clear_cold_publication(required,13,200,100,1,1,true)&&required==0);
 // A successfully repaired graph permits normal warm capture again.
 memory.mutate=false;memory.reads=0;RecordHeader warm;warm.publication_request=14;
 capture_scope_point(*plan,Phase::leave,context,configs->pin(0),read,&memory,*snapshot,warm,nullptr,cold_publication_required(14,required.load()));
 assert(snapshot->error==Error::ok&&warm.cold_revision==0&&snapshot->field_count==2);
 assert(publication_claimable(14,0,1)&&publication_claimable(14,1,1));
 assert(!publication_claimable(14,2,1)&&!publication_claimable(14,0,2));
 std::cout<<"PASS actual cold request handoff: delayed failure, fresh/mode/closed gates, owned roots and bounded claims\n";
}
