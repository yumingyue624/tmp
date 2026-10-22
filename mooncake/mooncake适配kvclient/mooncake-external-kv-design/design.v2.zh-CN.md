# Mooncake ExternalKVBackend：双写、删除与 Client 接口边界

评审稿 v2，2026-10-20。只定义写入、删除及相关生命周期；不定义 lookup/load。配套 [C++ 接口草案](external_kv_backend_v2.h)。本文是设计建议，未修改 Mooncake 或 KVClient 的实现。

## 1. 推荐结论

采用原设计文档已经选定的方式一：在 Store Client 的对象访问层增加 `ExternalKVBackend`，`KVClientBackend` 是一个实现。原生内存池继续走 Master + TE；外部对象由外部存储系统管理、按完整逻辑 key 访问，不向 Master 逐项登记外部副本。

接口核心只需要 `DumpAsync`、`DeleteAsync`、异步完成对象和能力描述。双写不是后端方法：Client 同时调用原生 Put 和后端 Dump，再汇合结果。后端没有必要知道 Mooncake 的原生副本数或 PutEnd。

沿用原文约定：外部失败不覆盖原生 Put 结果；但同步 Put 返回前，两端必须停止访问输入内存。删除与写入不同：显式 Remove 要尝试两端，即使 Master 已无记录，也要删除外部对象。不能把外部删除失败当成完整删除成功。

建议使用文档已有的 `ExternalKVBackend` 名字；用户提到的 extendkvbackend 可对应这个抽象。`External` 表达独立外部存储边界，优于把接口命名为 ASU、KVClient 或某种 Transport。DPO、DPU 或其他存储系统都可实现它，不要求沿用 KVClient 协议。

## 2. 图与文档怎样对应

图顶部是本次新方案：推理引擎 → RealClient/C++ Client → 两条路径。左侧 Master 负责原生空间、副本状态和租约；TE 负责原生数据传输。右侧 ExternalKVBackend → KVClient SDK，内部有 DHT、TransProvider、Poller 和 flag buffer。

外部 dump 的控制流是提交 key 和源 buffer 描述；远端根据 key 定位存储位置，主动 READ 调用方数据，再 WRITE 响应。只有协议定义的存储完成才是对象操作完成，SEND 完成只是命令传输完成。load 的远端 WRITE 箭头仅用于理解方向，本稿不展开读接口。

图中下方分别展示 FileStorage offload、NoF、原生内存池，是用于比较的不同路径。FileStorage 的 owner 暂存 buffer 和 `LOCAL_DISK` 目录，不属于本方案外部路径。图中“TE 里接 KVTransport”属于另一种接入讨论；文档 3.4 已明确选择方式一，应以该决策为准。

“控制方交给存储”在此应落实为：Mooncake 提供 key、源数据描述和期望的对象操作；存储系统决定对象位置、路由、搬运和内部调度。接口不提供远端地址、LBA、segment、rkey，也不要求远端使用 DHT、RDMA READ 或 flag buffer。Mooncake 仍决定什么时候双写和如何向应用返回结果。

## 3. 通用接口的最小契约

```cpp
class ExternalKVBackend {
 public:
    virtual Capabilities GetCapabilities() const = 0;
    virtual SubmitResult DumpAsync(
        const std::vector<DumpRequest>& requests) = 0;
    virtual SubmitResult DeleteAsync(
        const std::vector<ObjectKey>& keys) = 0;
};
```

单对象就是长度为 1 的批次，避免同时维护单对象和批量的两套虚函数。SDK 类型不出现在公共头文件中。

| 类型 | 约定 |
| --- | --- |
| `ObjectKey` | namespace/tenant 隔离域 + 完整字符串 key；按字节处理，不用 C 字符串截断 |
| `DumpRequest` | key、源 slices、逻辑总长；slices 按顺序拼接为一个完整对象 |
| `SourceSlice` | 地址、长度、memory domain、device ID；不包含远端地址和 SDK MRHandle |
| `SubmitResult` | 成功得到非空 operation；直接错误必须保证整个批次没有任何请求发出 |
| `ItemResult` | 每个 key 的状态及 effect：Applied、AlreadyAbsent、NotApplied、Unknown |
| `MutationOperation` | Poll、可重复 WaitUntil、Drain；不能因一次等待超时丢弃任务 |
| `Capabilities` | 支持的内存域、SG、空对象、对齐和长度/批量限制；不能用它假装实现尚不存在的保证 |

