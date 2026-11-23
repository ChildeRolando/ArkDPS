#include <cstdint>
#include <deque>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "json.hpp"

namespace {
using Json = nlohmann::json;
using Sequence = std::uint64_t;

constexpr int protocol_version = 1;
constexpr int source_version = 1;
constexpr auto source_provider = "internal-test-host";
constexpr std::size_t max_records = 4096;
constexpr std::size_t max_queue_bytes = 8 * 1024 * 1024;
constexpr std::size_t max_line_bytes = 8 * 1024 * 1024;
constexpr std::size_t max_mark_bytes = 256 * 1024;
constexpr int max_json_depth = 64;

void require(bool condition, const char* reason) {
    if (!condition) {
        throw std::runtime_error(reason);
    }
}

Sequence integer(const Json& value) {
    require(value.is_number_integer(), "expected a nonnegative integer");
    if (value.is_number_unsigned()) {
        return value.get<Sequence>();
    }
    const auto number = value.get<std::int64_t>();
    require(number >= 0, "expected a nonnegative integer");
    return static_cast<Sequence>(number);
}

struct StreamIdentity {
    std::string session;
    std::string build;

    explicit StreamIdentity(const Json& mark) {
        require(mark.is_object(), "watermark must be an object");
        for (const auto* key : {"native_session", "source_build"}) {
            const auto field = mark.find(key);
            require(field != mark.end() && field->is_string() && !field->get_ref<const std::string&>().empty(),
                    "watermark is missing stream identity");
        }
        session = mark.at("native_session").get<std::string>();
        build = mark.at("source_build").get<std::string>();
    }

    bool matches(const Json& value) const {
        return value.value("native_session", Json(session)) == session
            && value.value("source_build", Json(build)) == build;
    }
};

void validate_clock(const Json& value) {
    integer(value.at("frame"));
    const auto& seconds = value.at("game_time");
    require(seconds.is_number() && seconds >= 0, "invalid game time");
}

void clock_before_or_equal(const Json& earlier, const Json& later) {
    const auto& first_frame = earlier.at("frame");
    const auto& last_frame = later.at("frame");
    const auto& first_time = earlier.at("game_time");
    const auto& last_time = later.at("game_time");
    require(first_frame <= last_frame, "frame went backwards");
    require(first_time <= last_time, "game time went backwards");
    require(first_frame != last_frame || first_time == last_time, "same frame has a different game time");
}

struct Record {
    Sequence sequence;
    std::size_t bytes;
    Json event;
};

class EventHost {
public:
    Json handle(const Json& request, const std::string& operation) {
        if (operation == "hello") {
            return Json{{"protocol", protocol_version}, {"provider", source_provider},
                        {"source_version", source_version}};
        }
        if (operation == "advance") {
            advance(request);
            return true;
        }
        if (operation == "mark") {
            return mark_;
        }
        if (operation == "terminal") {
            return terminal_;
        }
        if (operation == "read") {
            return read(integer(request.at("sequence")), request.value("baseline", false));
        }
        if (operation == "commit") {
            return commit(integer(request.at("sequence")));
        }
        if (operation == "stop") {
            stopped = true;
            return true;
        }
        throw std::runtime_error("unknown operation");
    }

    bool stopped = false;

private:
    Json mark_ = Json::object();
    Json terminal_ = Json::object();
    std::deque<Record> records_;
    std::size_t queue_bytes_ = 0;
    Sequence newest_ = 0;
    Sequence committed_ = 0;
    Sequence read_ceiling_ = 0;

