# Mooncake 外部存储接入方案

**设计版本：v3｜更新：2026-10-22｜状态：接口与流程评审**

方案在 Store 客户端接入可替换的外部存储，保留 Mooncake 原生内存池；KVClientBackend 是第一个实现。本文聚焦模块边界、公共接口、预注册和双写流程，异常与 API 范围集中列出。完整声明见 [external_storage_backend_v3.h](external_storage_backend_v3.h)。

## 1. 接入位置与职责

采用已确定的**方式一：Store 调用完整 KVClient SDK**。双写与结果汇合放在 C++ Client；RealClient 处理上层入口和 IPC 地址转换。外部对象不登记为 Master 副本，原生副本淘汰不删除外部对象。

![图 1：接入位置与模块职责](assets/01-architecture.png)

*图 1 为模块调用关系，不表示 payload 经过 Master。[编辑 Excalidraw](assets/01-architecture.excalidraw) · [SVG](assets/01-architecture.svg)*

| 模块 | 实现职责 |
| --- | --- |
| C++ Client | 双写编排、同 key 本地顺序、原生与外部结果汇合 |
| RealClient | 适配公共入口，导入 IPC 内存，提供本进程有效地址 |
| ExternalStoreBackend | 定义对象操作与注册接口，不暴露 SDK 类型 |
| KVClientBackend | 转换 key/slices，查预注册资源，封装任务与逐项结果 |
| 完整 KVClient SDK | DHT、节点分组、协议、连接、注册实现与完成汇总 |

**接口与实现是继承关系。** Client 持有 ExternalStoreBackend 指针，实际调用 KVClientBackend 实例；并非两个独立 backend 串联转发。以后 DPUBackend 可以实现同一接口，不要求使用 KVClient 的 TaskId 或传输协议。

## 2. 公共接口：对象参数与完成句柄

### 2.1 方法定义

公共命名沿用 Mooncake，方法不加 Async 后缀；具体 backend 可以同步或异步执行。

| 操作 | 单对象 / 批量 | 主要参数 |
| --- | --- | --- |
| 写入 | Put / BatchPut | key(s)、有序 Slice(s) |
| 读取 | Get / BatchGet | key(s)、调用方目标 Slice(s) |
| 查询 | Query / BatchQuery | key(s) |
| 删除 | Remove / BatchRemove | key(s) |
| 注册 | RegisterRegions / UnregisterRegions | 内存区域 / 已注册资源 |

对象操作均直接返回 `tl::expected<std::unique_ptr<OperationHandle>, ErrorCode>`，不使用返回值别名。单对象方法包装长度为 1 的批量调用。以 BatchPut 为例：

```cpp
virtual tl::expected<std::unique_ptr<OperationHandle>, ErrorCode> BatchPut(
    const std::vector<ObjectKey>& keys,
    const std::vector<std::vector<Slice>>& batched_slices) = 0;
```

ObjectKey、Slice、ErrorCode 复用 Mooncake 类型。`keys[i]` 对应 `batched_slices[i]`；外部 backend 不修改描述容器。异步实现须在返回前保存需要的 key/Slice 描述，**只复制描述，不复制 payload**。BatchGet 使用同样的按下标多段数组；这是沿用 Mooncake 命名和对象语义，不是逐字复制其原生 map 参数。

一个 key 对应一个逻辑对象，例如 `slices=[layer0_K, layer0_V, layer1_K, layer1_V]`。逻辑内容按输入顺序拼接，长度为各段 size 的检查求和；不代表各段分别以相同 key 独立写入。

### 2.2 为什么返回 OperationHandle

```cpp
struct OperationResult {
    std::optional<bool> exists;
    std::optional<std::uint64_t> object_size;
};

class OperationHandle {
public:
    virtual ~OperationHandle() = default;
    virtual bool IsReady() const = 0;
    virtual std::vector<tl::expected<OperationResult, ErrorCode>> Wait() = 0;
};
```

**提交成功与执行成功分两次表达：** 提交返回可追踪的句柄；Wait 返回每个输入 key 的最终结果，数量、顺序与输入一致。部分请求已经派发时，也必须返回句柄追踪，不能只报一个错误后遗失在途任务。仅整个调用明确未派发时，提交接口才直接返回 ErrorCode。

| 具体 backend | 句柄内部保存什么 |
| --- | --- |
| KVClientBackend | SDK context、TaskId、下标映射、结果 |
| DPU 等异步实现 | 请求上下文、完成回调状态、结果；不强制存在数字 ID |
| 同步实现 | 已完成的结果；IsReady 为 true，Wait 直接返回 |

