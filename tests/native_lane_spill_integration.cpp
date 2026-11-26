#include "engine_capture_lane.h"
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>
using namespace arkdps::capture;
static std::array<std::byte,record_capacity> buffer;
static void initialize(Lane& lane){
 lane.snapshot.byte_count=128;lane.snapshot.segment_count=1;lane.snapshot.field_count=1;lane.snapshot.error=Error::ok;
 lane.snapshot.segments[0]={0x10000,1,1,0,128,0,0};
 lane.snapshot.fields[0]=FieldState{};lane.snapshot.fields[0].id=1;lane.snapshot.fields[0].role=1;lane.snapshot.fields[0].error=Error::ok;
 for(unsigned i=0;i<128;++i)lane.snapshot.bytes[i]=std::byte(i);
}
static RecordHeader header(Lane& lane,std::uint64_t ticket){
 RecordHeader h;h.ticket=ticket;h.invocation=((ticket-1)/2)*2+1;h.lane=0;h.depth=1;h.thread_birth=lane.birth.load();
 h.phase=ticket%2?Phase::entry:Phase::leave;return h;
}
static void consume(CaptureLanes& manager,std::uint64_t expected){
 const auto ready=manager.next_ready(expected);assert(ready&&ready->ticket==expected);
 std::uint64_t ticket=0;std::size_t size=0;Lane* owner=nullptr;
 assert(manager.try_pop(*ready,ticket,buffer.data(),buffer.size(),size,owner)==CaptureQueue::PopResult::popped&&owner&&ticket==expected);
 RecordHeader h;std::memcpy(&h,buffer.data(),sizeof h);assert(h.ticket==expected&&h.byte_count==128);
 const auto start=sizeof h+sizeof(Segment)+sizeof(FieldState);
 assert(size==start+128);for(unsigned i=0;i<128;++i)assert(buffer[start+i]==std::byte(i));
 owner->consumed.records.fetch_add(1,std::memory_order_release);owner->consumed.bytes.fetch_add(size,std::memory_order_release);
}
static void pressure(unsigned paused_ms,unsigned duration_ms){
 // The real manager, actual serialized 128B payloads and default 4x24MiB pool.
 // Private test queues retain the shipped 8192/8MiB capacity; no per-lane64MiB.
 CaptureLanes manager;
 const unsigned edges=60000*duration_ms/1000;
 std::atomic<bool> start{false},done{false};std::atomic<unsigned> produced{0},popped{0};
 const auto began=std::chrono::steady_clock::now()+std::chrono::milliseconds(20);
 std::thread producer([&]{while(!start.load(std::memory_order_acquire))std::this_thread::yield();
  auto* lane=manager.current();assert(lane==manager.lanes[0].get());initialize(*lane);
  for(unsigned i=1;i<=edges;++i){
   const auto when=began+std::chrono::nanoseconds((std::uint64_t(i-1)*1000000000)/60000);
   while(std::chrono::steady_clock::now()<when)YieldProcessor();
   assert(manager.current()==lane);assert(manager.publish(*lane,header(*lane,i)));produced.store(i,std::memory_order_release);
  }done.store(true,std::memory_order_release);
 });
 std::thread consumer([&]{while(!start.load(std::memory_order_acquire))std::this_thread::yield();
  std::this_thread::sleep_until(began+std::chrono::milliseconds(paused_ms));
  for(unsigned i=1;i<=edges;){if(manager.next_ready(i)){consume(manager,i);popped.store(i++);}else{assert(!manager.failure.load());std::this_thread::yield();}}
 });
 start.store(true,std::memory_order_release);producer.join();consumer.join();
 assert(done&&produced==edges&&popped==edges&&manager.failure.load()==0);
 auto& lane=*manager.lanes[0];const auto metric=lane_metric_snapshot(lane,false,0);
 assert(metric.consistent&&metric.resident_records==0&&metric.submitted==edges&&metric.peak_records>8192);
 assert(!manager.next_ready(edges+1));assert(manager.reclaim_terminated(0,true));
 assert(lane.generation==1&&lane.submitted.records==0&&lane.consumed.records==0);
 std::thread successor([&]{auto* same=manager.current();assert(same==&lane);initialize(*same);assert(manager.publish(*same,header(*same,edges+1)));});
 successor.join();assert(!manager.reclaim_terminated(0,true)); // Pending record pins the new birth.
 consume(manager,edges+1);const auto fresh=lane_metric_snapshot(lane,false,0);
 assert(fresh.generation==1&&fresh.submitted==1&&fresh.consumed==1&&fresh.thread_birth!=0);
 assert(manager.reclaim_terminated(0,true));
 std::cout<<"pressure pause="<<paused_ms<<"ms edges="<<edges<<" peak="<<metric.peak_records<<" bytes="<<metric.peak_bytes<<" fault=0\n";
}
int main(){
 static_assert(static_cast<unsigned>(LaneFault::queue_full)==4&&static_cast<unsigned>(LaneFault::publication_failure)==9);
 {
 auto malformed=std::make_unique<Lane>(CaptureQueue::Config{1,1024},1);std::atomic<unsigned> fault{0};
 malformed->snapshot.byte_count=max_bytes+1;
 assert(!CaptureLanes::publish_to_lane(*malformed,RecordHeader{},fault)&&fault==7);
 }
 pressure(300,600);pressure(500,800);
 {
 CaptureLanes manager({{1,1024},{2,1024}});auto* lane=manager.current();assert(lane);initialize(*lane);
 // One private record + four banks of two records. A full source is explicit4.
 for(unsigned i=1;i<=9;++i)assert(manager.publish(*lane,header(*lane,i)));
 assert(!manager.publish(*lane,header(*lane,10))&&manager.failure==4);
 manager.poison(LaneFault::publication_failure);assert(manager.failure==4); // First fault stays the cause.
 assert(!manager.next_ready(10));for(unsigned i=1;i<=9;++i)consume(manager,i);
 }
 {
 CaptureLanes manager({{1,1024},{1,1024}});
 // Sequential exited births are reusable only AFTER worker drain/replay proof.
 for(unsigned i=0;i<40;++i){std::thread thread([&]{auto* lane=manager.current();assert(lane==manager.lanes[0].get());});thread.join();
  assert(!manager.reclaim_terminated(0,false));assert(manager.reclaim_terminated(0,true));
 }
 assert(manager.lanes[0]->generation==40&&manager.lanes[0]->owner==0&&manager.failure==0);
 }
 {
 CaptureLanes manager({{1,1024},{1,1024}});manager.poison(LaneFault::publication_failure);manager.poison(LaneFault::queue_full);assert(manager.failure==9);
 }
 {
 CaptureLanes manager({{1,1024},{1,1024}});std::array<std::thread,16> threads;std::atomic<unsigned> entered{0};std::atomic<bool> release{false};
 for(auto& thread:threads)thread=std::thread([&]{assert(manager.current());entered.fetch_add(1);while(!release.load())std::this_thread::yield();});
 while(entered.load()!=16)std::this_thread::yield();
 assert(manager.current()==nullptr&&manager.failure==2); // True active17 is still unsupported.
 for(unsigned i=0;i<16;++i)assert(!manager.reclaim_terminated(i,true));
 release.store(true);for(auto& thread:threads)thread.join();for(unsigned i=0;i<16;++i)assert(manager.reclaim_terminated(i,true));
 }
 std::cout<<"lane spill integration: FIFO/60000edges/s/300+500ms/full4+9/sequential40/active17 passed\n";
}
