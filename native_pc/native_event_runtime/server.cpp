// Authenticated local transport. No engine or JavaScript work runs in DllMain.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <sddl.h>
#include <algorithm>
#include <cstdint>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "json.hpp"
#include "engine.h"

namespace {
using Json = nlohmann::json;
constexpr std::size_t config_limit = 4 * 1024 * 1024;
constexpr std::size_t wire_limit = 8 * 1024 * 1024;
constexpr DWORD io_timeout = 15000;

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE handle = INVALID_HANDLE_VALUE) : value(handle) {}
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};

struct SafeError : std::runtime_error { using std::runtime_error::runtime_error; };

Json parse(const std::string& text) {
    // nlohmann's default parser accepts duplicate keys; reject ambiguous credentials.
    std::vector<std::set<std::string>> fields;
    return Json::parse(text, [&fields](int depth, Json::parse_event_t event, Json& value) {
        if (depth > 64) throw std::runtime_error("JSON nesting limit exceeded");
        if (event == Json::parse_event_t::object_start) fields.emplace_back();
        if (event == Json::parse_event_t::key && !fields.back().insert(value.get<std::string>()).second)
            throw std::runtime_error("duplicate JSON field");
        if (event == Json::parse_event_t::object_end) fields.pop_back();
        return true;
    });
}

std::wstring wide(const std::string& input) {
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(), static_cast<int>(input.size()), nullptr, 0);
    if (!count) throw std::runtime_error("invalid UTF-8");
    std::wstring result(count, L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(), static_cast<int>(input.size()), result.data(), count))
        throw std::runtime_error("invalid UTF-8");
    return result;
}

bool safe_ascii(const std::string& value, std::size_t minimum, std::size_t maximum) {
    return value.size() >= minimum && value.size() <= maximum
        && std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                || (c >= '0' && c <= '9') || c == '_' || c == '-';
        });
}

std::uint64_t creation_time() {
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user))
        throw std::runtime_error("cannot verify process instance");
    return (std::uint64_t(created.dwHighDateTime) << 32) | created.dwLowDateTime;
}

struct Security {
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), nullptr, FALSE};
    Security() {
        HANDLE raw = nullptr;
        if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &raw))
            throw std::runtime_error("cannot obtain runtime user");
        Handle token(raw);
        DWORD size = 0;
        GetTokenInformation(token.value, TokenUser, nullptr, 0, &size);
        if (!size || size > 65536) throw std::runtime_error("invalid runtime user");
        std::vector<unsigned char> storage(size);
        if (!GetTokenInformation(token.value, TokenUser, storage.data(), size, &size))
            throw std::runtime_error("cannot obtain runtime user");
        LPWSTR sid = nullptr;
        if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(storage.data())->User.Sid, &sid))
            throw std::runtime_error("cannot encode runtime user");
        std::wstring rule = L"D:P(A;;GA;;;" + std::wstring(sid) + L")";
        LocalFree(sid);
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(rule.c_str(), SDDL_REVISION_1, &descriptor, nullptr))
            throw std::runtime_error("cannot protect runtime transport");
        attributes.lpSecurityDescriptor = descriptor;
    }
    ~Security() { if (descriptor) LocalFree(descriptor); }
};

struct Config { std::string pipe, token, source; };