OperationHandle 对调用方隐藏 TaskId。Put/Remove 成功时 OperationResult 可为空；Query 成功须给出 exists；只有真实取得长度才填写 object_size。**未知长度不等于 0，查询错误不等于 exists=false。** IsReady 只表示完成，不代表成功。超时、取消、重复领取和析构契约尚需单独确定，当前声明不是完整的资源安全实现。

## 3. 内存注册：提前建立，跨操作复用

![图 2：注册与操作分离](assets/02-registration.png)

*图 2 将一次性资源建立与高频操作分开。[编辑 Excalidraw](assets/02-registration.excalidraw) · [SVG](assets/02-registration.svg)*

### 3.1 两种部署，一个操作入口

| 模式 | 谁执行注册 | backend 最终使用的地址 |
| --- | --- | --- |
| 直接 C++ | 推理进程中的 Client/backend | 本进程 HBM/Host 地址 |
| Dummy/Real | Dummy 发起请求，Real 导入后调用 backend 注册 | Real 进程中的导入地址 |

注册发生在初始化、显式 register、内存池扩容或 IPC 建立阶段。**Put/Get 路径不调用注册接口**；未找到注册记录时外部项返回注册错误，不能临时注册补救。当前 SDK 注册涉及 provider 注册、token 获取及绑定，已有 benchmark 也是先注册 buffer 池，再复用 handle 提交 I/O。

TE 与 KVClient 各自管理注册资源。KVClientBackend 的 StorageBuffer 保存 SDK context、注册区域、MRHandle 和 IPC 映射引用；提交时 SDK 根据 MRHandle 查自己的注册表并填 mrKey，不能直接复用 TE 的凭据。

### 3.2 HBM 到 MRHandle 的映射

注册记录至少保存：**分配身份与代际、物理设备、基址/容量、SDK context、MRHandle、映射引用和在途计数**。同一数字地址不足以标识同一块内存。

| HBM 组织 | 注册与查找 |
| --- | --- |
| 同张 NPU 的独立分配 A、B | 分别注册，各自对应 MRHandle |
| 大池 P 中的多个子块 | P 注册一次，子块共用 handle，用源范围区分 |
| 不同 NPU 地址数值相同 | 按设备、分配身份、SDK context 区分 |
| 释放后新分配复用旧地址 | 旧记录失效，更新代际并重新注册 |

backend 在已绑定设备/context 中定位包含整个 Slice 的注册区域，验证 `size <= capacity - offset`。定位不唯一就报错，不能选第一个匹配项。Dummy 的 IPC 请求如何携带区域身份由其适配层处理，不在公共 Slice 中增加 SDK 字段。

撤销区域时先禁止新引用，再等待原生和外部访问结束，最后注销 MR、关闭 IPC 映射。每次操作完成只释放本次在途引用，长期注册记录继续保留。当前 SDK 未证明单实例任意多卡注册能力，建议 backend 内按设备分开管理 SDK context，具体组合须验证。

## 4. 双写执行流程

### 4.1 正常路径

![图 3：双写时序](assets/03-dual-put.png)

*图 3 保留已确定的调用顺序，OperationHandle 不限定 backend 同步或异步。[编辑 Excalidraw](assets/03-dual-put.excalidraw) · [SVG](assets/03-dual-put.svg)*

```cpp
auto external = backend->BatchPut(keys, batched_slices);
auto native_results = client->BatchPut(keys, batched_slices, config);
if (external) {
    auto external_results = external.value()->Wait();
}
return native_results;
```

示例仅表示顺序。实现需用作用域清理或等效机制保证：**原生路径失败或抛异常，也会收敛已启动的外部操作。** 两路使用同一份不可变源数据；源计算就绪由调用方/connector 保证。现有 AscendStore 普通保存路径在后台发送线程先等待 NPU event，再调用 Mooncake；layerwise 就绪依赖需单独核验。

### 4.2 返回规则

| 原生 Put | 外部 Put | 对外返回 | 记录与处理 |
| --- | --- | --- | --- |
| 成功 | 成功 | 原生成功 | 正常双写 |
| 成功 | 失败 / 未确认 | 原生成功 | 记录外部失败或不确定状态 |
| 失败 | 成功 | 原生失败 | 外部缓存可存在，不自动回滚删除 |
| 失败 | 失败 / 未确认 | 原生失败 | 保留两路诊断 |

上表所有返回都以“两路停止访问源内存”为前提。外部失败不改变 Put 结果，**但可能增加同步等待时间**。传输超时不能作为释放内存的依据；无法确认访问结束的问题见第 7 节。同步 backend 可能在提交时完成，异步 backend 可以与原生 Put 重叠；设备不允许同时访问同一源时，必须串行或拒绝该组合。

## 5. 查询、读取与删除

### 5.1 查询与读取

