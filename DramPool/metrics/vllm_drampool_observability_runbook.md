# vLLM 推理 + DramStore/DramPool 指标观测 完整操作手册

> 基于 latest develop + PR #1394 (b0c29266) + PR #1438 dashboard (e15bc740)
> 环境：昇腾 A2 / 910B3，容器 codex_drampool_cann851，CANN 8.5.1

---

## 第 0 步：Windows 上开 SSH 隧道（Grafana 用）

```powershell
# 保持这个窗口不关
ssh -L 3110:127.0.0.1:3110 root@110.138.0.3
# 密码: huawei@1234
```

---

## 第 1 步：宿主机上启动 Prometheus + Grafana

```bash
# SSH 到宿主机后执行（不是进容器）
# ssh root@110.138.0.3

# 检查是否已运行
docker ps -a | grep pr1394

# 如果没运行，启动：
docker start pr1394-prom pr1394-grafana pr1394-renderer 2>/dev/null || true

# 验证：
curl -s http://127.0.0.1:9110/-/ready          # 应返回 "Prometheus Server is Ready."
curl -s http://admin:admin1394@127.0.0.1:3110/api/health  # 应返回 version 11.1.0
```

### 如果 Prometheus 容器不存在，重建：

```bash
# 确保配置文件是文件（不是目录）
ls -la /tmp/pr1394-observability/prometheus.yml
# 如果是目录：rm -rf /tmp/pr1394-observability/prometheus.yml

# 确保 prometheus.yml 内容正确
cat > /tmp/pr1394-observability/prometheus.yml << 'EOF'
global:
  scrape_interval: 5s
  evaluation_interval: 5s
scrape_configs:
  - job_name: pr1394-vllm-qwen3
    static_configs:
      - targets: ['127.0.0.1:8300']
        labels:
          chain: pr1394-e2e
EOF

# 确保 Grafana provisioning 配置存在
mkdir -p /tmp/pr1394-observability/grafana/provisioning/dashboards
mkdir -p /tmp/pr1394-observability/grafana/provisioning/datasources
mkdir -p /tmp/pr1394-observability/grafana/dashboards
mkdir -p /tmp/pr1394-observability/promdata && chmod 777 /tmp/pr1394-observability/promdata

cat > /tmp/pr1394-observability/grafana/provisioning/datasources/pr1394-prom.yaml << 'EOF'
apiVersion: 1
datasources:
  - name: pr1394-prom
    uid: afytgdwun8p34c
    type: prometheus
    access: proxy
    url: http://127.0.0.1:9110
    isDefault: true
    jsonData:
      httpMethod: POST
      timeInterval: 5s
EOF

cat > /tmp/pr1394-observability/grafana/provisioning/dashboards/pr1394.yaml << 'EOF'
apiVersion: 1
providers:
  - name: pr1394
    folder: PR1394
    type: file
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
EOF

# 启动 Prometheus
docker rm -f pr1394-prom 2>/dev/null
docker run -d --name pr1394-prom --network host \
  --restart unless-stopped \
  -v /tmp/pr1394-observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  -v /tmp/pr1394-observability/promdata:/prometheus \
  quay.io/prometheus/prometheus:v2.53.0 \
  --config.file=/etc/prometheus/prometheus.yml \
  --web.listen-address=0.0.0.0:9110 \
  --storage.tsdb.path=/prometheus

# 重启 Grafana（加载 provisioning）
docker restart pr1394-grafana
sleep 15
```

---

## 第 2 步：进入容器

```bash
docker exec -it codex_drampool_cann851 bash
```

---

## 第 3 步：配置环境

```bash
cd /home/codex/pr1394-latest
source /usr/local/Ascend/cann/set_env.sh
export PYTHONPATH=/home/codex/pr1394-latest:$PYTHONPATH
export LD_LIBRARY_PATH=/home/codex/pr1394-latest/ucm/store/dram:$LD_LIBRARY_PATH
export UCM_LOG_LEVEL=info
```

---

## 第 4 步：清理旧进程和状态

