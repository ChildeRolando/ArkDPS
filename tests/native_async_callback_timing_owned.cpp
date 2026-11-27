#include "engine_capture_timing.h"
#include <cassert>
#include <atomic>
#include <thread>
#include <iostream>
using namespace arkdps::capture;
int main(){
 auto registry=new CallbackTimingRegistry;
 unsigned selected=0,positions=0;
 for(unsigned i=0;i<100000;++i)if(registry->select(0)){++selected;positions|=1u<<(i%32);}
 assert(selected>500&&selected<1100 && (positions&(positions-1)));
 auto& cell=registry->cell(0,3,false);assert(!cell.snapshot(1));
 cell.record(1,7,2,16,128,false);auto first=cell.snapshot(1);
 assert(first&&first->samples==1&&first->ticks==7&&first->reads==2&&first->enqueued_bytes==128);
 assert(!cell.snapshot(2));cell.record(2,3,4,32,0,true);auto second=cell.snapshot(2);
 assert(second&&second->samples==1&&second->ticks==3&&second->drops==1&&!cell.snapshot(1));
 auto& concurrent=registry->cell(1,4,true);std::atomic<bool> finished{false};
 std::thread producer([&]{for(unsigned i=0;i<20000;++i)concurrent.record(9,3,2,8,16,false);finished=true;});
 while(!finished.load())if(const auto sample=concurrent.snapshot(9)){
   assert(sample->ticks==sample->samples*3&&sample->reads==sample->samples*2
       &&sample->read_bytes==sample->samples*8&&sample->enqueued_bytes==sample->samples*16&&sample->maximum==3);
 }
 producer.join();assert(concurrent.snapshot(9)->samples==20000);delete registry;
 std::cout<<"random sampling, generation isolation and nonblocking consistent snapshots passed\n";
}