### 3.1 Dump 的语义

- 完整对象写入，不是远端内存写，不是对象范围更新，也不是“先写 Mooncake 内存成功后才 offload”。
- 同一个逻辑 key 内容不可变。重复写同一内容允许重试；后端必须确保重试不会破坏已写对象。不能把“key 已存在”无条件转换为本次写入成功，除非后端或已约定的 key 语义能保证内容一致。
- `logical_size == sum(slice.size)`，计算须检查溢出；每段地址和范围有效。设备源数据在提交前已生产完成，调用方负责必要的事件同步，首版不向公共接口暴露 Ascend/CUDA eventHandle。
- 批量结果与输入下标一一对应。批次不承诺事务原子性，不能因为一个节点失败就丢掉其他节点已成功的事实。
- 同批出现相同 key，首版在提交前拒绝；避免同批重复项的排序和冲突语义不明确。空批次可返回已完成的空结果。
- 成功代表外部缓存服务接受并完成对象发布，不默认承诺断电持久化、永久保留或禁止存储自主淘汰。

### 3.2 提交失败、执行失败和结果不确定

提交前验证不支持的 memory domain、无效范围、大小/对齐、重复 key 等。如果返回 `Status`，必须保证无外部副作用。若已向部分节点发出请求，必须返回 operation，再逐项记录结果；不能让调用方在一个普通错误返回后失去追踪在途 DMA 的办法。

`Unknown` 表示远端可能执行过，但本端无法确定，例如响应丢失。它既不是“肯定没写进去”，也不是“可以释放 buffer”。只有协议完成或连接/设备 fencing 能证明访问停止，才能设置 `sources_released=true`。

`done` 和 `sources_released` 分开：可以已知道某项失败，仍在收敛其远端访问。`WaitUntil` 只控制本地等多久，超时返回当前快照，operation 保留。`Drain` 必须等到结果终态且源内存安全才返回；无法 fencing 时不能保证有限耗时。实际实现若需要有限超时，必须先补充可证明的 transport fence/停止访问机制，不能仅放大超时时间。

### 3.3 buffer 和注册的归属

调用方拥有 payload，提交后到 `sources_released` 前不得修改、释放或解除 IPC 映射。operation 持有请求元数据、SDK 任务和注册资源，不接管 payload。同步 Client 必须显式 Drain；具体 operation 析构也必须收敛操作，防止错误分支漏等。析构的要求由实现落实，抽象基类析构本身不会自动调用虚函数 Drain。

TE 的注册和 KVClient 的注册是不同资源。建议首版在适配层根据源区间注册、缓存并按在途引用释放；复用 Client 现有 register/unregister 入口时，也要管理两套注册状态及失败回滚。不得把 TE 的注册句柄直接当成 SDK MRHandle。

这版只规定源内存生命周期，暂不把单独 Reg/Unreg 固化为必选公共虚函数：普通 CPU 后端可能不需要它。后续性能确需显式注册对象，可以另行增加通用注册能力；它不能持有或分配用户 payload 来偷换生命周期。

## 4. 双写流程与返回值

首版只有原文的 best-effort 外部写策略，不增加 required 双写、事务 commit、rollback 或跨层原子可见性。

1. Client 验证调用参数，按 `(namespace,key)` 进入本客户端的变更序列。
2. 对合法整对象写尝试原生 Put 和外部 Dump。二者可并行执行，不以前者成功作为后者执行的条件。外部参数/能力不满足时记录外部未提交，原生仍按既有行为执行。
3. 等待原生路径收敛，并对已提交的外部 operation Drain。任何早返回分支都不能跳过已启动操作的收敛。
4. 返回原生 Put 原有结果；记录外部逐项失败、耗时和结果不确定。批量每项分别汇合。