外部查询结果不伪装成 Master 的 QueryResult/Replica。建议默认采用原生优先：原生明确未命中后查询外部，外部命中再 Get 到调用方目标；原生查询错误单独返回。并行查询与自定义来源选择是扩展策略，不作为基础接口的隐含行为。

| 异常 / 边界 | 处理约定或实施要求 |
| --- | --- |
| 两端都明确未命中 | 返回 NotFound |
| 查询失败、超时、未就绪 | 保留错误/状态，不转换为未命中 |
| 并行模式一端已命中，另一端未返回/失败 | 可使用已知命中；是否额外等待偏好来源需显式配置 |
| Get 部分写入后失败 | 目标不可消费；切换来源前先确认前一次访问结束，成功回退须覆盖本次完整目标范围 |
| 目标区间重叠 | 建议整批派发前拒绝，不用完成顺序定义覆盖结果 |
| 目标容量不足、源范围越界 | 提交前拒绝，不截断或越界传输 |
| 策略缺少位置/网络字段 | 标为 Unknown；策略失败回退默认选择，不能伪造字段或命中 |
| content_tag/长度矛盾 | 报内容/元数据错误，不随机选一份 |

GetSize 需要真实对象长度，而当前 SDK lookup 仅有命中信息。OperationResult.object_size 可以表达长度，**不能凭该字段存在就声称后端已经能取得长度**。长度来源、查询模式组合、读回退与重试预算由读侧模块进一步落实；接口本轮不加入未经讨论的物理位置、tag 或策略参数。

### 5.2 显式删除

推荐顺序为：**同 key 前序写入收敛 → 原生 Remove → 根据原生结果执行外部 Remove → 汇合**。原生 LRU、内存卸载和节点离线清理不触发外部删除。

| 原生结果 | 外部动作 | 对外结果 |
| --- | --- | --- |
| 已删 / 明确不存在 | 执行外部 Remove | 外部已删/不存在则幂等成功 |
| 被租约、权限或其他条件拒绝 | 不删除外部 | 返回原生错误 |
| 超时 / 结果不确定 | 先确认原生结果，不盲删外部 | 返回明确不确定状态 |
| 已删，但外部失败 | 保留外部待处理状态 | 返回删除失败/部分失败，不能报完整成功 |

Master 没有该 key 也必须尝试删除外部对象。部分失败的重试不能误删后来重建的对象，需要明确幂等/对象代际语义；当前基础接口未定义这套协议，不在此增加未经确认的条件字段。

## 6. API 覆盖范围

“接入”表示需要走统一对象流程；具体设备/布局支持仍以 backend 验证为前提。“待扩展”不能对外宣称已支持。

| Mooncake 入口 / 类别 | 外部范围 | 实现边界 |
| --- | --- | --- |
| Put/BatchPut；put_from/batch_put_from | 基础接入 | 预注册源、逐项完成 |
| batch_put_from_multi_buffers、put_from_with_metadata | 按能力接入 | 保序；一个 key 只在整对象完整写入后可读 |
| put/put_parts/put_batch | 有条件接入 | 可复用现有预注册源；不新增临时注册或 payload gather |
| Get/BatchGet；get_into/batch_get_into/batch_get_into_multi_buffers | 基础接口已定义 | 调用方提供目标，需布局/对齐/设备验证 |
| Query/BatchQuery；存在性查询 | 基础接口已定义 | 不把外部结果塞入原生副本/租约结构，错误不等于 false |
| getSize / get_size | 待长度来源落实 | 未知长度不返回 0 |
| get_into_ranges、batch_get_into_multi_buffer_ranges | 待读侧扩展 | 定义精确范围、目标偏移与协议能力；不靠读完整对象再复制代替 |
| 原生 QueryResult snapshot、get session | 本期不接外部 | 不伪造租约，不暗中重查改变快照语义 |
| put session、分段/范围写、BatchTransferWriteRanges | 本期不接外部 | 与当前完整对象 Put 不同，调用前明确拒绝外部语义 |
| Upsert 及批量/多 buffer 变体 | 本期不接外部 | 与同 key 内容不可变冲突，不能偷换成 Put |
| Remove/BatchRemove | 基础接入 | 租约拒绝、原生缺失和部分失败按第 5 节处理 |
| RemoveAll / RemoveByRegex | 本期不接外部 | 全量/按条件删除必须覆盖外部目录，不能只枚举 Master |
| get_buffer / batch_get_buffer | 本期不加外部 fallback | 不新增由 Store 分配 payload 的路径 |
| replica descriptor、copy/move、replica clear、segment 操作 | 保持原生语义 | 外部不暴露 Master 物理副本，也不随清原生副本而删除 |

