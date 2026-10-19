---
name: ascend-container-test
description: Use when the user asks to test UCM ASU transport code on Ascend hardware in the ymy_ascend_test container, including unit tests, host-pinned buffer tests, AICPU Provider/HCOMM/RDMA two-rank tests, and client-to-server BatchStore/flag-buffer E2E validation.
---

# Ascend Container Test Skill

## Overview

This skill covers how to connect from the Windows host to the Ascend test machine, run commands inside the `ymy_ascend_test` container, sync local code into the container, build ASU test binaries, run ASU unit tests, and run the AICPU Provider/HCOMM/RDMA two-rank tests.

Use this skill only for UCM ASU transport testing on the Ascend hardware container.

## Environment

- Host OS: Windows PowerShell 5.1
- SSH target: `root@110.138.0.3`
- SSH password: `huawei@1234`
- Container: `ymy_ascend_test`
- Main container repo: `/home/ymy/unified-cache-management`
- Provider/RDMA test repo copy: `/tmp/ucm-provider-mem-test`
- Python: use `paramiko` from Windows. Do not assume `sshpass` exists.

## Connection

Use paramiko to SSH to the host, then use `docker exec` for commands inside the container. For repeatable runs, save a Python probe script in the local workspace and execute it with `python`.

```python
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("110.138.0.3", port=22, username="root", password="huawei@1234", timeout=15)

cmd = 'docker exec ymy_ascend_test bash -lc "<YOUR_COMMAND>"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
print(stdout.read().decode("utf-8", errors="replace"))
print(stderr.read().decode("utf-8", errors="replace"))
ssh.close()
```

Prefer `bash -lc` inside `docker exec` so `source`, environment exports, shell functions, and multiline scripts behave consistently.

## Code Sync

### Git Safe Directory

Run this once if Git complains about repository ownership:

```bash
docker exec ymy_ascend_test bash -lc \
  "git config --global --add safe.directory /home/ymy/unified-cache-management"
```

### Git Bundle Method

Use this when testing a committed branch or a full local branch state.

On Windows:

```bash
git bundle create <bundle_path> <branch_name>
```

Upload the bundle to `/tmp/` on the remote host with paramiko SFTP, then:

```bash
docker cp /tmp/<bundle_file> ymy_ascend_test:/tmp/<bundle_file>
docker exec ymy_ascend_test bash -lc '
  cd /home/ymy/unified-cache-management
  git fetch /tmp/<bundle_file> +<branch>:refs/remotes/bundle/<branch>
  git reset --hard refs/remotes/bundle/<branch>
'
```

Do not reset the user's local Windows worktree. Only reset the container checkout when the user explicitly wants the container to match a branch or bundle.

### Direct File Overlay Method

Use this for quick testing of uncommitted local files. Upload files by SFTP to the remote host, then `docker cp` them into the same relative path inside the container.

```python
from pathlib import Path
import posixpath
import paramiko

local_repo = Path(r"D:\workspace\unified-cache-management")
remote_stage = "/tmp/ucm-overlay"
container = "ymy_ascend_test"
container_repo = "/home/ymy/unified-cache-management"

files = [
    "ucm/shared/trans/ascend/ascend_buffer.cc",
    "ucm/shared/trans/ascend/ascend_buffer.h",
    "ucm/transport/kv/asu/trans/src/buffer_manager.cpp",
    "ucm/transport/kv/asu/trans/src/buffer_manager.h",
    "ucm/transport/kv/asu/trans/src/asu_submit_flow.cpp",
    "ucm/transport/kv/asu/trans/src/asu_transport_impl.cpp",
    "ucm/transport/kv/asu/trans/src/sqe_request.cpp",
    "ucm/transport/kv/asu/trans/test/asu_submit_flow_test.cpp",
    "ucm/transport/kv/asu/trans/test/sqe_request_test.cpp",
    "ucm/transport/kv/asu/test/case/buffer_manager_test.cc",
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("110.138.0.3", username="root", password="huawei@1234", timeout=15)
sftp = ssh.open_sftp()
ssh.exec_command(f"rm -rf {remote_stage} && mkdir -p {remote_stage}")[1].channel.recv_exit_status()

for rel in files:
    local = local_repo / rel
    remote = posixpath.join(remote_stage, rel.replace("\\", "/"))
    ssh.exec_command(f"mkdir -p {posixpath.dirname(remote)}")[1].channel.recv_exit_status()
    sftp.put(str(local), remote)
    ssh.exec_command(
        f"docker exec {container} mkdir -p {posixpath.dirname(posixpath.join(container_repo, rel))}"
    )[1].channel.recv_exit_status()
    ssh.exec_command(
        f"docker cp {remote} {container}:{posixpath.join(container_repo, rel)}"
    )[1].channel.recv_exit_status()

sftp.close()
ssh.close()
```

