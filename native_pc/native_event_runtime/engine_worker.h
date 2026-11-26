#pragma once
#include <chrono>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <future>
#include <mutex>
#include <string>
#include <thread>

namespace arkdps {
// Process-lifetime worker. Only host threads submit jobs or wait on futures;
// native callback producers never touch this mailbox, mutex or condition.
class EngineWorker final {
public:
    void start(std::function<void()> initialize, std::function<bool()> poll) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (started_) throw std::runtime_error("native worker already started");
        started_ = true;
        worker_ = std::thread([this, initialize = std::move(initialize), poll = std::move(poll)] {
            try { initialize(); } catch (...) { startup_error_ = std::current_exception(); }
            {
                std::lock_guard<std::mutex> guard(mutex_);
                initialized_ = true;
            }
            changed_.notify_all();
            for (;;) {
                const bool ready_more=!startup_error_ && poll();
                std::packaged_task<std::string()> job;
                {
                    std::unique_lock<std::mutex> guard(mutex_);
                    if (jobs_.empty() && !ready_more) changed_.wait_for(guard, std::chrono::milliseconds(1));
                    if (!jobs_.empty()) { job = std::move(jobs_.front()); jobs_.pop_front(); }
                }
                if (job.valid()) job();
            }
        });
    }

    void wait_initialized() {
        std::unique_lock<std::mutex> guard(mutex_);
        changed_.wait(guard, [this] { return initialized_; });
        if (startup_error_) std::rethrow_exception(startup_error_);
    }

    std::string submit(std::function<std::string()> operation) {
        std::packaged_task<std::string()> task(std::move(operation));
        auto result = task.get_future();
        {
            std::lock_guard<std::mutex> guard(mutex_);
            if (!initialized_) throw std::runtime_error("native worker not initialized");
            if (startup_error_) std::rethrow_exception(startup_error_);
            jobs_.push_back(std::move(task));
        }
        changed_.notify_one();
        return result.get();
    }
private:
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<std::packaged_task<std::string()>> jobs_;
    std::thread worker_;
    std::exception_ptr startup_error_;
    bool started_ = false, initialized_ = false;
};
} // namespace arkdps