Config configuration(HMODULE module) {
    std::vector<wchar_t> name(32768);
    DWORD length = GetModuleFileNameW(module, name.data(), static_cast<DWORD>(name.size()));
    if (!length || length >= name.size()) throw std::runtime_error("runtime module path unavailable");
    std::wstring path(name.data(), length);
    const auto dot = path.find_last_of(L'.');
    if (dot == std::wstring::npos) throw std::runtime_error("invalid runtime module path");
    path.replace(dot, path.size() - dot, L".json");
    Handle file(CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr));
    LARGE_INTEGER size{};
    if (file.value == INVALID_HANDLE_VALUE || !GetFileSizeEx(file.value, &size)
            || size.QuadPart <= 0 || size.QuadPart > static_cast<LONGLONG>(config_limit))
        throw std::runtime_error("runtime configuration unavailable or oversized");
    std::string contents(static_cast<std::size_t>(size.QuadPart), '\0');
    DWORD read = 0;
    if (!ReadFile(file.value, contents.data(), static_cast<DWORD>(contents.size()), &read, nullptr)
            || read != contents.size()) throw std::runtime_error("runtime configuration truncated");
    Json config = parse(contents);
    const std::set<std::string> required{"pipe", "token", "process_instance", "source"};
    if (!config.is_object() || config.size() != required.size()) throw std::runtime_error("invalid runtime configuration");
    for (const auto& key : required) if (!config.contains(key)) throw std::runtime_error("invalid runtime configuration");
    if (!config["pipe"].is_string() || !config["token"].is_string() || !config["source"].is_string())
        throw std::runtime_error("invalid runtime configuration fields");
    const auto& instance = config["process_instance"];
    if (!instance.is_array() || instance.size() != 2 || !instance[0].is_number_unsigned()
            || !instance[1].is_number_unsigned() || instance[0].get<std::uint64_t>() != GetCurrentProcessId()
            || instance[1].get<std::uint64_t>() != creation_time())
        throw std::runtime_error("runtime configuration belongs to another process instance");
    Config result{config["pipe"].get<std::string>(), config["token"].get<std::string>(), config["source"].get<std::string>()};
    const std::string prefix = "\\\\.\\pipe\\ArkDPS.NativeEvents.";
    if (result.pipe.compare(0, prefix.size(), prefix) != 0
            || !safe_ascii(result.pipe.substr(prefix.size()), 1, 128)
            || !safe_ascii(result.token, 32, 128) || result.source.empty() || result.source.size() > config_limit)
        throw std::runtime_error("invalid runtime transport configuration");
    return result;
}

bool wait_io(HANDLE pipe, OVERLAPPED& operation, BOOL immediate, DWORD& bytes, DWORD timeout) {
    if (immediate) return true;
    if (GetLastError() != ERROR_IO_PENDING) return false;
    if (WaitForSingleObject(operation.hEvent, timeout) == WAIT_OBJECT_0)
        return GetOverlappedResult(pipe, &operation, &bytes, FALSE) != FALSE;
    CancelIoEx(pipe, &operation);
    // CancelIoEx is asynchronous: do not free OVERLAPPED until completion.
    GetOverlappedResult(pipe, &operation, &bytes, TRUE);
    return false;
}

bool connect(HANDLE pipe) {
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!event.value) return false;
    OVERLAPPED operation{};
    operation.hEvent = event.value;
    BOOL immediate = ConnectNamedPipe(pipe, &operation);
    if (!immediate && GetLastError() == ERROR_PIPE_CONNECTED) return true;
    DWORD bytes = 0;
    return wait_io(pipe, operation, immediate, bytes, 120000);
}

bool read_line(HANDLE pipe, std::string& pending, std::string& line) {
    const ULONGLONG deadline = GetTickCount64() + io_timeout;
    while (true) {
        const auto newline = pending.find('\n');
        if (newline != std::string::npos) {
            if (newline > wire_limit) return false;
            line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            return !line.empty();
        }
        if (pending.size() > wire_limit) return false;
        char block[4096];
        DWORD bytes = 0;
        Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.value) return false;
        OVERLAPPED operation{};
        operation.hEvent = event.value;
        BOOL immediate = ReadFile(pipe, block, sizeof(block), &bytes, &operation);
        const ULONGLONG now = GetTickCount64();
        if (!wait_io(pipe, operation, immediate, bytes,
                     now < deadline ? static_cast<DWORD>(deadline - now) : 0) || !bytes) return false;
        pending.append(block, bytes);
    }
}