| 原生 Put | 外部 Dump | Put 对外结果 | 数据结果 |
| --- | --- | --- | --- |
| 成功 | 成功 | 原生成功 | 两端有缓存，之后都可能按各自策略淘汰 |
| 成功 | 失败/未知 | 原生成功 | 外部可能无副本或结果待确认 |
| 失败 | 成功 | 原生失败 | 外部可能仍有可发现对象 |
| 失败 | 失败/未知 | 原生失败 | 保留外部逐项诊断 |

所有行的返回前提都是两端停止访问源内存。“外部失败不影响结果”不等于“不影响延迟”。若希望 Put 在外部仍搬运数据时立即返回，就必须改变调用方生命周期协议或复制 payload；这与当前约定不同。

原生失败、外部成功时，首版不自动删外部对象：独立发现和不可变缓存语义允许这份缓存存在，也避免误删其他 Client 已成功写入的同 key 对象。该选择要在 API 文档写清楚：Put 失败不保证外部对象不存在；不能对读侧承诺只有原生 PutEnd 成功后外部才可见。

原生返回 already-exists 时保留原有返回语义。外部仍可尝试写入，但前提是业务确实满足同 key 同内容；不能利用它偷偷实现不同内容覆盖。

NoF/DFS 如果属于原生配置，其成功条件仍按原生实现执行，再额外汇合外部路径。本稿不修改它们的分配和提交语义，也不根据图上的“不会干扰”断言全部组合已经通过验证。原生路径自身可能有 staging；本稿不新增外部 payload staging。

## 5. Delete 定义

`DeleteAsync(keys)` 按逻辑 key 删除整个外部对象，包含实现内部的 chunks、manifest 或映射项。已不存在返回成功效果 `AlreadyAbsent`，重复删除幂等。SDK Delete 不带 payload，但它自己的报文/响应区仍需要安全收敛。

### 5.1 Remove 两路汇合

Client 在同 key 写入已经结束或被可靠隔离后，分别执行原生 Remove 和外部 Delete，并收集两路结果。一侧失败不阻止尝试另一侧；Master 的 NotFound 不允许短路外部删除。此处不承诺两端原子删除，中间过程可能只删掉一侧。

| 原生结果 | 外部结果 | 建议 Remove 对外语义 |
| --- | --- | --- |
| 已删/不存在 | 已删/不存在 | 逻辑删除成功；建议归一为幂等成功 |
| 已删/不存在 | 失败/未知 | 删除失败或结果不确定，不能报完整成功 |
| 失败/未知 | 已删/不存在 | 删除失败或结果不确定 |
| 失败/未知 | 失败/未知 | 删除失败，保存两路详情 |

原生 Remove 的公开返回值若必须保留“全部不存在时报 NotFound”的兼容习惯，可在最后一层转换；不能因此跳过外部删除。现有单一错误码返回入口按确定的优先级映射错误，同时日志/指标保留两路详情；错误码增补需要实现时对齐 Mooncake 的 `ErrorCode`，头文件的 Code 只是契约草案。

`BatchRemove` 返回逐项汇合结果。`force` 若保留，应遵循既有原生语义，不能解释成允许绕过外部收敛或安全地释放在途资源。原生 LRU、卸载、节点离线清理不是显式 Remove，不应调用外部 Delete。

### 5.2 同 key 顺序要保证到哪里

单纯先调用 `StoreAsync`、后调用 `DeleteAsync` 不等于先写后删：可能走不同队列、连接或路由视图。要求适配实现保证同实例同 key 的变更顺序；没有 SDK 契约时，首版串行等待前一变更完成，不能只依赖调用顺序。Client 自己还要序列化原生与外部两条路径的同 key 对象操作，多 key 可以并行。

尤其要防止 `Dump 超时 → Delete 成功 → 旧 Dump 迟到 → 对象重新出现`。即使源 buffer 已安全释放，也不代表旧存储变更已经被 fencing。前序变更 outcome=Unknown 时，必须确认完成/隔离旧请求，或者对后续同 key 操作返回 Busy/不确定；不能继续执行后宣称删除成功。operation 的 Drain 保证内存安全，不能替代变更顺序保证。

