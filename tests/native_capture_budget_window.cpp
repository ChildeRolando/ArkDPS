// Queue-only supplied-rate storage experiment. It does NOT model JS service.
#define main capture_budget_contract_main
#include "native_capture_budget_test.cpp"
#undef main
#include <iomanip>
static void full_capacity(unsigned mib){
 auto c=CaptureLanes::budget_config(mib);c.spill.idle_milliseconds=0;CaptureLanes lanes(c);auto* lane=lanes.current();initialize(*lane,max_bytes);
 std::uint64_t accepted=0;
 for(;;){forbid_allocation=true;const bool ok=lanes.publish(*lane,header(*lane,accepted+1));forbid_allocation=false;
  if(!ok)break;
  ++accepted;lanes.maintain_spill();assert(accepted<100000);
 }
 const auto at_full=lanes.memory_status();const auto fault=lanes.queue_failure_snapshot();
 assert(at_full.prepared_banks==at_full.maximum_banks&&lanes.failure==4&&fault.available&&fault.ticket==accepted+1);
 assert(fault.spill.unprepared_banks==0&&fault.spill.byte_rejections==at_full.maximum_banks&&fault.spill.slot_rejections==0);
 assert(at_full.prepared_storage_bytes==at_full.maximum_storage_bytes&&at_full.maximum_storage_bytes<=at_full.budget_bytes);
 for(std::uint64_t i=1;i<=accepted;++i)consume(lanes,i);
 for(unsigned i=0;i<512;++i){lanes.maintain_spill();}
 const auto after=lanes.memory_status();assert(after.prepared_banks==4&&after.free_banks==4&&after.trimmed==at_full.maximum_banks-4&&lanes.failure==4);
 std::cout<<"{\"kind\":\"full_capacity\",\"mib\":"<<mib<<",\"accepted\":"<<accepted<<",\"attempted_bytes\":"<<fault.attempted_bytes<<",\"banks\":"<<at_full.maximum_banks<<",\"maximum_storage\":"<<at_full.maximum_storage_bytes<<",\"maximum_arena\":"<<at_full.arena_capacity<<",\"slot_capacity\":"<<at_full.slot_capacity<<",\"full_byte_rejections\":"<<fault.spill.byte_rejections<<",\"unprepared\":0,\"failure\":4,\"trimmed\":"<<after.trimmed<<",\"final_prepared\":"<<after.prepared_storage_bytes<<"}"<<std::endl;
}
static void window(unsigned mib,unsigned duration_ms,unsigned burst_ms,unsigned arrival,unsigned service,unsigned tail,unsigned pause_ms,unsigned payload_bytes){
 assert(duration_ms<=200000&&duration_ms>=1000&&burst_ms<duration_ms&&burst_ms>0&&arrival>service&&service>tail&&tail>0&&arrival<=200000&&pause_ms<=500&&payload_bytes<=1024&&payload_bytes>0);
 auto c=CaptureLanes::budget_config(mib);CaptureLanes lanes(c);const auto before=lanes.memory_status();
 const auto burst_edges=std::uint64_t(burst_ms)*arrival/1000,tail_edges=std::uint64_t(duration_ms-burst_ms)*tail/1000,total=burst_edges+tail_edges;
 std::atomic<std::uint64_t> produced{0},consumed{0};std::atomic<bool> failed{false};std::uint32_t peak_banks=4;std::uint64_t peak_records=0,peak_bytes=0;
 double catchup_ms=-1,pause_actual_ms=0,producer_first_ms=-1,producer_burst_last_ms=-1,producer_last_ms=-1,consumer_first_ms=-1,consumer_last_ms=-1;
 std::uint64_t consumed_at_burst=0;double observed_burst_end_ms=-1;
 bool paused=false;const auto start=std::chrono::steady_clock::now()+std::chrono::milliseconds(100),burst_end=start+std::chrono::milliseconds(burst_ms),finish=start+std::chrono::milliseconds(duration_ms);
 std::thread producer([&]{auto* lane=lanes.current();assert(lane==lanes.lanes[0].get());initialize(*lane,payload_bytes);
  for(std::uint64_t i=1;i<=total;++i){const auto when=i<=burst_edges?start+std::chrono::nanoseconds((i-1)*1000000000/arrival):burst_end+std::chrono::nanoseconds((i-burst_edges-1)*1000000000/tail);
   while(std::chrono::steady_clock::now()<when){std::this_thread::yield();}
   forbid_allocation=true;const bool ok=lanes.publish(*lane,header(*lane,i));forbid_allocation=false;
   if(!ok){failed.store(true);break;}produced.store(i,std::memory_order_release);
   if(i==1)producer_first_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
   if(i==burst_edges)producer_burst_last_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
   if(i==total)producer_last_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
  }
 });
 std::thread consumer([&]{auto due=start,next_maintenance=start,next_report=start+std::chrono::seconds(1);const auto pause_at=start+std::chrono::milliseconds(burst_ms/2);
  while(consumed.load()<total&&!failed.load()){
   const auto now=std::chrono::steady_clock::now();
   if(observed_burst_end_ms<0&&now>=burst_end){consumed_at_burst=consumed.load();observed_burst_end_ms=std::chrono::duration<double,std::milli>(now-start).count();}
   if(!paused&&pause_ms&&now>=pause_at){const auto begin=std::chrono::steady_clock::now();std::this_thread::sleep_for(std::chrono::milliseconds(pause_ms));pause_actual_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count();paused=true;due+=std::chrono::milliseconds(pause_ms);continue;}
   if(now>=next_maintenance){lanes.maintain_spill();const auto state=lanes.memory_status();peak_banks=(std::max)(peak_banks,state.prepared_banks);const auto metric=lane_metric_snapshot(*lanes.lanes[0],false,0);peak_records=(std::max)(peak_records,metric.peak_records);peak_bytes=(std::max)(peak_bytes,metric.peak_bytes);next_maintenance=now+std::chrono::milliseconds(5);}
   if(now>=next_report){const auto state=lanes.memory_status();std::cout<<"{\"kind\":\"window_progress\",\"elapsed_ms\":"<<std::chrono::duration<double,std::milli>(now-start).count()<<",\"produced\":"<<produced.load()<<",\"consumed\":"<<consumed.load()<<",\"prepared_banks\":"<<state.prepared_banks<<"}"<<std::endl;next_report=now+std::chrono::seconds(1);}
   const auto expected=consumed.load()+1;
   if(expected>produced.load(std::memory_order_acquire)){due=now;std::this_thread::yield();continue;}
   if(now<due){std::this_thread::yield();continue;}
   if(lanes.next_ready(expected)){consume(lanes,expected);consumed.store(expected,std::memory_order_release);due+=std::chrono::nanoseconds(1000000000/service);
    if(expected==1)consumer_first_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    if(expected==total)consumer_last_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    if(catchup_ms<0&&now>=burst_end&&expected==produced.load(std::memory_order_acquire)
       &&lane_metric_snapshot(*lanes.lanes[0],false,0).resident_records==0)
        catchup_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-burst_end).count();
   }else std::this_thread::yield();
   if(now>finish+std::chrono::seconds(10)){failed.store(true);break;}
  }
 });
 producer.join();consumer.join();while(std::chrono::steady_clock::now()<finish){lanes.maintain_spill();std::this_thread::sleep_for(std::chrono::milliseconds(5));}
 const auto elapsed_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();const auto after=lanes.memory_status();const auto last=lane_metric_snapshot(*lanes.lanes[0],false,0);
 const double measured_arrival=double(burst_edges-1)*1000/(producer_burst_last_ms-producer_first_ms);
 const double measured_service=double(consumed_at_burst)*1000/(observed_burst_end_ms-consumer_first_ms);
 std::cout<<"{\"kind\":\"window_result\",\"scope\":\"actual queue storage, synthetic supplied rates; excludes capture RPM and JS\",\"mib\":"<<mib<<",\"requested_window_ms\":"<<duration_ms<<",\"actual_window_ms\":"<<elapsed_ms<<",\"burst_ms\":"<<burst_ms<<",\"burst_edges\":"<<burst_edges<<",\"tail_edges\":"<<tail_edges<<",\"arrival_edges_s\":"<<arrival<<",\"service_edges_s\":"<<service<<",\"tail_edges_s\":"<<tail<<",\"producer_first_ms\":"<<producer_first_ms<<",\"producer_burst_last_ms\":"<<producer_burst_last_ms<<",\"producer_last_ms\":"<<producer_last_ms<<",\"consumer_first_ms\":"<<consumer_first_ms<<",\"consumer_last_ms\":"<<consumer_last_ms<<",\"payload_bytes\":"<<payload_bytes<<",\"packed_record_bytes\":"<<(sizeof(RecordHeader)+sizeof(Segment)+sizeof(FieldState)+payload_bytes)<<",\"actual_offered_burst_edges_s\":"<<measured_arrival<<",\"actual_consumed_burst_edges_s\":"<<measured_service<<",\"consumed_at_burst_end\":"<<consumed_at_burst<<",\"produced\":"<<produced.load()<<",\"consumed\":"<<consumed.load()<<",\"failure\":"<<lanes.failure.load()<<",\"peak_records\":"<<peak_records<<",\"peak_bytes\":"<<peak_bytes<<",\"peak_banks\":"<<peak_banks<<",\"catchup_after_burst_ms\":"<<catchup_ms<<",\"pause_requested_ms\":"<<pause_ms<<",\"pause_actual_ms\":"<<pause_actual_ms<<",\"initial_prepared_storage\":"<<before.prepared_storage_bytes<<",\"final_prepared_storage\":"<<after.prepared_storage_bytes<<",\"trimmed\":"<<after.trimmed<<",\"resident_records\":"<<last.resident_records.value_or(UINT64_MAX)<<"}"<<std::endl;
 assert(!failed&&lanes.failure==0&&produced==total&&consumed==total&&last.resident_records==0&&last.resident_bytes==0&&peak_records>0&&catchup_ms>=0&&elapsed_ms>=duration_ms);
 assert(!pause_ms||(paused&&pause_actual_ms>=pause_ms));
 if(duration_ms-burst_ms>3000)assert(after.prepared_banks==4&&after.prepared_storage_bytes==before.prepared_storage_bytes);
}
int main(int argc,char** argv){
 std::cout<<std::setprecision(12);
 if(argc==2&&std::string(argv[1])=="--capacity"){for(auto mib:{512u,1024u,2048u})full_capacity(mib);return 0;}
 assert(argc==10);window(static_cast<unsigned>(std::stoul(argv[1])),static_cast<unsigned>(std::stoul(argv[2])),static_cast<unsigned>(std::stoul(argv[3])),static_cast<unsigned>(std::stoul(argv[4])),static_cast<unsigned>(std::stoul(argv[5])),static_cast<unsigned>(std::stoul(argv[6])),static_cast<unsigned>(std::stoul(argv[7])),static_cast<unsigned>(std::stoul(argv[8])));assert(std::string(argv[9])=="owned-storage-only");return 0;
}