bool write_payload(HANDLE pipe, std::string data) {
    if (data.size() > wire_limit) return false;
    data.push_back('\n');
    const ULONGLONG deadline = GetTickCount64() + io_timeout;
    std::size_t offset = 0;
    while (offset < data.size()) {
        Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event.value) return false;
        OVERLAPPED operation{};
        operation.hEvent = event.value;
        DWORD bytes = 0;
        DWORD count = static_cast<DWORD>(std::min<std::size_t>(65536, data.size() - offset));
        BOOL immediate = WriteFile(pipe, data.data() + offset, count, &bytes, &operation);
        const ULONGLONG now = GetTickCount64();
        if (!wait_io(pipe, operation, immediate, bytes,
                     now < deadline ? static_cast<DWORD>(deadline - now) : 0) || !bytes) return false;
        offset += bytes;
    }
    return true;
}

bool write_line(HANDLE pipe, const Json& value) {
    return write_payload(pipe, value.dump());
}

std::string response_limit_error(std::size_t bytes, const std::string& operation, const Json& request) {
    auto message = "response_too_large; bytes=" + std::to_string(bytes) + "; limit=" + std::to_string(wire_limit);
    if ((operation == "read" || operation == "terminal_read") && request.contains("after")
            && request["after"].is_number_unsigned() && request.contains("through")
            && request["through"].is_number_unsigned()) {
        const auto after = request["after"].get<std::uint64_t>();
        const auto through = request["through"].get<std::uint64_t>();
        if (through >= after) message += "; requested_event_count=" + std::to_string(through - after);
    }
    return message;
}

bool equal_token(const std::string& left, const std::string& right) {
    unsigned difference = static_cast<unsigned>(left.size() ^ right.size());
    for (std::size_t i = 0; i < right.size(); ++i)
        difference |= static_cast<unsigned char>(right[i]) ^ (i < left.size() ? static_cast<unsigned char>(left[i]) : 0);
    return difference == 0;
}

Json hello() {
    return Json{{"provider", "arkdps-native-hooks"}, {"protocol", 1}, {"ready", parse(arkdps::readiness())}};
}

