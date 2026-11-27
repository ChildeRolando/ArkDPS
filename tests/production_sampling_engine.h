// Self-owned production sampling integration only: no external process access.
static JSValue production_sampling_configure(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1)throw std::runtime_error("sampling setup arity");
 auto& r=state();arkdps::install_sampling_plan(r,J::parse(string(c,args[0])));
 auto g=JS_GetGlobalObject(c);r.sampling_dispatch=JS_GetPropertyStr(c,g,"__arkdpsSamplingDispatch");
 r.publication_request=JS_GetPropertyStr(c,g,"__arkdpsPublicationRequest");JS_FreeValue(c,g);
 return JS_UNDEFINED;
});}
struct ProductionSamplingRead {std::string fault;unsigned reads=0;std::uint64_t previous_mode=0;};
static bool production_sampling_read(void* user,std::uintptr_t at,void* out,std::size_t bytes) noexcept {
 auto& test=*static_cast<ProductionSamplingRead*>(user);++test.reads;
 if(test.fault=="mode"&&test.reads==2)state().capture_mode_epoch.fetch_add(1);
 if(test.fault=="clock"&&test.reads==2){auto a=state().sampling_plan->globals[0];auto* cell=region(a,4);if(cell){std::uint32_t value=99;std::memcpy(cell->data.data()+a-cell->address,&value,4);}}
 return read_memory(nullptr,at,out,bytes);
}
static JSValue production_sampling_run(JSContext* c,JSValueConst,int n,JSValueConst* args){return guarded(c,[&]{
 if(n!=1)throw std::runtime_error("sampling run arity");
 auto options=J::parse(string(c,args[0]));auto& r=state();
 const auto frame=options.at("frame").get<std::uint32_t>();const auto a=r.sampling_plan->globals[0];
 auto* cell=region(a,12);if(!cell)throw std::runtime_error("sampling clock is outside owned bytes");
 auto* data=cell->data.data()+a-cell->address;const std::uint64_t fixed=(std::uint64_t(frame)<<32)/30;
 std::memcpy(data,&frame,4);std::memcpy(data+4,&fixed,8);r.capture_paused.store(options.value("paused",false));
 const auto request=++r.sample_request;ProductionSamplingRead reader{options.value("fault",std::string()),0,r.capture_mode_epoch.load()};
 if(options.value("cold",false))require_cold_publication(r.cold_publication_request,request);
 std::array<std::byte,8> saved{};Region* changed=nullptr;std::uintptr_t target=0;
 if(reader.fault=="object"){target=plan_address(options.at("address"));changed=region(target,8);if(!changed)throw std::runtime_error("owned object guard missing");std::memcpy(saved.data(),changed->data.data()+target-changed->address,8);std::memset(changed->data.data()+target-changed->address,0,8);}
 const bool accepted=arkdps::sample_checkpoint(r,request,production_sampling_read,&reader);
 if(changed)std::memcpy(changed->data.data()+target-changed->address,saved.data(),8);
 r.capture_mode_epoch.store(reader.previous_mode);
 J result{{"accepted",accepted},{"request",request},{"sampling_serial",r.sampling_serial},{"raw",r.lanes->reserved.load()},{"processed",r.processed_ticket},{"closed",r.closed_ticket},{"sequence",r.published_sequence},{"reads",reader.reads},{"token",r.completed_sample_token},{"retry_reason",r.sampling_retry_reason},{"failure",r.lanes->failure.load()},{"cold_required",r.cold_publication_request.load()}};
 if(accepted){auto command=JS_ParseJSON(c,"{\"operation\":\"mark\"}",20,"sampling-mark");auto answer=JS_Call(c,r.publication_request,JS_UNDEFINED,1,&command);JS_FreeValue(c,command);if(JS_IsException(answer))throw std::runtime_error(exception(c));result["mark"]=json_value(c,answer);JS_FreeValue(c,answer);}
 return JS_NewString(c,result.dump().c_str());
});}


