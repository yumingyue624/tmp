from __future__ import annotations

import copy
import json
from pathlib import Path

from generate_design_diagrams import COLORS, add, arrow, base, ellipse, line, rect, text, to_svg


ROOT = Path(__file__).resolve().parent
LANE = {
    "store": 120,
    "receiver": 360,
    "protocol": 610,
    "worker": 850,
    "buffer": 1090,
    "metadata": 1340,
    "transport": 1590,
    "poller": 1830,
}


def load_scene(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def set_text(element: dict, value: str, font_size: int | None = None) -> None:
    element["text"] = value
    element["originalText"] = value
    if font_size is not None:
        element["fontSize"] = font_size


def replace_first(elements: list[dict], needle: str, value: str, font_size: int | None = None) -> None:
    for element in elements:
        if element.get("type") == "text" and needle in element.get("text", ""):
            set_text(element, value, font_size)
            return
    raise RuntimeError(f"text not found: {needle}")


def prepare_top(source: str, title: str, opcode: str, bottom: int, buffer_label: str) -> list[dict]:
    scene = load_scene(source)
    elements: list[dict] = []
    for original in scene["elements"]:
        element = copy.deepcopy(original)
        kind = element.get("type")
        y = element.get("y", 0)
        # The request-receive half of the old figure is retained.  Everything
        # below the sync/async divider is rebuilt from the current code.
        if kind in {"text", "arrow"} and y >= 500:
            continue
        if kind == "rectangle" and y >= 480:
            continue
        if (source.startswith("12_") and kind == "rectangle" and 280 <= y <= 390
                and element.get("x", 0) > 900 and element.get("width", 0) > 300):
            continue
        if kind == "line" and y >= 510:
            continue

        # Extend the existing lifelines and the DramPoolServer boundary.
        if kind == "line" and y < 260:
            points = element.get("points", [])
            if len(points) >= 2 and abs(points[-1][0] - points[0][0]) < 2:
                height = bottom - 100 - y
                element["height"] = height
                element["points"][-1][1] = height
        if kind == "rectangle" and y < 180 and element.get("width", 0) > 1200:
            element["height"] = bottom - 165
        if kind == "text" and "prefix_hit_count" in element.get("text", ""):
            continue
        elements.append(element)

    title_element = next(
        element for element in elements
        if element.get("type") == "text" and element.get("y", 999) < 100
        and "DramPool" in element.get("text", "")
    )
    set_text(title_element, title, 25)
    replace_first(elements, "Receiver\n", "RequestReceiver\nRequestReceiveLoop()", 14)
    replace_first(elements, "KvProtocol", "ProtocolManager", 16)
    worker_needle = "TaskWorker" if any(
        element.get("type") == "text" and "TaskWorker" in element.get("text", "")
        for element in elements
    ) else "ShardExecutor"
    replace_first(elements, worker_needle, "TaskWorker", 17)
    replace_first(elements, "BufferMgr", buffer_label, 14)
    replace_first(elements, "MetadataIndex", "MetadataManager\nShardMetadata + Entry", 13)
    replace_first(elements, "TransportMgr", "TransportManager", 15)
    replace_first(elements, "CompletionPoller", "CompletionPoller\n轮询线程", 14)
    for element in elements:
        if (element.get("type") in {"rectangle", "text"} and element.get("x", 0) > 1700
                and element.get("y", 999) < 230):
            element["strokeColor"] = COLORS["blue"][0]
            if element.get("type") == "rectangle":
                element["backgroundColor"] = COLORS["blue"][1]
    replace_first(elements, "同步阶段", "同步阶段：接收请求并构造 RequestTask", 17)
    replace_first(elements, "调用 KvProtocol", "2. ProtocolManager::UnpackRequest()", 14)
    replace_first(elements, "返回 request", "3. 返回 RequestPtr", 14)
    queue_element = next(
        element for element in elements
        if element.get("type") == "text" and element.get("x", 0) > 700
        and element.get("y", 0) > 360
        and ("requestQueue" in element.get("text", "") or "request_queue" in element.get("text", ""))
    )
    set_text(queue_element, "requestQueue_\nRequestQueue\nRequestReceiver → TaskWorker", 11)
    step4_element = next(
        element for element in elements
        if element.get("type") == "text" and element.get("text", "").lstrip().startswith("4.")
    )
    set_text(step4_element, "4. TryPush(requestQueue_)", 13)
    replace_first(elements, "同步/异步边界", "同步/异步边界：RequestTask 已进入 requestQueue_", 13)

    # The request label differs only by opcode; keep the old arrow and placement.
    request_element = next(
        element for element in elements
        if element.get("type") == "text" and element.get("text", "").lstrip().startswith("1.")
    )
    set_text(request_element, f"1. 发送 {opcode} Request", 14)
    return elements


def dashed_frame(elements: list[dict], x: int, y: int, w: int, h: int, label: str, color: str) -> None:
    add(elements, rect(x, y, w, h, "", color, dashed=True))
    add(elements, text(x + 18, y + 8, w - 36, 32, label, 17, COLORS[color][0], "left"))


def sequence_arrow(elements: list[dict], source: str, target: str, y: int, label: str,
                   color: str = "gray", dashed: bool = False, font: int = 13) -> None:
    x1, x2 = LANE[source], LANE[target]
    stroke = COLORS[color][0]
    add(elements, arrow(x1, y + 28, x2, y + 28, color=stroke, dashed=dashed))
    left = min(x1, x2) + 10
    width = max(190, abs(x2 - x1) - 20)
    add(elements, text(left, y, width, 28, label, font, stroke))


def stage_header(elements: list[dict], y: int, number: int, title: str, color: str,
                 left: int = 510, right: int = 1840) -> None:
    stroke, _ = COLORS[color]
    add(elements, line(left, y, right, y, stroke))
    add(elements, ellipse(left + 18, y + 14, 28, 28, color, fill_override=stroke, stroke_width=2))
    add(elements, text(left + 18, y + 14, 28, 28, str(number), 14, "#ffffff"))
    add(elements, text(left + 58, y + 8, 560, 40, title, 20, stroke, "left"))


def completion_queue(elements: list[dict], y: int, initial_stage: str, color: str) -> None:
    add(elements, rect(720, y, 300, 96, "", color, dashed=True))
    add(elements, text(738, y + 6, 264, 32, "completionQueue_", 15, COLORS[color][0]))
    add(elements, rect(760, y + 43, 220, 38, initial_stage, color, font=12))
    add(elements, arrow(LANE["worker"], y - 28, LANE["worker"], y, "Push", COLORS[color][0]))


def response_phase(elements: list[dict], y: int, number: int, color: str = "orange") -> int:
    dashed_frame(elements, 690, y, 1180, 520, "CompletionPoller：响应提交与回收", color)
    add(elements, text(735, y + 58, 290, 34,
                       f"{number}. FillPendingWindow()\ncompletionQueue_ → pending_", 14,
                       COLORS[color][0], "left"))
    add(elements, rect(1040, y + 52, 290, 74, "pending_\nCompletionRecord", color, font=13))
    add(elements, arrow(1020, y + 89, 1040, y + 89, color=COLORS[color][0]))

    add(elements, rect(760, y + 165, 315, 110,
                       "SubmitResponse\nflagBufferPool_.Allocate()\nProtocolManager::PackResponse()",
                       "purple", font=13))
    add(elements, rect(1160, y + 165, 315, 110,
                       "TransportManager::ExecuteAsync()\nresponse Write → resp_addr",
                       "teal", font=13))
    add(elements, rect(1560, y + 165, 260, 110,
                       "stage =\nPollResponseTransfer",
                       "orange", font=14))
    add(elements, arrow(1075, y + 220, 1160, y + 220, color=COLORS["purple"][0]))
    add(elements, arrow(1475, y + 220, 1560, y + 220, color=COLORS["teal"][0]))

    add(elements, rect(870, y + 330, 810, 98,
                       "GetStatus(response_handle)\nWaiting：保留 local_resp_slot；终态或查询失败：ReleaseResponseBuffer()，从 pending_ 移除",
                       "gray", font=13))
    add(elements, arrow(1690, y + 275, 1690, y + 330, color=COLORS["orange"][0]))
    add(elements, rect(735, y + 445, 1080, 48,
                       "flagBufferPool_ NoSpace 时不改变 stage，记录留在 pending_，下一轮继续尝试",
                       "yellow", font=12))
    return y + 520


def dump_diagram_v7() -> None:
    bottom = 2460
    elements = prepare_top(
        "10_drampool_dump.excalidraw",
        "DramPool DUMP 请求处理时序",
        "DUMP",
        bottom,
        "BufferManager",
    )
    # TaskWorker owns steps 5-11, so the entire region uses one module color.
    dashed_frame(elements, 700, 520, 1080, 565, "TaskWorker：逐项建元数据并提交数据 transfer 任务", "blue")
    add(elements, text(735, 565, 520, 32, "5. TryPop(requestQueue_)，进入 ProcessDump()", 14,
                       COLORS["blue"][0], "left"))
    dashed_frame(elements, 770, 625, 860, 300, "for (entry : request.entries)", "blue")
    sequence_arrow(elements, "worker", "metadata", 680,
                   "6. 构造 Entry，MetadataManager::StoreBegin(key, entry)", "blue")
    sequence_arrow(elements, "metadata", "buffer", 735,
                   "7. BufferManager::Allocate(len)", "blue")
    sequence_arrow(elements, "buffer", "metadata", 790,
                   "8. 返回 Host Buffer；插入 INITIALIZED Entry", "blue", dashed=True)
    sequence_arrow(elements, "metadata", "worker", 845,
                   "9. Success / DuplicateKey / Failed", "blue", dashed=True)
    add(elements, rect(1645, 650, 360, 210,
                       "逐项结果\nSuccess：加入 TransferItem 和 Segment\nDuplicateKey：result = Ok，不传输\nFailed：当前及后续项标为 Failed",
                       "blue", font=12))
    sequence_arrow(elements, "worker", "transport", 955,
                   "10. ExecuteAsync(Read)：远端 Device → 本地 Host", "blue")
    sequence_arrow(elements, "transport", "worker", 1015,
                   "11. 返回 data_handle", "blue", dashed=True)
    add(elements, text(740, 1040, 980, 30,
                       "无 TransferItem 或提交失败时直接进入 SubmitResponse；提交失败会先删除本次新建的元数据",
                       12, COLORS["blue"][0], "left"))

    completion_queue(elements, 1115, "CompletionRecord · PollDataTransfer", "blue")

    # CompletionPoller is one execution domain. Queue intake is neutral; each
    # CompletionRecord stage gets one stable color and a prominent ordinal.
    poller_y = 1240
    dashed_frame(elements, 480, poller_y, 1390, 1120, "CompletionPoller", "gray")
    add(elements, rect(1490, 1280, 340, 78,
                       "pending_\ndeque<CompletionRecord>",
                       "gray", font=14))
    sequence_arrow(elements, "poller", "worker", 1375,
                   "12. FillPendingWindow()：completionQueue_.TryPop()", "gray")
    sequence_arrow(elements, "worker", "poller", 1430,
                   "record → pending_.emplace_back()", "gray", dashed=True)

    stage_header(elements, 1495, 1, "PollDataTransfer", "blue")
    sequence_arrow(elements, "poller", "transport", 1580,
                   "13. GetStatus(data_handle)", "blue")
    sequence_arrow(elements, "transport", "poller", 1635,
                   "Waiting / Completed / Failed", "blue", dashed=True)
    add(elements, text(525, 1670, 760, 42,
                       "Waiting：记录保留在 pending_；超时只记录诊断日志，下一轮继续轮询",
                       12, COLORS["blue"][0], "left"))
    sequence_arrow(elements, "poller", "metadata", 1720,
                   "14. SettleDataTransfer()：StoreEnd() / Delete()", "blue")
    add(elements, text(1170, 1760, 620, 34,
                       "完成元数据收尾，stage = SubmitResponse", 14,
                       COLORS["purple"][0], "right"))

    stage_header(elements, 1810, 2, "SubmitResponse", "purple")
    sequence_arrow(elements, "poller", "protocol", 1895,
                   "15. flagBufferPool_.Allocate()；ProtocolManager::PackResponse()", "purple")
    sequence_arrow(elements, "protocol", "poller", 1950,
                   "打包结果 / flagBufferPool_ NoSpace", "purple", dashed=True)
    add(elements, text(1060, 1982, 730, 34,
                       "NoSpace：stage 保持 SubmitResponse，记录留在 pending_，下一轮重试",
                       12, COLORS["purple"][0], "right"))
    sequence_arrow(elements, "poller", "transport", 2030,
                   "16. ExecuteAsync(response Write → resp_addr)", "purple")
    sequence_arrow(elements, "transport", "poller", 2085,
                   "返回 response_handle", "purple", dashed=True)
    add(elements, text(1150, 2120, 640, 32,
                       "stage = PollResponseTransfer", 14,
                       COLORS["orange"][0], "right"))

    stage_header(elements, 2160, 3, "PollResponseTransfer", "orange")
    sequence_arrow(elements, "poller", "transport", 2245,
                   "17. GetStatus(response_handle)", "orange")
    sequence_arrow(elements, "transport", "poller", 2300,
                   "Waiting：保留 Slot / 终态或查询失败：释放 Slot 并移除记录",
                   "orange", dashed=True, font=12)
    save_scene("10_drampool_dump_v7", 2100, bottom, elements)


def load_diagram_v4() -> None:
    bottom = 2420
    elements = prepare_top(
        "11_drampool_load.excalidraw",
        "DramPool LOAD 请求处理时序",
        "LOAD",
        bottom,
        "BufferManager\nLOAD 不分配 Slot",
    )
    dashed_frame(elements, 700, 520, 1080, 530, "TaskWorker：逐项固定 Entry 并提交数据 transfer 任务", "blue")
    add(elements, text(735, 565, 520, 32, "5. TryPop(requestQueue_)，进入 ProcessLoad()", 14,
                       COLORS["blue"][0], "left"))
    dashed_frame(elements, 770, 625, 860, 270, "for (entry : request.entries)", "blue")
    sequence_arrow(elements, "worker", "metadata", 680,
                   "6. MetadataManager::LoadBegin(key)", "blue")
    sequence_arrow(elements, "metadata", "worker", 740,
                   "7. READY Entry：refCnt++，返回 Host Buffer", "blue", dashed=True)
    add(elements, rect(1645, 650, 360, 200,
                       "逐项结果\n不存在或非 READY：Failed\n请求 len > Entry::size：LoadEnd() 后 Failed\n其余项：加入 TransferItem 和 Segment",
                       "blue", font=12))
    sequence_arrow(elements, "worker", "transport", 925,
                   "8. ExecuteAsync(Write)：本地 Host → 远端 Device", "blue")
    sequence_arrow(elements, "transport", "worker", 985,
                   "9. 返回 data_handle", "blue", dashed=True)
    add(elements, text(740, 1015, 980, 28,
                       "无 TransferItem 时直接响应；提交失败时先对已固定的 Entry 执行 LoadEnd()",
                       12, COLORS["blue"][0], "left"))

    completion_queue(elements, 1080, "CompletionRecord · PollDataTransfer", "blue")

    poller_y = 1210
    dashed_frame(elements, 480, poller_y, 1390, 1120, "CompletionPoller", "gray")
    add(elements, rect(1490, 1250, 340, 78,
                       "pending_\ndeque<CompletionRecord>",
                       "gray", font=14))
    sequence_arrow(elements, "poller", "worker", 1345,
                   "10. FillPendingWindow()：completionQueue_.TryPop()", "gray")
    sequence_arrow(elements, "worker", "poller", 1400,
                   "record → pending_.emplace_back()", "gray", dashed=True)

    stage_header(elements, 1460, 1, "PollDataTransfer", "blue")
    sequence_arrow(elements, "poller", "transport", 1545,
                   "11. GetStatus(data_handle)", "blue")
    sequence_arrow(elements, "transport", "poller", 1600,
                   "Waiting / Completed / Failed", "blue", dashed=True)
    add(elements, text(525, 1635, 760, 42,
                       "Waiting：记录保留在 pending_；超时只记录诊断日志，下一轮继续轮询",
                       12, COLORS["blue"][0], "left"))
    sequence_arrow(elements, "poller", "metadata", 1690,
                   "12. SettleDataTransfer()：每个 TransferItem 执行 LoadEnd(key)", "blue")
    add(elements, text(1030, 1730, 760, 34,
                       "仅 Completed 且 LoadEnd() 成功时结果为 Ok；stage = SubmitResponse",
                       13, COLORS["purple"][0], "right"))

    stage_header(elements, 1780, 2, "SubmitResponse", "purple")
    sequence_arrow(elements, "poller", "protocol", 1865,
                   "13. flagBufferPool_.Allocate()；ProtocolManager::PackResponse()", "purple")
    sequence_arrow(elements, "protocol", "poller", 1920,
                   "打包结果 / flagBufferPool_ NoSpace", "purple", dashed=True)
    add(elements, text(1060, 1952, 730, 34,
                       "NoSpace：stage 保持 SubmitResponse，记录留在 pending_，下一轮重试",
                       12, COLORS["purple"][0], "right"))
    sequence_arrow(elements, "poller", "transport", 2000,
                   "14. ExecuteAsync(response Write → resp_addr)", "purple")
    sequence_arrow(elements, "transport", "poller", 2055,
                   "返回 response_handle", "purple", dashed=True)
    add(elements, text(1150, 2090, 640, 32,
                       "stage = PollResponseTransfer", 14,
                       COLORS["orange"][0], "right"))

    stage_header(elements, 2130, 3, "PollResponseTransfer", "orange")
    sequence_arrow(elements, "poller", "transport", 2215,
                   "15. GetStatus(response_handle)", "orange")
    sequence_arrow(elements, "transport", "poller", 2270,
                   "Waiting：保留 Slot / 终态或查询失败：释放 Slot 并移除记录",
                   "orange", dashed=True, font=12)
    save_scene("11_drampool_load_v4", 2100, bottom, elements)


def lookup_diagram_v3() -> None:
    bottom = 2080
    elements = prepare_top(
        "12_drampool_lookup.excalidraw",
        "DramPool LOOKUP 请求处理时序",
        "LOOKUP",
        bottom,
        "BufferManager\n数据面不参与",
    )
    dashed_frame(elements, 700, 520, 1080, 410, "TaskWorker：逐项查询并生成结果", "blue")
    add(elements, text(735, 565, 520, 32, "5. TryPop(requestQueue_)，进入 ProcessLookup()", 14,
                       COLORS["blue"][0], "left"))
    dashed_frame(elements, 770, 625, 860, 210, "for (entry : request.entries)", "blue")
    sequence_arrow(elements, "worker", "metadata", 680,
                   "6. MetadataManager::Exist(key)", "blue")
    sequence_arrow(elements, "metadata", "worker", 745,
                   "7. Exists / NotFound", "blue", dashed=True)
    add(elements, rect(1645, 630, 360, 215,
                       "Exist() 语义\n仅 READY Entry 命中\nTryMarkHit() 在 Entry 锁内刷新 leaseTimeout\n每个 key 独立判断，不因中间 miss 提前结束",
                       "blue", font=12))
    add(elements, text(760, 860, 980, 46,
                       "8. QueueResponse()：results 写入 CompletionRecord，初始 stage = SubmitResponse",
                       13, COLORS["blue"][0], "left"))

    completion_queue(elements, 960, "CompletionRecord · SubmitResponse", "purple")

    poller_y = 1090
    dashed_frame(elements, 480, poller_y, 1390, 900, "CompletionPoller", "gray")
    add(elements, rect(1490, 1130, 340, 78,
                       "pending_\ndeque<CompletionRecord>",
                       "gray", font=14))
    sequence_arrow(elements, "poller", "worker", 1225,
                   "9. FillPendingWindow()：completionQueue_.TryPop()", "gray")
    sequence_arrow(elements, "worker", "poller", 1280,
                   "record → pending_.emplace_back()", "gray", dashed=True)
    add(elements, text(525, 1315, 1260, 34,
                       "Lookup 无数据 transfer 任务：初始 stage = SubmitResponse，跳过 ① PollDataTransfer",
                       13, COLORS["gray"][0], "left"))

    stage_header(elements, 1360, 2, "SubmitResponse", "purple")
    sequence_arrow(elements, "poller", "protocol", 1445,
                   "10. flagBufferPool_.Allocate()；ProtocolManager::PackResponse()", "purple")
    sequence_arrow(elements, "protocol", "poller", 1500,
                   "打包结果 / flagBufferPool_ NoSpace", "purple", dashed=True)
    add(elements, text(1060, 1532, 730, 34,
                       "NoSpace：stage 保持 SubmitResponse，记录留在 pending_，下一轮重试",
                       12, COLORS["purple"][0], "right"))
    sequence_arrow(elements, "poller", "transport", 1580,
                   "11. ExecuteAsync(response Write → resp_addr)", "purple")
    sequence_arrow(elements, "transport", "poller", 1635,
                   "返回 response_handle", "purple", dashed=True)
    add(elements, text(1150, 1670, 640, 32,
                       "stage = PollResponseTransfer", 14,
                       COLORS["orange"][0], "right"))

    stage_header(elements, 1710, 3, "PollResponseTransfer", "orange")
    sequence_arrow(elements, "poller", "transport", 1795,
                   "12. GetStatus(response_handle)", "orange")
    sequence_arrow(elements, "transport", "poller", 1850,
                   "Waiting：保留 Slot / 终态或查询失败：释放 Slot 并移除记录",
                   "orange", dashed=True, font=12)
    save_scene("12_drampool_lookup_v3", 2100, bottom, elements)


def save_scene(name: str, width: int, height: int, elements: list[dict]) -> None:
    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }
    (ROOT / f"{name}.excalidraw").write_text(
        json.dumps(scene, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    (ROOT / f"{name}.svg").write_text(to_svg(width, height, elements), encoding="utf-8")


if __name__ == "__main__":
    dump_diagram_v7()
    load_diagram_v4()
    lookup_diagram_v3()
