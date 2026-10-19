---
name: hixl-transport-test
description: Test HIXL one-sided RDMA (sync/async read/write) and two-sided TCP message channel on Ascend NPU. Covers p2p transport e2e validation, port architecture, and DramPool integration testing.
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: testing
---

## HIXL Transport E2E Testing Guide

This skill documents the testing methodology for the p2p transport layer, covering one-sided HIXL RDMA transfers and two-sided TCP messaging. Based on ayaka836's `hixl_e2e` test framework.

### Container Environment Setup

#### Create Test Container

The test container `nt_p2p_test` is created from the `ymy_ascend_backup` image with full NPU device access:

```bash
docker run -d --name nt_p2p_test \
  --privileged \
  --network host \
  --device /dev/davinci_manager:/dev/davinci_manager \
  --device /dev/hisi_hdc:/dev/hisi_hdc \
  --device /dev/devmm_svm:/dev/devmm_svm \
  -v /home:/home \
  -v /tmp:/tmp \
  -v /mnt:/mnt \
  -v /mnt/fsds:/mnt/fsds \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
  -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
  -v /usr/local/dcmi:/usr/local/dcmi \
  -v /usr/local/sbin:/usr/local/sbin \
  -v /usr/share/zoneinfo/Asia/Shanghai:/etc/localtime \
  --security-opt label=disable \
  -w /home/ymy \
  ymy_ascend_backup \
  /bin/bash -c "source /usr/local/Ascend/ascend-toolkit/set_env.sh && sleep infinity"
```

#### Install Required Packages

```bash
docker exec nt_p2p_test bash -lc "dnf install -y fmt-devel spdlog-devel zlib-devel"
```

#### Install CANN 8.5.1

Download and install CANN 8.5.1 toolkit:

```bash
# Download (from Huawei OBS)
wget -O /tmp/Ascend-cann-toolkit_8.5.1_linux-aarch64.run \
  "https://ascend-repo.obs.cn-east-2.myhuaweicloud.com/CANN/CANN%208.5.1/Ascend-cann-toolkit_8.5.1_linux-aarch64.run"

# Copy into container
docker cp /tmp/Ascend-cann-toolkit_8.5.1_linux-aarch64.run nt_p2p_test:/tmp/

# Install
docker exec nt_p2p_test bash -lc "chmod +x /tmp/Ascend-cann-toolkit_8.5.1_linux-aarch64.run && /tmp/Ascend-cann-toolkit_8.5.1_linux-aarch64.run --install --quiet"
```

#### Build HIXL 8.5.0 from Source

CANN 8.5.1 toolkit does not include HIXL. Build from the colleague's HIXL repository:

```bash
# Checkout 8.5.0 branch
docker exec nt_p2p_test bash -lc "cd /home/nt/code/hixl && git checkout remotes/origin/8.5.0"

# Build with CANN 8.5.1
docker exec nt_p2p_test bash -lc "cd /home/nt/code/hixl && rm -rf build build_out && mkdir build && ASCEND_HOME_PATH=/usr/local/Ascend/cann-8.5.1 bash build.sh"

# Install HIXL into CANN 8.5.1
docker exec nt_p2p_test bash -lc "cd /home/nt/code/hixl/build_out && chmod +x cann-hixl_8.5.0-beta.1_linux-aarch64.run && ./cann-hixl_8.5.0-beta.1_linux-aarch64.run --full --install-path=/usr/local/Ascend/cann-8.5.1"
```

Verify installation:

```bash
docker exec nt_p2p_test bash -lc "cat /usr/local/Ascend/cann-8.5.1/share/info/hixl/version.info"
# Expected: Version=8.5.0-beta.1
```

#### Container Summary

