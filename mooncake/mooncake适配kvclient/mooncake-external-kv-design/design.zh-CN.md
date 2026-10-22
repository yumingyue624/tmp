# Mooncake ExternalStorageBackend 写入、删除接口评审草案

日期：2026-10-20。范围：双写、外部后端写入/删除，以及 Client 能力边界。本文不定义 lookup/load 接口，不修改现有工程。

## 1. 建议先达成的结论

1. 通用接口统一命名为 `ExternalStorageBackend`，KVClient 适配实现命名为 `KVClientStorageBackend`。命名延续 Mooncake 的 StorageBackend 术语，表达由 Client 调用的可替换外部存储后端。
2. 它是请求端 Client 调用的“按对象 key 访问外部 KV 服务”接口，不继承现有 `StorageBackendInterface`，也不模拟远端内存 segment。
3. Mooncake Client 决定是否双写、怎样汇总两条路径的结果；外部 backend 决定存储位置、路由、协议、注册、传输和设备侧完成。
4. 对外只暴露整对象 `DumpAsync`、精确对象身份的 `DeleteAsync`、可重复查询的异步操作对象，以及必要的能力声明。内部可映射成 SDK 的 Store/BatchStore/Delete。
5. 首版限定 immutable 整对象，明确批量逐项结果、部分失败、超时结果不确定性和 DMA 生命周期；不声称提供跨内存与外部存储的原子事务。
6. 通用接口使用完整逻辑 key；8 字节 key、512B 对齐、flag buffer、DHT、SDK TaskId、ASU/OD1800 参数均留在适配层。

配套头文件：[external_storage_backend_draft.h](external_storage_backend_draft.h)。这是独立评审草案，不是已接入并验证的 Mooncake 接口。

## 2. 原图的含义

原图包含四组架构，不能把它们合成一条调用链：

| 图中区域 | 写入/控制路径 | 与本项目的关系 |
| --- | --- | --- |
| 顶部新方案 | 推理引擎 → Client；一路走原生 Master + TE 内存池，一路走 ExternalStorageBackend → KVClient SDK → 外部存储 | 本次接口定义针对这里 |
| 中部本地磁盘 offload | FileStorage 向 Master 获取任务，经 StorageBackendInterface 落本地文件；读取时 RPC 恢复到 owner 暂存 buffer，再走 TE | 不能直接拿它的 backend 接口代替外部 KV 接口 |
| 下部 NoF | Master 分配内存/NoF 副本；TransferSubmitter 分别提交 TE 与 SPDK NoF 操作 | 是 Mooncake 管理 placement 的另一路径 |
| 最底部内存池 | Master 管理内存副本、租约；TE 传输调用方数据 | 新方案保留的原生分支 |

顶部的数据流非常关键：KVClient 经 DHT 选择存储、TransProvider 发送命令，远端在写入时读取调用方数据，并写回完成响应；Poller 检查 flag buffer，SDK 才能形成操作完成结果。命令发送成功、SEND 完成、TaskId 返回，均不等于对象写入完成。

图中的 `READ(dump)` 是“远端读调用方源数据来完成存储写入”，`WRITE(load)` 是“远端把数据写回调用方”。因此命名应明确方向：本文 `Dump` 表示调用方 → 外部存储；SDK 对应 `StoreAsync/BatchStoreAsync`。不据此改动同事的读接口。

### “控制方交给存储”的具体边界

按图，合理解释是：**外部存储的 placement、路由和执行控制交给外部存储系统；Mooncake 不为它分配地址、LBA、rkey，也不维护它的设备副本。** DPO 或其他后端可用完全不同的内部机制。

这不自动意味着所有逻辑 key 的可见性、并发删除和版本协调也已移交。原图没有定义这部分。必须区分：

