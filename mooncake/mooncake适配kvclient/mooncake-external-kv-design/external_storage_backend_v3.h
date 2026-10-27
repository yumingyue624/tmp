#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <ylt/util/tl/expected.hpp>

#include "types.h"

namespace mooncake::external_storage_v3 {

struct MemoryRegion {
    const void* base = nullptr;
    std::uint64_t capacity = 0;

    std::string memory_domain;
    std::int32_t device_id = -1;

    std::shared_ptr<const void> access_lease;
};

struct BufferInfo {
    std::uint64_t capacity = 0;
    std::string memory_domain;
    std::int32_t device_id = -1;
};

class StorageBuffer {
 public:
    virtual ~StorageBuffer() = default;
    virtual BufferInfo GetInfo() const = 0;
};

class ExternalStoreBackend {
 public:
    virtual ~ExternalStoreBackend() = default;

    virtual tl::expected<void*, ErrorCode> Put(
        const std::string& tenant, const ObjectKey& key, const std::vector<Slice>& slices) = 0;

    virtual tl::expected<void*, ErrorCode> BatchPut(
        const std::string& tenant, const std::vector<ObjectKey>& keys,
        const std::vector<std::vector<Slice>>& batched_slices) = 0;
    virtual tl::expected<void*, ErrorCode> Remove(
        const std::string& tenant, const ObjectKey& key) = 0;

    virtual tl::expected<void*, ErrorCode> BatchRemove(
        const std::string& tenant, const std::vector<ObjectKey>& keys) = 0;

    virtual std::vector<tl::expected<void, ErrorCode>> Wait(
        void* task, std::uint64_t timeout_ms) = 0;

    virtual tl::expected<void, ErrorCode> RegisterRegions(
        const std::vector<MemoryRegion>& regions,
        std::vector<std::shared_ptr<const StorageBuffer>>& registered_regions) = 0;
    virtual tl::expected<void, ErrorCode> UnregisterRegions(
        const std::vector<std::shared_ptr<const StorageBuffer>>& regions) = 0;

};

}
