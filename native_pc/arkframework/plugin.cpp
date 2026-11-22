#include <windows.h>

#include <ark/battle.h>
#include <ark/plugin.h>

#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <locale>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

constexpr size_t batch_size = 256;
constexpr uint32_t imgui_version = ARKDPS_IMGUI_VERSION;
const char* const dependencies[] = {"damage_meter.dll"};

std::ostringstream json_line() {
    std::ostringstream out;
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.imbue(std::locale::classic());
    out << std::setprecision(std::numeric_limits<double>::max_digits10);
    return out;
}

uint64_t filetime_value(FILETIME time) {
    return (uint64_t(time.dwHighDateTime) << 32) | time.dwLowDateTime;
}

// SDK ids are fixed-size UTF-8 arrays. The final element need not be NUL.
std::string_view id_text(const char (&value)[ARK_MAX_ID]) {
    size_t length = 0;
    while (length < sizeof(value) && value[length] != '\0') {
        ++length;
    }
    return {value, length};
}

void write_string(std::ostream& out, std::string_view text) {
    if (!text.empty() && MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
                                             static_cast<int>(text.size()), nullptr, 0) == 0) {
        throw std::runtime_error("invalid UTF-8 in event text");
    }
    constexpr char hex[] = "0123456789abcdef";
    out.put('"');
    for (unsigned char ch : text) {
        if (ch == '"' || ch == '\\') {
            out.put('\\').put(static_cast<char>(ch));
        } else if (ch < 0x20) {
            out << "\\u00" << hex[ch >> 4] << hex[ch & 15];
        } else {
            out.put(static_cast<char>(ch));
        }
    }
    out.put('"');
}

void write_event(std::ostream& out, const ArkEventV2& event) {
    if (event.size != sizeof(ArkEventV2) || !std::isfinite(event.game_time) ||
        !std::isfinite(event.calculated_damage) || !std::isfinite(event.actual_damage)) {
        throw std::runtime_error("invalid V2 event size or numerical value");
    }
    out << "{\"size\":" << event.size << ",\"kind\":" << event.kind
        << ",\"frame\":" << event.frame << ",\"frame_event_index\":" << event.frame_event_index
        << ",\"sequence\":" << event.sequence << ",\"battle\":" << event.battle
        << ",\"game_time\":" << event.game_time
        << ",\"calculated_damage\":" << event.calculated_damage
        << ",\"actual_damage\":" << event.actual_damage
        << ",\"damage_type\":" << event.damage_type << ",\"partial\":" << event.partial
        << ",\"actor_id\":";
    write_string(out, id_text(event.actor_id));
    out << ",\"owner_id\":";
    write_string(out, id_text(event.owner_id));
    out << ",\"target_id\":";
    write_string(out, id_text(event.target_id));
    out << ",\"original_source_id\":";
    write_string(out, id_text(event.original_source_id));
    out << ",\"stage_id\":";
    write_string(out, id_text(event.stage_id));
    if (event.kind == ARK_DAMAGE && event.damage_type == ARK_DAMAGE_HEAL &&
        event.calculated_damage >= 0 && event.actual_damage >= 0) {
        // MuMu counts the modifier amount as direct healing. V2 reports the
        // HP change separately and does not identify the cause of a difference.
        out << ",\"direct_healing\":{\"amount\":" << event.calculated_damage
            << ",\"actual_hp_restored\":" << event.actual_damage << '}';
    }
    out << '}';
}

class Journal {
public:
    Journal(const ArkPluginApiV1* host, const ArkBattleEventsApiV2* service)
        : host_(host), service_(service) {}

    ~Journal() {
        if (file_ != INVALID_HANDLE_VALUE) {
            CloseHandle(file_);
        }
    }

