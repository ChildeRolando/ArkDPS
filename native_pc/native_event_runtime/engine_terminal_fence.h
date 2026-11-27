#pragma once
#include "native_capture.h"
#include <cstring>

namespace arkdps::capture {
// Historical terminal reuse only vetoes changes. It never creates a later
// source clock, event prefix, or terminal certificate.
struct TerminalFence {
    struct Witness { std::uintptr_t address=0; std::uint32_t size=0; std::array<std::byte,16> bytes{}; };
    std::array<Witness,64> witnesses{};
    unsigned count=0;
    std::uint64_t ticket=0, activity=0;
    bool verified=false,singleton_null=false,singleton_observed=false,retained_complete=false;
    void clear() noexcept { verified=false;singleton_null=false;singleton_observed=false;retained_complete=false;count=0;ticket=0;activity=0; }
    bool certify_singleton(const std::byte* bytes,std::size_t size) noexcept {
        std::uintptr_t value=1;
        if(size==sizeof value)std::memcpy(&value,bytes,sizeof value);
        singleton_observed=size==sizeof value;singleton_null=singleton_observed&&value==0;
        retained_complete=false;return singleton_observed;
    }
    bool add(std::uintptr_t address,const std::byte* bytes,std::size_t size) noexcept {
        if (!address || !size || size>16 || address+size<address) return false;
        for(unsigned i=0;i<count;++i)
            if(witnesses[i].address==address&&witnesses[i].size==size)
                return std::memcmp(witnesses[i].bytes.data(),bytes,size)==0;
        if(count==witnesses.size())return false;
        auto& witness=witnesses[count++];witness.address=address;witness.size=size;
        std::memcpy(witness.bytes.data(),bytes,size);return true;
    }
    bool add_range(std::uintptr_t address,const std::byte* bytes,std::size_t size) noexcept {
        if(!size)return false;
        for(std::size_t offset=0;offset<size;) {
            const auto chunk=size-offset>16?16:size-offset;
            if(address+offset<address||!add(address+offset,bytes+offset,chunk))return false;
            offset+=chunk;
        }
        return true;
    }
    bool accepts(unsigned active,std::uint64_t reserved,std::uint64_t generation,
                 ReadMemory reader,void* user) const noexcept {
        if (!verified || !singleton_observed || (!singleton_null&&!retained_complete) || !count || active || reserved!=ticket || generation!=activity) return false;
        std::array<std::byte,16> actual;
        for (unsigned i=0;i<count;++i) {
            const auto& witness=witnesses[i];
            if (!reader(user,witness.address,actual.data(),witness.size)
                || std::memcmp(actual.data(),witness.bytes.data(),witness.size)) return false;
        }
        return true;
    }
};
enum class TerminalFieldSlice { all,controller,logger };
inline bool terminal_field_state(const void* fields,std::size_t count,std::uint16_t id,FieldState& found) noexcept {
    if(!id||!fields)return false;
    for(std::size_t i=0;i<count;++i) {
        FieldState state;std::memcpy(&state,static_cast<const std::byte*>(fields)+i*sizeof state,sizeof state);
        if(state.id==id&&state.checkpoint==0){found=state;return state.error==Error::ok;}
    }
    return false;
}
inline bool terminal_field_witnesses(TerminalFence& fence,const void* segments,std::size_t segment_count,
                                    const void* fields,std::size_t field_count,const std::byte* payload,std::size_t payload_bytes,
                                    std::uint16_t id,TerminalFieldSlice slice=TerminalFieldSlice::all) noexcept {
    FieldState state;
    if((segment_count&&!segments)||(payload_bytes&&!payload))return false;
    if(!terminal_field_state(fields,field_count,id,state)||state.segment_begin>=state.segment_end
        ||state.segment_end>segment_count||state.final_segment<state.segment_begin||state.final_segment>=state.segment_end)return false;
    for(unsigned i=state.segment_begin;i<state.segment_end;++i) {
        Segment segment;std::memcpy(&segment,static_cast<const std::byte*>(segments)+i*sizeof segment,sizeof segment);
        if(segment.field_id!=id||segment.role!=state.role||segment.checkpoint||segment.offset>payload_bytes
            ||segment.size>payload_bytes-segment.offset||segment.address+segment.size<segment.address)return false;
        const auto* bytes=payload+segment.offset;
        if(i==state.final_segment&&slice==TerminalFieldSlice::controller) {
            if(segment.size<24||!fence.add_range(segment.address,bytes,8)||!fence.add_range(segment.address+16,bytes+16,8))return false;
        }else if(i==state.final_segment&&slice==TerminalFieldSlice::logger) {
            if(segment.size<56||!fence.add_range(segment.address,bytes,8)||!fence.add_range(segment.address+24,bytes+24,8)
                ||!fence.add_range(segment.address+48,bytes+48,8))return false;
        }else if(!fence.add_range(segment.address,bytes,segment.size))return false;
    }
    return true;
}
} // namespace arkdps::capture
