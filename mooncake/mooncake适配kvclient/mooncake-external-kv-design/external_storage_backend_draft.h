#pragma once

// Review draft, not a patch to Mooncake. Only write/delete are specified.
// Types and errors should be reconciled with Mooncake before implementation.
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <variant>
#include <vector>

namespace mooncake::external_storage_draft {

enum class Code {
    kOk,
    kInvalidArgument,
    kUnsupported,
    kResourceExhausted,
    kConflict,
    kUnavailable,
    kIoError,
    kDeadlineExceeded,
    kInternal,
};

struct Status {
    Code code = Code::kOk;
    std::string message;
};

// A complete immutable object identity. Incarnation is shared by all tiers.
// It must come from the agreed logical mutation coordinator, not each writer.
struct ObjectId {
    std::string name_space;
    std::string key;
    std::string incarnation;
};

enum class MemoryKind { kHost, kDevice };

struct SourceSlice {
    const void* data = nullptr;
    std::uint64_t size = 0;
    MemoryKind kind = MemoryKind::kHost;
    std::int32_t device_id = -1;
    // Required ownership/lease of the underlying allocation. It must prohibit
    // deallocation and unregistration while retained. Data remains immutable.
    std::shared_ptr<const void> lease;
};

struct DumpRequest {
    ObjectId id;
    // Object bytes = concatenation in vector order. Not object range writes.
    std::vector<SourceSlice> sources;
    std::uint64_t logical_size = 0;
};

struct DeleteRequest {
    ObjectId id;  // Delete exactly this incarnation, never "latest by key".
};

enum class Effect {
    kApplied,
    kAlreadyAbsent,  // Successful idempotent delete.
    kNotApplied,
    kUnknown,        // May have reached storage; not safe for blind rollback.
};

struct ItemResult {
    Status status;
    Effect effect = Effect::kUnknown;
};

struct OperationSnapshot {
    // Terminal result available. This does not by itself fence remote DMA.
    bool done = false;
    // No source access by this operation can occur after this becomes true.
    // Sticky; independent of done. Leases may be released only at this point.
    bool sources_released = false;
    // Empty while !done; exactly submission-size entries when done.
    std::vector<ItemResult> items;
};

class MutationOperation {
 public:
    virtual ~MutationOperation() = default;
    virtual OperationSnapshot Poll() = 0;
    // deadline == now means poll. A local waiting deadline neither cancels
    // nor consumes the operation. Results remain available to later calls.
    virtual OperationSnapshot WaitUntil(
        std::chrono::steady_clock::time_point deadline) = 0;
    // Dropping this handle never authorizes releasing still-used sources:
    // the backend retains internal tasks and their leases until fenced.
};

using SubmitResult =
    std::variant<std::shared_ptr<MutationOperation>, Status>;

struct Capabilities {
    bool device_sources = false;
    bool empty_objects = false;
    // 0 means no advertised limit, not a promise of infinite resources.
    std::uint64_t max_logical_object_bytes = 0;
    std::uint64_t max_logical_key_bytes = 0;
    std::size_t max_batch_items = 0;
};

class ExternalStorageBackend {
 public:
    virtual ~ExternalStorageBackend() = default;
    virtual Capabilities GetCapabilities() const = 0;
    // A Status alternative must be non-OK and means no item was dispatched.
    // Once any item may have been dispatched, return an operation instead.
    // Inputs and leases are copied/retained before returning.
    // Immutable whole-object semantics; same identity may never change bytes.
    virtual SubmitResult DumpAsync(
        const std::vector<DumpRequest>& requests) = 0;
    virtual SubmitResult DeleteAsync(
        const std::vector<DeleteRequest>& requests) = 0;
    // Lookup/load intentionally omitted: owned by another design task.
};

}  // namespace mooncake::external_storage_draft
