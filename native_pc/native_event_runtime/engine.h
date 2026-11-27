#pragma once
#include <string>
#include <cstdint>

namespace arkdps {
void initialize(const std::string& source, std::uint32_t event_queue_memory_mib = 1024);
std::string request(const std::string& json);
std::string readiness();
// Bounded first-failure numbers only; remains readable after capture is fatal.
std::string queue_failure_diagnostic();
std::string publication_failure_diagnostic();
// Zero when disabled; transport summaries are consumed by the next mark.
std::uint64_t timing_begin() noexcept;
void timing_transport(unsigned phase, std::uint64_t start, std::size_t bytes = 0) noexcept;
}
