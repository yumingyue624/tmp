#include "protocols/hixl/hixl_instance.h"
#include <acl/acl.h>
#include <atomic>
#include <chrono>
#include <exception>
#include <utility>
#include "hixl/hixl.h"
#include "logger/logger.h"

namespace transport {
namespace {

std::uint64_t SteadyNowUs()
{
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(now).count());
}

std::uint64_t UnixNowUs()
{
    const auto now = std::chrono::system_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(now).count());
}

std::vector<hixl::TransferOpDesc> BuildTransferOpDesc(const std::vector<Segment>& segments)
{
    std::vector<hixl::TransferOpDesc> descs;
    descs.reserve(segments.size());
    for (const auto& segment : segments) {
        descs.push_back(hixl::TransferOpDesc{
            reinterpret_cast<uintptr_t>(segment.local_addr),
            static_cast<uintptr_t>(segment.remote_addr),
            static_cast<size_t>(segment.length),
        });
    }
    return descs;
}

}  // namespace

HixlInstance::HixlInstance(Endpoint local_endpoint, int32_t device_id)
    : local_endpoint_(std::move(local_endpoint)), device_id_(device_id)
{
}

HixlInstance::~HixlInstance() { Finalize(); }

Status HixlInstance::Initialize(const std::map<std::string, std::string>& options)
{
    std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (initialized_) {
            UC_DEBUG("[Transport][HIXL] instance already initialized: engine={} device={}",
                     local_endpoint_.ToString(), device_id_);
            return Status::OK();
        }
        stopping_ = false;
    }
    if (worker_.joinable()) { worker_.join(); }

    std::promise<Status> initialize_result;
    auto initialize_future = initialize_result.get_future();
    worker_ = std::thread(&HixlInstance::WorkerMain, this, options, std::move(initialize_result));
    const auto status = initialize_future.get();
    if (status != Status::OK() && worker_.joinable()) { worker_.join(); }
    return status;
}

Status HixlInstance::Run(Task task)
{
    static std::atomic<std::uint64_t> next_task_id{1};
    const auto task_id = next_task_id.fetch_add(1, std::memory_order_relaxed);
    const auto run_started_us = SteadyNowUs();
    std::uint64_t task_started_us = run_started_us;
    std::uint64_t task_completed_us = run_started_us;
    QueuedTask queued(
        [&task_started_us, &task_completed_us, task = std::move(task)](hixl::Hixl& engine) mutable {
            task_started_us = SteadyNowUs();
            const auto status = task(engine);
            task_completed_us = SteadyNowUs();
            return status;
        });
    auto result = queued.get_future();
    std::size_t queue_depth = 0;
    std::uint64_t enqueue_locked_us = run_started_us;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        enqueue_locked_us = SteadyNowUs();
        if (!initialized_ || stopping_) {
            UC_ERROR(
                "[Transport][HIXL] reject worker task: engine={} device={} initialized={} "
                "stopping={}",
                local_endpoint_.ToString(), device_id_, initialized_, stopping_);
            return Status::Error();
        }
        tasks_.push_back(std::move(queued));
        queue_depth = tasks_.size();
    }
    const auto enqueued_us = SteadyNowUs();
    cv_.notify_one();
    try {
        const auto run_status = result.get();
        const auto run_completed_us = SteadyNowUs();
        UC_INFO(
            "[HIXL_WORKER_PERF] task_id={} queue_depth={} enqueue_lock_wait_us={} enqueue_us={} "
            "queue_wait_us={} execute_us={} result_post_us={} total_us={} status={}",
            task_id, queue_depth, enqueue_locked_us - run_started_us,
            enqueued_us - enqueue_locked_us,
            task_started_us >= enqueued_us ? task_started_us - enqueued_us : 0,
            task_completed_us >= task_started_us ? task_completed_us - task_started_us : 0,
            run_completed_us >= task_completed_us ? run_completed_us - task_completed_us : 0,
            run_completed_us - run_started_us, run_status.Underlying());
        return run_status;
    } catch (const std::exception& e) {
        UC_ERROR("[Transport][HIXL] worker task failed: {}", e.what());
    } catch (...) {
        UC_ERROR("[Transport][HIXL] worker task failed with unknown exception");
    }
    return Status::Error();
}

void HixlInstance::Finalize()
{
    std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!worker_.joinable()) { return; }
        stopping_ = true;
    }
    cv_.notify_one();
    worker_.join();
}

