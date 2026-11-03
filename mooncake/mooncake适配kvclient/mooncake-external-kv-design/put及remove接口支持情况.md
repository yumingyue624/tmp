# Put / Remove 接口支持情况

## 双写接口支持情况

<table>
<thead>
<tr>
<th rowspan="2">接口</th>
<th rowspan="2">RealClient 对应入口</th>
<th colspan="2">接口支持</th>
<th colspan="3">原接口功能</th>
</tr>
<tr>
<th>KVClientBackend 是否支持</th>
<th>处理方式</th>
<th>入参</th>
<th>行为</th>
<th>使用场景 / 调用者</th>
</tr>
</thead>
<tbody>
<tr><td><code>Client::Put(key, slices, config)</code></td><td>RealClient::put_from、put_from_with_metadata；put、put_parts（中转入口）</td><td>是</td><td>外部 Put 提交 → 原生 Put → Wait；仅支持无需内部中转的源 buffer</td><td>key + <code>std::vector&lt;Slice&gt;</code></td><td>PutStart 分配副本，传输，PutEnd 提交</td><td><code>RealClient::put_from</code>；put、put_parts 虽调用此接口，但不在 KVClientBackend 支持范围内</td></tr>
<tr><td><code>Client::BatchPut(keys, batched_slices, config)</code></td><td>RealClient::batch_put_from、batch_put_from_multi_buffers；put_batch（中转入口）</td><td>是</td><td>以空 stager 委托共同实现；仅支持无需内部中转的源 buffer</td><td>多 key + 每个 key 的 slices</td><td>批量分配、传输、提交</td><td><code>RealClient::batch_put_from</code>、<code>batch_put_from_multi_buffers</code>；不支持 put_batch 中转入口</td></tr>
<tr><td><code>Client::BatchPut(keys, batched_slices, config, WriteBufferStager)</code></td><td>RealClient::batch_put_from_multi_buffers（stage_nonlocal=true）</td><td>否</td><td>不支持启用 stager 的中转场景；空 stager 委托按普通 BatchPut 处理</td><td>同上 + stager</td><td>非本地 buffer 可先 staged 到内部 buffer</td><td>RealClient 的 <code>stage_nonlocal=true</code> 路径</td></tr>
<tr><td><code>Client::Upsert / BatchUpsert</code></td><td>RealClient::upsert_from、batch_upsert_from、batch_upsert_from_multi_buffers；upsert、upsert_parts、upsert_batch（中转入口）</td><td>是</td><td>外部 Put / BatchPut 提交 → 原生 Upsert / BatchUpsert → Wait；KVClient Store 支持同 key 覆盖写，不因 key 已存在报错</td><td>key(s) + slices</td><td>存在则更新，不存在则插入</td><td>upsert 系列</td></tr>
<tr><td><code>Client::TransferWriteRange(replica, slices, dst_offset)</code></td><td>无直接对应入口</td><td>否</td><td>Client 层无 key，仅执行原生范围传输</td><td>已缓存的副本描述 + slices + 目标偏移</td><td>往已有 memory replica 的指定区间写</td><td>Put session 的分段写</td></tr>
<tr><td><code>Client::StartBatchPutForSizes(keys, sizes, config)</code></td><td>RealClient::batch_put_session_start</td><td>无需适配</td><td>沿用原生会话初始化；外部数据在范围写阶段提交</td><td>keys + 对象大小</td><td>只做批量写的分配阶段，预留副本空间</td><td>Put session：开始</td></tr>
<tr><td><code>Client::BatchTransferWriteRanges(...)</code></td><td>RealClient::batch_put_from_multi_buffer_ranges</td><td>是</td><td>RealClient 提交外部范围写 → 原生批量范围写 → Wait，逐 key 合并结果</td><td>多副本 + 多 slices + 多偏移</td><td>批量区间写</td><td>Put session：分段写（Start 与 End / Revoke 之间，可多次调用）</td></tr>
<tr><td><code>Client::BatchPutEnd(object_metas, replica_type)</code></td><td>RealClient::batch_put_session_end</td><td>无需适配</td><td>沿用原生会话提交；各次外部范围写已在返回前完成 Wait</td><td>对象元数据 + 副本类型</td><td>提交 Put session</td><td>Put session：提交</td></tr>
<tr><td><code>Client::BatchPutRevoke(keys, replica_type)</code></td><td>RealClient::batch_put_session_revoke</td><td>无需适配</td><td>沿用原生会话撤销；等待在途范围写结束，不回滚或删除已写入的外部数据</td><td>keys + 副本类型</td><td>撤销原生预留副本</td><td>Put session：撤销 / 失败清理</td></tr>
</tbody>
</table>