| Component | Version | Location |
|-----------|---------|----------|
| Container name | `nt_p2p_test` | - |
| Base image | `ymy_ascend_backup` | - |
| CANN toolkit | 8.5.1 | `/usr/local/Ascend/cann-8.5.1` |
| HIXL library | 8.5.0-beta.1 | `/usr/local/Ascend/cann-8.5.1/aarch64-linux/lib64/libcann_hixl.so` |
| HIXL headers | 8.5.0-beta.1 | `/usr/local/Ascend/cann-8.5.1/aarch64-linux/include/hixl/` |
| NPU devices | 0-7 (910B3) | `/dev/davinci0` - `/dev/davinci7` |
| Test repo | `drampool_daemon` branch | `/root/ucm-p2p-test` |

#### NPU Device Selection

Use `npu-smi info` to check device availability. Devices 0-3 may be occupied by VLLM workers. Use devices 4-7 for testing:

```bash
# Check device status
npu-smi info

# Check specific device memory
npu-smi info -i 4 -t memory
```

### Architecture Overview

The transport layer has **three independent channels**, each requiring its own port:

| Channel | Class | Purpose | Port Example |
|---------|-------|---------|--------------|
| MetadataChannel (one-sided control) | `MetadataChannel` | HIXL peer discovery, metadata exchange | 4501/4502 |
| TcpMessageChannel (two-sided data) | `TcpMessageChannel` | Application KV request/response messages | 7701/7702 |
| HIXL engine | `hixl::Hixl` | RDMA data transfer engine | 5501/5502 |

**Critical**: These three channels **cannot share the same port**. MetadataChannel uses a blocking accept loop (one-shot connections), while TcpMessageChannel uses epoll (persistent connections). Binding them to the same address causes conflicts.

### Port Configuration

Each test instance needs 6 ports (3 per side):

```cpp
struct Config {
    // TransportManager (MetadataChannel) - HIXL peer discovery
    uint16_t server_manager_port = 4501;
    uint16_t client_manager_port = 4502;

    // TcpMessageChannel - application KV messages
    uint16_t server_control_port = 7701;
    uint16_t client_control_port = 7702;

    // HIXL engine - RDMA transfers
    uint16_t server_hixl_port = 5501;
    uint16_t client_hixl_port = 5502;

    // NPU device IDs (must be different for same-host tests)
    int server_device_id = 0;
    int client_device_id = 1;
};
```

### Environment Variables

```bash
HIXL_TEST_LOCAL_HOST=110.138.0.3    # Local IP
HIXL_TEST_PEER_HOST=110.138.0.3     # Peer IP (same for loopback)
HIXL_TEST_DEVICE_A=0                 # Server NPU device
HIXL_TEST_DEVICE_B=1                 # Client NPU device
TRANSPORT_TEST_PORT_A=4501           # Server manager port
TRANSPORT_TEST_PORT_B=4502           # Client manager port
TRANSPORT_CONTROL_PORT_A=7701        # Server TCP control port
TRANSPORT_CONTROL_PORT_B=7702        # Client TCP control port
HIXL_TEST_PORT_A=5501               # Server HIXL engine port
HIXL_TEST_PORT_B=5502               # Client HIXL engine port
```

### Build Instructions

```bash
# Set CANN environment
source /usr/local/Ascend/cann-8.5.1/set_env.sh

# Build with build.sh script
cd ucm/transport/p2p
export ASCEND_ROOT=/usr/local/Ascend/cann-8.5.1
export HIXL_INCLUDE_DIR=/usr/local/Ascend/cann-8.5.1/aarch64-linux/include
export HIXL_LIB_DIR=/usr/local/Ascend/cann-8.5.1/aarch64-linux/lib64
export CXXFLAGS="-DSPDLOG_FMT_EXTERNAL -include /tmp/fmt_underlying_compat.h"
bash scripts/build.sh
```

The build produces:
- `build/hixl_e2e` - Main e2e test binary
- `build/hixl_multi_conn_bench` - Multi-connection benchmark
- `build/hixl_multi_instance_verify` - Multi-instance verification
- `build/libtransport.a` - Static transport library

### One-Sided HIXL RDMA Test Flow

#### Server Side