void HixlInstance::WorkerMain(std::map<std::string, std::string> options,
                              std::promise<Status> initialize_result)
{
    const auto set_device_status = aclrtSetDevice(device_id_);
    if (set_device_status != ACL_ERROR_NONE) {
        UC_ERROR("[Transport][HIXL] set device failed: aclrtSetDevice({}) returned {}", device_id_,
                 static_cast<int>(set_device_status));
        initialize_result.set_value(Status::Error());
        return;
    }

    {
        hixl::Hixl engine;
        std::map<hixl::AscendString, hixl::AscendString> hixl_options;
        for (const auto& item : options) {
            hixl_options.emplace(item.first.c_str(), item.second.c_str());
        }

        const auto local_engine = local_endpoint_.ToString();
        const auto init_status = engine.Initialize(local_engine.c_str(), hixl_options);
        if (init_status != hixl::SUCCESS) {
            UC_ERROR("[Transport][HIXL] init failed: Initialize(\"{}\") returned {}", local_engine,
                     static_cast<int>(init_status));
            initialize_result.set_value(Status::Error());
        } else {
            {
                std::lock_guard<std::mutex> lock(mutex_);
                initialized_ = true;
            }
            initialize_result.set_value(Status::OK());
            UC_DEBUG("[Transport][HIXL] instance initialized: engine={} device={}", local_engine,
                     device_id_);
            ProcessTasks(engine);
            engine.Finalize();
        }
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        initialized_ = false;
    }
    const auto reset_device_status = aclrtResetDevice(device_id_);
    if (reset_device_status != ACL_ERROR_NONE) {
        UC_WARN("[Transport][HIXL] reset device failed: aclrtResetDevice({}) returned {}",
                device_id_, static_cast<int>(reset_device_status));
    }
}

void HixlInstance::ProcessTasks(hixl::Hixl& engine)
{
    for (;;) {
        QueuedTask queued;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            cv_.wait(lock, [this] { return stopping_ || !tasks_.empty(); });
            if (stopping_ && tasks_.empty()) { break; }
            queued = std::move(tasks_.front());
            tasks_.pop_front();
        }
        queued(engine);
    }
}

Status HixlInstance::RegisterMemory(const MemoryRegion& memory, hixl::MemHandle& handle)
{
    hixl::MemHandle native_handle = nullptr;
    const auto status = Run([&](hixl::Hixl& engine) {
        hixl::MemDesc desc{};
        desc.addr = reinterpret_cast<uintptr_t>(memory.addr);
        desc.len = static_cast<size_t>(memory.length);
        const auto type = memory.type == MemoryType::Device ? hixl::MEM_DEVICE : hixl::MEM_HOST;
        const auto native_status = engine.RegisterMem(desc, type, native_handle);
        if (native_status != hixl::SUCCESS) {
            UC_ERROR(
                "[Transport][HIXL] register memory failed: engine={} device={} "
                "RegisterMem(addr=0x{:x}, length={}) returned {}",
                local_endpoint_.ToString(), device_id_, reinterpret_cast<uintptr_t>(memory.addr),
                memory.length, static_cast<int>(native_status));
            return Status::Error();
        }
        UC_DEBUG(
            "[Transport][HIXL] memory registered: engine={} device={} addr=0x{:x} length={} "
            "handle={}",
            local_endpoint_.ToString(), device_id_, reinterpret_cast<uintptr_t>(memory.addr),
            memory.length, native_handle);
        return Status::OK();
    });
    if (status == Status::OK()) { handle = native_handle; }
    return status;
}

Status HixlInstance::UnregisterMemory(hixl::MemHandle handle)
{
    return Run([&](hixl::Hixl& engine) {
        const auto native_status = engine.DeregisterMem(handle);
        if (native_status != hixl::SUCCESS) {
            UC_ERROR(
                "[Transport][HIXL] unregister memory failed: engine={} DeregisterMem(handle={}) "
                "returned {}",
                local_endpoint_.ToString(), handle, static_cast<int>(native_status));
            return Status::Error();
        }
        UC_DEBUG("[Transport][HIXL] memory unregistered: engine={} device={} handle={}",
                 local_endpoint_.ToString(), device_id_, handle);
        return Status::OK();
    });
}

Status HixlInstance::Connect(const std::string& remote_engine, int32_t timeout_ms)
{
    return Run([&](hixl::Hixl& engine) {
        const auto native_status = engine.Connect(remote_engine.c_str(), timeout_ms);
        if (native_status != hixl::SUCCESS) {
            UC_ERROR(
                "[Transport][HIXL] connect failed: local_engine=\"{}\" remote_engine=\"{}\" "
                "returned {}",
                local_endpoint_.ToString(), remote_engine, static_cast<int>(native_status));
            return Status::Error();
        }
        UC_DEBUG(
            "[Transport][HIXL] connection established: local_engine={} device={} "
            "remote_engine={}",
            local_endpoint_.ToString(), device_id_, remote_engine);
        return Status::OK();
    });
}

Status HixlInstance::Disconnect(const std::string& remote_engine, int32_t timeout_ms)
{
    return Run([&](hixl::Hixl& engine) {
        const auto native_status = engine.Disconnect(remote_engine.c_str(), timeout_ms);
        if (native_status != hixl::SUCCESS) {
            UC_ERROR(
                "[Transport][HIXL] disconnect failed: local_engine={} device={} "
                "remote_engine={} returned {}",
                local_endpoint_.ToString(), device_id_, remote_engine,
                static_cast<int>(native_status));
            return Status::Error();
        }
        UC_DEBUG(
            "[Transport][HIXL] connection disconnected: local_engine={} device={} "
            "remote_engine={}",
            local_endpoint_.ToString(), device_id_, remote_engine);
        return Status::OK();
    });
}