    void advance(const Json& request) {
        auto mark = request.at("mark");
        const auto stream = StreamIdentity(mark);
        const auto through = integer(mark.at("sequence"));
        const auto epoch = integer(mark.at("epoch"));
        integer(mark.at("active"));
        require(mark.dump().size() <= max_mark_bytes && !mark.contains("events"), "invalid watermark payload");
        validate_clock(mark);
        const bool first = mark_.empty();
        if (!first) {
            require(stream.matches(mark_), "cannot change stream identity");
            require(through >= newest_, "watermark went backwards");
            require(epoch >= integer(mark_.at("epoch")), "watermark generation went backwards");
            clock_before_or_equal(mark_, mark);
            require(terminal_.empty() || mark.dump() == mark_.dump(), "cannot advance a finished stream");
        }

        auto terminal = request.value("terminal_mark", Json(nullptr));
        if (!terminal.is_null()) {
            const auto terminal_stream = StreamIdentity(terminal);
            require(terminal_stream.matches(mark), "terminal has a different stream identity");
            require(!terminal.contains("events") && terminal.dump().size() <= max_mark_bytes,
                    "invalid terminal payload");
            require(integer(terminal.at("sequence")) == through, "terminal does not match its watermark");
            require(integer(terminal.at("epoch")) == epoch && integer(terminal.at("active")) == 0
                        && integer(mark.at("active")) == 0,
                    "terminal generation or callback activity disagrees with watermark");
            validate_clock(terminal);
            clock_before_or_equal(terminal, mark);
            clock_before_or_equal(mark, terminal);
            require(terminal_.empty() || terminal.dump() == terminal_.dump(),
                    "terminal disagrees with accepted proof");
        }

        const auto& events = request.at("events");
        require(events.is_array(), "events must be an array");
        require(events.size() <= max_records, "event batch exceeds queue capacity");
        Sequence baseline = newest_;
        if (first) {
            baseline = through;
            if (!events.empty()) {
                const auto earliest = integer(events.front().at("sequence"));
                require(earliest > 0, "event sequence must be positive");
                baseline = earliest - 1;
            }
        }
        const auto next_commit = first ? baseline : committed_;
        auto verified = baseline;
        const Json* previous = nullptr;
        const Json* verified_clock = first ? nullptr : &mark_;
        Sequence previous_sequence = 0;
        std::deque<Record> additions;
        std::size_t added_bytes = 0;
        for (const auto& event : events) {
            require(event.is_object() && stream.matches(event), "event has an invalid stream identity");
            const auto sequence = integer(event.at("sequence"));
            require(sequence > 0 && sequence <= through, "event lies outside its watermark");
            validate_clock(event);
            clock_before_or_equal(event, mark);
            const auto encoded = event.dump();
            if (previous && sequence == previous_sequence) {
                require(encoded == previous->dump(), "conflicting duplicate event");
                continue;
            }
            require(sequence > previous_sequence, "event batch is out of order");
            previous = &event;
            previous_sequence = sequence;
            if (sequence <= baseline) {
                require(!records_.empty() && sequence >= records_.front().sequence,
                        "event replay predates the retained queue");
                const auto index = static_cast<std::size_t>(sequence - records_.front().sequence);
                require(index < records_.size() && records_[index].event.dump() == encoded,
                        "conflicting event replay");
                continue;
            }
            require(sequence - verified == 1, "event sequence gap");
            if (verified_clock) {
                clock_before_or_equal(*verified_clock, event);
            }
            verified = sequence;
            verified_clock = &event;
            const auto bytes = encoded.size();
            added_bytes += bytes;
            additions.push_back({sequence, bytes, event});
        }
        require(verified == through, "events do not reach the watermark");

        auto next_size = records_.size() + additions.size();
        auto next_bytes = queue_bytes_ + added_bytes;
        std::size_t trim = 0;
        // Keep acknowledged payloads for lost responses and duplicate checks
        // until bounded capacity is needed for new records.
        while (next_size > max_records || next_bytes > max_queue_bytes) {
            require(trim < records_.size() && records_[trim].sequence <= next_commit,
                    "uncommitted queue capacity exceeded; event stream gap");
            --next_size;
            next_bytes -= records_[trim].bytes;
            ++trim;
        }

        // All protocol validation above is read-only. Rejecting a bad batch
        // leaves its watermark, terminal proof and queue unchanged.
        for (auto& record : additions) {
            records_.push_back(std::move(record));
        }
        for (std::size_t removed = 0; removed < trim; ++removed) {
            records_.pop_front();
        }
        queue_bytes_ = next_bytes;
        newest_ = through;
        committed_ = next_commit;
        mark_ = std::move(mark);
        if (!terminal.is_null()) {
            terminal_ = std::move(terminal);
        }
    }