```cpp
// 1. ACL runtime init
aclInit(nullptr);
aclrtSetDevice(server_device_id);

// 2. TransportManager init (binds MetadataChannel to manager_port)
TransportManager manager(server_manager_id);  // "IP:4501"
manager.Init();

// 3. Install HIXL transport (binds HIXL engine to hixl_port)
HixlInitAttrs attrs;
attrs.local_engine = "IP:5501";
attrs.device_id = server_device_id;
manager.InstallTransport(TransportProtocol::Hixl, attrs);

// 4. Allocate and register device memory
void* dev_mem;
aclrtMalloc(&dev_mem, 4096, ACL_MEM_MALLOC_HUGE_ONLY);
MemoryRegion region{dev_mem, 4096, MemoryType::Device, server_device_id};
MemoryHandle handle;
manager.RegisterMemory(region, handle);

// 5. Wait for client via TcpMessageChannel
TcpMessageChannel control;
control.Init({local_host, server_control_port});  // Bind to 7701
Endpoint client_peer;
std::string ready_msg;
control.Receive(client_peer, ready_msg);  // Blocking wait for "READY"

// 6. Send device address back
std::string addr_msg = "ADDR " + std::to_string((uintptr_t)dev_mem);
control.Send(client_peer, addr_msg);

// 7. Wait for completion
std::string done_msg;
control.Receive(client_peer, done_msg);  // Wait for "DONE"

// 8. Verify data (optional)
uint8_t verify[16];
aclrtMemcpy(verify, 16, dev_mem, 16, ACL_MEMCPY_DEVICE_TO_HOST);

// 9. Cleanup
manager.Shutdown();
control.Shutdown();
aclrtFree(dev_mem);
aclrtResetDevice(server_device_id);
aclFinalize();
```

#### Client Side

```cpp
// 1. ACL runtime init
aclInit(nullptr);
aclrtSetDevice(client_device_id);

// 2. TransportManager init
TransportManager manager(client_manager_id);  // "IP:4502"
manager.Init();

// 3. Install HIXL transport
HixlInitAttrs attrs;
attrs.local_engine = "IP:5502";
attrs.device_id = client_device_id;
manager.InstallTransport(TransportProtocol::Hixl, attrs);

// 4. Allocate and register host memory
void* host_mem;
aclrtMallocHost(&host_mem, 4096);
// Fill with test pattern
for (size_t i = 0; i < 4096; ++i) {
    static_cast<uint8_t*>(host_mem)[i] = i & 0xFF;
}
MemoryRegion region{host_mem, 4096, MemoryType::Host};
MemoryHandle handle;
manager.RegisterMemory(region, handle);

// 5. Connect to server via TcpMessageChannel
TcpMessageChannel control;
control.Init({local_host, client_control_port});  // Bind to 7702
Endpoint server_peer{peer_host, server_control_port};  // 7701
control.Send(server_peer, "READY");

// 6. Receive server device address
std::string addr_msg;
control.Receive(server_peer, addr_msg);
uint64_t remote_dev_addr = parseAddress(addr_msg);

// 7. Exchange HIXL metadata (peer discovery)
manager.ExchangeMetadata(server_manager_id);  // "IP:4501"

// 8. Connect HIXL transport
manager.Connect(TransportProtocol::Hixl, server_manager_id);

// 9. Sync RDMA WRITE (host -> remote device)
Operation op;
op.target_manager = server_manager_id;
op.direct = OperationDirect::RemoteDeviceHost;
op.opcode = Opcode::Write;
op.ops.push_back({host_mem, remote_dev_addr, 4096});
manager.ExecuteSync(op);

// 10. Sync RDMA READ (remote device -> host)
std::memset(host_mem, 0, 4096);
op.opcode = Opcode::Read;
manager.ExecuteSync(op);

// 11. Verify data
bool ok = verifyPattern(host_mem, 4096);

// 12. Async RDMA WRITE
for (size_t i = 0; i < 4096; ++i) {
    static_cast<uint8_t*>(host_mem)[i] = i & 0xFF;
}
op.opcode = Opcode::Write;
TransferHandle async_handle;
manager.ExecuteAsync(op, async_handle);

// 13. Poll async status
TransferStatus status;
do {
    manager.GetStatus(async_handle, status);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
} while (status == TransferStatus::Waiting);

// 14. Async RDMA READ
std::memset(host_mem, 0, 4096);
op.opcode = Opcode::Read;
manager.ExecuteAsync(op, async_handle);
do {
    manager.GetStatus(async_handle, status);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
} while (status == TransferStatus::Waiting);

// 15. Notify server
control.Send(server_peer, "DONE");

// 16. Cleanup
manager.Shutdown();
control.Shutdown();
aclrtFreeHost(host_mem);
aclrtResetDevice(client_device_id);
aclFinalize();
```