Status HixlInstance::TransferSync(const std::string& remote_engine, Opcode opcode,
                                  const std::vector<Segment>& segments, int32_t timeout_ms)
{
    return Run([&](hixl::Hixl& engine) {
        const auto descs = BuildTransferOpDesc(segments);
        const auto operation = opcode == Opcode::Read ? hixl::READ : hixl::WRITE;
        const auto native_status =
            engine.TransferSync(remote_engine.c_str(), operation, descs, timeout_ms);
        if (native_status != hixl::SUCCESS) {
            UC_ERROR(
                "[Transport][HIXL] operation failed: TransferSync(\"{}\", ops={}, timeout_ms={}) "
                "returned {}",
                remote_engine, descs.size(), timeout_ms, static_cast<int>(native_status));
            return Status::Error();
        }
        UC_DEBUG(
            "[Transport][HIXL] synchronous transfer completed: engine={} remote_engine={} "
            "opcode={} segments={}",
            local_endpoint_.ToString(), remote_engine, static_cast<int>(opcode), descs.size());
        return Status::OK();
    });
}

Status HixlInstance::TransferAsync(const std::string& remote_engine, Opcode opcode,
                                   const std::vector<Segment>& segments, hixl::TransferReq& request,
                                   TransportCallTiming* timing)
{
    hixl::TransferReq native_request = nullptr;
    const auto status = Run([&](hixl::Hixl& engine) {
        const auto descs = BuildTransferOpDesc(segments);
        hixl::TransferArgs args;
        const auto operation = opcode == Opcode::Read ? hixl::READ : hixl::WRITE;
        if (timing != nullptr) {
            timing->backend_called_us = SteadyNowUs();
            timing->backend_called_ts_us = UnixNowUs();
        }
        const auto native_status =
            engine.TransferAsync(remote_engine.c_str(), operation, descs, args, native_request);
        if (native_status != hixl::SUCCESS || native_request == nullptr) {
            UC_ERROR(
                "[Transport][HIXL] async operation failed: TransferAsync(\"{}\", ops={}) returned "
                "{} request={}",
                remote_engine, descs.size(), static_cast<int>(native_status), native_request);
            return Status::Error();
        }
        UC_DEBUG(
            "[Transport][HIXL] asynchronous transfer submitted: engine={} remote_engine={} "
            "opcode={} segments={} request={}",
            local_endpoint_.ToString(), remote_engine, static_cast<int>(opcode), descs.size(),
            native_request);
        return Status::OK();
    });
    if (status == Status::OK()) { request = native_request; }
    return status;
}

Status HixlInstance::GetTransferStatus(hixl::TransferReq request, TransferStatus& status,
                                       TransportCallTiming* timing)
{
    const auto query_started_us = SteadyNowUs();
    std::uint64_t callback_entered_us = query_started_us;
    std::uint64_t backend_completed_us = query_started_us;
    status = TransferStatus::Failed;
    const auto run_status = Run([&](hixl::Hixl& engine) {
        callback_entered_us = SteadyNowUs();
        hixl::TransferStatus native_transfer_status = hixl::TransferStatus::WAITING;
        if (timing != nullptr) {
            timing->backend_called_us = callback_entered_us;
            timing->backend_called_ts_us = UnixNowUs();
        }
        const auto native_status = engine.GetTransferStatus(request, native_transfer_status);
        backend_completed_us = SteadyNowUs();
        if (native_status != hixl::SUCCESS) {
            UC_ERROR("[Transport][HIXL] get transfer status failed: req={} returned {}", request,
                     static_cast<int>(native_status));
            return Status::Error();
        }
        switch (native_transfer_status) {
            case hixl::TransferStatus::WAITING: status = TransferStatus::Waiting; break;
            case hixl::TransferStatus::COMPLETED: status = TransferStatus::Completed; break;
            case hixl::TransferStatus::FAILED:
            case hixl::TransferStatus::TIMEOUT: status = TransferStatus::Failed; break;
        }
        return Status::OK();
    });
    const auto query_completed_us = SteadyNowUs();
    UC_INFO(
        "[QUERY_PERF] component=ucm layer=hixl_instance request={} queue_wait_us={} "
        "backend_us={} run_post_us={} total_us={} api_status={} transfer_status={}",
        request, callback_entered_us - query_started_us,
        backend_completed_us >= callback_entered_us ? backend_completed_us - callback_entered_us : 0,
        query_completed_us >= backend_completed_us ? query_completed_us - backend_completed_us : 0,
        query_completed_us - query_started_us, run_status.Underlying(),
        static_cast<int>(status));
    return run_status;
}

const Endpoint& HixlInstance::LocalEndpoint() const { return local_endpoint_; }

int32_t HixlInstance::DeviceId() const { return device_id_; }

}  // namespace transport
