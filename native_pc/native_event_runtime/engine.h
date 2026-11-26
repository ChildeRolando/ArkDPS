#pragma once
#include <string>
#include <cstdint>

namespace arkdps {
void initialize(const std::string& source);
std::string request(const std::string& json);
std::string readiness();
// Immutable projection generated from closed-record owned facts on the worker.
std::string captured_prefix_publication();
// Zero when disabled; transport summaries are consumed by the next mark.
std::uint64_t timing_begin() noexcept;
void timing_transport(unsigned phase, std::uint64_t start, std::size_t bytes = 0) noexcept;
}
