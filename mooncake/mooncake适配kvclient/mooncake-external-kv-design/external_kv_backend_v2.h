#pragma once

// Contract sketch for review; not an integrated Mooncake implementation.
// Lookup/load are intentionally left to their owners.
#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include <variant>
#include <vector>

namespace mooncake::external_kv_v2 {

enum class Code {
    kOk, kInvalidArgument, kUnsupported, kBusy, kConflict,
    kUnavailable, kIoError, kUnknownOutcome, kInternal
};
struct Status {
    Code code = Code::kOk;
    std::string message;
};

// Namespace includes the agreed tenant isolation domain. No SDK key type.
struct ObjectKey {
    std::string name_space;
    std::string key;
};

struct SourceSlice {
    const void* address = nullptr;
    std::uint64_t size = 0;
    // "host", "cuda", "ascend", or another backend-supported domain.
    std::string memory_domain;
    std::int32_t device_id = -1;
};

struct DumpRequest {
    ObjectKey key;
    // Complete object = concatenation in order; not partial object updates.
    std::vector<SourceSlice> sources;
    std::uint64_t logical_size = 0;
};

enum class Effect {
    kApplied,
    kAlreadyAbsent,  // Successful idempotent delete only.
    kNotApplied,
    kUnknown
};
struct ItemResult {
    Status status;
    Effect effect = Effect::kUnknown;
};

struct MutationSnapshot {
    bool done = false;
    // True only after all payload access has ended or has been fenced.
    // Neither an error result nor a local deadline proves this condition.
    bool sources_released = false;
    // Empty until done; thereafter one result per submitted item, in order.
    std::vector<ItemResult> items;
};

class MutationOperation {
 public:
    virtual ~MutationOperation() = default;
    virtual MutationSnapshot Poll() = 0;
    // Local wait only: never cancels, consumes, or forgets an operation.
    virtual MutationSnapshot WaitUntil(
        std::chrono::steady_clock::time_point deadline) = 0;
    // Blocks until done AND sources_released, and returns that snapshot.
    // Cannot promise a finite bound without transport fencing support.
    virtual MutationSnapshot Drain() = 0;
    // Concrete destruction MUST Drain before releasing operation resources.
    // The caller still owns payload allocations and must keep them immutable
    // and mapped until sources_released. Metadata ownership is not payload ownership.
};

using SubmitResult =
    std::variant<std::unique_ptr<MutationOperation>, Status>;

struct Capabilities {
    std::vector<std::string> source_memory_domains;
    bool scatter_gather = false;
    bool empty_objects = false;
    std::uint64_t source_address_alignment = 1;
    std::uint64_t source_size_alignment = 1;
    // Zero means implementation-defined: validation is still required.
    std::uint64_t max_key_bytes = 0;
    std::uint64_t max_object_bytes = 0;
    std::uint64_t max_batch_items = 0;
};

class ExternalKVBackend {
 public:
    virtual ~ExternalKVBackend() = default;
    virtual Capabilities GetCapabilities() const = 0;
    // Copies metadata before return; retains its registration/task resources.
    // Status alternative is non-OK and guarantees NO item was dispatched.
    // Any possible partial dispatch MUST return a non-null operation instead.
    // Same logical key is immutable; no overwrite/CAS promise is implied.
    virtual SubmitResult DumpAsync(
        const std::vector<DumpRequest>& requests) = 0;
    virtual SubmitResult DeleteAsync(
        const std::vector<ObjectKey>& keys) = 0;
    // Adapter must enforce same-key mutation ordering within this instance.
    // Unknown prior mutations require reconciliation/fencing or kBusy; never
    // start a later mutation that a delayed prior request could invalidate.
    // No cluster-wide ordering or delete-vs-load contract is declared here.
};

}  // namespace mooncake::external_kv_v2