| 职责 | 建议归属 |
| --- | --- |
| 是否双写、成功条件、每路错误汇总 | Mooncake Client 的策略层 |
| 原生内存副本分配、原生租约与 PutEnd/PutRevoke | 原生 Master 路径 |
| 外部 KV 的节点选择、寻址、内部副本、协议与完成 | ExternalStorageBackend 实现及其存储系统 |
| 跨路径的同一逻辑对象身份、删除后重建的代际、全局可见性 | 必须明确指定的逻辑变更协调机制；不能由两个 Client 各自决定 |

接口不能为了接 KVClient，把整个 ASU replica descriptor、SDK TaskId 或协议状态加到 Master。若最终选择 Master 协调逻辑代际，它只需要通用的逻辑身份/版本关系，不应接管外部 placement。若选择存储协调逻辑代际，则要验证其协议确实具备相应能力。

## 3. 代码核对与基线

本地检查的基线：

- `D:/workspace/Mooncake`：`4ae3ffb04cc5f2f1f52fa70300864edd44bed015`。
- `D:/workspace/unified-cache-management`：`33b59e53b164acb0da481834efca2e706b2ce027`。
- `D:/workspace/mooncake-kvclient`：`fe50d489`，已有另一版 ASU replica 接入实现，只作为风险和实现参考，不当成图中方案既定规范。

本地 `Mooncake` 已有 `KvDeviceClient` port，但 Client 目前仅持有/设置它，不能把“已有 port”当成“Put/Delete 已完成接入”。它的 Entry 仍接受 string key，不过另外定义了 `KvCacheKey = array<uint8_t, 8>` 及 hash 映射辅助函数，并公开注册和 SDK 风格 TaskHandle，更接近设备 SDK 适配口。新通用接口不宜继续扩展成 ASU SDK 镜像；不能把这个辅助类型误读成所有现有方法已强制接受 8 字节 key。

`mooncake-kvclient` 工作树则把 ASU 加入 Master replica 元数据，由 Master 控制可见性，采用 generation、host staging 和 SDK 注册。它说明了工程代价，但它的控制面归属与本图的外部存储独立路径不同，不能直接照搬。

本地 SDK 的事实：

| 内容 | 代码事实 | 对设计的影响 |
| --- | --- | --- |
| 写入/删除 | `StoreAsync`、`BatchStoreAsync`、`DeleteAsync` | 能实现基本数据面；提交成功后仍须获取结果 |
| key | `CacheKey` 固定 8 字节 | 适配层必须解决逻辑 key 映射和冲突，不能让通用层固定 64 位 key |
| 数据描述 | `KVBuffer` 含 region、MRHandle、offset；设备协议有 512B 对齐和 32 位长度约束 | 多 slice、任意逻辑长度需要 framing/staging 或明确拒绝 |
| 内存注册 | 有 `RegisterRegions/UnregisterRegions`，声明在途任务引用的 MR 必须保持注册 | TE 注册不能自动等同于 SDK 注册 |
| 完成结果 | `TaskResult.status` 与逐项 `entryStatus` | 必须逐项转换；不能只检查提交返回码 |
| Check | `!task || task->Done()` | true 也可能是任务不存在，不代表成功 |
| Wait | `WaitContext` 之后无条件 `Remove(taskId)`，包括等待超时 | 不可直接把 SDK Wait 暴露成“可重复等待”的通用契约 |
| 高级语义 | 公共 API 无 CAS、条件删除、枚举、跨服务事务和持久化等级声明 | 不得在适配器里假称已有这些保证 |

源码定位见文末。2026-10-20 还核对了上游公开头文件和贡献文档；本地分支与上游存在差异，提交前应再次以目标 commit 为准。

## 4. 推荐接口及其语义

头文件是完整的数据类型草案。核心签名：

```cpp
class ExternalStorageBackend {
 public:
    virtual Capabilities GetCapabilities() const = 0;
    virtual SubmitResult DumpAsync(
        const std::vector<DumpRequest>& requests) = 0;
    virtual SubmitResult DeleteAsync(
        const std::vector<DeleteRequest>& requests) = 0;
};
```

单对象与批量共用一套接口，单对象就是一个元素。不额外设计单 key 与 batch 两套 virtual 方法，也不暴露 SDK TaskId。