std::string safe_diagnostic(const char* message, const char* phase) {
    // Diagnostics are reconstructed from fixed vocabulary, never returned as a
    // truncated exception: a short first line can itself contain credentials.
    std::size_t length = 0;
    if (message) while (length < 4096 && message[length]) ++length;
    const std::string bounded(message ? message : "", length);
    const auto end = bounded.find_first_of("\r\n");
    const std::string first = bounded.substr(0, std::min<std::size_t>(end, 512));
    std::string type = "std::exception";
    for (const char* name : {"Error", "InternalError", "TypeError", "ReferenceError", "RangeError", "SyntaxError"}) {
        if (first.rfind(std::string(name) + ":", 0) == 0) { type = name; break; }
    }
    std::string detail;
    const std::vector<std::string> classes{"Entity", "Unit", "Buff", "BuffContainer", "BlockDamage", "Modifier",
        "BattleController", "Character", "BasicSkill", "SkillData", "LevelData", "Enemy", "BObject", "Attributes",
        "BattleFormula", "Ability", "BattleCharacterData"};
    const std::vector<std::string> fields{"m_hp", "<id>k__BackingField", "m_source", "m_blackboard",
        "<key>k__BackingField", "<instanceUid>k__BackingField", "<hasShield>k__BackingField", "m_target",
        "m_originalSource", "m_value", "realDelta", "<targetType>k__BackingField", "<damageType>k__BackingField",
        "<cancelReason>k__BackingField", "s_fixedFrameCnt", "s_fixedPlayTime", "m_logger", "m_levelData", "m_state",
        "levelId", "<data>k__BackingField", "uniqueId", "m_skill", "m_owner", "m_data", "prefabKey", "skillId",
        "name", "m_waitForSkillEnd", "m_currentTile", "<borned>k__BackingField", "<isDisappeared>k__BackingField"};
    for (const char* prefix : {"unsupported layout ", "missing field "}) {
        const std::string expected = std::string("Error: ") + prefix;
        if (first.rfind(expected, 0) != 0) continue;
        detail = expected;
        for (const auto& klass : classes) for (const auto& field : fields) {
            const auto symbol = klass + "." + field;
            if (first.substr(expected.size()) == symbol) detail += symbol;
        }
        if (detail == expected) detail.pop_back();
    }
    for (const char* prefix : {"class not found ", "ambiguous class "}) {
        const std::string expected = std::string("Error: ") + prefix;
        if (first.rfind(expected, 0) != 0) continue;
        detail = expected;
        for (const auto& klass : classes) if (first.substr(expected.size()) == klass) detail += klass;
        if (detail == expected) detail.pop_back();
    }
    for (const char* prefix : {"required method missing ", "missing or ambiguous method "}) {
        const std::string expected = std::string("Error: ") + prefix;
        if (first.rfind(expected, 0) != 0) continue;
        detail = expected;
        for (const char* method : {"Entity.ApplyModifier", "Buff.OnTakeDamage", "BlockDamage._AssignDynamicVar",
                                  "OnDestroy", "set_state", "OnCastFinish", "OnSkillStart", "OnSkillEnd"})
            if (first.substr(expected.size()) == method) detail += method;
        if (detail == expected) detail.pop_back();
    }
    for (const char* text : {"unreadable native memory", "module unavailable", "required IL2CPP export missing",
            "IL2CPP domain is not initialized", "IL2CPP thread could not attach", "Assembly-CSharp not found",
            "invalid field metadata", "field metadata count mismatch", "field metadata table missing",
            "field metadata iterator mismatch", "field owner mismatch", "invalid method metadata",
            "method metadata count mismatch", "method metadata table missing", "method metadata iterator mismatch",
            "hook thunk allocation failed", "hook thunk protection failed", "could not initialize native hook library",
            "could not create JavaScript runtime", "could not create JavaScript context", "probe did not publish readiness",
            "could not queue native hooks", "could not enable native hooks", "could not apply native hook changes",
            "memory length exceeds the bounded runtime limit", "allocation limit exceeded", "unsafe integer"}) {
        if (first == text || first == type + ": " + text) detail = text;
    }
    if (first.rfind("InternalError: native hook creation failed:", 0) == 0
            || first.rfind("Error: native hook creation failed:", 0) == 0
            || first.rfind("native hook creation failed:", 0) == 0) {
        detail = "native hook creation failed";
        for (const char* code : {"MH_UNKNOWN", "MH_ERROR_ALREADY_INITIALIZED", "MH_ERROR_NOT_INITIALIZED",
                "MH_ERROR_ALREADY_CREATED", "MH_ERROR_NOT_CREATED", "MH_ERROR_ENABLED", "MH_ERROR_DISABLED",
                "MH_ERROR_NOT_EXECUTABLE", "MH_ERROR_UNSUPPORTED_FUNCTION", "MH_ERROR_MEMORY_ALLOC",
                "MH_ERROR_MEMORY_PROTECT", "MH_ERROR_MODULE_NOT_FOUND", "MH_ERROR_FUNCTION_NOT_FOUND"}) {
            const std::string suffix = std::string(": ") + code;
            if (first.size() >= suffix.size() && first.compare(first.size() - suffix.size(), suffix.size(), suffix) == 0)
                detail += suffix;
        }
    }
    if (type == "TypeError" && first.find("not a function") != std::string::npos)
        detail = "TypeError: not a function";
    std::string result = std::string("native runtime ") + phase + " failed; error_type=" + type;
    if (!detail.empty()) result += "; " + detail;

    if (result.size() > 256) result.resize(256);
    return result;
}

