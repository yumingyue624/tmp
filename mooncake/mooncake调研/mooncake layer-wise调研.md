## Mooncake Layerwise Connector KV Cache 传输机制分析

### 一、P 端发送机制 (`KVCacheSendingLayerThread`)

**核心结论：逐层发送，但只在最后一层发送一次完成信号。**

#### 1. 每层发送入口 - `save_kv_layer` (第1531-1676行)

```python
# mooncake_layerwise_connector.py:1531-1676
def save_kv_layer(self, layer_name, kv_layer, attn_metadata, connector_metadata, **kwargs):
    # 构造该层的 SendTask
    layer_send_task = SendTask(
        wait_event=reshape_cache_event,
        k_cache=keys,
        v_cache=values,
        layer_idx=self.current_layer,      # 当前层索引
        layer_name=layer_name,
        ...
    )
    # 放入发送队列，由独立线程处理
    self.kv_send_layer_thread.send_queue.put(layer_send_task)
    self.current_layer += 1  # 层数+1
```

#### 2. 实际传输逻辑 - `_transfer_kv_cache` (第427-506行)

```python
# 发送线程
def run(self):
    local_rank = get_world_group().local_rank
    device = torch.device(f"npu:{local_rank}")
    torch.npu.set_device(device)
    self.ready_event.set()
    while True:
        send_task = self.send_queue.get() # 从队列中获取发送任务
        self._handle_request(send_task)ask) # 执行传输


# mooncake_layerwise_connector.py:427-506
def _transfer_kv_cache(self, send_task: SendTask):
    # 执行 RDMA 传输（Mooncake batch_transfer_sync_write）
    for session_id, transfer_meta in session_meta.items():
        ret = self.engine.batch_transfer_sync_write(
            session_id, transfer_meta.src, transfer_meta.dst, transfer_meta.length
        )

        # ★ 关键：只有最后一层才发送完成信号
        if send_task.layer_idx == (self.total_layers - 1):
            for req_id in transfer_meta.req_ids:
                req_meta = send_task.send_request[req_id]
                if req_meta.chunk_finish:  # chunk 也需完成
                    self.callback_func(req_id, req_meta, layer_group_idx)

# mooncake_layerwise_connector.py:1246
# MooncakeLayerwiseConnectorWorker.register_kv_caches() 中
self.kv_send_layer_thread = KVCacheSendingLayerThread(
    engine=self.engine,
    ...
    callback_func=self.send_done_send_signal,  # ← 绑定回调函数
)
```

#### 3. 发送完成信号 - `send_done_send_signal` (第1753-1801行)

```python
# mooncake_layerwise_connector.py:1753-1801
def send_done_send_signal(self, req_id, req_meta, group_idx):
    """P 端所有层传输完成后，通过 ZMQ 发送 DONE_SENDING_MSG 给 D 端"""
    encoded_data = msg_encoder.encode((
        DONE_SENDING_MSG,           # b"done_sending_msg"
        external_req_id,            # 请求 ID
        req_meta.trans_count[group_idx],  # 需要接收的通道数（TP rank 数）
        side_channel_path           # P 端 ZMQ 地址 "host:port"
    ))
    # 通过 ZMQ REQ-ROUTER 发送，等待 ACK
    with zmq_ctx(zmq.REQ, path) as sock:
        ensure_zmq_send(sock, encoded_data, path)
        ack = sock.recv()  # 等待 D 端确认
```

---

### 二、D 端接收机制 (`KVCacheRecvingLayerThread`)

**D 端通过统计收到的 `DONE_SENDING_MSG` 信号数量，当数量等于预期的发送源数量（`trans_count`）时，判定该请求的所有数据已接收完毕，随后将请求 ID 加入完成队列供 vLLM 调度器消费。**

#### 1. 侧信道监听循环 - `run` (第553-590行)