接入点建议是 `Client::SetExternalStorageBackend(shared_ptr<ExternalStorageBackend>)` 或等价的创建期注入，默认 nullptr。配置和 SDK 初始化留给 factory/adapter；服务期间不允许无协调地替换后端。通用接口及默认构建不 include/link `kv_client.h`，只有可选的 KVClientStorageBackend 目标依赖 SDK。Public Put/BatchPut/Remove 的原有签名尽量保持，具体采用哪种双写策略由创建期配置决定。

### 4.1 对象身份

`ObjectId = {namespace, logical_key, incarnation}`。

- namespace 是稳定的存储命名空间，至少隔离租户和部署；所有参与 Client 的解析必须一致。
- logical_key 是原始完整 key，允许按字节表达，不能假设 NUL 终止或只含可打印字符。
- incarnation 是同一次对象创建的代际身份，两条写入路径使用同一值。删除后重建必须换代。它不是存储地址，也不是 SDK TaskId。
- incarnation 必须由双方认可的逻辑协调机制分配/解析；随机给每次调用生成新 UUID 并不能替代逻辑 key 协调。已有 key 的幂等 Put 必须拿到已有身份，不能悄悄创建另一份。
- 同一完整身份只允许同一内容；不同内容返回冲突，不能“最后写入覆盖”。这项约束需要协调层或存储提供排他性，先查后写不能在并发场景保证它。

代际放在公共身份里，是为了防止旧请求清理新对象。**它本身不保证逻辑 key 不会复活，不保证双写原子可见。** 其来源和 key→当前代际的权威映射是接入前必须闭合的依赖，不是当前 SDK 已有功能。

如果首版暂时不具备这个协调机制，可以只评审数据面接口，或显式限制为“不可复用的不可变 key、无跨 Client 同 key 并发变更”的验证版本；不应宣称完整的可重建 key 语义已经实现。

### 4.2 DumpAsync

`DumpRequest = {ObjectId, sources[], logical_size}`。

- sources 按顺序拼接成一个完整对象；所有长度之和必须等于 logical_size，检查溢出、空指针和非法 memory kind。
- sources 是调用方输入内存，不是 Mooncake 远端内存池地址。多个 slice 不等于多个对象，也不等于允许 range update。
- host 为基线；device 必须通过能力声明和实际适配支持，不能把 device pointer 当 CPU pointer memcpy。
- 每段带 allocation/registration lease，操作保留 lease；调用方在后端不再读取前不得修改内容。适配器也可以先复制到自己持有的 staging buffer。
- 整批本地预校验失败且未派发任何项，返回非 OK `Status`。只要某项可能已派发，就必须返回操作对象，逐项报告，不能用一个提交失败掩盖已产生的副作用。
- 空批次返回立即完成的空结果。空对象由 capability 声明，未支持时明确拒绝，不得把零字节 Put 当成成功落存。
- 批量只保证结果与输入一一对应，不保证整批事务。首版拒绝同批重复完整 ObjectId，避免隐含顺序。
- `OK + Applied` 表示完整对象已达到该后端约定的可读取完成点；不自动代表掉电持久化。若项目需要介质持久化，必须另行确认设备协议和能力，不能由 flag 返回推导。

### 4.3 DeleteAsync

`DeleteRequest = {ObjectId}`，删除精确代际，不是无条件删除当前 logical_key。

- 删除成功有两种：确实删除 `OK + Applied`；原本就不存在 `OK + AlreadyAbsent`。
- “不存在”仅在 SDK/存储明确返回不存在时映射；网络不可达、权限错误、超时不能映射成不存在。
- 一次删除不承担枚举、regex、namespace clear、段卸载或删除所有历史代际。
- 延迟的旧代际删除不得影响新代际。物理 key 必须包含代际或提供等价条件删除机制。
- SDK 原生只接收 8 字节 key。把代际编码进物理 key 可降低旧删除误伤风险，但不能解决物理 key 哈希冲突，也不能自动生成逻辑删除 tombstone。
- 物理删除完成不等于逻辑 key 全局不可见。全局可见性和在途写入 fencing 由上层协调，不由这个数据面接口偷偷承诺。

