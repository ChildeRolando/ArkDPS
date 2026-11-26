#include "engine_terminal_fence.h"
#include <cassert>
#include <iostream>
using namespace arkdps::capture;
struct Fixture { std::uint64_t root=0,clock=77;unsigned reads=0;bool unreadable=false; };
bool read(void* p,std::uintptr_t address,void* out,std::size_t size) noexcept {
 auto& f=*static_cast<Fixture*>(p);++f.reads;if(f.unreadable)return false;
 std::memcpy(out,reinterpret_cast<const void*>(address),size);return true;
}
int main(){
 Fixture f;TerminalFence fence;
 assert(fence.add(reinterpret_cast<std::uintptr_t>(&f.root),reinterpret_cast<const std::byte*>(&f.root),8));
 assert(fence.add(reinterpret_cast<std::uintptr_t>(&f.clock),reinterpret_cast<const std::byte*>(&f.clock),8));
 fence.ticket=101;fence.activity=202;
 assert(!fence.accepts(0,101,202,read,&f)); // uncertified ordinary publication
 fence.verified=true;assert(fence.accepts(0,101,202,read,&f));
 assert(!fence.accepts(1,101,202,read,&f));assert(!fence.accepts(0,102,202,read,&f));
 assert(!fence.accepts(0,101,203,read,&f));
 f.root=99;assert(!fence.accepts(0,101,202,read,&f));f.root=0;
 ++f.clock;assert(!fence.accepts(0,101,202,read,&f));--f.clock;
 f.unreadable=true;assert(!fence.accepts(0,101,202,read,&f));f.unreadable=false;
 assert(fence.accepts(0,101,202,read,&f));fence.clear();assert(!fence.accepts(0,101,202,read,&f));
 std::cout<<"terminal certification, activity/root/clock/unreadable veto passed\n";
}