Report whether the container was synced by bundle, Git, or direct file overlay.

## ASU Unit Test Workflow

Known-good build directory:

```bash
/home/ymy/unified-cache-management/build-asu-host-pinned
```

Configure:

```bash
cd /home/ymy/unified-cache-management
source /usr/local/Ascend/cann/set_env.sh
cmake -B build-asu-host-pinned \
  -DBUILD_UNIT_TESTS=ON \
  -DBUILD_UCM_ASU=ON \
  -DRUNTIME_ENVIRONMENT=ascend \
  -DASCEND_SUPPORTS_REGISTER_PIN=ON \
  -DCMAKE_BUILD_TYPE=Debug \
  -DPython_ROOT_DIR=/usr/local/python3.12.13
```

Build:

```bash
cmake --build build-asu-host-pinned --target asu.test -j4
```

Run the focused host-pinned/transport-buffer test set:

```bash
./build-asu-host-pinned/ucm/transport/kv/asu/asu.test \
  --gtest_filter="BufferManagerTest.*:AsuSubmitFlowBufferTest.*:SqeRequestTest.*"
```

Expected result from the previously validated run:

```text
[==========] 42 tests from 3 test suites ran.
[  PASSED  ] 42 tests.
```

Run the broader ASU unit test set:

```bash
./build-asu-host-pinned/ucm/transport/kv/asu/asu.test \
  --gtest_filter="-TransportTaskCompletionTest.*"
```

Expected result from the previously validated run:

```text
[==========] 309 tests from 17 test suites ran.
[  PASSED  ] 309 tests.
```

`TransportTaskCompletionTest.*` is excluded in the broad run because it has a known test-double/stub issue unrelated to the host-pinned buffer changes. State the exclusion explicitly in reports.

Some negative-path unit tests intentionally print `[UC][E]` logs, for example malformed SQE attrs or missing buffers. Treat those logs as expected when the corresponding GTest case reports `[ OK ]` and the final summary is `[ PASSED ]`.

## CMake Python Notes

If configure fails with Python errors like missing `Interpreter` or `Development.Module`, pass the known Python root:

```bash
cmake -B build-asu-host-pinned \
  -DBUILD_UNIT_TESTS=ON \
  -DBUILD_UCM_ASU=ON \
  -DRUNTIME_ENVIRONMENT=ascend \
  -DASCEND_SUPPORTS_REGISTER_PIN=ON \
  -DCMAKE_BUILD_TYPE=Debug \
  -DPython_ROOT_DIR=/usr/local/python3.12.13
```

If the include directory is still missing, inspect the container Python installation before installing packages or changing system state.

## Provider/HCOMM/RDMA Environment

The AICPU Provider/HCOMM/RDMA two-rank tests use a separate repo copy and build:

| Item | Path |
| --- | --- |
| Test repo copy | `/tmp/ucm-provider-mem-test` |
| Build dir | `/tmp/ucm-provider-mem-test/build-provider-custom` |
| Provider binary | `/tmp/ucm-provider-mem-test/build-provider-custom/ucm/transport/kv/asu/aicpu_send_with_provider` |
| HIXL kernel JSON | `/home/lx/test/hixl/build/device_build/src/ops/hixl_kernel/libcann_hixl_kernel.json` |
| HCOMM lib dir | `/home/lx/test/hcomm/build/_CPack_Packages/Linux/External/hcomm-9.1.0-Linux/aarch64-linux/lib64` |
| IP map file | `/tmp/npu_ip_map.txt` |