### 4.4 完成、超时与资源

`MutationOperation::Poll/WaitUntil` 返回快照：`done`、`sources_released`、`items[]`。

- `done=false` 表示暂无最终结果；本地 Wait 截止时间到达仅结束等待，不取消、不消费操作。
- `done=true` 后逐项结果稳定，可重复读取。每项有业务状态和 `Applied/AlreadyAbsent/NotApplied/Unknown` 副作用分类。
- `sources_released=true` 才证明该操作不再访问源内存，状态只能从 false 变 true。可以先复制完释放源 lease，但后台存储写入仍未完成。
- 终态超时可能为 `done=true + Unknown + sources_released=false`。调用方可以得到失败，后端仍须保留内存直至 DMA fence。
- 操作句柄析构不等于取消、不等于 DMA 完成。实现必须继续持有内部任务、SDK 实例、MR 和 buffer；不能为了符合 RAII 立即释放在途数据。
- 后端替换只允许停止接收新请求后执行；已有任务持有旧后端直到 drain/fence 完成。初始化/关闭具体形式复用项目现有 factory/lifecycle 习惯，不把厂商配置塞进通用写接口。
- 关闭失败时不能释放可能仍被 DMA 使用的内存。需要可观测的隔离/保留策略，且不能无限制吞掉错误；资源压力应通过 backpressure 明确上报。

为什么不直接使用 `Check(bool) + Wait(TaskId)`：现有 SDK Check 无法区分不存在与完成，Wait 超时会摘除任务；通用契约如果直接继承这些行为，调用方很容易提前释放 MR。适配器需要自有操作对象和完成结果缓存，而非直接转发。

## 5. 双写定义：策略归 Client，不放到 backend

推荐首版只有两种配置状态：未启用外部 backend 时保持原路径；启用后选择 **required 双写**。先不引入多种宽松降级模式。

`ExternalStorageBackend::DumpAsync` 只写外部存储，不回调 Mooncake Put。Client 才知道原生内存副本策略和外部要求。

### 5.1 建议执行顺序

1. 校验策略、能力、源数据和逻辑身份，保留源 lease；不支持的模式在任何一条路径产生副作用前拒绝。
2. 进入原生内存写流程，并获取/验证两路共用的对象身份。沿用 Master 原生创建竞争和已有 key 行为，不绕过它直接覆盖。
3. 提交外部 Dump 和内存传输。允许并行；不必先等待内存完成再开始外部 IO。所有普通、batch、same-node/staging 分支均必须经过相同策略钩子。
4. 等待两路业务结果，**两路都成功且内存 finalize 成功才返回 Put 成功**。外部提交成功不是外部写入成功。
5. 失败时按结果进行原生 revoke 和精确代际外部清理，形成可重试的 cleanup 记录；先确保在途写不会在 Delete 之后重新落入旧对象。若要求崩溃后仍能回收，记录必须持久化或有存储侧 GC/reconciliation；纯进程内队列不能提供此保证。

| 内存路径 | 外部路径 | API 结果与动作 |
| --- | --- | --- |
| 成功 | 成功 | 原生 finalize 确认后成功；但不宣称跨服务原子可见 |
| 成功 | 明确失败且未落存 | Put 失败，撤销本次原生写入 |
| 明确失败 | 成功 | Put 失败，撤销原生待发布项；清理本次精确外部代际 |
| 失败 | 失败 | Put 失败，按每路副作用分类清理 |
| 任一路结果不确定 | 任意 | 报不确定/超时，保留任务与 reconciliation 记录；不能当作“肯定没写入” |
| 两路数据成功，finalize RPC 超时 | 成功 | 提交结果不确定；不能立刻删外部对象，Master 可能已经提交 |

