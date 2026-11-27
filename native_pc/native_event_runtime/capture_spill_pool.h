#pragma once
#include "capture_queue.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>

namespace arkdps::capture {
// Descriptors live for the stream. A queue is replaceable only while its owner
// excludes writers; no producer follows a pointer until after its owner CAS.
class CaptureSpillPool final {
public:
    static constexpr std::size_t bank_count=4,max_bank_count=128;
    static constexpr std::uint32_t busy=0x80000000u,releasing=0x40000000u;
    static constexpr std::uint32_t owner_mask=releasing-1;
    static constexpr std::uint16_t no_bank=static_cast<std::uint16_t>(max_bank_count);
    struct ProducerCursor {std::uint16_t bank=no_bank;std::array<std::uint64_t,2> owned{};};
    struct Config {
        std::size_t metadata_slots=32768,arena_bytes=24*1024*1024;
        std::size_t banks=bank_count;
        bool elastic=false;
        std::size_t warm_banks=4,free_low_water=4;
        std::uint64_t idle_milliseconds=1000;
    };
    CaptureSpillPool():CaptureSpillPool(Config{}){}
    explicit CaptureSpillPool(Config config):config_(config) {
        if(!config_.banks||config_.banks>max_bank_count||!config_.metadata_slots||!config_.arena_bytes
                ||!config_.warm_banks||config_.warm_banks>config_.banks
                ||!config_.free_low_water||config_.free_low_water>config_.warm_banks)
            throw std::invalid_argument("invalid capture spill bank geometry");
        const auto initial=config_.elastic?config_.warm_banks:config_.banks;
        for(std::size_t i=0;i<initial;++i)prepare(i);
    }
    CaptureSpillPool(const CaptureSpillPool&)=delete;
    CaptureSpillPool& operator=(const CaptureSpillPool&)=delete;