Create the IP map inside the container:

```bash
cat > /tmp/npu_ip_map.txt <<'EOF'
192.168.190.170 0
192.168.190.172 2
EOF
```

Use this environment before running the provider binary:

```bash
source /usr/local/Ascend/cann/set_env.sh
export HCOMM_LIB=/home/lx/test/hcomm/build/_CPack_Packages/Linux/External/hcomm-9.1.0-Linux/aarch64-linux/lib64
export LD_LIBRARY_PATH=/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/cann-9.1.0-beta.1/aarch64-linux/lib64:/usr/local/Ascend/cann-9.1.0-beta.1/aarch64-linux/lib64/device/lib64:${HCOMM_LIB}:/tmp/ucm-provider-mem-test/build-provider-custom/ucm/transport/kv/asu:${LD_LIBRARY_PATH}
export LIBRARY_PATH=/tmp/ucm-provider-mem-test/build-provider-custom/_deps/zlib-build:/usr/local/Ascend/cann-9.1.0-beta.1/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LIBRARY_PATH}
```

The `libascend_hal.so` loader failure means `LD_LIBRARY_PATH` is incomplete. Add `/usr/local/Ascend/driver/lib64/driver`.

HCCN ping is a host-level check, not a container command on many setups:

```bash
hccn_tool -i 0 -ping -g address 192.168.190.172
hccn_tool -i 2 -ping -g address 192.168.190.170
```

Expected ping result:

```text
3 packets transmitted, 3 received, 0.00% packet loss
```

## Provider Memory RDMA Read/Write Test

This test verifies that the provider can register the selected memory type and use the two-card connection for remote access. Run rank 1 first in the background, then rank 0.

```bash
run_provider_rw_test() {
  local MEMORY="$1"
  local TAG="${MEMORY}"
  local MESSAGE="RW_${MEMORY}_OK"
  local BIN=/tmp/ucm-provider-mem-test/build-provider-custom/ucm/transport/kv/asu/aicpu_send_with_provider
  local JSON=/home/lx/test/hixl/build/device_build/src/ops/hixl_kernel/libcann_hixl_kernel.json

  rm -f /tmp/r0-${TAG}.bin /tmp/r1-${TAG}.bin /tmp/done-${TAG} \
        /tmp/r0-${TAG}.log /tmp/r1-${TAG}.log

  timeout 90s ${BIN} \
    --rank=1 --logic-dev=2 --phy-dev=2 --ip=192.168.190.172 \
    --bytes=4096 --memory=${MEMORY} --message="${MESSAGE}" \
    --local-file=/tmp/r1-${TAG}.bin --peer-file=/tmp/r0-${TAG}.bin \
    --done-file=/tmp/done-${TAG} --kernel-json=${JSON} --ip-map=/tmp/npu_ip_map.txt \
    > /tmp/r1-${TAG}.log 2>&1 &
  local R1_PID=$!

  sleep 2

  timeout 90s ${BIN} \
    --rank=0 --logic-dev=0 --phy-dev=0 --ip=192.168.190.170 \
    --bytes=4096 --memory=${MEMORY} --message="${MESSAGE}" \
    --local-file=/tmp/r0-${TAG}.bin --peer-file=/tmp/r1-${TAG}.bin \
    --done-file=/tmp/done-${TAG} --kernel-json=${JSON} --ip-map=/tmp/npu_ip_map.txt \
    > /tmp/r0-${TAG}.log 2>&1
  local R0=$?

  wait ${R1_PID}
  local R1=$?

  echo "===== ${MEMORY}: rank0 ====="
  cat /tmp/r0-${TAG}.log
  echo "===== ${MEMORY}: rank1 ====="
  cat /tmp/r1-${TAG}.log
  echo "RESULT memory=${MEMORY} rank0=${R0} rank1=${R1}"
}

run_provider_rw_test device
run_provider_rw_test host
run_provider_rw_test host-pinned
```