清理只针对这次操作拥有的代际，不能对“已存在对象的幂等成功”执行回滚删除。批量按 key 独立收敛，不能因一个 key 失败删除整批成功对象。

### 5.2 必须讲清的保证级别

required 双写在这里定义的是 **成功响应门槛**，不是分布式事务。外部对象可能先于内存 finalize 变为可读，失败清理也可能失败，存在短暂单边对象和 orphan。

如果 lookup 将来直接信任外部 key 存在，而项目又要求“Put 返回失败的对象绝不可见”，单纯 Dump/Delete 不够：必须新增共同 publication/fencing 协议或权威可见性记录。这是与同事对齐的语义依赖，不在本文擅自定义 lookup/load，更不能声称通过 PutEnd 就约束了独立外部存储。

“内存没有 key 但外部已有 key”的幂等行为也需要共同权威：不能只看 Master OBJECT_ALREADY_EXISTS，也不能用本次提交成功推断之前已完成双写。若需补齐缺失副本，要有显式补齐流程，不能暗中改变普通 Put 的不可变语义。

## 6. Client 删除应如何组合两条路径

这里只定义操作语义，不替同事定义查询接口。

1. 在逻辑变更协调下解析目标代际，并阻止该代际在途写入后续发布；保留后续物理清理需要的身份信息。
2. 使该目标代际在所有可读路径上逻辑失效/不可新获取，并处理原生租约。`force` 只能沿用已有原生语义，不能偷偷绕过设备安全条件。
3. 原生移除和外部精确 Delete 分别执行、记录结果；外部 cleanup 任务可以后续重试，但不得丢失目标代际。
4. 只有两路完成所需删除、且协调可见性条件成立，才报告完整成功。单边失败应保留每路状态，映射现有单返回码时至少返回失败，不能静默成功。

关键例子：旧对象为 `(K, g1)`，删除后新建 `(K, g2)`。延迟 cleanup 只能删除 g1。若旧 Dump 尚在途，先删 g1 后它才落存，g1 会复活；必须先 drain/fence 旧任务，或由存储/协调机制执行代际 fencing。一个进程内 mutex 不能解决跨 Client、崩溃重启和乱序重试。

普通 Delete 对不存在的对象幂等；但“Master 不存在”不等于“外部也不存在”。当外部是独立存储时，不能看到 Master NOT_FOUND 就直接跳过外部处理。精确身份必须从约定的权威记录或保留的 cleanup 记录得到，不能随手生成 UUID。

内存 eviction、卸载 segment、原生副本清理不是用户逻辑 Delete，不应连带删除外部数据，否则外部后端无法承担内存淘汰后的存储作用。反过来，外部存储自行淘汰后是否允许内存继续命中，也必须在两路生命周期策略中明确，不能由 API 名称推断。

## 7. “不走内存 Buffer”应该怎样理解

至少有五种不同东西：

| Buffer/内存概念 | 外部 KV 分支是否需要 | 说明 |
| --- | --- | --- |
| 推理引擎提供的输入/输出内存 | 需要 | dump 的源数据、读的目标数据必须有地方放 |
| Mooncake 分布式内存池 segment 中的副本 BufferDescriptor | 不需要用于外部寻址 | 它只属于原生内存分支 |
| 请求端 ClientBufferAllocator/BufferHandle | 不强制；可选便利层 | 可以不用它分配源内存，也可以用它持有本地数据 |
| FileStorage/offload owner 的恢复暂存 buffer | 不需要复用该路径 | 外部 KV 没必要经本地文件恢复 RPC 再走 TE |
| KVClient 内部 SEND/flag buffer 与注册数据 MR | 需要，由 SDK/适配器管理 | flag buffer 是协议完成缓冲，不是业务数据副本 |

因此适合汇报的说法是：**“外部 KV 路径不依赖 Mooncake 分布式内存池的副本地址和 offload 恢复 Buffer；它仍需要调用方数据内存，以及该存储协议要求的内存注册和完成 Buffer。”**

这属于根据图和代码作出的解释，不能当成已向领导确认的原话含义。

