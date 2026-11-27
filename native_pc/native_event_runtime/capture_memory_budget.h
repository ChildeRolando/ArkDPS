#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace arkdps::capture {
constexpr std::size_t capture_page_bytes=4096;
inline std::size_t capture_page_round(std::size_t bytes) {
    if(bytes>std::numeric_limits<std::size_t>::max()-(capture_page_bytes-1))
        throw std::invalid_argument("capture storage size overflow");
    return (bytes+capture_page_bytes-1)&~(capture_page_bytes-1);
}
// Created/destroyed only by the consumer before publishing an available bank.
// VirtualFree returns Windows commitment; heap deletion alone cannot promise it.
class CaptureStorage final {
public:
    CaptureStorage(std::size_t bytes,bool mapped):size_(mapped?capture_page_round(bytes):bytes),mapped_(mapped) {
#ifdef _WIN32
        if(mapped_){data_=static_cast<std::byte*>(VirtualAlloc(nullptr,size_,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
            if(!data_)throw std::bad_alloc();}
        else
#endif
        {heap_.reset(new std::byte[size_]);data_=heap_.get();}
        // Prefault every page, including the final one, on this worker only.
        auto* touched=reinterpret_cast<volatile unsigned char*>(data_);
        for(std::size_t i=0;i<size_;i+=capture_page_bytes)touched[i]=0;
        if(size_)touched[size_-1]=0;
    }
    ~CaptureStorage(){
#ifdef _WIN32
        if(mapped_&&data_)VirtualFree(data_,0,MEM_RELEASE);
#endif
    }
    CaptureStorage(const CaptureStorage&)=delete;
    CaptureStorage& operator=(const CaptureStorage&)=delete;
    std::byte* data() noexcept{return data_;}
    std::size_t size()const noexcept{return size_;}
private:
    const std::size_t size_;
    const bool mapped_;
    std::unique_ptr<std::byte[]> heap_;
    std::byte* data_=nullptr;
};
struct QueueOccupancy {
    std::uint64_t slot_capacity=0,arena_capacity=0,used_slots=0,used_bytes=0;
};
struct SpillFailureSnapshot {
    std::uint32_t configured_banks=0,prepared_banks=0,free_banks=0,owned_banks=0,
        foreign_owned_banks=0,busy_banks=0,unprepared_banks=0,
        slot_rejections=0,byte_rejections=0,oversize_rejections=0;
};
struct QueueFailureSnapshot {
    bool available=false;
    std::uint64_t ticket=0;
    std::uint32_t lane=0;
    std::uint64_t attempted_bytes=0;
    QueueOccupancy private_queue;
    SpillFailureSnapshot spill;
};
struct QueueMemoryStatus {
    bool enabled=false;
    std::uint32_t requested_budget_mib=0;
    std::uint64_t budget_bytes=0,maximum_storage_bytes=0,prepared_storage_bytes=0,
        fixed_capture_working_bytes=0,slot_capacity=0,arena_capacity=0,
        prepared_slot_capacity=0,prepared_arena_capacity=0;
    std::uint32_t maximum_banks=0,prepared_banks=0,free_banks=0;
    std::uint64_t trimmed=0,prepare_failures=0;
};
} // namespace arkdps::capture
