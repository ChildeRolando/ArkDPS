// Test-only -fno-access-control: inject an allowed stale hint/schedule state.
// This translation unit never changes product headers or publishes a proof.
#include "capture_spill_pool.h"
#include <cassert>
#include <cstdint>
#include <iostream>
using namespace arkdps::capture;
int main(){
 CaptureSpillPool pool({1,64,4,true,1,1,0});CaptureSpillPool::ProducerCursor cursor;std::uint64_t value=1,ticket=0;std::size_t size=0;
 assert(pool.try_push(1,1,&value,8,&cursor));
 // A delayed pre-reap free-word snapshot can leave a ghost bit after another
 // borrower wins owner CAS. The bitmap is a hint, not actual free supply.
 pool.free_[0].fetch_or(1,std::memory_order_release);assert(pool.free_[0].load()&1);
 assert(pool.free_banks()==0);pool.maintain();assert(pool.prepared_banks()==2&&pool.free_banks()==1);
 assert(pool.try_push(1,2,&value,8,&cursor));
 assert(pool.try_pop(0,ticket,&value,8,size)==CaptureQueue::PopResult::popped&&ticket==1);
 // Model worker pop/reap seeing a writer Busy, followed by that writer's
 // rejected push publishing no record. A later maintenance poll must reap it.
 pool.banks_[0].owner.store(1|CaptureSpillPool::busy,std::memory_order_release);assert(!pool.reap_empty(0));
 pool.banks_[0].owner.store(1,std::memory_order_release);pool.maintain();assert(pool.bank_owner(0)==0&&pool.free_banks()>=1);
 std::cout<<"PASS capture budget: stale-free-hint supply/maintenance-reap-after-busy\n";
}