一个特别容易误判的点：本地 `RealClient::get_buffer_internal` 是先从本地 allocator 分配空间，再调用 Get 填充，并非一律返回远端内存池零拷贝视图。因此不能因为它叫 `get_buffer` 就判定必然不支持；是否接通外部元数据和读路径，留给同事评估。

## 8. Client 接口支持边界清单

下表描述**建议首版边界**，不是声称工作区所有代码已支持。拒绝范围应针对启用外部策略的请求/目标，而不是破坏全局原生功能。

### 8.1 首版明确不支持或不直接映射

| 现有接口/能力 | 首版结论 | 原因及要求 |
| --- | --- | --- |
| `Upsert/BatchUpsert`；`upsert* / batch_upsert_from*` | 不支持外部双写 | 原生可变更新与 SDK Store 的整对象写不能自动形成两路同版本更新；预检查拒绝 |
| `TransferWriteRange/BatchTransferWriteRanges` | 不映射到外部 KV | 依赖副本 descriptor、对象内偏移及可变 range write；不是多 slice 整对象 Dump |
| `batch_put_session_start / batch_put_from_multi_buffer_ranges / batch_put_session_end / revoke` | 首版不支持外部双写 session | 分段写入、seal、提交和回滚需额外协议；不能 session 只更新内存却声称已双写 |
| `RemoveByRegex/RemoveAll`；`removeByRegex/removeAll` | 首版不支持完整两路删除 | SDK 无枚举/namespace clear；只枚举 Master 无法覆盖外部独有对象，也缺少清理并发边界 |
| `CreateCopyTask/CreateMoveTask` 指定原生 segment 来迁移外部对象 | 不支持这种映射 | 外部 placement 归存储，不能将外部对象解释成可指定的内存目标地址；原生副本间任务可继续存在 |
| 外部对象返回可直接访问的远端指针、rkey、segment-local view | 不支持 | 对象存储不承诺稳定字节地址；不能伪造内存 BufferDescriptor |
| 将 `acquire_buffer_dummy/release_buffer_dummy` 等本地映射流程直接用于外部对象地址 | 不支持这种直映射 | 本地共享内存/allocator handle 不能代表远端 KV 对象；若复制到本地 buffer，需要另一条显式适配 |
| `BatchGetOffloadObject/BatchTransferReadOffloadRanges` 直接处理外部对象 | 不直接映射 | 它们使用 owner 暂存 buffer、TE endpoint、batch_id 等本地磁盘恢复协议；外部 KV 不走这条协议 |

### 8.2 仍可保留，但不属于外部 backend 接口

| 现有接口/能力 | 结论 |
| --- | --- |
| `MountSegment/UnmountSegment/GetBaseAddr` | 原生内存池功能保留；不是给外部后端挂盘/分配地址的接口 |
| `RegisterLocalMemory/unregisterLocalMemory`、`register_buffer/unregister_buffer` | 继续服务 TE/RealClient 的内存要求；不能据此认定 SDK 已注册，外部分支另行管理注册 |
| `BatchReplicaClear`、segment eviction、原生副本 Copy/Move | 只改变所选原生副本，不等价于删除外部逻辑对象；若调用方期望删除整个 key，应走逻辑 Delete |
| `MountLocalDiskSegment`、FileStorage heartbeat、offload 任务 | 属于原生本地磁盘路径，不应强塞到 ExternalStorageBackend |
| `prefer_alloc_in_same_node` / 原生副本数 / 指定 segment | 原生策略仍可生效；不约束外部 placement。已有实现确实拒绝某组合时明确返回，不能忽略请求条件 |

### 8.3 可以适配，不能因名称含 buffer 而拒绝