    Json read(Sequence through, bool baseline) {
        require(!mark_.empty(), "no watermark established");
        require(through <= newest_, "read exceeds the newest watermark");
        auto result = mark_;
        auto& events = result["events"] = Json::array();
        if (!baseline) {
            for (const auto& record : records_) {
                if (record.sequence > through) {
                    break;
                }
                if (record.sequence > committed_) {
                    events.push_back(record.event);
                }
            }
        }
        if (through > read_ceiling_) {
            read_ceiling_ = through;
        }
        return result;
    }

    Json commit(Sequence through) {
        if (mark_.empty() || (through > committed_ && through > read_ceiling_)) {
            return Json{{"acknowledged", nullptr}};
        }
        if (through > committed_) {
            committed_ = through;
        }
        return Json{{"acknowledged", through}};
    }
};

Json parse_request(const std::string& line) {
    require(line.compare(0, 3, "\xef\xbb\xbf") != 0, "request must be UTF-8 without a BOM");
    std::vector<std::unordered_set<std::string>> keys;
    const auto validate = [&keys](int depth, Json::parse_event_t event, Json& value) {
        require(depth <= max_json_depth, "request exceeds JSON nesting limit");
        if (event == Json::parse_event_t::object_start) {
            keys.emplace_back();
        } else if (event == Json::parse_event_t::object_end) {
            keys.pop_back();
        } else if (event == Json::parse_event_t::key) {
            require(keys.back().insert(value.get<std::string>()).second, "duplicate JSON field");
        }
        return true;
    };
    auto request = Json::parse(line, validate, false);
    require(request.is_object(), "request must be a UTF-8 JSON object");
    return request;
}

bool next_line(std::istream& input, std::string& line, bool& oversized) {
    line.clear();
    oversized = false;
    auto* buffer = input.rdbuf();
    for (auto character = buffer->sbumpc(); character != std::char_traits<char>::eof();
         character = buffer->sbumpc()) {
        if (character == '\n') {
            return true;
        }
        if (line.size() < max_line_bytes) {
            line.push_back(static_cast<char>(character));
        } else {
            oversized = true;
        }
    }
    return false;  // An interrupted final write is not a complete request.
}
}    // namespace

int main() {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    EventHost host;
    std::string line;
    std::optional<Sequence> previous_id;
    std::string previous_commit;
    Json previous_response;
    bool oversized = false;
    while (!host.stopped && next_line(std::cin, line, oversized)) {
        Json id = nullptr;
        Json response;
        try {
            require(!oversized, "request exceeds line length limit");
            const auto request = parse_request(line);
            const auto& incoming_id = request.at("id");
            const auto number = integer(incoming_id);
            id = incoming_id;
            require(integer(request.at("version")) == protocol_version, "unsupported request version");
            require(number > 0 && (!previous_id || number >= *previous_id), "request ID went backwards");
            const auto operation = request.at("op").get<std::string>();
            if (previous_id && number == *previous_id) {
                require(!previous_commit.empty() && line == previous_commit, "conflicting request replay");
                response = previous_response;
            } else {
                previous_id = number;
                previous_commit.clear();
                auto result = host.handle(request, operation);
                response = Json{{"version", protocol_version}, {"id", id}, {"result", std::move(result)}};
                if (operation == "commit") {
                    previous_commit = line;
                    previous_response = response;
                }
            }
        } catch (const std::bad_alloc&) {
            // Continuing after a failed allocation might expose a partial commit.
            return 1;
        } catch (const Json::exception&) {
            response = Json{{"version", protocol_version}, {"id", id},
                            {"error", "missing or invalid request field"}};
        } catch (const std::exception& error) {
            response = Json{{"version", protocol_version}, {"id", id}, {"error", error.what()}};
        }
        std::cout << response.dump() << '\n' << std::flush;
        if (!std::cout) {
            return 1;
        }
    }
}