```bash
# 杀掉所有旧进程
pkill -f traffic_loop 2>/dev/null
for i in 1 2 3; do
  pkill -9 -f "entrypoints.cli" 2>/dev/null
  pkill -9 -f "EngineCore" 2>/dev/null
  sleep 1
done
pkill -f "drampool" 2>/dev/null
sleep 3

# 清理 reporter state（防毒化卡死）和 metrics 目录
rm -f /dev/shm/ucm_drampool*.json /dev/shm/ucm_blocksize_*
rm -rf /tmp/pr1394-chain/metrics /tmp/pr1394-chain/vllm-multiproc
mkdir -p /tmp/pr1394-chain/metrics /tmp/pr1394-chain/vllm-multiproc
```

> **重要**：每次重启 server 前必须删 `/dev/shm/ucm_drampool*.json`，否则 reporter 会毒化卡死（已知 PR 缺陷）。

---

## 第 5 步：选一张空闲 NPU 卡

```bash
# 查看各卡内存占用
for dev in 0 1 2 3 4 5 6 7; do
  MEM=$(npu-smi info 2>/dev/null | grep -A 1 "| $dev.*910B3" | tail -1 | awk '{print $NF}' | sed 's|/.*||' | tr -d ' ')
  if [ -n "$MEM" ] && [ "$MEM" -gt 0 ] 2>/dev/null; then
    FREE=$((65536 - MEM))
    echo "device $dev: used=${MEM}MB free=${FREE}MB"
  fi
done
```

> **选 free > 20000MB 的卡**，记下设备号，下面用 `$DEV` 代替。

```bash
# 设置设备号（按实际情况改）
DEV=3    # ← 改成你选的卡
```

> 如果所有卡都 < 20000MB free（NPU 内存泄漏），尝试降低 `--gpu-memory-utilization` 到 0.2，或联系管理员重载 NPU 驱动。

---

## 第 6 步：启动 DramPool Server

```bash
nohup ./build-latest/ucm/store/dram/drampool \
  --addr 110.138.0.3:57200 \
  --pool-size-gb 5 \
  --kvcache-block-sizes 262144 \
  --ttl-minutes 1 \
  --config /tmp/pr1394-chain/drampool.yaml \
  > /tmp/pr1394-chain/server.log 2>&1 &

echo "SERVER_PID=$!"
sleep 8

# 确认 server 就绪
grep "service ready" /tmp/pr1394-chain/server.log
# 应输出: DramPool service ready, addr=110.138.0.3:57200
```

---

## 第 7 步：确认 UCM 配置正确

```bash
# 检查关键配置（必须是新版键名）
grep -E "local_addr|peer_addrs|metrics_config_path" /tmp/pr1394-chain/ucm_config_vllm.yaml
# 必须包含：
#   local_addr: "110.138.0.3:58301"（不是 local_host！）
#   peer_addrs: ["110.138.0.3:57200"]（不是 node_control_endpoints！）
#   metrics_config_path 在 yaml 顶层（不是 ucm_connector_config 里！）

# 检查 metrics 配置已生成且包含 drampool 指标
grep -c "drampool_" /tmp/pr1394-chain/metrics_config_vllm.yaml
# 应该 > 20
```

### 如果 metrics 配置缺失，重新生成：

```bash
/home/codex/pr1396-validation-3c26b610-src/.venv/bin/python -c "
import yaml
with open('examples/metrics/metrics_configs.yaml') as f:
    cfg = yaml.safe_load(f)
cfg.setdefault('consumers', {})['multiproc'] = True
cfg['log_interval'] = 2
with open('/tmp/pr1394-chain/metrics_config_vllm.yaml', 'w') as f:
    yaml.safe_dump(cfg, f)
dp = [c['name'] for s in ['counter','gauge'] for c in cfg.get(s,[]) if 'drampool' in c['name']]
print('drampool metrics:', len(dp))
"
```

---

## 第 8 步：启动 vLLM（约 4-5 分钟）