这版不引入 Master 全局 generation 或全局锁作为前提。跨 Client 的同 key 写删、删除后以不同内容重用 key、严格防复活，需要存储端 tombstone、条件操作或版本/fencing 协议支持。当前 SDK 公共 API 没有声明这些能力；首版限定由上层协调跨 Client 变更，并延续同 key 同内容约定。若产品要求 Remove 对所有并发 Client 严格线性化，就必须扩大设计，不能宣称当前接口已解决。

## 6. “不走内存 Buffer”究竟限制哪些接口

这里最容易把三种 buffer 混在一起：

| buffer | 本方案是否需要 |
| --- | --- |
| 应用持有的 Host/GPU/NPU payload buffer | 必须需要，写入读它、读取写它 |
| Mooncake `ClientBufferAllocator` 分配并返回的 Host 暂存 buffer/BufferHandle | 外部首版不新增这条路径 |
| KVClient 命令和响应 flag buffer | SDK 内部仍需要，不是 KVCache payload 中转 |

所以领导的话更可能是“不经 Mooncake 的 Host 暂存池搬运 payload，外部路径直接使用调用方的 buffer”。这是结合文档作出的解释，不是已经确认的逐字需求。

本地 `get_buffer_internal` 确实先 Query 得长度，再通过 ClientBufferAllocator 分配 buffer，然后读取并返回 BufferHandle。`put_parts_internal` 也会申请连续 buffer，把各段 gather 后再 Put。因此不能只按方法名判断是否零中转。

### 6.1 首版外部路径不支持的接口

这里的“不支持”指不承诺访问外部对象；后端未启用时不应改变已有 Client 行为。

| 接口/类别 | 首版边界 | 原因 |
| --- | --- | --- |
| `get_buffer`、`batch_get_buffer`，及依赖它们返回 bytes/内部 BufferHandle 的包装入口 | 不增加外部 fallback | 调用方没有提供目标内存，当前实现依赖 Mooncake 分配暂存空间；与文档仅支持 get_into 类读取的范围不符 |
| `upsert`、`upsert_from`、`batch_upsert_from`、`batch_upsert_from_multi_buffers`、`upsert_parts`、`upsert_batch`；C++ `BatchUpsert` | 不提供外部覆盖写 | 与同 key 内容不可变冲突；不是 buffer 限制 |
| put session、`batch_put_from_multi_buffer_ranges`、C++ `BatchTransferWriteRanges` 等范围写 | 不接入外部写 | 当前是分段更新已分配位置，本接口是不可变整对象；需单独的聚合发布协议 |
| `removeByRegex` / `RemoveByRegex` | 不承诺删除外部所有匹配对象 | Master 目录不包含全部外部 key；SDK 无枚举接口 |
| `removeAll` / `RemoveAll` | 不承诺清空外部 namespace | 同上；遍历 Master 再删会漏掉仅外部保留的对象 |
| `get_replica_desc`、`batch_get_replica_desc`、copy/move task、副本位置操作 | 不返回/操作外部副本 | 外部对象没有 Master replica descriptor，不暴露物理 placement |
| `batch_replica_clear`、disk replica eviction、segment 操作 | 仅保留原生意义 | 清原生副本不等于删除独立外部缓存 |

Regex/RemoveAll 在启用外部后端后，如果公开语义仍声称删除整个 Store，建议在产生副作用前返回 unsupported；或者提供明确命名的 native-only 管理操作。不能静默只删 Master 后返回全量删除成功。这是上游审查时应明确的兼容性决策。

`get_buffer` 的局限是本期选择，不是技术上永远无法支持。将来增加“分配已注册目标 buffer → load → 返回句柄”的独立功能即可支持，但会引入当前文档排除的内存管理路径。

### 6.2 本次支持或有条件支持的写入口

