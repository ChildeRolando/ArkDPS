#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace arkdps::capture {
// Worker-only. Role strings are append-only in CaptureConfigurations. Cache
// indexes and hashes, never c_str/string_view pointers into a growing vector.
class OwnedRoleIndex final {
public:
    static constexpr std::size_t capacity=131072, maximum_roles=65535;
    static std::uint32_t hash(std::string_view name) noexcept {
        std::uint32_t value=2166136261u;
        for(const unsigned char byte:name)value=(value^byte)*16777619u;
        return value;
    }
    bool ensure(const std::vector<std::string>& roles) noexcept {
        if(owner_==&roles && count_==roles.size())return true;
        for(unsigned i=0;i<used_count_;++i)slots_[used_[i]].id=0;
        used_count_=0;owner_=nullptr;count_=0;
        if(roles.size()>maximum_roles)return false;
        for(std::size_t i=0;i<roles.size();++i) {
            const auto& name=roles[i];
            if(name.empty() || name.size()>96)return false;
            const auto fingerprint=hash(name);
            auto slot=fingerprint&(capacity-1);
            while(slots_[slot].id) {
                const auto& previous=slots_[slot];
                if(previous.hash==fingerprint && previous.length==name.size() && roles[previous.id-1]==name)
                    return false;
                slot=(slot+1)&(capacity-1);
            }
            slots_[slot]={fingerprint,static_cast<std::uint16_t>(i+1),static_cast<std::uint16_t>(name.size())};
            used_[used_count_++]=slot;
        }
        owner_=&roles;count_=roles.size();return true;
    }
    std::uint16_t find(const std::vector<std::string>& roles,std::string_view name) noexcept {
        if(!ensure(roles) || name.empty() || name.size()>96)return 0;
        const auto fingerprint=hash(name);auto slot=fingerprint&(capacity-1);
        while(slots_[slot].id) {
            const auto& candidate=slots_[slot];
            if(candidate.hash==fingerprint && candidate.length==name.size()
                && std::string_view(roles[candidate.id-1])==name)return candidate.id;
            slot=(slot+1)&(capacity-1);
        }
        return 0;
    }
private:
    struct Slot {std::uint32_t hash=0;std::uint16_t id=0,length=0;};
    std::array<Slot,capacity> slots_{};
    std::array<std::uint32_t,maximum_roles> used_{};
    std::uint32_t used_count_=0;
    const std::vector<std::string>* owner_=nullptr;
    std::size_t count_=0;
};
} // namespace arkdps::capture