```bash
export ASCEND_RT_VISIBLE_DEVICES=$DEV
export PROMETHEUS_MULTIPROC_DIR=/tmp/pr1394-chain/vllm-multiproc

VLLMPY=/home/codex/pr1396-validation-3c26b610-src/.venv/bin/python
MODEL=/home/codex/models-hf/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca

nohup $VLLMPY -c "from vllm.entrypoints.cli.main import main; main()" serve "$MODEL" \
  --served-model-name qwen3-0.6b \
  --port 8300 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.3 \
  --kv-transfer-config '{"kv_connector":"UCMConnector","kv_role":"kv_both","kv_connector_extra_config":{"UCM_CONFIG_FILE":"/tmp/pr1394-chain/ucm_config_vllm.yaml"}}' \
  > /tmp/pr1394-chain/vllm.log 2>&1 &

echo "VLLM_PID=$!"
echo "等待 4-5 分钟..."

# 每 30 秒检查一次是否就绪
for i in $(seq 1 12); do
  sleep 30
  if grep -q "startup complete" /tmp/pr1394-chain/vllm.log 2>/dev/null; then
    echo "vLLM READY!"
    break
  fi
  echo "  waiting... ($((i*30))s)"
done

# 验证 API
curl -s http://127.0.0.1:8300/v1/models | head -c 60
```

---

## 第 9 步：发一个测试请求确认推理正常

```bash
LONG=$(python3 -c "print(' '.join(['The quick brown fox jumps over the lazy dog and runs through the forest.'] * 12))")

curl -s http://127.0.0.1:8300/v1/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"qwen3-0.6b\",\"prompt\":\"$LONG\",\"max_tokens\":20,\"temperature\":0}"
```

> 应返回包含 `"text": " The quick brown fox..."` 的 JSON。

---

## 第 10 步：启动持续流量

```bash
nohup /home/codex/pr1396-validation-3c26b610-src/.venv/bin/python \
  /home/codex/dl-work/traffic_loop.py \
  > /tmp/pr1394-chain/traffic.log 2>&1 &

echo "TRAFFIC_PID=$!"
sleep 15
tail -5 /tmp/pr1394-chain/traffic.log
# 应看到 #1 new: {'prompt_tokens': 13xx, ...} 之类的成功请求
```

---

## 第 11 步：验证指标全链路

```bash
# 1. vLLM /metrics 上有 UCM 指标
curl -s http://127.0.0.1:8300/metrics | grep -c "^ucm:"
# 应 > 200

# 2. dump 计数在涨
curl -s http://127.0.0.1:8300/metrics | grep "drampool_dump_requests_total"
# 应有非零值

# 3. Prometheus 能查到
curl -s -G 'http://127.0.0.1:9110/api/v1/query' \
  --data-urlencode 'query=ucm:drampool_dump_requests_total'
# 应返回 series

# 4. server JSON 有数据
python3 -c "
import json
d = json.load(open('/tmp/pr1394-chain/metrics/drampool_metrics.json'))
print('dump:', d['counters']['drampool_dump_requests_total'])
print('entries:', d['gauges']['drampool_metadata_entry_count'])"
```

---

## 第 12 步：浏览器打开 Grafana

在 Windows 上打开（SSH 隧道保持开着）：

```
http://localhost:3110/d/ucm-dram-metrics/
```

账号 `admin` / 密码 `admin1394`

---

## 已知问题和处理

| 问题 | 症状 | 处理 |
| --- | --- | --- |
| **vLLM 周期性卡死** | API 不响应、流量 `timed out`、进程还活着 | 杀掉重启（用下面的重启脚本，server 不用重启） |
| **NPU 内存泄漏** | 换卡后仍然 `Free memory...less than desired` | 换空闲卡 + 降低 `--gpu-memory-utilization`；终极方案：管理员重载 NPU 驱动 |
| **reporter 毒化** | server 重启后 drampool 面板全空 | 重启 server 前先删 `/dev/shm/ucm_drampool*.json` |
| **配置键名错误** | `missing peer_addrs` 或 `local_addr must use host:port` | 用新版键名：`local_addr`（host:port 格式）、`peer_addrs`（列表） |

---

