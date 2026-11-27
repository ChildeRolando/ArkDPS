// Self-owned native method stand-ins. This executes declarative nested calls
// and byte writes only; every business decision remains in the real JS source.
#include <windows.h>
#include "json.hpp"
#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>
using J=nlohmann::json;
struct Write{std::uintptr_t at;std::uint64_t value;unsigned size;};
struct Step{unsigned hook;bool leave;std::array<std::uint64_t,16> args{};std::uint64_t result=0;std::vector<Write> writes;};
static std::vector<std::vector<Step>> routes;static const std::vector<Step>* route=nullptr;static unsigned cursor=0;
static std::uintptr_t owned_begin=0,owned_end=0;static std::string error;
static std::uint64_t number(const J& v){return v.is_string()?std::stoull(v.get<std::string>(),nullptr,0):v.get<std::uint64_t>();}
static void write(const Step& s){for(const auto& w:s.writes){if(w.at<owned_begin||w.at>owned_end||w.size>owned_end-w.at||!w.size||w.size>8)throw std::runtime_error("write outside owned heap");std::memcpy(reinterpret_cast<void*>(w.at),&w.value,w.size);}}
static std::uint64_t invoke();
static std::uint64_t body(unsigned hook){while(cursor<route->size()&&!(*route)[cursor].leave)invoke();if(cursor>=route->size()||(*route)[cursor].hook!=hook)throw std::runtime_error("native recipe topology");const auto& leave=(*route)[cursor++];write(leave);return leave.result;}
#define PARAMS std::uint64_t a0,std::uint64_t a1,std::uint64_t a2,std::uint64_t a3,std::uint64_t a4,std::uint64_t a5,std::uint64_t a6,std::uint64_t a7,std::uint64_t a8,std::uint64_t a9,std::uint64_t a10,std::uint64_t a11,std::uint64_t a12,std::uint64_t a13,std::uint64_t a14,std::uint64_t a15
#define METHOD(N) extern "C" __declspec(dllexport) __declspec(noinline) std::uint64_t DemandHook##N(PARAMS){volatile auto guard=a0+a1+a2+a3+a4+a5+a6+a7+a8+a9+a10+a11+a12+a13+a14+a15+N;(void)guard;return body(N);}
METHOD(0) METHOD(1) METHOD(2) METHOD(3) METHOD(4) METHOD(5) METHOD(6) METHOD(7) METHOD(8) METHOD(9) METHOD(10) METHOD(11)
using Method=std::uint64_t(*)(PARAMS);
static std::array<Method,12> methods{{DemandHook0,DemandHook1,DemandHook2,DemandHook3,DemandHook4,DemandHook5,DemandHook6,DemandHook7,DemandHook8,DemandHook9,DemandHook10,DemandHook11}};
static std::uint64_t invoke(){const auto step=(*route)[cursor++];if(step.leave||step.hook>=methods.size())throw std::runtime_error("invalid owned entry");write(step);const auto& a=step.args;const auto value=methods[step.hook](a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7],a[8],a[9],a[10],a[11],a[12],a[13],a[14],a[15]);return value;}
extern "C" __declspec(dllexport) int DemandInstall(const char* file,std::uintptr_t begin,std::uintptr_t end)noexcept{try{
 J input;std::ifstream(file)>>input;owned_begin=begin;owned_end=end;if(end<=begin||end-begin>64*1024*1024)throw std::runtime_error("owned heap bounds");
 for(const auto& r:input.at("routes")){if(r.size()>64)throw std::runtime_error("owned recipe bound");std::vector<Step> steps;for(const auto& v:r){Step s{};s.hook=v.at("hook");s.leave=v.at("edge")=="leave";for(unsigned i=0;i<v.at("args").size();++i)s.args.at(i)=number(v.at("args")[i]);s.result=number(v.at("return_value"));for(const auto& w:v.at("writes"))s.writes.push_back({number(w.at("address")),number(w.at("value")),w.at("bytes")});steps.push_back(s);}routes.push_back(std::move(steps));}return 0;
 }catch(const std::exception& e){error=e.what();return -1;}}
extern "C" __declspec(dllexport) int DemandRun(unsigned index)noexcept{try{route=&routes.at(index);cursor=0;while(cursor<route->size())invoke();return static_cast<int>(cursor);}catch(const std::exception& e){error=e.what();return -1;}}
extern "C" __declspec(dllexport) const char* DemandError()noexcept{return error.c_str();}
