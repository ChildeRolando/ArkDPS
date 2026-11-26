#pragma once
#include "native_capture.h"
#include <cstring>

namespace arkdps::capture {
// Historical terminal reuse only vetoes changes. It never creates a later
// source clock, event prefix, or terminal certificate.
struct TerminalFence {
    struct Witness { std::uintptr_t address=0; std::uint32_t size=0; std::array<std::byte,16> bytes{}; };
    std::array<Witness,32> witnesses{};
    unsigned count=0;
    std::uint64_t ticket=0, activity=0;
    bool verified=false,singleton_null=false;
    void clear() noexcept { verified=false;singleton_null=false;count=0;ticket=0;activity=0; }
    bool certify_singleton(const std::byte* bytes,std::size_t size) noexcept {
        std::uintptr_t value=1;
        if(size==sizeof value)std::memcpy(&value,bytes,sizeof value);
        singleton_null=value==0;return singleton_null;
    }
    bool add(std::uintptr_t address,const std::byte* bytes,std::size_t size) noexcept {
        if (!address || !size || size>16 || count==witnesses.size()) return false;
        auto& witness=witnesses[count++];witness.address=address;witness.size=size;
        std::memcpy(witness.bytes.data(),bytes,size);return true;
    }
    bool accepts(unsigned active,std::uint64_t reserved,std::uint64_t generation,
                 ReadMemory reader,void* user) const noexcept {
        if (!verified || !singleton_null || !count || active || reserved!=ticket || generation!=activity) return false;
        std::array<std::byte,16> actual;
        for (unsigned i=0;i<count;++i) {
            const auto& witness=witnesses[i];
            if (!reader(user,witness.address,actual.data(),witness.size)
                || std::memcmp(actual.data(),witness.bytes.data(),witness.size)) return false;
        }
        return true;
    }
};
} // namespace arkdps::capture