| 入口 | 处理建议 |
| --- | --- |
| C++ `Put/BatchPut`，Real/Dummy `put_from/batch_put_from` | 首批接入目标，复用调用方源 buffer 并汇合两路结果 |
| `batch_put_from_multi_buffers` | 通用接口用 slices 表达；实际后端必须支持 SG 或在协议中安全映射，不能默认 SDK 已支持一个 key 多个 buffer |
| `put_from_with_metadata` | 明确 payload+metadata 的字节顺序、逻辑长度；只有能无中转表达且读侧使用相同布局才启用 |
| `put`、`put_parts`、`put_batch` | 现有实现可有 Host gather/staging；严格无中转的外部首版不覆盖。若允许复用既有暂存 buffer，也能接入，但必须明确这不满足全程无 Host 中转目标 |
| Dummy/Real IPC buffer | 在 Real 进程导入后的有效地址上注册；保留 IPC 映射至两端操作结束，不能把 Dummy 的裸虚拟地址交给 SDK |

某个源不满足外部能力时，在当前 best-effort 策略下可以保留原生写并记录外部 Unsupported；这表示该次写没有完成外部备份。不要把这种情况向应用描述成“两份都已写好”。

`get_into`、`batch_get_into` 等是文档确定的读侧方向，本稿不定义其签名。`Query/IsExist/getSize`、range read、query snapshot 和 get session 的外部支持，由读侧设计决定；不能因为方法带 buffer 或不带 buffer 就直接判定支持。尤其原生 QueryResult 的地址和租约不能用于伪装外部对象。

## 7. KVClient 适配中尚需解决的问题

| 已核对的 SDK 事实 | 接口设计和落地要求 |
| --- | --- |
| `CacheKey` 固定 8 字节 | 通用接口保留完整逻辑 key。不能直接 hash64 后宣称无碰撞；删除同样会误删。首版需限制为可无损编码的 key 域，或实现安全映射机制 |
| KV 协议要求设备 buffer/offset 512B 对齐；KVBuffer 有 uint32 offset | 对齐和长度上限留在能力与 adapter 验证中；不能静默 padding 越界读取，也不能截断 |
| 一个 KVBuffer 描述一个 key 和一个 buffer | 不能假设重复同 key 的多个 KVBuffer 就会自动拼接；多 slice/变长对象的协议能力须核实 |
| 有 RegisterRegions/UnregisterRegions，要求在途引用保持注册 | 维护独立 SDK 注册生命周期；TE 注册成功不等于 SDK 可访问 |
| `Check` 对不存在的任务也返回 true | true 只表示可能可取结果，不可当作成功证据；TaskNotFound 是异常 |
| `WaitContext` 后无条件 Remove task，包括超时 | 公共 WaitUntil 不可直接透传 SDK Wait；适配层缓存结果，且 SDK 本身需要保留超时任务或提供可证明安全的收敛机制 |
| 有任务级和逐项状态 | 同时解析提交、任务、逐项结果，检查条数和索引，不能只看一个 OK |
| 公共 API 未声明跨请求同 key 有序、条件删除或 fencing | 必须补充协议/SDK 契约或限制适用范围，不能靠 adapter 注释承诺 |

本项目要求不复制 payload，因而不能用“适配层统一先复制到对齐 Host frame”来默认解决 key、长度和 SG 的所有问题。设备能力不足时应明确拒绝外部写；若要在存储端按 chunks/manifest 组织，必须使整对象发布与 Delete 一致，并同读侧共享格式。此处不定义那套读接口。

同样，轮询 SDK Check、直到 true 后只调用一次 Wait，可以避开主动短超时消费任务的常见问题，但不能证明 transport 自己超时后不会再 DMA。这个问题要由实际 provider/协议完成语义证明。当前设计不声称硬件层已验证。

## 8. 与 lookup/load 同事只对齐六条边界