## 一键重启 vLLM（卡死时用）

```bash
#!/bin/bash
# 保存为 /tmp/restart-vllm.sh，卡死时直接跑
# 记得改 ASCEND_RT_VISIBLE_DEVICES 为你选的卡号

for i in 1 2 3; do pkill -9 -f "entrypoints.cli"; pkill -9 -f "EngineCore"; sleep 2; done
rm -rf /tmp/pr1394-chain/vllm-multiproc && mkdir -p /tmp/pr1394-chain/vllm-multiproc

cd /home/codex/pr1394-latest
source /usr/local/Ascend/cann/set_env.sh
export PYTHONPATH=/home/codex/pr1394-latest:$PYTHONPATH
export LD_LIBRARY_PATH=/home/codex/pr1394-latest/ucm/store/dram:$LD_LIBRARY_PATH
export PROMETHEUS_MULTIPROC_DIR=/tmp/pr1394-chain/vllm-multiproc
export ASCEND_RT_VISIBLE_DEVICES=3   # ← 改成你的卡号
export UCM_LOG_LEVEL=info

MODEL=/home/codex/models-hf/models--Qwen--Qwen3-0.6B/snapshots/c1899de289a04d12100db370d81485cdf75e47ca

nohup /home/codex/pr1396-validation-3c26b610-src/.venv/bin/python \
  -c "from vllm.entrypoints.cli.main import main; main()" serve "$MODEL" \
  --served-model-name qwen3-0.6b --port 8300 --max-model-len 4096 \
  --gpu-memory-utilization 0.3 \
  --kv-transfer-config '{"kv_connector":"UCMConnector","kv_role":"kv_both","kv_connector_extra_config":{"UCM_CONFIG_FILE":"/tmp/pr1394-chain/ucm_config_vllm.yaml"}}' \
  >> /tmp/pr1394-chain/vllm.log 2>&1 &

echo "restarted, PID=$!"
```

---

## 关键配置文件位置

| 文件 | 路径 | 说明 |
| --- | --- | --- |
| drampool server 配置 | `/tmp/pr1394-chain/drampool.yaml` | metrics/queue/GC/TTL 等 |
| DramStore UCM 配置 | `/tmp/pr1394-chain/ucm_config_vllm.yaml` | 连接端点、metrics_config_path（**必须在顶层**） |
| metrics 消费配置 | `/tmp/pr1394-chain/metrics_config_vllm.yaml` | 从 checkout 的 yaml 派生，含 `consumers.multiproc: true` |
| Prometheus 抓取配置 | `/tmp/pr1394-observability/prometheus.yml` | 抓取 `127.0.0.1:8300` |
| Grafana dashboard | `/tmp/pr1394-observability/grafana/dashboards/grafana_dram_metrics.json` | PR #1438 最新版，provisioning 自动加载 |
| Grafana provisioning | `/tmp/pr1394-observability/grafana/provisioning/` | datasource + dashboard provider YAML |

---

## 停止全部

```bash
pkill -f traffic_loop
for i in 1 2 3; do pkill -9 -f "entrypoints.cli"; pkill -9 -f "EngineCore"; sleep 1; done
pkill -f drampool
```

---

## 架构概览

```
vLLM serve (Qwen3-0.6B, device $DEV, :8300)
 ├─ APIServer 进程: /metrics (PROMETHEUS_MULTIPROC_DIR 聚合)
 └─ EngineCore 进程: UCMConnector(vllm_ascend) → UcmPipelineStore("Dram")
     ├─ DramStore worker (device $DEV, libdramstore.so 真实流水线)
     └─ DramStore scheduler (device -1, 含 DramPoolResourceReporter)
          ↕ HIXL (A2 host-sync mock, 32KB 拆段同步传输)
 drampool server (device 4, production 二进制, :57200)
  └─ MetricsReporter 原子写 /tmp/pr1394-chain/metrics/drampool_metrics.json
 Prometheus (pr1394-prom :9110, 5s 抓取 :8300)
 Grafana (pr1394-grafana :3110, provisioning 声明式管理)
```
