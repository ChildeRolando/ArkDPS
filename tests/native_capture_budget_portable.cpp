// Portable queue ownership/preparation contract; no Windows Lane identity API.
#include "capture_spill_pool.h"
#include <cassert>
#include <cstdint>
#include <iostream>
using namespace arkdps::capture;
int main(){
 CaptureQueue exact({2,10});std::uint64_t value=11,ticket=0;std::size_t size=0;
 assert(exact.push(1,&value,8));const auto occupancy=exact.producer_occupancy();assert(occupancy.used_slots==1&&occupancy.used_bytes==8&&!exact.push(2,&value,8));
 assert(exact.try_pop(ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==1);
 assert(CaptureQueue::storage_bytes({2,10},true)==8192);
 CaptureSpillPool pool({2,64,6,true,2,2,0});CaptureSpillPool::ProducerCursor cursor;
 for(std::uint64_t i=1;i<=12;++i){assert(pool.try_push(1,i,&value,8,&cursor));pool.maintain();}
 assert(pool.prepared_banks()==6&&!pool.try_push(1,13,&value,8,&cursor));auto f=pool.failure_snapshot(1,8);assert(f.slot_rejections==6&&f.unprepared_banks==0);
 for(std::uint64_t expected=1;expected<=12;++expected){std::size_t bank=128;for(std::size_t i=0;i<6;++i)if(pool.head_ticket(i)==expected)bank=i;assert(bank<6);assert(pool.try_pop(bank,ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==expected);pool.reap_empty(bank);}
 for(unsigned i=0;i<20;++i){pool.maintain();}
 assert(pool.prepared_banks()==2&&pool.free_banks()==2&&pool.trimmed()==4);
 // Re-enter after queues have been destroyed/reprepared: stale cursor is a hint.
 assert(pool.try_push(1,14,&value,8,&cursor));std::size_t bank=128;for(std::size_t i=0;i<6;++i)if(pool.head_ticket(i)==14)bank=i;assert(bank<6);assert(pool.try_pop(bank,ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==14);
 std::cout<<"PASS capture budget: portable mapped-storage fallback/slot+byte/prepare/trim/stale-hint\n";
}
