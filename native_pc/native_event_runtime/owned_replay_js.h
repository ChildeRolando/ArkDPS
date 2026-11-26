#pragma once
#include "owned_replay_view.h"
#include "owned_role_index.h"
#include "engine_capture_config.h"
#include "quickjs.h"
#include <cmath>
#include <charconv>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace arkdps::owned_js {
// One worker and one VM own this binding. The view borrows the immutable
// consumer buffer only for the current replay lease, never game memory.
constexpr unsigned read_plan_slots=32, read_plan_limit=32, read_batch_bytes=16384;
static_assert(16+read_plan_slots*16+read_plan_slots*(12+capture::max_string_units*2)<=read_batch_bytes,"owned read batch output bound");
static_assert(16+read_plan_slots*16+(read_plan_slots+1)*(12+capture::max_string_units*2)<=read_batch_bytes,"graph projected output plus one static scratch bound");
using ReadPlanField=capture::OwnedReadField;
using ReadPlan=capture::OwnedReadProgram;
struct Binding {
    capture::OwnedReplayView view;
    capture::OwnedRoleIndex role_index;
    const std::vector<std::string>* roles = nullptr;
    std::uint64_t ticket = 0;
    capture::Error error = capture::Error::missing_plan_field;
    void (*poison)() noexcept = nullptr;
    bool active = false;
    bool concurrent = false;
    bool js_exception = false;
    const capture::CaptureConfiguration* configuration=nullptr;
    std::uint64_t scope_revision=0,publication_revision=0,graph_mode=0;
    std::uint32_t graph_checkpoint=0;
    std::uint32_t graph_failure=UINT32_MAX;
    bool read_plans_open=true;
    std::vector<ReadPlan> read_plans;
};
inline Binding& binding() { static auto* value = new Binding; return *value; }
inline void error(capture::Error value) noexcept {
    auto& owner=binding();owner.error=value;
    if (capture::OwnedReplayView::structural(value) && owner.poison) owner.poison();
}
inline capture::Error begin(const void* segments,std::size_t segment_count,
                           const void* fields,std::size_t field_count,
                           const std::byte* payload,std::size_t payload_bytes,
                           const std::vector<std::string>& roles,std::uint64_t ticket,
                           capture::Error record_error,bool concurrent=false) noexcept {
    auto& owner=binding();owner.roles=&roles;owner.ticket=ticket;owner.concurrent=concurrent;
    owner.js_exception=false;owner.configuration=nullptr;
    owner.error=owner.view.initialize(segments,segment_count,fields,field_count,payload,payload_bytes,
                                     static_cast<std::uint16_t>(roles.size()),record_error);
    owner.active=owner.error==capture::Error::ok;
    if (!owner.active) error(owner.error);
    return owner.error;
}
inline void end() noexcept {
    auto& owner=binding();owner.active=false;owner.concurrent=false;owner.ticket=0;owner.roles=nullptr;owner.configuration=nullptr;owner.view.reset();
}
struct Lease {
    Lease()=default;Lease(const Lease&)=delete;Lease& operator=(const Lease&)=delete;
    ~Lease(){end();}
};
inline void conversion_exception() noexcept {
    binding().js_exception=true;error(capture::Error::invalid_plan);
}
inline JSValue unavailable() noexcept { return binding().js_exception ? JS_EXCEPTION : JS_UNDEFINED; }
inline JSValue allocated(JSValue result) noexcept {
    if (JS_IsException(result)) conversion_exception();
    return result;
}
inline bool number(JSContext* c,JSValueConst value,double& out,double minimum,double maximum) noexcept {
    if (!JS_IsNumber(value)) return false;
    if (JS_ToFloat64(c,&out,value)<0) {conversion_exception();return false;}
    return std::isfinite(out) && std::floor(out)==out && minimum<=out && out<=maximum;
}
inline std::uint16_t role(JSContext* c,JSValueConst value) noexcept {
    auto& owner=binding();
    if (!owner.active || !owner.roles || !JS_IsString(value)) {error(capture::Error::missing_plan_field);return 0;}
    std::size_t length=0;const auto* name=JS_ToCStringLen(c,&length,value);
    if (!name) {conversion_exception();return 0;}
    if (!owner.role_index.ensure(*owner.roles)) {
        JS_FreeCString(c,name);error(capture::Error::invalid_plan);return 0;
    }
    const auto result=owner.role_index.find(*owner.roles,std::string_view(name,length));
    JS_FreeCString(c,name);
    if (!result) error(capture::Error::missing_plan_field);
    return result;
}
inline bool address(JSContext* c,JSValueConst value,std::uintptr_t& out) noexcept {
    // NativePointer has already canonicalized real Windows user addresses.
    // Reject overflow/negative values instead of wrapping an arbitrary BigInt.
    if (JS_IsNumber(value)) {
        double numeric=0;
        const auto maximum=(std::min)(9007199254740991.0,static_cast<double>(std::numeric_limits<std::uintptr_t>::max()));
        if (!number(c,value,numeric,0,maximum)) return false;
        out=static_cast<std::uintptr_t>(numeric);return true;
    }
    if (!JS_IsBigInt(c,value)) return false;
    std::size_t length=0;const auto* text=JS_ToCStringLen(c,&length,value);
    if (!text) {conversion_exception();return false;}
    std::uint64_t integer=0;
    const auto parsed=std::from_chars(text,text+length,integer);
    const bool exact=length>0 && length<=20 && parsed.ec==std::errc{}
        && parsed.ptr==text+length && integer<=std::numeric_limits<std::uintptr_t>::max();
    JS_FreeCString(c,text);
    if (!exact) return false;
    out=static_cast<std::uintptr_t>(integer);return true;
}
struct Request {
    std::uint16_t role_id=0;
    std::uint32_t checkpoint=0;
    std::optional<std::uint16_t> field;
    std::uintptr_t address=0;
};
inline bool request(JSContext* c,int count,JSValueConst* args,Request& result) noexcept {
    if (count!=5) {error(capture::Error::invalid_plan);return false;}
    result.role_id=role(c,args[0]);double checkpoint=0,field=0;
    if (!result.role_id || !number(c,args[1],checkpoint,0,UINT32_MAX)
        || !number(c,args[2],field,-1,65535) || !address(c,args[3],result.address)) {
        if (!binding().js_exception) error(capture::Error::missing_plan_field);
        return false;
    }
    result.checkpoint=static_cast<std::uint32_t>(checkpoint);
    if (field>=0) result.field=static_cast<std::uint16_t>(field);
    return true;
}
inline JSValue bytes(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    binding().js_exception=false;Request point;double length=0;
    if (!request(c,count,args,point)) return unavailable();
    if (!number(c,args[4],length,0,capture::max_bytes)) {if (!binding().js_exception) error(capture::Error::bounds);return unavailable();}
    const auto size=static_cast<std::size_t>(length);
    const auto result=binding().view.read(point.role_id,point.checkpoint,point.field,point.address,size);
    error(result.error);
    if (result.error!=capture::Error::ok) return JS_UNDEFINED;
    if (binding().concurrent) {error(capture::Error::changed_guard);return JS_UNDEFINED;}
    return allocated(JS_NewArrayBufferCopy(c,reinterpret_cast<const std::uint8_t*>(result.data),size));
}
inline JSValue scalar(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    binding().js_exception=false;Request point;double operation=0;
    if (!request(c,count,args,point)) return unavailable();
    if (!number(c,args[4],operation,0,6)) {if (!binding().js_exception) error(capture::Error::invalid_plan);return unavailable();}
    constexpr unsigned sizes[]{1,2,4,4,8,8,4};const auto kind=static_cast<unsigned>(operation);
    const auto result=binding().view.read(point.role_id,point.checkpoint,point.field,point.address,sizes[kind]);
    error(result.error);
    if (result.error!=capture::Error::ok) return JS_UNDEFINED;
    if (binding().concurrent) {error(capture::Error::changed_guard);return JS_UNDEFINED;}
    switch(kind){
    case 0: {std::uint8_t v;std::memcpy(&v,result.data,1);return allocated(JS_NewUint32(c,v));}
    case 1: {std::uint16_t v;std::memcpy(&v,result.data,2);return allocated(JS_NewUint32(c,v));}
    case 2: {std::uint32_t v;std::memcpy(&v,result.data,4);return allocated(JS_NewUint32(c,v));}
    case 3: {std::int32_t v;std::memcpy(&v,result.data,4);return allocated(JS_NewInt32(c,v));}
    case 4: {std::uint64_t v;std::memcpy(&v,result.data,8);return allocated(JS_NewBigUint64(c,v));}
    case 5: {std::int64_t v;std::memcpy(&v,result.data,8);return allocated(JS_NewBigInt64(c,v));}
    default:{float v;std::memcpy(&v,result.data,4);return allocated(JS_NewFloat64(c,v));}
    }
}
// Registration is installation-only. Plans contain raw read geometry, never
// values, ownership rules, HP arithmetic or cross-record facts.
inline void seal_read_plans() noexcept {binding().read_plans_open=false;}
inline JSValue register_read_plan(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    auto& owner=binding();owner.js_exception=false;double metadata=0;
    if(count!=2 || owner.active || !owner.read_plans_open
        || !number(c,args[0],metadata,1,9007199254740991.0) || !JS_IsObject(args[1])) {
        error(capture::Error::invalid_plan);return JS_ThrowInternalError(c,"owned read plan registration is unavailable");
    }
    auto encoded=JS_JSONStringify(c,args[1],JS_UNDEFINED,JS_UNDEFINED);
    if(JS_IsException(encoded)){conversion_exception();return encoded;}
    std::size_t length=0;const auto* text=JS_ToCStringLen(c,&length,encoded);
    if(!text){JS_FreeValue(c,encoded);conversion_exception();return JS_EXCEPTION;}
    try {
        if(length>65536 || owner.read_plans.size()>=read_plan_limit)throw std::runtime_error("read plan budget");
        const auto spec=nlohmann::json::parse(text,text+length);
        if(!spec.is_object() || spec.size()!=2 || capture::plan_uint(spec.at("version"),1)!=1
            || !spec.at("fields").is_array() || spec.at("fields").empty() || spec.at("fields").size()>read_plan_slots)
            throw std::runtime_error("read plan shape");
        ReadPlan plan;plan.metadata=static_cast<std::uint64_t>(metadata);
        for(const auto& value:spec.at("fields")) {
            if(!value.is_object())throw std::runtime_error("read field shape");
            for(auto i=value.begin();i!=value.end();++i) {
                const auto& key=i.key();
                if(key!="tier"&&key!="role"&&key!="checkpoint"&&key!="field_id"&&key!="root"
                    &&key!="absolute_address"&&key!="pointer_offsets"&&key!="offset"&&key!="kind"&&key!="bytes"&&key!="max_units")
                    throw std::runtime_error("read field option");
            }
            ReadPlanField field;field.tier=capture::plan_uint(value.at("tier"),128);
            if(!field.tier || (field.tier&(field.tier-1)))throw std::runtime_error("read field tier");
            field.role=value.at("role").get<std::string>();
            if(field.role.empty()||field.role.size()>96||std::any_of(field.role.begin(),field.role.end(),[](unsigned char x){
                return !(x>='a'&&x<='z')&&!(x>='A'&&x<='Z')&&!(x>='0'&&x<='9')&&x!='_'&&x!='.';
            }))throw std::runtime_error("read field role");
            const auto cp=value.value("checkpoint",nlohmann::json(0));
            field.current_checkpoint=cp=="current";
            if(!field.current_checkpoint)field.checkpoint=capture::plan_uint(cp,UINT32_MAX);
            const auto selector=value.value("field_id",nlohmann::json(-1));
            if(selector=="unique")field.selector=-2;
            else if(selector!=-1){field.selector=capture::plan_uint(selector,65535);if(!field.selector)throw std::runtime_error("read field ID");}
            const auto root=value.at("root").get<std::string>();
            if(root!="arg0"&&root!="absolute")throw std::runtime_error("read field root");
            field.absolute_root=root=="absolute";
            if(field.absolute_root){field.absolute=capture::plan_address(value.at("absolute_address"));if(!field.absolute)throw std::runtime_error("read field root");}
            else if(value.contains("absolute_address"))throw std::runtime_error("read field root option");
            const auto path=value.value("pointer_offsets",nlohmann::json::array());
            if(!path.is_array()||path.size()>capture::max_depth)throw std::runtime_error("read field path");
            for(const auto& offset:path)field.path[field.depth++]=capture::plan_uint(offset,UINT32_MAX);
            field.offset=capture::plan_uint(value.value("offset",nlohmann::json(0)),UINT32_MAX);
            const auto kind=value.value("kind",std::string("bytes"));
            if(kind=="bytes"){
                if(value.contains("max_units"))throw std::runtime_error("read field bytes option");
                field.bytes=capture::plan_uint(value.at("bytes"),128);if(!field.bytes)throw std::runtime_error("read field bytes");
            }else if(kind=="utf16"){
                if(value.contains("bytes"))throw std::runtime_error("read field string option");
                field.kind=1;field.max_units=capture::plan_uint(value.at("max_units"),capture::max_string_units);
                if(!field.max_units)throw std::runtime_error("read field string bound");
            }else throw std::runtime_error("read field kind");
            plan.tiers|=field.tier;plan.fields.push_back(std::move(field));
        }
        owner.read_plans.push_back(std::move(plan));
        JS_FreeCString(c,text);JS_FreeValue(c,encoded);
        return JS_NewUint32(c,owner.read_plans.size());
    }catch(...){
        JS_FreeCString(c,text);JS_FreeValue(c,encoded);error(capture::Error::invalid_plan);
        return JS_ThrowInternalError(c,"invalid owned read plan");
    }
}
inline JSValue execute_read_batch(JSContext* c,const ReadPlan& plan,unsigned mask,std::uintptr_t base,std::uint32_t checkpoint,bool check=false) {
    auto& owner=binding();unsigned slots=plan.fields.size();
    if(check){slots=0;for(const auto& field:plan.fields)slots+=field.project;}
    if(slots>32){error(capture::Error::invalid_plan);return JS_UNDEFINED;}
    std::array<std::byte,read_batch_bytes> output;const auto head=16+16*slots;std::size_t cursor=head;
    auto first_error=capture::Error::ok;std::uint32_t first_index=UINT32_MAX;unsigned projected=0;
    const auto put=[&](std::size_t at,auto value){std::memcpy(output.data()+at,&value,sizeof value);};
    put(0,std::uint32_t{1});put(4,static_cast<std::uint32_t>(slots));put(8,static_cast<std::uint32_t>(mask));
    for(std::size_t i=0;i<plan.fields.size();++i) {
        const auto& field=plan.fields[i];const bool emit=!check||field.project;const auto entry=16+(check?projected:i)*16;const auto start=cursor;
        if(emit){++projected;put(entry,std::uint16_t{0xffff});put(entry+2,static_cast<std::uint16_t>(field.kind));put(entry+4,std::uint32_t{0});put(entry+8,std::uint32_t{0});put(entry+12,std::uint32_t{0});}
        if(!(mask&field.tier))continue;
        auto status=capture::Error::ok;const auto role_id=field.role_id?field.role_id:owner.role_index.find(*owner.roles,field.role);
        const auto cp=field.current_checkpoint?checkpoint:field.checkpoint;
        std::optional<std::uint16_t> selected;
        if(!role_id)status=capture::Error::missing_plan_field;
        else if(field.selector==-2){const auto point=owner.view.point_info(role_id,cp);
            if(point.error!=capture::Error::ok)status=point.error;
            else if(point.field_count!=1)status=point.field_count?capture::Error::ambiguous_read:capture::Error::missing_plan_field;
            else if(point.single_field_error!=capture::Error::ok)status=point.single_field_error;
            else if(owner.concurrent)status=capture::Error::changed_guard;
            else if(field.expected_field_id&&field.expected_field_id!=point.single_field_id)status=capture::Error::missing_plan_field;
            else selected=point.single_field_id;
        }else if(field.selector>=0)selected=static_cast<std::uint16_t>(field.selector);
        const auto read=[&](std::uintptr_t address,std::size_t bytes,void* target) noexcept {
            if(status!=capture::Error::ok)return;
            const auto value=owner.view.read(role_id,cp,selected,address,bytes);status=value.error;
            if(status==capture::Error::ok&&owner.concurrent)status=capture::Error::changed_guard;
            if(status==capture::Error::ok&&bytes)std::memcpy(target,value.data,bytes);
        };
        const auto add=[&](std::uintptr_t address,std::uint32_t offset) noexcept {
            if(status!=capture::Error::ok)return address;
            if(offset>std::numeric_limits<std::uintptr_t>::max()-address){status=capture::Error::bounds;return std::uintptr_t{0};}
            return address+offset;
        };
        auto address=field.absolute_root?field.absolute:base;
        for(unsigned n=0;n<field.depth&&status==capture::Error::ok;++n){const auto link=add(address,field.path[n]);read(link,8,&address);}
        address=add(address,field.offset);
        if(field.kind==0){read(address,field.bytes,output.data()+cursor);if(status==capture::Error::ok)cursor+=field.bytes;}
        else {
            std::int32_t units=0;read(address,8,output.data()+cursor);
            read(add(address,16),4,&units);
            if(status==capture::Error::ok&&(units<0||static_cast<unsigned>(units)>field.max_units))status=capture::Error::invalid_length;
            if(status==capture::Error::ok){put(cursor+8,units);read(add(address,20),units*2,output.data()+cursor+12);if(status==capture::Error::ok)cursor+=12+units*2;}
        }
        if(check&&status==capture::Error::ok) {
            for(const auto& word:field.words){
                const auto* data=output.data()+start+word.offset;bool equal=false;
                if(word.kind==0){std::uint8_t value;std::memcpy(&value,data,1);equal=value==word.bits;}
                else if(word.kind==1){std::uint32_t value;std::memcpy(&value,data,4);equal=value==word.bits;}
                else {std::uint64_t value;std::memcpy(&value,data,8);equal=static_cast<double>(value)==word.number;}
                if(!equal){status=capture::Error::changed_guard;break;}
            }
            if(status==capture::Error::ok&&field.compare_utf16){
                std::int32_t length;std::memcpy(&length,output.data()+start+8,4);
                if(length!=static_cast<std::int32_t>(field.utf16.size())||(!field.utf16.empty()&&std::memcmp(output.data()+start+12,field.utf16.data(),field.utf16.size()*2)!=0))status=capture::Error::changed_guard;
            }
        }
        if(capture::OwnedReplayView::structural(status)){owner.graph_failure=i;error(status);return JS_UNDEFINED;}
        if(check&&status!=capture::Error::ok&&first_error==capture::Error::ok){first_error=status;first_index=i;}
        if(!emit){cursor=start;continue;}
        put(entry,static_cast<std::uint16_t>(status));
        if(status==capture::Error::ok){put(entry+4,static_cast<std::uint32_t>(start));put(entry+8,static_cast<std::uint32_t>(cursor-start));}
        else cursor=start;
    }
    if(first_error!=capture::Error::ok){owner.graph_failure=first_index;error(first_error);return JS_UNDEFINED;}
    put(12,static_cast<std::uint32_t>(cursor-head));error(capture::Error::ok);
    return allocated(JS_NewArrayBufferCopy(c,reinterpret_cast<const std::uint8_t*>(output.data()),cursor));
}
inline JSValue read_batch(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    auto& owner=binding();owner.js_exception=false;double id=0,metadata=0,tiers=0,checkpoint=0;std::uintptr_t base=0;
    if(count!=5 || !owner.active || !owner.roles || !number(c,args[0],id,1,owner.read_plans.size())
        || !number(c,args[1],metadata,1,9007199254740991.0) || !address(c,args[2],base)
        || !number(c,args[3],tiers,1,255) || !number(c,args[4],checkpoint,0,UINT32_MAX)) {
        if(!owner.js_exception)error(capture::Error::invalid_plan);
        return unavailable();
    }
    const auto& plan=owner.read_plans[static_cast<std::size_t>(id)-1];const auto mask=static_cast<unsigned>(tiers);
    if(plan.metadata!=static_cast<std::uint64_t>(metadata)||(mask&~plan.tiers)||!owner.role_index.ensure(*owner.roles)) {
        error(capture::Error::invalid_plan);return JS_UNDEFINED;
    }
    return execute_read_batch(c,plan,mask,base,static_cast<std::uint32_t>(checkpoint));
}
// Exact record configuration is selected by engine, never by a JS supplied id.
inline void bind_graphs(const capture::CaptureConfiguration* config,std::uint64_t scope,
                        std::uint64_t publication,std::uint64_t mode,std::uint32_t checkpoint=0) noexcept {
    auto& owner=binding();owner.configuration=config;owner.scope_revision=scope;
    owner.publication_revision=publication;owner.graph_mode=mode;owner.graph_checkpoint=checkpoint;
}
inline JSValue graph_batch(JSContext* c,int count,JSValueConst* args,bool check) {
    auto& owner=binding();owner.js_exception=false;owner.graph_failure=UINT32_MAX;double revision=0,mode=0,chunk=0;
    if(count!=(check?3:4)||!owner.active||!owner.configuration||!JS_IsString(args[0])
        ||!number(c,args[1],revision,1,9007199254740991.0)
        ||!number(c,args[2],mode,0,9007199254740991.0)||(!check&&!number(c,args[3],chunk,0,65535))) {
        if(!owner.js_exception)error(capture::Error::invalid_plan);
        return unavailable();
    }
    const auto* kind=JS_ToCString(c,args[0]);if(!kind){conversion_exception();return JS_EXCEPTION;}
    const bool scope=std::string_view(kind)=="scope",publication=std::string_view(kind)=="publication";
    JS_FreeCString(c,kind);
    const auto* graph=scope?owner.configuration->scope.get():publication?owner.configuration->publication.get():nullptr;
    const auto recorded=scope?owner.scope_revision:owner.publication_revision;
    if(!graph||!graph->available||revision!=recorded||revision!=graph->revision||mode!=graph->mode_epoch
        ||mode!=owner.graph_mode||owner.configuration->mode_epoch!=owner.graph_mode||(check?!graph->check_ready:chunk>=graph->read_chunks.size())) {
        error(capture::Error::missing_plan_field);return JS_UNDEFINED;
    }
    return execute_read_batch(c,check?graph->check_program:graph->read_chunks[static_cast<std::size_t>(chunk)],1,0,owner.graph_checkpoint,check);
}
inline JSValue graph_read_batch(JSContext* c,JSValueConst,int count,JSValueConst* args){return graph_batch(c,count,args,false);}
inline JSValue graph_check(JSContext* c,JSValueConst,int count,JSValueConst* args){return graph_batch(c,count,args,true);}
inline JSValue graph_failure(JSContext* c,JSValueConst,int count,JSValueConst*) {
    if(count||!binding().active){error(capture::Error::invalid_plan);return JS_UNDEFINED;}
    return JS_NewUint32(c,binding().graph_failure);
}
inline JSValue status(JSContext* c,JSValueConst,int,JSValueConst*) {return JS_NewInt32(c,static_cast<int>(binding().error));}
inline JSValue point(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    binding().js_exception=false;double checkpoint=0;
    if(count!=2) {error(capture::Error::invalid_plan);return JS_NewUint32(c,0);}
    const auto role_id=role(c,args[0]);
    if(!role_id || !number(c,args[1],checkpoint,0,UINT32_MAX)) {if (!binding().js_exception) error(capture::Error::missing_plan_field);return binding().js_exception?JS_EXCEPTION:JS_NewUint32(c,0);}
    const auto result=binding().view.point_info(role_id,static_cast<std::uint32_t>(checkpoint));
    error(result.error);
    const auto count_code=result.field_count==1?1u:result.field_count?2u:0u;
    const auto field_error=result.error==capture::Error::ok && result.field_count==1
        && result.single_field_error==capture::Error::ok && binding().concurrent
        ? capture::Error::changed_guard : result.single_field_error;
    return JS_NewUint32(c,(count_code<<24)|(static_cast<unsigned>(field_error)<<16)|result.single_field_id);
}
inline JSValue lease(JSContext* c,JSValueConst,int count,JSValueConst* args) {
    auto& owner=binding();owner.js_exception=false;
    if(count!=1 || !owner.active || !JS_IsString(args[0])) {error(capture::Error::invalid_plan);return JS_FALSE;}
    std::size_t length=0;const auto* text=JS_ToCStringLen(c,&length,args[0]);
    if(!text) {conversion_exception();return JS_EXCEPTION;}
    char expected[20];const auto encoded=std::to_chars(expected,expected+sizeof expected,owner.ticket);
    const bool matches=encoded.ec==std::errc{} && std::string_view(text,length)==std::string_view(expected,encoded.ptr-expected);JS_FreeCString(c,text);
    if(!matches) error(capture::Error::invalid_plan);
    return JS_NewBool(c,matches);
}
inline void install(JSContext* c,void(*poison)() noexcept) {
    binding().poison=poison;
    auto global=JS_GetGlobalObject(c);
    JS_SetPropertyStr(c,global,"__nativeOwnedNumericAddress",JS_TRUE);
    struct Entry{const char* name;JSCFunction* function;int arguments;};
    const Entry entries[]{
        {"__nativeOwnedRead",bytes,5},{"__nativeOwnedScalar",scalar,5},{"__nativeOwnedStatus",status,0},
        {"__nativeOwnedPoint",point,2},{"__nativeOwnedLease",lease,1},
        {"__nativeOwnedGraphReadBatch",graph_read_batch,4},{"__nativeOwnedGraphCheck",graph_check,3},{"__nativeOwnedGraphFailure",graph_failure,0},
        {"__nativeOwnedReadPlanRegister",register_read_plan,2},{"__nativeOwnedReadBatch",read_batch,5}};
    for(const auto& entry:entries)JS_SetPropertyStr(c,global,entry.name,JS_NewCFunction(c,entry.function,entry.name,entry.arguments));
    JS_FreeValue(c,global);
}
} // namespace arkdps::owned_js