Success indicators:

- `memory registered`
- `connection created`
- `RDMA WRITE completed`
- `RDMA READ verify ok`
- `RDMA WRITE verify ok`
- `done`
- `RESULT memory=<type> rank0=0 rank1=0`

Failure triage:

- `Exit 127`: binary not found or shared library missing. Inspect `/tmp/r0-*.log` and `/tmp/r1-*.log`.
- `error while loading shared libraries: libascend_hal.so`: fix `LD_LIBRARY_PATH`.
- `ChannelCreate failed, ret=9` or `ret=15`: memory registration/import already got past the earlier stage, but HCOMM/HIXL channel creation failed. Check HCCN ping, stale processes, rank/IP/device mapping, provider build, and HCOMM runtime state. This alone is not proof that the memory type cannot be registered.
- If only registration is needed, inspect whether the log reached `memory registered` and `peer memory imported`.

## BatchStore Flag-Buffer E2E Test

Use this when validating the real client-to-server path: client packs a BatchStore SQE into the send buffer, sends it to server, and server writes the completion flag/CQE back to the client's flag buffer by RDMA write.

If the demo source was staged as `/tmp/batch_store_flag_e2e.cc`, overlay it into the provider test repo:

```bash
cp /tmp/batch_store_flag_e2e.cc \
  /tmp/ucm-provider-mem-test/ucm/transport/kv/asu/test/demo/aicpu_send_with_provider.cc
sed -i 's/^void PrintBytes/__attribute__((unused)) void PrintBytes/' \
  /tmp/ucm-provider-mem-test/ucm/transport/kv/asu/test/demo/aicpu_send_with_provider.cc
```

If testing uncommitted ASU code, overlay the current local files into `/tmp/ucm-provider-mem-test` before building.

Build the provider demo:

```bash
cd /tmp/ucm-provider-mem-test
source /usr/local/Ascend/cann/set_env.sh
export HCOMM_LIB=/home/lx/test/hcomm/build/_CPack_Packages/Linux/External/hcomm-9.1.0-Linux/aarch64-linux/lib64
export LIBRARY_PATH=/tmp/ucm-provider-mem-test/build-provider-custom/_deps/zlib-build:/usr/local/Ascend/cann-9.1.0-beta.1/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LIBRARY_PATH}
cmake --build /tmp/ucm-provider-mem-test/build-provider-custom --target aicpu_send_with_provider -j4
```

Run the two ranks with host-pinned memory:

```bash
run_batch_store_flag_e2e() {
  local MEMORY=host-pinned
  local TAG=batch-store-${MEMORY}
  local BIN=/tmp/ucm-provider-mem-test/build-provider-custom/ucm/transport/kv/asu/aicpu_send_with_provider
  local JSON=/home/lx/test/hixl/build/device_build/src/ops/hixl_kernel/libcann_hixl_kernel.json

  rm -f /tmp/r0-${TAG}.bin /tmp/r1-${TAG}.bin /tmp/done-${TAG} \
        /tmp/r0-${TAG}.log /tmp/r1-${TAG}.log

  timeout 120s ${BIN} \
    --rank=1 --logic-dev=2 --phy-dev=2 --ip=192.168.190.172 \
    --bytes=4096 --memory=${MEMORY} --message=BATCH_STORE_HOST_PINNED \
    --local-file=/tmp/r1-${TAG}.bin --peer-file=/tmp/r0-${TAG}.bin \
    --done-file=/tmp/done-${TAG} --kernel-json=${JSON} --ip-map=/tmp/npu_ip_map.txt \
    > /tmp/r1-${TAG}.log 2>&1 &
  local R1_PID=$!

  sleep 2

  timeout 120s ${BIN} \
    --rank=0 --logic-dev=0 --phy-dev=0 --ip=192.168.190.170 \
    --bytes=4096 --memory=${MEMORY} --message=BATCH_STORE_HOST_PINNED \
    --local-file=/tmp/r0-${TAG}.bin --peer-file=/tmp/r1-${TAG}.bin \
    --done-file=/tmp/done-${TAG} --kernel-json=${JSON} --ip-map=/tmp/npu_ip_map.txt \
    > /tmp/r0-${TAG}.log 2>&1
  local R0=$?

  wait ${R1_PID}
  local R1=$?

  echo "===== batch-store rank0 ====="
  cat /tmp/r0-${TAG}.log
  echo "===== batch-store rank1 ====="
  cat /tmp/r1-${TAG}.log
  echo "RESULT batch-store-${MEMORY} rank0=${R0} rank1=${R1}"
}

run_batch_store_flag_e2e
```