### Two-Sided TCP Message Channel Test

`TcpMessageChannel` provides bidirectional message passing with an epoll-based I/O thread.

#### Key API

```cpp
class TcpMessageChannel {
public:
    Status Init(const Endpoint& local);  // Bind and listen
    Status Send(const Endpoint& peer, const void* data, size_t length);
    Status Receive(Endpoint& peer, Metadata& data);  // Blocking
    Status Shutdown();
};
```

#### Receive Semantics

- **Blocking**: `Receive()` blocks on `condition_variable` until `receive_queue_` has data or `Shutdown()` is called
- **Thread-safe**: Multiple threads can call `Receive()`, but each `Message` is delivered to only one consumer (competitive consumption)
- **Data structure**: `Message { Endpoint peer; Metadata data; }` where `Metadata = std::vector<uint8_t>`

#### Internal Threading

| Thread | Role | Operation |
|--------|------|-----------|
| `io_thread_` | Producer | epoll_wait → recv → decode → push to `receive_queue_` → notify_one |
| Caller thread | Consumer | `Receive()` → wait → pop from `receive_queue_` |

#### Wire Format

```
Frame: [4-byte length (network order)] [encoded message]
Message: [string: sender-host] [u16: sender-port] [payload bytes...]
```

### Running Tests

#### Server

```bash
source /usr/local/Ascend/cann-8.5.1/set_env.sh
export HIXL_TEST_LOCAL_HOST=110.138.0.3
export HIXL_TEST_PEER_HOST=110.138.0.3
export HIXL_TEST_DEVICE_A=4
export HIXL_TEST_DEVICE_B=5
export TRANSPORT_TEST_PORT_A=4501
export TRANSPORT_TEST_PORT_B=4502
export TRANSPORT_CONTROL_PORT_A=7701
export TRANSPORT_CONTROL_PORT_B=7702
export HIXL_TEST_PORT_A=5501
export HIXL_TEST_PORT_B=5502

./build/hixl_e2e server
```

#### Client (in separate terminal)

```bash
source /usr/local/Ascend/cann-8.5.1/set_env.sh
# Same environment variables as server
./build/hixl_e2e client
```

#### Expected Output

**Server**:
```
[HIXL e2e server] init standalone TCP listen 110.138.0.3:7701
[HIXL e2e server] init ACL device 4
[HIXL e2e] aclInit return 0
[HIXL e2e] aclrtSetDevice(4) return 0
[HIXL e2e server] manager_id=110.138.0.3:4501 peer_control=110.138.0.3:7702 hixl_engine="110.138.0.3:5501" device_id=4
[HIXL e2e server] allocate/register device memory
[B] waiting READY from client on standalone TCP port 7701
[B] client READY from 110.138.0.3:7702
[B] waiting DONE from client
[B] first bytes after A->B: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15
```

