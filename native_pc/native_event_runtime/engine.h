#pragma once
#include <string>

namespace arkdps {
void initialize(const std::string& source);
std::string request(const std::string& json);
std::string readiness();
}