## 删除接口支持情况

<table>
<thead>
<tr>
<th rowspan="2">接口</th>
<th rowspan="2">RealClient 对应入口</th>
<th colspan="2">接口支持</th>
<th colspan="3">原接口功能</th>
</tr>
<tr>
<th>KVClientBackend 是否支持</th>
<th>处理方式</th>
<th>入参</th>
<th>行为</th>
<th>使用场景 / 调用者</th>
</tr>
</thead>
<tbody>
<tr><td><code>Client::Remove(key, force)</code></td><td>RealClient::remove</td><td>是</td><td>外部 Remove 提交 → 原生 Remove → Wait；汇合两侧结果</td><td>key + force</td><td>删除对象及其原生副本；force 控制原生租约检查</td><td><code>RealClient::remove</code></td></tr>
<tr><td><code>Client::BatchRemove(keys, force)</code></td><td>RealClient::batchRemove</td><td>是</td><td>外部 BatchRemove 提交 → 原生批量删除 → Wait；结果按原始下标还原</td><td>keys + force</td><td>批量删除对象，逐项返回结果</td><td><code>RealClient::batchRemove</code></td></tr>
<tr><td><code>Client::RemoveByRegex(str, force)</code></td><td>RealClient::removeByRegex</td><td>否</td><td>kvclient未提供正则删除的能力</td><td>正则表达式 + force</td><td>MasterService 遍历所有元数据 shard 中当前 tenant 的 key，以 regex_search 匹配后删除满足删除条件的对象</td><td><code>RealClient::removeByRegex</code>；按条件清理</td></tr>
<tr><td><code>Client::RemoveAll(force)</code></td><td>RealClient::removeAll</td><td>否</td><td>kvclient全量清理能力未提供</td><td>force</td><td>删除原生对象及其副本</td><td><code>RealClient::removeAll</code>；全量清理</td></tr>
<tr><td><code>Client::EvictDiskReplica(...)</code>（两个重载）</td><td>无直接对应入口</td><td>否</td><td>仅通知 Master 淘汰原生磁盘副本，保留外部对象</td><td>key + replica_type；可显式指定 tenant_id</td><td>移除对应磁盘副本记录</td><td>本地磁盘副本淘汰 / 失效处理</td></tr>
<tr><td><code>Client::EvictDiskReplicaIfCurrent(...)</code></td><td>无直接对应入口</td><td>否</td><td>仅在副本身份仍匹配时淘汰原生副本</td><td>key + tenant_id + replica_type + expected_replica_id</td><td>条件淘汰，防止误清理已替换的副本</td><td>读取失败后的副本核验与清理</td></tr>
<tr><td><code>Client::BatchEvictDiskReplica(...)</code>（两个重载）</td><td>无直接对应入口</td><td>否</td><td>批量淘汰原生磁盘副本，保留外部对象</td><td>keys + replica_type；可显式指定 tenant_id</td><td>批量移除对应磁盘副本记录</td><td>批量磁盘淘汰 / 失效处理</td></tr>
<tr><td><code>Client::PollRemoveAll()</code></td><td>无直接对应入口</td><td>否</td><td>仅查询原生清理状态，不发起删除</td><td>无</td><td>向 Master 轮询清理状态</td><td>原生清理状态轮询</td></tr>
</tbody>
</table>
