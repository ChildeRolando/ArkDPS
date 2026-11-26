#include "owned_role_index.h"
#include <atomic>
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <new>
using namespace arkdps::capture;
std::atomic<bool> track{false};std::atomic<unsigned> allocations{0};
__attribute__((noinline)) void* operator new(std::size_t size){if(track)++allocations;if(auto* p=std::malloc(size))return p;throw std::bad_alloc();}
__attribute__((noinline)) void operator delete(void* p) noexcept {std::free(p);}__attribute__((noinline)) void operator delete(void* p,std::size_t) noexcept {std::free(p);}
int main(){
 auto* index=new OwnedRoleIndex;std::vector<std::string> roles;roles.reserve(1);roles.push_back("body");
 track=true;assert(index->find(roles,"body")==1);assert(!index->find(roles,"missing"));track=false;
 assert(allocations==0);const auto* old_data=roles.data();
 for(unsigned i=0;i<1000;++i)roles.push_back("r"+std::to_string(i));
 assert(roles.data()!=old_data);roles.push_back("costarring");roles.push_back("liquid");
 assert(OwnedRoleIndex::hash("costarring")==OwnedRoleIndex::hash("liquid"));
 track=true;
 assert(index->find(roles,"body")==1);assert(index->find(roles,"r999")==1001);
 assert(index->find(roles,"costarring")==1002);assert(index->find(roles,"liquid")==1003);
 assert(!index->find(roles,"liquiD"));track=false;assert(allocations==0);
 std::vector<std::string> replacement{"liquid","costarring","body"};
 assert(index->find(replacement,"liquid")==1&&index->find(replacement,"body")==3);
 replacement.push_back("body");assert(!index->ensure(replacement));
 std::vector<std::string> maximum;maximum.reserve(65536);
 for(unsigned i=0;i<65535;++i)maximum.push_back("publication.open."+std::to_string(i));
 track=true;assert(index->ensure(maximum));assert(index->find(maximum,"publication.open.65534")==65535);track=false;
 assert(allocations==0);maximum.push_back("overflow");assert(!index->ensure(maximum));
 delete index;std::cout<<"role exact hash collision, append/SSO relocation, owner change, 65535 budget, hot zero allocation passed\n";
}
