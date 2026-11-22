// Load the consumer into this test process and supply the public SDK service.
#include <ark/battle.h>
#include <ark/plugin.h>
#include <windows.h>

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
ArkFrameCallback callback = nullptr;
void* callback_user = nullptr;
std::vector<ArkEventV2> history;
uint32_t partial = 0;

size_t read_events(uint64_t after, ArkEventV2* out, size_t capacity, ArkReadStatusV1* status) {
    *status = {};
    status->partial = partial;
    if (history.empty()) return 0;
    status->oldest_sequence = history.front().sequence;
    status->newest_sequence = history.back().sequence;
    status->overrun = after + 1 < status->oldest_sequence;
    status->ended = history.back().kind == ARK_BATTLE_END;
    size_t copied = 0;
    for (const auto& record : history) {
        if (record.sequence > after && copied < capacity) out[copied++] = record;
    }
    return copied;
}

bool cached_frame(ArkBattleFrameV1* out) {
    if (history.empty()) return false;
    out->frame = history.back().frame;
    out->battle = history.back().battle;
    return true;
}

const void* require_service(void*, const char* name, uint32_t abi, size_t minimum_size) {
    static const ArkBattleEventsApiV2 service{
        sizeof(ArkBattleEventsApiV2), ARK_BATTLE_ABI_VERSION_V2, read_events, cached_frame
    };
    if (std::strcmp(name, ARK_BATTLE_SERVICE_NAME_V2) != 0
        || abi != ARK_BATTLE_ABI_VERSION_V2 || minimum_size > sizeof(service)) return nullptr;
    return &service;
}

void tick() {
    ArkFrameV1 frame;
    frame.imgui_version = ARKDPS_IMGUI_VERSION;
    frame.delta_time = 1. / 30.;
    callback(callback_user, &frame);
}

ArkEventV2 record(ArkEventKind kind, uint64_t sequence, uint32_t frame, uint32_t index) {
    ArkEventV2 event;
    event.kind = kind;
    event.sequence = sequence;
    event.battle = 21;
    event.frame = frame;
    event.frame_event_index = index;
    event.game_time = frame / 30.;
    std::strcpy(event.stage_id, "stage-test");
    return event;
}

void feed(bool invalid) {
    history.push_back(record(ARK_BATTLE_START, 1, 0, 0));
    tick();
    auto damage = record(ARK_DAMAGE, 2, 30, 0);
    damage.calculated_damage = 125.5;
    damage.actual_damage = 99.25;
    damage.damage_type = ARK_DAMAGE_PHYSICAL;
    std::strcpy(damage.actor_id, "token_\"A\"");
    std::strcpy(damage.owner_id, "char_host");
    std::strcpy(damage.original_source_id, "char_original");
    std::strcpy(damage.target_id, "enemy_target");
    history.push_back(damage);
    auto heal = record(ARK_DAMAGE, 3, 30, 1);
    heal.damage_type = ARK_DAMAGE_HEAL;
    heal.calculated_damage = 80;
    heal.actual_damage = 23.5;
    std::strcpy(heal.actor_id, "char_medic");
    std::strcpy(heal.target_id, "char_host");
    history.push_back(heal);
    if (invalid) history.push_back(heal); // Duplicate sequence within this batch.
    tick();
    if (invalid) return;
    tick(); // Polling a second time must not write either event again.
    auto no_hp_change = heal;
    no_hp_change.sequence = 4;
    no_hp_change.frame_event_index = 2;
    no_hp_change.actual_damage = 0;
    history.push_back(no_hp_change);
    auto elemental = record(ARK_ELEMENTAL, 5, 60, 0);
    elemental.damage_type = ARK_DAMAGE_HEAL; // A type alone cannot establish an HP settlement.
    history.push_back(elemental);
    history.push_back(record(ARK_BATTLE_END, 6, 60, 1));
    tick();
    auto next = record(ARK_CLOCK, 9, 3, 0);
    next.battle = 22;
    next.partial = 1;
    partial = 1;
    history = {next}; // Missing seq 7–8; the provider now retains only seq 9.
    tick();
}
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 4) return 2;
    const auto module = LoadLibraryW(argv[1]);
    if (!module) {
        std::fprintf(stderr, "LoadLibraryW failed: %lu\n", GetLastError());
        return 1;
    }
    try {
        const auto query = reinterpret_cast<ArkPluginQuery>(GetProcAddress(module, "ark_plugin_query"));
        const auto initialize = reinterpret_cast<ArkPluginInitialize>(GetProcAddress(module, "ark_plugin_initialize"));
        const auto shutdown = reinterpret_cast<ArkPluginShutdown>(GetProcAddress(module, "ark_plugin_shutdown"));
        if (!query || !initialize || !shutdown) throw std::runtime_error("missing SDK export");
        const auto* info = query();
        if (!info || info->size < sizeof(*info) || info->abi_version != ARK_ABI_VERSION
            || info->imgui_version != ARKDPS_IMGUI_VERSION || info->dependency_count != 1
            || std::strcmp(info->dependencies[0], "damage_meter.dll") != 0)
            throw std::runtime_error("invalid plugin metadata");
        const auto root = std::filesystem::path(argv[2]).u8string();
        ArkPluginApiV1 api{};
        api.size = sizeof(api);
        api.abi_version = ARK_ABI_VERSION;
        api.imgui_version = ARKDPS_IMGUI_VERSION;
        api.test_host = 1;
        api.owner = &api;
        api.root_directory = root.c_str();
        api.log = [](void*, uint32_t, const char* text) { std::fprintf(stderr, "%s\n", text); };
        api.on_frame = [](void*, ArkFrameCallback fn, void* user) -> uint64_t {
            callback = fn;
            callback_user = user;
            return 1;
        };
        api.remove_callback = [](void*, uint64_t token) { callback = nullptr; return token == 1; };
        api.require_service = require_service;
        if (!initialize(&api) || !callback) throw std::runtime_error("collector failed to subscribe");
        const std::wstring scenario(argv[3]);
        if (scenario == L"backlog") {
            history.push_back(record(ARK_BATTLE_START, 1, 0, 0));
            for (uint32_t sequence = 2; sequence <= 600; ++sequence)
                history.push_back(record(ARK_CLOCK, sequence, sequence, 0));
            tick(); // Leave further pages pending for shutdown's bounded drain.
        } else if (scenario == L"valid" || scenario == L"invalid") {
            feed(scenario == L"invalid");
        } else {
            throw std::runtime_error("unknown test scenario");
        }
        callback = nullptr; // The SDK host drains registered callbacks before shutdown.
        if (!shutdown()) throw std::runtime_error("collector failed to stop");
        if (!FreeLibrary(module)) throw std::runtime_error("collector failed to unload");
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
    return 0;
}
