#pragma once
#include "../../native_pc/native_event_runtime/owned_replay_js.h"

namespace arkdps::owned_js {
// Mechanical facts only. These helpers neither advance replay watermarks nor
// decide HP attribution/eligibility. A declined candidate must retain its raw
// records for the ordinary prepare/finalize path.
struct HpLeafPairFacts {
    RawHpObservation entry,leave;
    std::int64_t before=0,after=0;
    bool unchanged=false;
};
inline const RawHpField* leaf_field(const capture::CompiledPlan& plan,const RawHpObservation& raw,
                                  bool leave,capture::HpIoSlot slot) noexcept {
    const auto& layout=plan.hp_io_layouts[leave?1:0];
    if(!layout.supported)return nullptr;
    const auto index=layout.fields[static_cast<unsigned>(slot)].index;
    return index<16&&raw.fields[index].status==0?&raw.fields[index]:nullptr;
}
inline bool collect_hp_leaf_pair(const capture::CompiledPlan& plan,const OwnedReadContext& entry,
                                const OwnedReadContext& leave,std::uintptr_t root,
                                std::uint32_t entry_checkpoint,std::uint32_t leave_checkpoint,
                                HpLeafPairFacts& facts) noexcept {
    using capture::HpIoSlot;
    if(!plan.hp_observations_ready||!plan.hp_io_layouts[0].supported||!plan.hp_io_layouts[1].supported)return false;
    collect_hp_observation(entry,plan.hp_observations[0],root,entry_checkpoint,facts.entry);
    collect_hp_observation(leave,plan.hp_observations[1],root,leave_checkpoint,facts.leave);
    const auto* identity=leaf_field(plan,facts.entry,false,HpIoSlot::identity);
    const auto* born=leaf_field(plan,facts.entry,false,HpIoSlot::born);
    const auto* id=leaf_field(plan,facts.entry,false,HpIoSlot::id);
    const auto* before=leaf_field(plan,facts.entry,false,HpIoSlot::hp);
    const auto* closing=leaf_field(plan,facts.leave,true,HpIoSlot::closing);
    if(!identity||identity->size!=28||!born||born->size!=1||!id||id->size<12
        ||!before||before->size!=8||!closing||closing->size!=48)return false;
    std::uint32_t opening_uid=0,closing_uid=0;
    std::memcpy(&opening_uid,identity->bytes.data()+24,4);std::memcpy(&closing_uid,closing->bytes.data(),4);
    if(opening_uid!=closing_uid)return false;
    std::memcpy(&facts.before,before->bytes.data(),8);std::memcpy(&facts.after,closing->bytes.data()+40,8);
    facts.unchanged=facts.before==facts.after;
    const auto* clock=leaf_field(plan,facts.entry,false,HpIoSlot::clock_open);
    if(!clock||clock->size!=12)return false;
    for(const auto& point:{std::pair<bool,HpIoSlot>{false,HpIoSlot::clock_close},
                           {true,HpIoSlot::clock_open},{true,HpIoSlot::clock_close}}){
        const auto* other=leaf_field(plan,point.first?facts.leave:facts.entry,point.first,point.second);
        if(!other||other->size!=12||std::memcmp(clock->bytes.data(),other->bytes.data(),12))return false;
    }
    // Unchanged ordinary setters do not demand MaxHP or scope. Their optional
    // slots retain their real failure statuses; they are never defaulted to 0.
    if(!facts.unchanged&&(!leaf_field(plan,facts.leave,true,HpIoSlot::count)
                         ||!leaf_field(plan,facts.leave,true,HpIoSlot::words)))return false;
    return true;
}
inline bool same_hp_leaf_window(const capture::CompiledPlan& plan,const HpLeafPairFacts& first,
                               const HpLeafPairFacts& next) noexcept {
    if(!plan.hp_io_layouts[0].supported||!plan.hp_io_layouts[1].supported||first.unchanged!=next.unchanged)return false;
    for(unsigned edge=0;edge<2;++edge){
        const auto& a=edge?first.leave:first.entry;const auto& b=edge?next.leave:next.entry;
        const auto& layout=plan.hp_io_layouts[edge];
        for(unsigned slot=0;slot<static_cast<unsigned>(capture::HpIoSlot::total);++slot){
            const auto index=layout.fields[slot].index;if(index==UINT16_MAX)continue;
            if(first.unchanged&&(slot==static_cast<unsigned>(capture::HpIoSlot::count)
                                ||slot==static_cast<unsigned>(capture::HpIoSlot::words)))continue;
            const auto& left=a.fields[index];const auto& right=b.fields[index];
            if(left.status!=0||right.status!=0||left.size!=right.size)return false;
            if(slot==static_cast<unsigned>(capture::HpIoSlot::hp))continue;
            const auto bytes=slot==static_cast<unsigned>(capture::HpIoSlot::closing)?40:left.size;
            if(std::memcmp(left.bytes.data(),right.bytes.data(),bytes))return false;
        }
    }
    return true;
}
struct HpLeafScopeFacts {std::array<RawHpField,32> projected;unsigned count=0;};
inline bool collect_hp_leaf_scope(const OwnedReadContext& context,const ReadProgram& plan,
                                 std::uint32_t checkpoint,HpLeafScopeFacts& output) noexcept {
    output.count=0;RawHpField scratch;
    for(const auto& field:plan.fields){
        if(field.kind>1||(field.kind?field.max_units>capture::max_string_units:field.bytes>scratch.bytes.size()))return false;
        auto& target=field.project&&output.count<output.projected.size()?output.projected[output.count]:scratch;
        if(read_owned_field_from(context,field,0,checkpoint,target.bytes.data(),target.size)!=capture::Error::ok
            ||compare_owned_field(field,target.bytes.data())!=capture::Error::ok)return false;
        target.status=0;
        if(field.project){if(output.count==output.projected.size())return false;++output.count;}
    }
    return true;
}
inline bool same_hp_leaf_scope(const HpLeafScopeFacts& a,const HpLeafScopeFacts& b) noexcept {
    if(a.count!=b.count)return false;
    for(unsigned i=0;i<a.count;++i)
        if(a.projected[i].size!=b.projected[i].size
            ||std::memcmp(a.projected[i].bytes.data(),b.projected[i].bytes.data(),a.projected[i].size))return false;
    return true;
}
} // namespace arkdps::owned_js