普通 Put 的某项外部不支持，可按 best-effort 保留原生结果并暴露外部失败。Upsert、会话写、RemoveAll/Regex 等会改变完整操作语义的入口，应在产生副作用前拒绝或明确仅原生的独立管理语义，不能静默只执行一侧却报告全量完成。

## 7. 实现前必须收敛的问题

以下是能力门槛，不是已实现功能。旧稿中的独立元数据服务、mutation ticket、manifest 等都只是可能的实现手段，本版不将它们直接定为必选架构。

| 问题 | 当前事实 | 必须交付的结论 / 验证 |
| --- | --- | --- |
| 字符串 key 与 tenant | SDK key 固定 8 字节 | 确定完整身份的安全映射与租户隔离；hash64 不能证明无碰撞，不能直接截断 |
| 对象长度与多 buffer | lookup 不返回长度；批量不是单对象原子发布 | 确定持久长度来源、布局及完整发布点；部分成功不能被查询为完整对象 |
| 对齐与上限 | 当前单条 Store 的 buffer_length 要求 512B 对齐且为 24 位字段 | 按具体命令验证边界；不向上取整越界读 padding；无精确访问能力时拒绝，禁止偷偷复制 payload |
| 路由与同 key 顺序 | 部分策略依赖批次组成；顺序提交不等于顺序完成 | 写/查/筛选后读的路由必须一致；Client 序列覆盖两路操作；跨 Client 写删语义单独确定，不宣称全局有序 |
| GPU/NPU 与 UB/RoCE | 已有导入地址不等于 SDK 能注册访问 | 按设备×协议×直接/IPC 建验证矩阵；覆盖多卡、地址复用、断链和注销 |
| 等待、退出与内存安全 | 当前 SDK Wait 超时也移除任务；Check 对不存在任务也可为 true | 分开结果状态与访问结束；补齐可收敛超时任务、停止访问依据及退出释放顺序 |
| 初始化失败 | 需要明确模式可见性 | 建议默认启动失败；允许显式 native-only 降级并暴露状态，不能运行中静默把外部错误当 miss |
| 读策略及错误预算 | 基础接口不包含策略参数 | 明确默认查询模式、策略组合、缺字段/异常回退、重试次数与总 deadline；不编造无硬件依据的超时初值 |

**等待超时 ≠ 远端停止访问。** 注册引用留在后台只能避免部分资源提前销毁，不能自动允许调用方复用 payload。当前 Wait 签名尚未表达完整安全契约；在此问题解决并验证前，不把同步返回后的内存安全写成“已经由 SDK 保证”。

实施顺序建议：先完成接口与 fake backend，验证同步/异步两种句柄及逐项结果；再接预注册和 KVClient；最后逐项启用通过验证的设备、布局和读取入口。验收至少覆盖双写四组合、部分提交、原生拒绝删除、Master 未命中仍删外部、key 冲突、整对象可见性，以及超时/退出时无迟到内存访问。

## 8. 源码依据与交付范围

本次只重写本文并新增图件，**未修改接口头文件或运行时实现，未执行设备测试**。示意图描述目标关系，不能作为 SDK 已支持相应能力的证据。

| 核对项 | 本地代码 |
| --- | --- |
| Mooncake 原生类型与对象接口 | [types.h](/D:/workspace/Mooncake/mooncake-store/include/types.h)、[client_service.h](/D:/workspace/Mooncake/mooncake-store/include/client_service.h) |
| SDK 注册及按 handle 填 mrKey | [注册实现](/D:/workspace/unified-cache-management/kv_semantics/src/client/kv_client_impl.cc:330)、[提交转换](/D:/workspace/unified-cache-management/kv_semantics/src/client/kv_client_impl.cc:454) |
| SDK Wait 行为与对应测试 | [Wait](/D:/workspace/unified-cache-management/kv_semantics/src/client/task/task_manager.cc:108)、[超时测试](/D:/workspace/unified-cache-management/kv_semantics/tests/client/kv_client_impl_test.cc:1959) |
| key、长度信息和协议检查 | [kv_types.h](/D:/workspace/unified-cache-management/kv_semantics/include/kv_types.h)、[kv_protocol.cc](/D:/workspace/unified-cache-management/kv_semantics/src/trans/proto/kv_protocol.cc:185) |
| Real 的 NPU IPC 导入 | [real_client.cpp](/D:/workspace/Mooncake/mooncake-store/src/real_client.cpp:3260) |
| AscendStore 在 Put 前等生产 event | [kv_transfer.py](/D:/workspace/vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/kv_transfer.py:237) |

三张图均提供原生 Excalidraw、PNG 和 SVG。PNG 用于 Markdown 直接阅读；Excalidraw 用于后续修改；生成脚本位于 [assets/generate_figures.py](assets/generate_figures.py)。