```python
# mooncake_layerwise_connector.py:553-590
def run(self):
    """D 端 ZMQ ROUTER 监听侧信道消息"""
    with zmq_ctx(zmq.ROUTER, path) as sock:
        self.ready_event.set()
        while True:
            frames = sock.recv_multipart()
            msg = decoder.decode(payload[0])

            if msg[0] == GET_META_MSG:
                # D 端返回自己的 layer_metadata 给 P 端查询
                sock.send_multipart((identity, b"", encoded_data))

            elif msg[0] == DONE_SENDING_MSG:
                # ★ 收到 P 端完成信号
                request_id = msg[1]
                trans_count = msg[2]          # 需要接收的 TP rank 数量
                side_channel_path = msg[3]    # P 端的 ZMQ 地址
                self.update_task(request_id, trans_count, side_channel_path)
                sock.send_multipart((identity, b"", b"ACK"))
```

D端的处理信号只有两个：

| `GET_META_MSG`     | **每个请求 1 次** | 第一层传输前  | P 端查询 D 端的 KV 内存地址   |
| ------------------ | ------------ | ------- | -------------------- |
| `DONE_SENDING_MSG` | **每个请求 1 次** | 最后一层传输后 | P 端通知 D 端可以开始 Decode |

#### 2. 任务完成追踪 - `update_task` (第544-551行)

```python
# mooncake_layerwise_connector.py:544-551
def update_task(self, req_id, trans_count, side_channel_path):
    """追踪每个请求的 TP rank 接收完成情况，更新任务接收状态，判断是否所有层、所有并行源的 KV Cache 都已接收完成"""
    with self.lock:
        if req_id not in self.task_tracker:
            # 初始化该请求的追踪集合，用于记录收到了哪些 P 端 Rank 的信号
            self.task_tracker[req_id] = set()
        # 记录收到某个 TP rank 的完成信号
        self.task_tracker[req_id].add(side_channel_path)

        # ★ 当所有 TP rank 都发送了完成信号，标记请求完成
        if len(self.task_tracker[req_id]) == trans_count:
            self.task_tracker.pop(req_id)
            self.done_requests.add(req_id)  # 加入完成队列，通知 vLLM 该请求可以开始 Decode 计算了
```

#### 3. 获取完成请求 - `get_and_clear_finished_requests` (第533-542行)

```python
# mooncake_layerwise_connector.py:533-542
def get_and_clear_finished_requests(self) -> set[str]:
    """供调度器调用，获取已接收完成的请求"""
    with self.lock:
        finished_requests = self.done_requests
        self.done_requests = set()  # 清空
    return finished_requests
```

### 4. 暴露给 vLLM 调度器：`get_finished`

vLLM 的 Scheduler 会在每个 Step 调用 `get_finished` 来获取接收完成的请求。Scheduler 拿到 `done_recving` 集合后，将对应的请求从 `waiting` 队列移动到 `running` 队列。

```python
# mooncake_layerwise_connector.py:1265-1282
class MooncakeLayerwiseConnectorWorker:
    def get_finished(self) -> tuple[set[str], set[str]]:
        """
        获取已完成接收的请求 ID 集合
        返回: (done_sending, done_recving)
        """
        # 从接收线程中获取并清空完成队列
        done_recving = (
            self.kv_recv_layer_thread.get_and_clear_finished_requests()
            if self.vllm_config.kv_transfer_config.is_kv_consumer
            else set()
        )

        # 映射外部请求 ID 到内部请求 ID
        done_recving = {self.request_map[s] for s in done_recving if s in self.request_map}
        # ... 处理虚拟请求等 ...

        # 返回 done_recving 集合给 vLLM Scheduler
        return set(), done_recving
```

### 

### 总结流程图

```text
P 端最后一层传输完成
  ↓ 发送 DONE_SENDING_MSG (携带 trans_count, side_channel_path)

D 端 ZMQ 线程收到信号
  ↓ 调用 update_task(req_id, trans_count, side_channel_path)
  ↓ task_tracker[req_id].add(side_channel_path)
  ↓ 判断 len(task_tracker) == trans_count ?
      ├─ 否: 继续等待其他 P 端 Rank 的信号 (处理并行场景)
      └─ 是: ★ 判定所有层接收完成
           ↓ done_requests.add(req_id)

vLLM Scheduler 轮询
  ↓ 调用 connector.get_finished()
  ↓ 获取 done_recving 集合
  ↓ 将请求移入 running 队列
  ↓ ★ 触发 NPU 执行 Decode Forward 计算
```