DWORD WINAPI worker(void* argument) {
    bool initialized = false;
    bool mutex_owned = false;
    HANDLE mutex = nullptr;
    try {
        const Config config = configuration(static_cast<HMODULE>(argument));
        Security security;
        Handle pipe(CreateNamedPipeW(wide(config.pipe).c_str(), PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
            | FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1, 65536, 65536, io_timeout, &security.attributes));
        if (pipe.value == INVALID_HANDLE_VALUE || !connect(pipe.value)) return 0;
        std::string pending, line;
        std::uint64_t expected = 1;
        bool authenticated = false;
        while (read_line(pipe.value, pending, line)) {
            Json id = nullptr;
            const char* phase = authenticated ? "request" : "authentication";
            try {
                Json request = parse(line);
                if (!request.is_object() || !request.contains("id") || !request["id"].is_number_unsigned()
                        || request["id"].get<std::uint64_t>() != expected || expected > 9007199254740991ULL
                        || !request.contains("operation") || !request["operation"].is_string())
                    throw SafeError("invalid request sequence or operation");
                id = request["id"];
                const std::string operation = request["operation"].get<std::string>();
                Json result;
                if (!initialized) {
                    if (operation != "initialize" || request.size() != 3 || !request.contains("token")
                            || !request["token"].is_string()
                            || !equal_token(request["token"].get<std::string>(), config.token))
                        throw SafeError("runtime authentication failed");
                    authenticated = true;
                    phase = "initialize";
                    const auto name = L"Local\\ArkDPS.NativeEvents." + std::to_wstring(GetCurrentProcessId())
                        + L"." + std::to_wstring(creation_time());
                    mutex = CreateMutexW(&security.attributes, TRUE, name.c_str());
                    if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS)
                        throw SafeError("native runtime already active or unavailable");
                    mutex_owned = true;
                    // Pin after authorization. Even after stop, callbacks may be returning through this DLL.
                    HMODULE pinned = nullptr;
                    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            reinterpret_cast<LPCWSTR>(&worker), &pinned))
                        throw SafeError("cannot preserve runtime callback lifetime");
                    initialized = true; // Also clean up a partially failed initialize.
                    arkdps::initialize(config.source);
                    phase = "readiness";
                    result = hello();
                } else if (operation == "initialize" || request.contains("token")) {
                    throw SafeError("runtime already authenticated");
                } else if (operation == "hello") {
                    phase = "readiness";
                    result = hello();
                } else {
                    for (const char* known : {"mark", "read", "ack", "stop"}) if (operation == known) phase = known;
                    request.erase("id");
                    const auto response = arkdps::request(request.dump());
                    // Do not parse and encode an already oversized QuickJS reply again.
                    const auto reply_bytes = response.size() + sizeof("{\"id\":,\"result\":}") - 1
                        + std::to_string(expected).size();
                    if (reply_bytes > wire_limit)
                        throw SafeError(response_limit_error(reply_bytes, operation, request));
                    result = parse(response);
                }
                auto wire_response = Json{{"id", id}, {"result", std::move(result)}}.dump();
                if (wire_response.size() > wire_limit)
                    throw SafeError(response_limit_error(wire_response.size(), operation, request));
                if (!write_payload(pipe.value, std::move(wire_response))) break;
                ++expected;
                if (operation == "stop") break;
            } catch (const SafeError& error) {
                write_line(pipe.value, Json{{"id", id}, {"error", error.what()}});
                break;
            } catch (const std::exception& error) {
                std::string diagnostic = "native runtime request failed";
                if (authenticated) {
                    try { diagnostic = safe_diagnostic(error.what(), phase); } catch (...) {}
                }
                write_line(pipe.value, Json{{"id", id}, {"error", diagnostic}});
                break;
            } catch (...) {
                // Never echo engine exceptions, configuration, token, or caller source.
                write_line(pipe.value, Json{{"id", id}, {"error", "native runtime request failed"}});
                break;
            }
        }
        // This instance is never reused. Closing the server handle keeps
        // buffered replies available for the client. DisconnectNamedPipe
        // would discard a final diagnostic or stop reply still in the pipe.
    } catch (...) {
        // Pre-authentication failures have no trusted recipient and produce no diagnostic file.
    }
    bool stopped = true;
    if (initialized) {
        try { arkdps::request("{\"operation\":\"stop\"}"); } catch (...) { stopped = false; }
    }
    // A failed detach must keep the named object alive and reject replacement hooks.
    if (stopped) {
        if (mutex_owned) ReleaseMutex(mutex);
        if (mutex) CloseHandle(mutex);
    }
    return 0;
}
} // namespace

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, worker, module, 0, nullptr);
        if (!thread) return FALSE;
        CloseHandle(thread);
    }
    return TRUE;
}