**Client**:
```
[HIXL e2e client] init standalone TCP listen 110.138.0.3:7702
[HIXL e2e client] init ACL device 5
[HIXL e2e] aclInit return 0
[HIXL e2e] aclrtSetDevice(5) return 0
[HIXL e2e client] manager_id=110.138.0.3:4502 peer_control=110.138.0.3:7701 hixl_engine="110.138.0.3:5502" device_id=5
[HIXL e2e client] allocate/register host memory
[A] waiting server device address on standalone TCP port 7702
[A] remote B dev addr = 0x12c045200000
[A] Host -> B Device WRITE sync
[A] H2D WRITE sync done
[A] B Device -> Host READ sync
[A] D2H READ sync done
[A] >>> VERIFY sync: PASS <<<
[A] Host -> B Device WRITE async
[A] H2D WRITE async done
[A] B Device -> Host READ async
[A] D2H READ async done
[A] >>> VERIFY async: PASS <<<
```

### DramPool Integration Testing

DramPool uses the transport layer for KV cache operations. The `ReceiverLoop` should integrate `TcpMessageChannel` as follows:

#### Architecture

```
TcpMessageChannel (port: g_config.kvAddr, e.g., 19101)
    ↓ io_thread_ epoll
    ↓ Receive(peer, data)
    
ReceiverLoop thread (consumer)
    ↓ protocolManager_.UnpackRequest(data, size, request)
    ↓ requestQueue_.Push(RequestTask{request, peer_manager_id})
    
TaskWorker thread (processor)
    ↓ requestQueue_.ConsumerLoop()
    ↓ ProcessOneRequest(task)
    ↓   ├── ProcessDump() → AllocateBuffer → ReserveDumpEntry → ExecuteAsync
    ↓   ├── ProcessLoad() → LookupAndPinLoad → ExecuteAsync
    ↓   └── ProcessLookup() → LookupReady → WriteResponse
    
CompletionPoller thread
    ↓ transHandleQueue_.TryPop(record)
    ↓ transport.GetStatus(handle, status)
    ↓ ApplyTerminal() → PublishDump/AbortDump/ReleaseLoadIo
    ↓ WriteResponse() via ExecuteSync
```

#### Required Configuration

Add to `DramPoolConfig`:

```cpp
struct DramPoolConfig {
    std::string addr;              // MetadataChannel port (HIXL peer discovery)
    std::string kvAddr;            // TcpMessageChannel port (KV data) - NEW
    std::string transportLocalEngine;  // HIXL engine (RDMA)
    // ...
};
```

#### ReceiverLoop Implementation

```cpp
void DramPoolServer::ReceiverLoop()
{
    UC_INFO_UNLIMITED("DramPool Receiver started, kvAddr={}", g_config.kvAddr);

    // 1. Parse kvAddr into Endpoint
    Endpoint local_endpoint;
    if (!ParseEndpoint(g_config.kvAddr, local_endpoint)) {
        UC_ERROR_UNLIMITED("Failed to parse kvAddr: {}", g_config.kvAddr);
        return;
    }

    // 2. Init TcpMessageChannel
    transport::TcpMessageChannel control;
    if (control.Init(local_endpoint) != transport::Status::Ok) {
        UC_ERROR_UNLIMITED("TcpMessageChannel::Init failed");
        return;
    }

    // 3. Receive loop
    while (!receiverStop_.load(std::memory_order_acquire)) {
        transport::Endpoint peer;
        transport::Metadata data;

        auto recv_status = control.Receive(peer, data);
        if (recv_status != transport::Status::Ok) {
            if (receiverStop_.load(std::memory_order_acquire)) { break; }
            UC_ERROR("TcpMessageChannel::Receive failed: {}", static_cast<int>(recv_status));
            continue;
        }

        // 4. Unpack KV protocol
        std::unique_ptr<KvRequest> request;
        auto unpack_status = protocolManager_->UnpackRequest(
            data.data(), data.size(), request);
        if (!unpack_status.ok()) {
            UC_ERROR("UnpackRequest failed: {}", unpack_status.message);
            continue;
        }

        // 5. Construct peer_manager_id from peer Endpoint
        transport::ManagerID peer_manager_id = peer.host + ":" + std::to_string(peer.port);

        // 6. Push to requestQueue_
        auto task = std::make_unique<RequestTask>();
        task->request = std::move(request);
        task->peer_manager_id = peer_manager_id;
        requestQueue_.Push(std::move(task));
    }

    control.Shutdown();
    UC_INFO_UNLIMITED("DramPool Receiver stopped");
}
```