1. 相同的 namespace、完整 key 编码与对象布局；不能两侧各做一次不同的 hash 或拼接。
2. 写入逻辑长度等于 slices 拼接长度；padding 和存储内部元数据不计入用户对象。
3. 外部目录独立于 Master；原生失败不保证外部对象不存在，原生淘汰不触发外部删除。
4. 写成功后的可见性以外部服务语义为准；lookup 命中不是持续保留承诺。
5. Delete 成功的并发范围按第 5 节约定；本稿未承诺取消已有 load，读侧仍需定义在途读取的收敛。
6. 共享内存安全原则：超时不代表可释放，SDK/TE 注册互不替代。不强制读侧采用本稿 MutationOperation 类型。

这些约束不规定 lookup/load 的参数、返回类型或实现，也不要求同事接入全局代际协调。

## 9. 上游可合入性的建议

公共接口和 Client 策略不 include 厂商 SDK；KVClientBackend 作为可选构建目标，默认关闭。通过创建期注入 backend，生命周期内不随意替换；构造/SDK 初始化参数留在工厂中。普通内存 fake 或另一种 KV 服务也应能实现该接口，验证抽象没有绑定 ASU。

沿用 Mooncake expected/ErrorCode 和命名规范完成实现时的类型整合，独立草案的 variant/Code 不代表必须新增一套生产错误体系。不要新增 ASU replica enum、Master 外部对象目录或改 TE Request 来承载 key；这些与选定方式一无关。

建议先提交小范围 RFC：对象边界、best-effort 双写、删除汇合、buffer 生命周期和不支持接口，再分别提交通用 Client 接入与可选 SDK adapter。不能仅因接口通用就保证上游会接受；尤其跨 Client 删除语义、有限超时安全退出和完整 key 映射需要给出可验证依据。

实现阶段的必要验证应包括：双写四种结果、Master 无记录仍 Delete、批量部分派发失败、所有早返回都 Drain、超时任务保留、迟到写入不越过删除、设备/IPC 映射的有效期、key 碰撞与对齐拒绝、unsupported 管理接口的预检查，以及默认关闭时原生行为不变。本文交付只做设计与源码核对，未运行 SDK 或硬件测试。

## 10. 依据和基线

原始依据是用户指定的 Excalidraw JSON、其导出 Markdown，以及《Mooncake + KVClient 双写》完整正文。文档引用的 Mooncake `5d0636f5d39f9de729a58ea036f98c395dd0b291` 在本地 Git 中可读，已用该 commit 的 Client/PyClient 头文件核对接口名。本地工作树另有设备 port 改动，不能将其当成上游接口已经定稿。

- Mooncake 本地 HEAD：`4ae3ffb04cc5f2f1f52fa70300864edd44bed015`。
- unified-cache-management 本地 HEAD：`33b59e53b164acb0da481834efca2e706b2ce027`；分析针对当前可见文件，不声称工作树无修改。
- `mooncake-kvclient` 中已有 Master 管理 ASU generation 的另一种实现，本稿不沿用其 Master 副本控制面。

本地定位：

- [Client 接口](/D:/workspace/Mooncake/mooncake-store/include/client_service.h)、[PyClient 接口](/D:/workspace/Mooncake/mooncake-store/include/pyclient.h)。
- [get_buffer 分配 Host buffer](/D:/workspace/Mooncake/mooncake-store/src/real_client.cpp:3679)、[put_parts gather](/D:/workspace/Mooncake/mooncake-store/src/real_client.cpp:2619)。
- [KVClient 公共接口](/D:/workspace/unified-cache-management/kv_semantics/include/kv_client.h)、[key/buffer/状态定义](/D:/workspace/unified-cache-management/kv_semantics/include/kv_types.h)。
- [SDK Check/Wait 与任务移除](/D:/workspace/unified-cache-management/kv_semantics/src/client/task/task_manager.cc:102)、[KVClient Wait 封装](/D:/workspace/unified-cache-management/kv_semantics/src/client/kv_client_impl.cc:301)。

在线读取文档指定 commit 的原始头文件本次失败，故技术判断以本地该 commit 与工作树代码为依据，不声称已核验最新上游状态。

已有 design.zh-CN.md 和 external_storage_backend_draft.h 保留。本 v2 优先遵循本次原文范围，不采用旧稿中的强制 incarnation、required 双写或默认 payload staging 作为首版前提。
