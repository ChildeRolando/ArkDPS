#include "engine_capture_lane.h"
#include <cassert>
#include <atomic>
#include <thread>
#include <iostream>
using namespace arkdps::capture;
int main(){
 static_assert(sizeof(RecordHeader)==312);
 std::atomic<unsigned> active{1},stage{0};std::atomic<std::uint64_t> activity{1},reserved{2},hp{10};
 RecordHeader a;a.depth=1;a.phase=Phase::leave;std::uint64_t observed=99;
 std::thread producer_a([&]{
  a.activity_begin=activity.load();a.ticket=reserved.fetch_add(1)+1;assert(a.ticket==3);stage=1;
  while(stage.load()!=2)std::this_thread::yield();
  a.active=active.load();observed=hp.load();a.activity=activity.fetch_add(1)+1;a.active_after=active.fetch_sub(1)-1;
  a.capture_concurrent=leave_capture_concurrent(a.active,a.depth,a.activity_begin,a.activity,a.active_after);
 });
 std::thread producer_b([&]{while(stage.load()!=1)std::this_thread::yield();
  active.fetch_add(1);activity.fetch_add(1);assert(reserved.fetch_add(1)+1==4);
  hp=0;assert(reserved.fetch_add(1)+1==5);activity.fetch_add(1);active.fetch_sub(1);stage=2;
 });
 producer_a.join();producer_b.join();assert(observed==0&&a.capture_concurrent&&a.activity_begin==1&&a.activity==4);
 assert(!entry_capture_concurrent(2,2,7,7,2)); // Same-thread child entry.
 assert(!leave_capture_concurrent(2,2,7,8,1)); // Same-thread child leave.
 assert(!leave_capture_concurrent(1,1,8,9,0)); // Parent after its completed nested child.
 assert(entry_capture_concurrent(2,1,7,7,2));assert(entry_capture_concurrent(1,1,7,8,1));
 assert(entry_capture_concurrent(1,1,7,7,2));assert(leave_capture_concurrent(2,1,7,8,1));
 auto lane=std::make_unique<Lane>(CaptureQueue::Config{1,1024},1);Context ctx{};capture_begin(lane->snapshot,Phase::entry,ctx,128,8);capture_finalize(lane->snapshot);
 std::atomic<unsigned> failure{0};RecordHeader header;header.ticket=1;header.capture_concurrent=true;
 assert(CaptureLanes::publish_to_lane(*lane,header,failure));assert(lane->submitted.uncertain_records==1);
 assert(!CaptureLanes::publish_to_lane(*lane,header,failure));assert(lane->submitted.uncertain_records==1&&failure==4);
 CaptureLanes::Config config;config.lane_queue={4,1024};config.spill={4,1024};auto manager=std::make_unique<CaptureLanes>(config);
 unsigned index=99;
 std::thread owner([&]{auto* owned=manager->current();assert(owned);for(unsigned i=0;i<lane_count;++i)if(manager->lanes[i].get()==owned)index=i;
  capture_begin(owned->snapshot,Phase::entry,ctx,128,8);capture_finalize(owned->snapshot);RecordHeader raw;raw.ticket=1;raw.lane=index;raw.thread_birth=owned->birth;raw.capture_concurrent=true;assert(manager->publish(*owned,raw));
 });owner.join();auto ready=manager->next_ready(1);assert(ready);
 std::array<std::byte,1024> bytes;std::uint64_t ticket=0;std::size_t size=0;Lane* owned=nullptr;
 assert(manager->try_pop(*ready,ticket,bytes.data(),bytes.size(),size,owned)==CaptureQueue::PopResult::popped&&owned);
 owned->consumed.records=1;owned->consumed.bytes=size;
 assert(lane_metric_snapshot(*owned,false,0).uncertain_records==1);
 assert(manager->reclaim_terminated(index,true));assert(manager->lanes[index]->submitted.uncertain_records==0);
 std::cout<<"PASS concurrent capture: reserve-before-preempt L3/E4/L5 uncertainty, same-thread nesting healthy, queued-only counter and birth reset\n";
}