#### Testing DramPool with Transport

```bash
# Server instance
./build/drampool \
    --addr 110.138.0.3:19100 \
    --kv-addr 110.138.0.3:19101 \
    --transport-engine 110.138.0.3:19200 \
    --transport-device 4 \
    --nics mlx5_0 \
    --pool-size-gb 1 \
    --kvcache-block-sizes 4096 8192

# Client instance (separate terminal)
./build/drampool \
    --addr 110.138.0.3:19102 \
    --kv-addr 110.138.0.3:19103 \
    --transport-engine 110.138.0.3:19201 \
    --transport-device 5 \
    --nics mlx5_0 \
    --pool-size-gb 1 \
    --kvcache-block-sizes 4096 8192
```

### Common Issues

#### 1. HIXL Connect Returns 503900 (FAILED)

**Cause**: CANN version mismatch. HIXL 9.1.0 library with CANN 8.5.1 toolkit.

**Solution**: Use matching versions:
- CANN 8.5.1 + HIXL 8.5.0-beta.1 (built from `/home/nt/code/hixl` branch `8.5.0`)
- CANN 9.1.0-beta.1 + HIXL 9.1.0

#### 2. HIXL Initialize Returns 103900 (PARAM_INVALID)

**Cause**: `local_engine` is not in `IP:port` format.

**Solution**: Ensure `transportLocalEngine` is `"IP:port"`, not `"drampool-0"` or other invalid strings.

#### 3. aclrtSetDevice Returns 507033

**Cause**: Device already in use by another process.

**Solution**: Use `npu-smi info` to check device status. Select unused devices (e.g., 4-7 instead of 0-3 which may be used by VLLM).

#### 4. Port Binding Conflict

**Cause**: MetadataChannel and TcpMessageChannel bound to same port.

**Solution**: Use separate ports for each channel. Never share ports between the three channel types.

#### 5. TransferSync Returns 507001

**Cause**: HIXL library version incompatibility or RDMA fabric configuration issue.

**Solution**: 
- Verify HIXL and CANN versions match
- Check RDMA network connectivity between NPU devices
- Ensure HCCN IPs are configured correctly

### Version Compatibility Matrix

| CANN Version | HIXL Version | Status | Notes |
|--------------|--------------|--------|-------|
| 8.5.1 | 8.5.0-beta.1 | ✅ PASS | Sync + async RDMA work |
| 9.1.0-beta.1 | 9.1.0 | ⚠️ PARTIAL | Sync works, async GetStatus fails (503900) |
| 9.1.0-beta.1 | 8.5.0-beta.1 | ❌ FAIL | Connect fails (503900) |

### Key Source Files

| File | Purpose |
|------|---------|
| `ucm/transport/p2p/tests/e2e/hixl_e2e.cpp` | Main e2e test implementation |
| `ucm/transport/p2p/src/one_sided/hixl/hixl_transport.cpp` | HIXL transport wrapper |
| `ucm/transport/p2p/src/one_sided/core/transport_manager.cpp` | Transport orchestration |
| `ucm/transport/p2p/src/one_sided/control/metadata_channel.cpp` | One-sided metadata exchange |
| `ucm/transport/p2p/src/two_sided/tcp/tcp_message_channel.cpp` | Two-sided TCP messaging |
| `ucm/transport/p2p/scripts/build.sh` | Build script |

### References

- HIXL API: `/usr/local/Ascend/cann-8.5.1/aarch64-linux/include/hixl/hixl.h`
- ACL API: `/usr/local/Ascend/cann-8.5.1/aarch64-linux/include/acl/acl.h`
- CANN documentation: `/usr/local/Ascend/cann-8.5.1/share/doc/`
- NPU device management: `npu-smi info`