    void open() {
        FILETIME created{}, exited{}, kernel{}, user{}, now{};
        if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) {
            throw std::runtime_error("could not identify the host process");
        }
        GetSystemTimeAsFileTime(&now);
        const DWORD pid = GetCurrentProcessId();
        const auto directory = std::filesystem::u8path(host_->root_directory) / "exports" / "ArkDPS";
        std::filesystem::create_directories(directory);
        const std::wstring stem = L"events-" + std::to_wstring(pid) + L"-" + std::to_wstring(filetime_value(now));
        std::filesystem::path path;
        for (unsigned index = 0; index < 100; ++index) {
            const auto suffix = index ? L"-" + std::to_wstring(index) : L"";
            path = directory / (stem + suffix + L".jsonl");
            file_ = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (file_ != INVALID_HANDLE_VALUE) {
                break;
            }
            if (GetLastError() != ERROR_FILE_EXISTS) {
                break;
            }
        }
        if (file_ == INVALID_HANDLE_VALUE) {
            throw std::runtime_error("could not create the capture file");
        }
        auto out = json_line();
        out << "{\"kind\":\"header\",\"schema_version\":1,\"service\":\"" ARK_BATTLE_SERVICE_NAME_V2
               "\",\"abi_version\":" << ARK_BATTLE_ABI_VERSION_V2
            << ",\"pid\":" << pid << ",\"process_created_filetime\":" << filetime_value(created)
            << ",\"test_host\":" << (host_->test_host ? "true" : "false") << "}\n";
        write(out.str());
        host_->log(host_->owner, 1, ("ArkDPS capture: " + path.u8string()).c_str());
    }

    uint64_t poll() {
        ArkReadStatusV1 status{};
        const size_t count = service_->read(sequence_, events_.data(), events_.size(), &status);
        if (status.size != sizeof(status) || count > events_.size() ||
            status.oldest_sequence > status.newest_sequence ||
            status.newest_sequence < sequence_ || (!count && status.newest_sequence > sequence_)) {
            throw std::runtime_error("inconsistent V2 read status or backwards cursor");
        }
        if (!count && has_status_ && status.oldest_sequence == last_.oldest_sequence &&
            status.newest_sequence == last_.newest_sequence && status.partial == last_.partial &&
            status.overrun == last_.overrun && status.ended == last_.ended) {
            return status.newest_sequence;
        }

        auto out = json_line();
        out << "{\"kind\":\"batch\",\"after\":" << sequence_
            << ",\"oldest_sequence\":" << status.oldest_sequence << ",\"newest_sequence\":" << status.newest_sequence
            << ",\"partial\":" << status.partial << ",\"overrun\":" << status.overrun
            << ",\"ended\":" << status.ended << ",\"events\":[";
        uint64_t next = sequence_;
        bool gap = status.overrun != 0;
        for (size_t i = 0; i < count; ++i) {
            const auto& event = events_[i];
            if (event.sequence <= next || event.sequence < status.oldest_sequence ||
                event.sequence > status.newest_sequence) {
                throw std::runtime_error("V2 read returned an out-of-order or unbounded sequence");
            }
            // The provider clears history on battle start even when overrun is zero.
            gap |= event.sequence - next != 1;
            next = event.sequence;
            if (i) {
                out.put(',');
            }
            write_event(out, event);
        }
        out << "],\"gap\":" << (gap ? "true" : "false") << "}\n";
        write(out.str());
        sequence_ = next;
        last_ = status;
        has_status_ = true;
        return status.newest_sequence;
    }

    void frame() noexcept {
        if (failed_) {
            return;
        }
        try {
            poll();
        } catch (const std::exception& error) {
            fail(error.what());
        } catch (...) {
            fail("unexpected V2 read failure");
        }
    }

    void finish() noexcept {
        if (failed_) {
            return;
        }
        try {
            // SDK callbacks have drained; the declared provider is still leased.
            // Only wait for the prefix that existed at shutdown, even in battle.
            const auto through = poll();
            while (sequence_ < through) {
                poll();
            }
            write("{\"kind\":\"stop\",\"sequence\":" + std::to_string(sequence_) + "}\n");
        } catch (const std::exception& error) {
            fail(error.what());
        } catch (...) {
            fail("unexpected final read failure");
        }
    }

    void fail(const char* message) noexcept {
        failed_ = true;
        host_->log(host_->owner, 3, message);
        try {
            if (!io_failed_ && file_ != INVALID_HANDLE_VALUE) {
                auto out = json_line();
                out << "{\"kind\":\"error\",\"sequence\":" << sequence_ << ",\"message\":";
                write_string(out, message);
                out << "}\n";
                write(out.str());
            }
        } catch (...) {
            // The original failure is already available in the host log.
        }
    }

private:
    void write(const std::string& line) {
        size_t offset = 0;
        while (offset < line.size()) {
            DWORD written = 0;
            if (!WriteFile(file_, line.data() + offset, static_cast<DWORD>(line.size() - offset),
                           &written, nullptr) || !written) {
                io_failed_ = true;
                throw std::runtime_error("could not write the ArkDPS capture file");
            }
            offset += written;
        }
    }

    const ArkPluginApiV1* host_;
    const ArkBattleEventsApiV2* service_;
    HANDLE file_ = INVALID_HANDLE_VALUE;
    std::array<ArkEventV2, batch_size> events_{};
    uint64_t sequence_ = 0;
    ArkReadStatusV1 last_{};
    bool has_status_ = false;
    bool failed_ = false;
    bool io_failed_ = false;
};

std::unique_ptr<Journal> journal;

void on_frame(void* user, const ArkFrameV1*) {
    static_cast<Journal*>(user)->frame();
}

}  // namespace

ARK_EXPORT const ArkPluginInfoV1* ark_plugin_query() {
    static const ArkPluginInfoV1 info{sizeof(ArkPluginInfoV1), ARK_ABI_VERSION, imgui_version,
        "ArkDPS Collector", "1.1.0", "ArkDPS", ARK_PLUGIN_DEFAULT, dependencies, 1};
    return &info;
}

ARK_EXPORT bool ark_plugin_initialize(const ArkPluginApiV1* host) {
    if (journal || !host || host->size < sizeof(ArkPluginApiV1) || host->abi_version != ARK_ABI_VERSION ||
        host->imgui_version != imgui_version || !host->root_directory || !*host->root_directory ||
        !host->require_service || !host->on_frame || !host->log) {
        return false;
    }
    try {
        const auto* service = static_cast<const ArkBattleEventsApiV2*>(host->require_service(
            host->owner, ARK_BATTLE_SERVICE_NAME_V2, ARK_BATTLE_ABI_VERSION_V2, sizeof(ArkBattleEventsApiV2)));
        if (!service || service->size < sizeof(*service) ||
            service->abi_version != ARK_BATTLE_ABI_VERSION_V2 || !service->read) {
            host->log(host->owner, 3, "ArkDPS requires the ark.battle.events.v2 read service");
            return false;
        }
        auto writer = std::make_unique<Journal>(host, service);
        writer->open();
        if (!host->on_frame(host->owner, on_frame, writer.get())) {
            writer->fail("could not register ArkDPS frame callback");
            return false;
        }
        journal = std::move(writer);
        return true;
    } catch (const std::exception& error) {
        host->log(host->owner, 3, error.what());
        return false;
    } catch (...) {
        host->log(host->owner, 3, "could not start ArkDPS capture");
        return false;
    }
}

ARK_EXPORT bool ark_plugin_shutdown() {
    if (journal) {
        journal->finish();
        journal.reset();
    }
    return true;
}