| 现有接口/能力 | 本次结论 |
| --- | --- |
| `Put/BatchPut`、`put/put_parts/put_batch` | 本次双写接入目标；parts 必须拼成一个完整对象，不是 range update |
| `put_from/batch_put_from/batch_put_from_multi_buffers/put_from_with_metadata` | 可接入整对象 Dump；保持现有参数及注册要求，必要时 coalesce/staging。metadata 与 payload 的顺序必须固定 |
| device/CUDA IPC 输入 | 只在识别 pointer 类型、正确同步生产事件并支持复制或 direct transport 后启用；不支持时预先拒绝，不能 CPU memcpy |
| `Remove/BatchRemove`、`remove/batchRemove` | 本次两路删除接入目标；必须逐项报告 |
| `get_into/get_buffer/batch_get_buffer/getSize/isExist/Query` 等 | 不因 Buffer 理由列为永久不支持；是否接通外部元数据和读路径由同事设计，本稿不定义其签名或实现 |
| get session、range read | 本稿不定是否支持或接口；需要与同事确认代际和失效约束，不能借写侧结论代替读设计 |

NoF/DFS 与新 backend 在架构上可隔离，但图里“不会干扰”不是已经证明所有配置可组合。首版可明确拒绝未验证组合；长期只要策略、资源所有权和结果汇总清晰，可以允许原生组合再加一条外部写。不要把“ASU 不能与 NoF/DFS 同时启用”固化为通用接口限制。

## 9. KVClientStorageBackend 实现注意事项

1. 使用 `BatchStoreAsync`/`StoreAsync` 与 `DeleteAsync`；SDK 实例、DHT、provider、TaskId 和 flag buffer 全部封装。DPOBackend 可以不用任何这些机制。
2. 对于多 slice 或不对齐对象，可以选择 adapter-owned 对齐 frame：头部存格式版本、完整身份、logical_size、完整性信息，payload 只含逻辑数据，尾部 padding 不对用户可见。能力上限按 frame 扣除头部/padding 后计算，不直接填 UINT32_MAX。
3. key 映射编码必须带字段长度/版本，避免简单字符串连接歧义。不能把 FNV/hash64 当成唯一性保证。存完整 key 并在读取校验只能避免错误返回，不能防止碰撞覆盖或错误删除。
4. 安全支持任意长 key 需要碰撞安全的目录/slot 分配、受协调的映射，或设备可验证身份的条件操作。当前 SDK 公共 API 未提供这些原语；应标为待解决。验证阶段可只接受能无损映射的受限 key 域，但必须明确限制，并由能力/适配器校验，不能对外宣称任意 key 已安全支持。
5. 同时解析提交 status、任务 status、entryStatus 和结果数目。命令级失败不能留下默认 OK 的 entryStatus 被误当成功；逐项数目不符应视为内部协议错误/不确定，而非补零成功。
6. 自有完成线程/事件循环监控有效 SDK TaskId；正常路径待 Check 指示可能终态后获取一次 SDK Wait 结果并缓存。Check 不是成功判断。任务不存在要报告故障，不能吞掉。
7. 针对 SDK transport timeout，必须核实任务终态是否还可能发生远端 DMA；没有设备/transport fence 的证据就保留 MR/数据。轮询到 SDK 终态也不自动证明硬件安全。
8. 注册缓存需要同时管理申请范围、别名、在途引用计数和 backend 生命周期。TE MR 与 SDK MR 不复用句柄；Delete 无 payload 不表示 SDK 内部协议 buffer 无需保留。
9. 支持取消、掉电持久化、原子 create-if-absent、条件 delete、enumeration 都是后续独立能力。不能因为有一个 bool capability 就假设现有硬件实现了它们。

## 10. 与 lookup/load 同事只需对齐的约束

不定义接口，仅对齐以下契约，避免两边各自做出冲突假设：

- 相同 namespace、完整 key、incarnation 和 frame 格式；删除/重建不能误读旧代际。
- 对象的逻辑长度与物理 padding 分离；多 slice 顺序固定。
- 明确成功完成点与可见性来源；外部存在不是天然等价于成功双写已提交。
- 删除时如何使所有路径失效，以及旧查询/在途读写如何处理；不能只清 Master，却让外部独立 lookup 再次返回被删除 key。
- 发生碰撞、身份不符、checksum 错误和已删除代际时必须失败，不能返回其他对象内容。
- 不要求外部存储提供远端内存地址；不把本稿的整对象写限制扩张成未经讨论的读 API 限制。

