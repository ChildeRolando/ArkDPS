#pragma once
#include <string>
#include <cstdint>

namespace arkdps {
void initialize(const std::string& source);
std::string request(const std::string& json);
std::string readiness();
// Zero when disabled; transport summaries are consumed by the next mark.
std::uint64_t timing_begin() noexcept;
void timing_transport(unsigned phase, std::uint64_t start, std::size_t bytes = 0) noexcept;
}