Success indicators:

- `client packed BatchStore SQE`
- `client RDMA SEND BatchStore SQE`
- `server parsed BatchStore SQE`
- `server RDMA WRITE CQE`
- `server CQE RDMA WRITE completed`
- `client completion verify ok via CPU mapping`
- `RESULT batch-store-host-pinned rank0=0 rank1=0`

If the run fails at `ChannelCreate failed`, it did not reach the SEND/WRITE verification stage.

## Host-Pinned Buffer Expectations

For UCM ASU host-pinned transport buffers:

- Allocate host memory with ACL host allocation.
- Register the host memory to the NPU.
- Keep both CPU local address and NPU/device-visible address.
- CPU packing code writes to the local CPU address.
- Provider/HCOMM registration and remote RDMA operations use the device-visible address when required by the provider path.
- Buffer slots must preserve the ACL host-allocation suballocation rule: `stride = ALIGN_UP(ALIGN_UP(capacity, 32) + 32, 64)`.

Do not treat the user-requested slot size as the physical slot stride. The requested size is the payload capacity. The stride includes alignment and the extra 32-byte allowance required by the ACL host allocation documentation for subdivided large host allocations.

## Padding Experiment Result

A previous manual experiment tested removing the extra 32-byte slot padding for the provider RDMA read/write test:

- `host` without padding: 5/5 passed
- `host-pinned` without padding: 5/5 passed
- No corruption was observed in that limited test

This does not justify removing the padding from production code. Keep the documented stride rule because it is an ACL contract for subdividing memory returned by `aclrtMallocHost`.

## Reporting Rules

When reporting results to the user:

- Include the exact command or GTest filter used.
- Include the exact pass count, for example `42/42 passed` or `309/309 passed`.
- Say how code was synced into the container: Git, bundle, or direct file overlay.
- For RDMA tests, report both rank exit codes.
- For RDMA tests, distinguish registration success from connection/data-transfer success.
- For failures, quote the first decisive error line, not the entire log.

## Key Paths

| Item | Container Path |
| --- | --- |
| Main source root | `/home/ymy/unified-cache-management` |
| Main ASU build dir | `/home/ymy/unified-cache-management/build-asu-host-pinned` |
| ASU test binary | `/home/ymy/unified-cache-management/build-asu-host-pinned/ucm/transport/kv/asu/asu.test` |
| ASU transport sources | `ucm/transport/kv/asu/trans/src/` |
| ASU transport tests | `ucm/transport/kv/asu/trans/test/` |
| ASU legacy case tests | `ucm/transport/kv/asu/test/case/` |
| Ascend CANN env | `/usr/local/Ascend/cann/set_env.sh` |
| Provider test root | `/tmp/ucm-provider-mem-test` |
| Provider build dir | `/tmp/ucm-provider-mem-test/build-provider-custom` |
| Provider binary | `/tmp/ucm-provider-mem-test/build-provider-custom/ucm/transport/kv/asu/aicpu_send_with_provider` |
| HIXL kernel JSON | `/home/lx/test/hixl/build/device_build/src/ops/hixl_kernel/libcann_hixl_kernel.json` |
| HCOMM lib dir | `/home/lx/test/hcomm/build/_CPack_Packages/Linux/External/hcomm-9.1.0-Linux/aarch64-linux/lib64` |
| NPU IP map | `/tmp/npu_ip_map.txt` |