## 11. 上游拆分与验证建议

建议先讨论小范围 RFC：问题陈述、独立外部 KV 控制面的边界、接口契约、两种示例 adapter（KVClient + 内存 fake 或另一后端）、明确的一致性等级。不要首个 patch 同时加入 ASU enum、修改全部 Master replica wire format、引入厂商 SDK 和完成所有新存储功能。

实际实现时至少覆盖：

- 默认关闭时原生 Put/BatchPut/Remove 行为与链接依赖不变。
- 单对象、多 slice、批量逐项失败、同批重复 key、空对象策略、设备指针拒绝/适配。
- required 双写四种成败组合、提交后超时、finalize 响应丢失，确保不误清已提交对象。
- local wait 超时可重试等待；SDK Wait 只取一次结果；未知 TaskId 不伪装成功。
- 旧代际 cleanup 与新代际并发、Delete 与在途 Dump 乱序、跨 Client 同 key 创建和重启重试。
- MR/源 buffer 在远端完成/fence 前不释放；关闭失败不提前释放 DMA 内存。
- 长 key、namespace/代际编码歧义、强制物理 key 碰撞下不覆盖/误删；未具备保护时拒绝启用对应通用能力。
- 所有写入口一致应用策略，特别是 same-node batch、metadata、多 buffer 和 session 拒绝入口。

这份交付是静态设计与源码分析，没有运行 CANN/ASU/OD 硬件测试，也没有宣称这些并发保证已经由当前 SDK 实现。

## 12. 源码与参考

本地源码（优先作为此次结论依据）：

- [图文件](</C:/Users/Xuuuuuun/Downloads/Mooncake + KVCleint.excalidraw>) 与 [Obsidian 文本](</C:/Users/Xuuuuuun/Downloads/Mooncake + KVCleint.excalidraw.md>)。
- [Mooncake Client 接口](/D:/workspace/Mooncake/mooncake-store/include/client_service.h)、[RealClient 接口](/D:/workspace/Mooncake/mooncake-store/include/real_client.h)。
- [BufferHandle 定义](/D:/workspace/Mooncake/mooncake-store/include/client_buffer.h)、[get_buffer 本地分配再 Get](/D:/workspace/Mooncake/mooncake-store/src/real_client.cpp:3679)。
- [已有设备 port](/D:/workspace/Mooncake/mooncake-store/include/kv_device_client.h)、[原有 StorageBackendInterface](/D:/workspace/Mooncake/mooncake-store/include/storage_backend.h:262)。
- [KvClient 公共接口](/D:/workspace/unified-cache-management/kv_semantics/include/kv_client.h)、[key / buffer / status 类型](/D:/workspace/unified-cache-management/kv_semantics/include/kv_types.h)。
- [SDK Check/Wait 实现](/D:/workspace/unified-cache-management/kv_semantics/src/client/task/task_manager.cc:102)、[协议对齐检查](/D:/workspace/unified-cache-management/kv_semantics/src/trans/proto/kv_protocol.cc)。
- [已有 ASU replica 接入说明](/D:/workspace/mooncake-kvclient/mooncake-store/kvclient.md)、[其 IO 包装](/D:/workspace/mooncake-kvclient/mooncake-store/include/kv_client_io.h)。

公开上游核对入口（只用于上游基线核验；本文提案不代表上游已有此接口）：

- [Mooncake 上游 Client 头文件](https://github.com/kvcache-ai/Mooncake/blob/main/mooncake-store/include/client_service.h)。
- [Mooncake 上游 storage backend 头文件](https://github.com/kvcache-ai/Mooncake/blob/main/mooncake-store/include/storage_backend.h)。
- [贡献要求](https://github.com/kvcache-ai/Mooncake/blob/main/CONTRIBUTING.md)。