    bool try_push(std::uint32_t tag,std::uint64_t ticket,const void* payload,std::size_t size,
                  ProducerCursor* cached=nullptr) noexcept {
        if(!tag||tag>owner_mask||size>config_.arena_bytes||(size&&!payload))return false;
        if(!config_.elastic||!cached){
            // Compatibility for small, eager standalone queues. Production's
            // elastic path below never scans the descriptor table per edge.
            for(unsigned pass=0;pass<2;++pass)for(std::size_t i=0;i<config_.banks;++i)
                if(push_bank(i,pass?0u:tag,tag,ticket,payload,size)){if(cached)remember(*cached,i);return true;}
            return false;
        }
        if(cached->bank<config_.banks&&push_bank(cached->bank,tag,tag,ticket,payload,size))return true;
        // Only bits in the two prepared-free words are candidates. Each bit is
        // attempted at most once, with no retry loop, allocation or page work.
        // A race may remove a hint; owner CAS remains the authority.
        for(std::size_t word=0;word<free_.size();++word){
            const auto free=free_[word].load(std::memory_order_acquire);
            auto bits=free|cached->owned[word];
            while(bits){const unsigned bit=lowest_bit(bits);bits&=bits-1;
                const auto index=word*64+bit;
                const auto mask=std::uint64_t{1}<<bit;
                if(index!=cached->bank&&(cached->owned[word]&mask)&&push_bank(index,tag,tag,ticket,payload,size)){
                    remember(*cached,index);return true;}
                if((free&mask)&&push_bank(index,0,tag,ticket,payload,size)){remember(*cached,index);return true;}
            }
        }
        return false;
    }
    std::optional<std::uint64_t> head_ticket(std::size_t index)const noexcept {
        return index<config_.banks&&banks_[index].queue?banks_[index].queue->head_ticket():std::nullopt;
    }
    CaptureQueue::PopResult try_pop(std::size_t index,std::uint64_t& ticket,void* destination,
                                   std::size_t capacity,std::size_t& size)noexcept {
        if(index>=config_.banks||!banks_[index].queue){size=0;return CaptureQueue::PopResult::empty;}
        return banks_[index].queue->try_pop(ticket,destination,capacity,size);
    }
    bool reap_empty(std::size_t index)noexcept {
        if(index>=config_.banks)return false;
        auto& bank=banks_[index];
        if(!bank.queue||bank.queue->head_ticket())return false;
        auto owner=bank.owner.load(std::memory_order_acquire);
        if(!owner||(owner&(busy|releasing)))return false;
        const auto previous=owner;
        if(!bank.owner.compare_exchange_strong(owner,previous|releasing,std::memory_order_acq_rel))return false;
        if(bank.queue->head_ticket()){bank.owner.store(previous,std::memory_order_release);return false;}
        bank.empty_since=clock_ms();
        bank.owner.store(0,std::memory_order_release);free_bit(index,true);
        return true;
    }
    // Worker only; no queue is prepared or destroyed on a callback. Limit each
    // poll to one allocation and one trim, with a grace period after last use.
    void maintain()noexcept {
        if(!config_.elastic)return;
        const auto now=clock_ms();
        // A pop can have found a busy writer and missed its empty-bank reap.
        // Retry four descriptors per poll, independently of call-stack state.
        for(unsigned attempt=0;attempt<4;++attempt){reap_empty(reap_cursor_);reap_cursor_=(reap_cursor_+1)%config_.banks;}
        if(now>=next_prepare_retry_ms_&&free_banks()<config_.free_low_water&&prepared_banks()<config_.banks){
            for(std::size_t i=0;i<config_.banks;++i)
                if(banks_[i].owner.load(std::memory_order_acquire)==releasing){
                    try{prepare(i);}catch(...){prepare_failures_.fetch_add(1,std::memory_order_relaxed);next_prepare_retry_ms_=clock_ms()+1000;}break;
                }
        }
        if(prepared_banks()<=config_.warm_banks||free_banks()<=config_.warm_banks)return;
        for(std::size_t i=config_.banks;i-->0;){auto& bank=banks_[i];
            if(!bank.queue||now-bank.empty_since<config_.idle_milliseconds)continue;
            auto expected=0u;
            if(!bank.owner.compare_exchange_strong(expected,releasing,std::memory_order_acq_rel))continue;
            // A producer that removed a free hint but lost the owner CAS cannot
            // dereference the queue. The single consumer is this same worker.
            free_bit(i,false);
            if(bank.queue->head_ticket()){bank.owner.store(0,std::memory_order_release);free_bit(i,true);continue;}
            bank.queue.reset();prepared_.fetch_sub(1,std::memory_order_relaxed);
            trimmed_.fetch_add(1,std::memory_order_relaxed);break;
        }
    }
    std::uint32_t owner_state(std::size_t i)const noexcept{return i<config_.banks?banks_[i].owner.load(std::memory_order_acquire):0;}
    std::uint32_t bank_owner(std::size_t i)const noexcept{return owner_state(i)&owner_mask;}
    bool has_owner_pending(std::uint32_t tag)const noexcept {
        if(!tag||tag>owner_mask)return false;
        for(std::size_t i=0;i<config_.banks;++i)if(bank_owner(i)==tag)return true;
        return false;
    }
    std::uint32_t prepared_banks()const noexcept{return prepared_.load(std::memory_order_relaxed);}
    std::uint32_t free_banks()const noexcept {
        std::uint32_t total=0;
        // Worker/diagnostic only. Free-word hints can survive an old delayed
        // claimant across a reap. They must never inflate maintenance's supply.
        for(std::size_t i=0;i<config_.banks;++i)if(banks_[i].owner.load(std::memory_order_acquire)==0)++total;
        return total;
    }
    std::size_t configured_banks()const noexcept{return config_.banks;}
    std::uint64_t trimmed()const noexcept{return trimmed_.load(std::memory_order_relaxed);}
    std::uint64_t prepare_failures()const noexcept{return prepare_failures_.load(std::memory_order_relaxed);}
    Config capacity()const noexcept{return config_;}
    // Failure-only bounded snapshot. Pin owned/free queues before looking at
    // producer cursors; never race destruction or read a foreign producer cursor.
    SpillFailureSnapshot failure_snapshot(std::uint32_t tag,std::size_t bytes)noexcept {
        SpillFailureSnapshot out;out.configured_banks=static_cast<std::uint32_t>(config_.banks);
        out.prepared_banks=prepared_banks();
        for(std::size_t i=0;i<config_.banks;++i){auto& bank=banks_[i];auto state=bank.owner.load(std::memory_order_acquire);
            if(state==releasing){++out.unprepared_banks;continue;}
            if(state&(busy|releasing)){++out.busy_banks;continue;}
            if(state&&state!=tag){++out.foreign_owned_banks;continue;}
            if(state==tag)++out.owned_banks;else ++out.free_banks;
            const auto previous=state;
            if(!bank.owner.compare_exchange_strong(state,previous|busy,std::memory_order_acq_rel)){++out.busy_banks;continue;}
            const auto q=bank.queue->producer_occupancy();
            if(q.used_slots>=q.slot_capacity)++out.slot_rejections;
            if(bytes>q.arena_capacity-q.used_bytes)++out.byte_rejections;
            if(bytes>q.arena_capacity)++out.oversize_rejections;
            bank.owner.store(previous,std::memory_order_release);
        }
        return out;
    }
private:
    struct Bank {
        alignas(64)std::atomic<std::uint32_t> owner{releasing};
        std::unique_ptr<CaptureQueue> queue;
        std::uint64_t empty_since=0;
    };
    static unsigned lowest_bit(std::uint64_t bits)noexcept {
        unsigned bit=0;while((bits&1u)==0u){bits>>=1;++bit;}return bit;
    }
    static void remember(ProducerCursor& cursor,std::size_t i)noexcept {
        cursor.bank=static_cast<std::uint16_t>(i);cursor.owned[i/64]|=std::uint64_t{1}<<(i%64);
    }
    static std::uint64_t clock_ms()noexcept {
        return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    }
    void free_bit(std::size_t i,bool available)noexcept {
        const auto bit=std::uint64_t{1}<<(i%64);
        if(available)free_[i/64].fetch_or(bit,std::memory_order_release);
        else free_[i/64].fetch_and(~bit,std::memory_order_acq_rel);
    }
    bool push_bank(std::size_t i,std::uint32_t expected,std::uint32_t tag,std::uint64_t ticket,
                   const void* payload,std::size_t size)noexcept {
        if(i>=config_.banks)return false;
        auto& bank=banks_[i];
        const bool claimed_free=expected==0;
        if(!bank.owner.compare_exchange_strong(expected,tag|busy,std::memory_order_acq_rel,std::memory_order_relaxed))return false;
        if(claimed_free)free_bit(i,false);
        const bool pushed=bank.queue->push(ticket,payload,size);
        bank.owner.store(tag,std::memory_order_release);return pushed;
    }
    void prepare(std::size_t i){
        auto& bank=banks_[i];
        bank.queue=std::make_unique<CaptureQueue>(CaptureQueue::Config{config_.metadata_slots,config_.arena_bytes},config_.elastic);
        bank.empty_since=clock_ms();prepared_.fetch_add(1,std::memory_order_relaxed);
        bank.owner.store(0,std::memory_order_release);free_bit(i,true);
    }
    const Config config_;
    std::array<Bank,max_bank_count> banks_{};
    std::array<std::atomic<std::uint64_t>,2> free_{};
    std::atomic<std::uint32_t> prepared_{0};
    std::atomic<std::uint64_t> trimmed_{0},prepare_failures_{0};
    std::uint64_t next_prepare_retry_ms_=0;
    std::size_t reap_cursor_=0;
};
static_assert(std::atomic<std::uint32_t>::is_always_lock_free&&std::atomic<std::uint64_t>::is_always_lock_free,
              "spill ownership and prepared masks require lock-free atomics");
} // namespace arkdps::capture
