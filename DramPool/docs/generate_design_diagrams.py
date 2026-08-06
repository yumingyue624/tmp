from __future__ import annotations

import html
import json
import math
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent

COLORS = {
    "blue": ("#1971c2", "#e7f5ff"),
    "green": ("#2b8a3e", "#ebfbee"),
    "orange": ("#e67700", "#fff4e6"),
    "purple": ("#7048e8", "#f3f0ff"),
    "red": ("#c92a2a", "#fff5f5"),
    "gray": ("#495057", "#f1f3f5"),
    "teal": ("#087f5b", "#e6fcf5"),
    "yellow": ("#f08c00", "#fff9db"),
}


def element_id() -> str:
    return uuid.uuid4().hex[:16]


def base(kind: str, x: float, y: float, w: float, h: float, stroke: str, fill: str):
    return {
        "id": element_id(),
        "type": kind,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "angle": 0,
        "strokeColor": stroke,
        "backgroundColor": fill,
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 0,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": {"type": 3},
        "seed": int(uuid.uuid4().int % 2_000_000_000),
        "version": 1,
        "versionNonce": int(uuid.uuid4().int % 2_000_000_000),
        "isDeleted": False,
        "boundElements": [],
        "updated": 1,
        "link": None,
        "locked": False,
    }


def rect(x, y, w, h, label, color="blue", font=18, dashed=False, align="center"):
    stroke, fill = COLORS[color]
    shape = base("rectangle", x, y, w, h, stroke, fill)
    if dashed:
        shape["strokeStyle"] = "dashed"
        shape["backgroundColor"] = "transparent"
    txt = text(x + 10, y + 12, w - 20, h - 24, label, font, stroke, align)
    return [shape, txt]


def diamond(x, y, w, h, label, color="yellow", font=16):
    stroke, fill = COLORS[color]
    shape = base("diamond", x, y, w, h, stroke, fill)
    txt = text(x + 24, y + h / 2 - 22, w - 48, 44, label, font, stroke, "center")
    return [shape, txt]


def ellipse(x, y, w, h, color="gray", fill_override=None, stroke_width=2):
    stroke, fill = COLORS[color]
    shape = base("ellipse", x, y, w, h, stroke, fill_override if fill_override is not None else fill)
    shape["strokeWidth"] = stroke_width
    shape["roundness"] = None
    return shape


def text(x, y, w, h, label, font=18, color="#212529", align="center"):
    obj = base("text", x, y, w, h, color, "transparent")
    obj.update(
        {
            "text": label,
            "originalText": label,
            "fontSize": font,
            "fontFamily": 3,
            "textAlign": align,
            "verticalAlign": "middle",
            "baseline": font,
            "containerId": None,
            "autoResize": False,
            "lineHeight": 1.25,
        }
    )
    return obj


def arrow(x1, y1, x2, y2, label="", color="#495057", dashed=False):
    obj = base("arrow", x1, y1, abs(x2 - x1), abs(y2 - y1), color, "transparent")
    obj.update(
        {
            "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "elbowed": False,
        }
    )
    if dashed:
        obj["strokeStyle"] = "dashed"
    out = [obj]
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        out.append(text(mx - 90, my - 34, 180, 28, label, 14, color))
    return out


def polyarrow(points, label="", label_pos=None, color="#495057", dashed=False):
    x0, y0 = points[0]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    obj = base("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys), color, "transparent")
    obj.update(
        {
            "points": [[x - x0, y - y0] for x, y in points],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "elbowed": False,
        }
    )
    if dashed:
        obj["strokeStyle"] = "dashed"
    out = [obj]
    if label:
        lx, ly = label_pos if label_pos is not None else points[len(points) // 2]
        out.append(text(lx - 100, ly - 34, 200, 28, label, 14, color))
    return out


def line(x1, y1, x2, y2, color="#868e96", dashed=False):
    obj = base("line", x1, y1, abs(x2 - x1), abs(y2 - y1), color, "transparent")
    obj.update({"points": [[0, 0], [x2 - x1, y2 - y1]], "lastCommittedPoint": None})
    if dashed:
        obj["strokeStyle"] = "dashed"
    return obj


def add(target, items):
    if isinstance(items, list):
        target.extend(items)
    else:
        target.append(items)


def spsc_queue(x, y, w, h, title, item_prefix, item_description, color="gray"):
    out = []
    add(out, rect(x, y, w, h, "", color, dashed=True))
    add(out, text(x + 15, y + 8, w - 30, 54, title, 15, COLORS[color][0]))
    slot_y = y + 72
    slot_w = 58
    gap = 10
    start_x = x + (w - (4 * slot_w + 3 * gap)) / 2
    for index in range(4):
        sx = start_x + index * (slot_w + gap)
        add(out, rect(sx, slot_y, slot_w, 70, "", "gray", font=12))
        if index < 3:
            add(out, rect(sx + 7, slot_y + 10, slot_w - 14, 48,
                          f"{item_prefix}{index}", color, font=13))
        else:
            add(out, text(sx + 5, slot_y + 15, slot_w - 10, 38, "empty", 10, "#868e96"))
    add(out, text(x + 15, y + h - 48, w - 30, 38,
                  f"{item_prefix}*: {item_description}\nSPSC · 1 producer / 1 consumer",
                  11, "#495057"))
    return out


def save(name: str, width: int, height: int, elements: list[dict]):
    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }
    excalidraw = ROOT / f"{name}.excalidraw"
    excalidraw.write_text(json.dumps(scene, ensure_ascii=True, indent=2), encoding="utf-8")
    (ROOT / f"{name}.svg").write_text(to_svg(width, height, elements), encoding="utf-8")


def svg_text(e):
    lines = e["text"].split("\n")
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(e.get("textAlign"), "middle")
    if anchor == "start":
        x = e["x"]
    elif anchor == "end":
        x = e["x"] + e["width"]
    else:
        x = e["x"] + e["width"] / 2
    line_h = e.get("fontSize", 18) * 1.25
    total = line_h * len(lines)
    y = e["y"] + max(line_h, (e["height"] - total) / 2 + line_h * 0.8)
    chunks = []
    for i, value in enumerate(lines):
        chunks.append(
            f'<tspan x="{x:.1f}" y="{y + i * line_h:.1f}">{html.escape(value)}</tspan>'
        )
    return (
        f'<text font-family="Consolas, Microsoft YaHei, sans-serif" font-size="{e.get("fontSize",18)}" '
        f'fill="{e["strokeColor"]}" text-anchor="{anchor}">' + "".join(chunks) + "</text>"
    )


def to_svg(width: int, height: int, elements: list[dict]) -> str:
    body = []
    for e in elements:
        kind = e["type"]
        stroke = e["strokeColor"]
        fill = e["backgroundColor"] if e["backgroundColor"] != "transparent" else "none"
        dash = ' stroke-dasharray="9 7"' if e.get("strokeStyle") == "dashed" else ""
        if kind == "rectangle":
            body.append(
                f'<rect x="{e["x"]}" y="{e["y"]}" width="{e["width"]}" height="{e["height"]}" '
                f'rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>'
            )
        elif kind == "ellipse":
            body.append(
                f'<ellipse cx="{e["x"]+e["width"]/2}" cy="{e["y"]+e["height"]/2}" '
                f'rx="{e["width"]/2}" ry="{e["height"]/2}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{e.get("strokeWidth",2)}"{dash}/>'
            )
        elif kind == "diamond":
            x, y, w, h = e["x"], e["y"], e["width"], e["height"]
            pts = f'{x+w/2},{y} {x+w},{y+h/2} {x+w/2},{y+h} {x},{y+h/2}'
            body.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/>' )
        elif kind in {"arrow", "line"}:
            p0, p1 = e["points"][0], e["points"][-1]
            x1, y1 = e["x"] + p0[0], e["y"] + p0[1]
            x2, y2 = e["x"] + p1[0], e["y"] + p1[1]
            marker = ' marker-end="url(#arrow)"' if kind == "arrow" and e.get("endArrowhead") else ""
            if len(e["points"]) > 2:
                pts = " ".join(f'{e["x"]+p[0]},{e["y"]+p[1]}' for p in e["points"])
                body.append(
                    f'<polyline points="{pts}" fill="none" stroke="{stroke}" '
                    f'stroke-width="2"{dash}{marker}/>'
                )
            else:
                body.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
                    f'stroke-width="2"{dash}{marker}/>'
                )
        elif kind == "text":
            body.append(svg_text(e))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
        'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#495057"/></marker></defs>'
        '<rect width="100%" height="100%" fill="#ffffff"/>' + "".join(body) + "</svg>"
    )


def drampool_constructor_v2():
    e = []
    add(e, text(40, 18, 1520, 45, "DramStore 与 DramPool 节点部署架构", 26))
    add(e, rect(30, 75, 1540, 990, "", "gray", dashed=True))
    add(e, text(50, 88, 300, 32, "UCM KVCache 集群", 20, COLORS["gray"][0], "left"))

    add(e, rect(70, 135, 1460, 600, "", "blue", dashed=True))
    add(e, text(95, 150, 300, 32, "计算节点 0", 21, COLORS["blue"][0], "left"))

    add(e, rect(110, 205, 600, 390, "", "gray", dashed=True))
    add(e, text(135, 218, 300, 32, "rank0 - 推理进程0", 20, COLORS["gray"][0], "left"))
    add(e, rect(155, 275, 510, 60, "UCMConnector", "purple", font=19))
    add(e, rect(155, 370, 510, 185, "", "green", dashed=True))
    add(e, text(180, 382, 220, 30, "DramStore", 20, COLORS["green"][0], "left"))
    add(e, rect(185, 440, 135, 70, "NodeScheduler", "green", font=15))
    add(e, rect(345, 440, 135, 70, "ReplyService", "green", font=15))
    add(e, rect(505, 440, 135, 70, "TransportExecutor", "green", font=14))

    add(e, rect(110, 625, 600, 70, "rank1 ... rank7 - 推理进程", "gray", font=19))

    add(e, rect(850, 205, 640, 490, "", "orange", dashed=True))
    add(e, text(875, 218, 300, 32, "DramPool 进程", 21, COLORS["orange"][0], "left"))
    add(e, rect(950, 270, 440, 65, "DramPoolServer", "blue", font=20))
    add(e, rect(895, 380, 170, 75, "RequestReceiveLoop", "purple", font=15))
    add(e, rect(1090, 380, 160, 75, "TaskWorker", "blue", font=17))
    add(e, rect(1275, 380, 170, 75, "CompletionPoller", "orange", font=15))
    add(e, rect(965, 530, 210, 80, "MetadataManager\n└ BufferManager", "green", font=16))
    add(e, rect(1225, 530, 210, 80, "TransportManager", "teal", font=17))
    add(e, arrow(1065, 417, 1090, 417))
    add(e, arrow(1250, 417, 1275, 417))
    add(e, arrow(1170, 455, 1070, 530, "元数据"))
    add(e, arrow(1210, 455, 1300, 530, "异步传输"))

    add(e, arrow(665, 475, 895, 417, "本节点DramPool"))
    add(e, arrow(710, 660, 850, 585, "共享" , dashed=True))

    add(e, rect(70, 790, 1460, 220, "", "blue", dashed=True))
    add(e, text(95, 805, 300, 32, "计算节点 1 ... N", 21, COLORS["blue"][0], "left"))
    add(e, rect(120, 875, 540, 80, "推理进程  × N\nUCMConnector + DramStore", "gray", font=18))
    add(e, rect(1080, 865, 370, 100, "DramPool 进程\nDramPoolServer", "orange", font=19))
    add(e, arrow(660, 915, 1080, 915, "本节点路由"))
    add(e, polyarrow([(665, 510), (775, 510), (775, 845), (1080, 885)],
                     "跨节点路由", (790, 745), dashed=True))
    save("drampool_constructor_v2", 1600, 1110, e)


def drampool_constructor_v3():
    e = []
    add(e, text(40, 18, 1520, 45, "DramStore 与 DramPool 节点部署架构", 26))

    # Keep the original two-node, left-inference/right-DramPool composition.
    add(e, rect(45, 80, 1510, 680, "", "gray", dashed=True))
    add(e, text(70, 94, 260, 34, "计算节点 0", 21, COLORS["gray"][0], "left"))

    add(e, rect(100, 155, 650, 500, "", "blue", dashed=True))
    add(e, text(125, 168, 300, 34, "rank0 - 推理进程0", 20, COLORS["blue"][0], "left"))
    add(e, rect(160, 225, 530, 58, "UCMConnector", "blue", font=18))
    add(e, rect(160, 310, 530, 58, "Store dispatcher", "blue", font=18))
    add(e, rect(160, 395, 530, 220, "", "green", dashed=True))
    add(e, text(185, 407, 260, 30, "DramStore（.so库）", 20, COLORS["green"][0], "left"))
    add(e, rect(195, 465, 210, 52, "Buffer Delegator", "green", font=15))
    add(e, rect(445, 465, 210, 52, "Router打散", "green", font=16))
    add(e, rect(195, 540, 210, 52, "KvProtocol + BufferMgr", "green", font=14))
    add(e, rect(445, 540, 210, 52, "TransportMgr", "green", font=16))

    add(e, rect(100, 680, 650, 55, "rank1 - 推理进程1          ···          rank7 - 推理进程7", "gray", font=17))

    add(e, rect(850, 155, 540, 580, "", "orange", dashed=True))
    add(e, text(875, 168, 300, 34, "DramPool进程", 20, COLORS["orange"][0], "left"))
    add(e, rect(915, 220, 410, 58, "DramPoolServer", "orange", font=19))
    add(e, rect(915, 310, 410, 58, "KvProtocol", "orange", font=18))
    add(e, rect(915, 400, 410, 58, "MetadataManager", "orange", font=18))
    add(e, rect(915, 490, 410, 58, "Buffer Manager", "orange", font=18))
    add(e, rect(915, 580, 410, 58, "TransportMgr", "orange", font=18))

    add(e, rect(45, 820, 1510, 220, "", "gray", dashed=True))
    add(e, text(70, 834, 300, 34, "计算节点 1 ... N", 21, COLORS["gray"][0], "left"))
    add(e, rect(100, 900, 650, 80, "推理进程\nUCMConnector + DramStore", "blue", font=18))
    add(e, rect(850, 900, 540, 80, "DramPool进程\nDramPoolServer", "orange", font=18))

    save("drampool_constructor_v3", 1600, 1080, e)


def internal_architecture():
    e = []
    add(e, text(40, 18, 1920, 45, "DramPool 内部架构", 28))
    add(e, rect(30, 80, 1940, 970, "", "gray", dashed=True))
    add(e, text(55, 92, 320, 32, "DramPool 进程", 20, COLORS["gray"][0], "left"))

    # Lifecycle layer.
    add(e, text(85, 130, 260, 32, "Lifecycle", 18, "#868e96", "left"))
    add(e, rect(690, 120, 320, 70, "DramPoolDaemon", "blue", font=22))
    add(e, rect(1580, 120, 260, 70, "HealthServer", "purple", font=20))
    add(e, arrow(1010, 155, 1580, 155, "Start / Stop", dashed=True))

    # DramPoolServer boundary and execution pipeline.
    add(e, rect(70, 240, 1860, 740, "", "gray", dashed=True))
    add(e, text(95, 255, 360, 34, "DramPoolServer", 22, COLORS["gray"][0], "left"))
    add(e, arrow(850, 190, 850, 240, "Init / Start / Stop", dashed=True))
    add(e, text(105, 305, 300, 32, "Async execution pipeline", 18, "#868e96", "left"))

    add(e, rect(110, 380, 210, 105, "TcpMessageChannel", "gray", font=17))
    add(e, rect(370, 365, 245, 135,
                "RequestReceiveLoop\nrequestReceiverThread_", "purple", font=17))
    add(e, spsc_queue(680, 320, 300, 225,
                      "requestQueue_\nSpscRingQueue<RequestTaskPtr>",
                      "R", "RequestTaskPtr", "purple"))
    add(e, rect(1045, 365, 225, 135, "TaskWorker::Run\ntaskWorkerThread_", "blue", font=17))
    add(e, spsc_queue(1335, 320, 300, 225,
                      "completionQueue_\nSpscRingQueue<CompletionRecord>",
                      "C", "CompletionRecord", "orange"))
    add(e, rect(1695, 365, 205, 135,
                "CompletionPoller::Run\ncompletionPollerThread_", "orange", font=16))

    add(e, arrow(320, 432, 370, 432, "Receive"))
    add(e, arrow(615, 432, 680, 432, "TryPush"))
    add(e, arrow(980, 432, 1045, 432, "TryPop"))
    add(e, arrow(1270, 432, 1335, 432, "Push"))
    add(e, arrow(1635, 432, 1695, 432, "TryPop"))

    # Shared resource layer.
    add(e, text(105, 625, 300, 32, "Core services", 18, "#868e96", "left"))
    # A shared access rail replaces the former fan of crossing diagonal arrows.
    # Both worker threads reach the rail; the rail then branches cleanly to the
    # two concrete shared modules below it.
    add(e, line(950, 655, 1795, 655, "#868e96"))
    add(e, line(1155, 500, 1155, 655, "#868e96"))
    add(e, text(900, 530, 235, 70,
                "Store / Load / Exist\nExecuteAsync", 14, "#495057", "right"))
    add(e, line(1795, 500, 1795, 655, "#868e96"))
    add(e, text(1500, 520, 270, 92,
                "GetStatus / 响应 Write\nStoreEnd / LoadEnd / Delete", 13, "#495057", "right"))

    add(e, rect(720, 720, 460, 180, "", "green", dashed=True))
    add(e, text(745, 735, 270, 32, "MetadataManager", 20, COLORS["green"][0], "left"))
    add(e, rect(820, 805, 260, 60, "BufferManager", "green", font=18))
    add(e, rect(1330, 720, 360, 145, "TransportManager\nHIXL Transport", "teal", font=19))
    add(e, arrow(950, 655, 950, 720))
    add(e, arrow(1510, 655, 1510, 720))
    save("02_internal_architecture_v1", 2000, 1100, e)


def core_data_path():
    e = []
    add(e, text(40, 20, 1420, 45, "请求接收、执行与完成回写的数据通路", 26))
    nodes = [
        (40, 180, 170, "DramStore", "green"),
        (270, 180, 230, "DramPoolServer::\nRequestReceiveLoop", "blue"),
        (560, 180, 190, "requestQueue_", "gray"),
        (810, 180, 190, "TaskWorker", "blue"),
        (1060, 180, 210, "completionQueue_", "gray"),
        (1330, 180, 220, "CompletionPoller", "orange"),
    ]
    for x, y, w, label, c in nodes:
        add(e, rect(x, y, w, 100, label, c))
    for (x1, w1), x2, label in [
        ((40, 170), 270, "TCP Metadata"),
        ((270, 230), 560, "RequestTaskPtr"),
        ((560, 190), 810, "TryPop"),
        ((810, 190), 1060, "CompletionRecord"),
        ((1060, 210), 1330, "TryPop"),
    ]:
        add(e, arrow(x1 + w1, 230, x2, 230, label))
    add(e, rect(790, 430, 240, 110, "MetadataManager\n└ BufferManager", "green"))
    add(e, rect(1110, 430, 240, 110, "TransportManager", "teal"))
    add(e, rect(1390, 430, 170, 110, "flagBufferPool_", "purple"))
    add(e, arrow(905, 280, 910, 430, "Store/Load/Exist"))
    add(e, arrow(950, 280, 1190, 430, "ExecuteAsync"))
    add(e, arrow(1440, 280, 1230, 430, "GetStatus"))
    add(e, arrow(1460, 280, 1470, 430, "响应暂存"))
    add(e, arrow(1390, 485, 1350, 485, "Write响应"))
    add(e, arrow(1110, 500, 210, 500, "HIXL单边数据/响应传输"))
    save("03_core_data_path_v1", 1600, 610, e)


def entry_state():
    e = []
    add(e, text(40, 18, 1720, 45, "EntryStatus 生命周期状态机", 28))
    add(e, rect(45, 85, 1710, 620, "", "gray", dashed=True))
    add(e, text(70, 100, 360, 32, "Entry lifecycle", 18, "#868e96", "left"))

    # UML initial pseudostate.
    add(e, ellipse(75, 300, 34, 34, "gray", "#343a40", 2))

    # State nodes use a title compartment and a concise invariant list.
    add(e, rect(180, 225, 320, 190, "", "yellow"))
    add(e, text(205, 245, 270, 40, "INITIALIZED", 23, COLORS["yellow"][0]))
    add(e, line(205, 300, 475, 300, COLORS["yellow"][0]))
    add(e, text(210, 315, 260, 75,
                "Buffer 已分配\n数据尚未发布\nLoad / Exist 不可见", 16, "#495057"))

    add(e, rect(680, 200, 360, 240, "", "green"))
    add(e, text(710, 222, 300, 42, "READY", 24, COLORS["green"][0]))
    add(e, line(710, 278, 1010, 278, COLORS["green"][0]))
    add(e, text(720, 295, 280, 115,
                "数据已发布\nLoadBegin / LoadEnd\nExist 刷新 leaseTimeout\n允许进入淘汰判断", 16, "#495057"))

    add(e, rect(1300, 225, 320, 190, "", "red"))
    add(e, text(1325, 245, 270, 40, "DELETING", 23, COLORS["red"][0]))
    add(e, line(1325, 300, 1595, 300, COLORS["red"][0]))
    add(e, text(1335, 315, 250, 75,
                "拒绝新的 Load / Exist\n等待释放 Buffer Slot\n并从索引移除", 16, "#495057"))

    # UML final pseudostate.
    add(e, ellipse(1710, 295, 44, 44, "gray", "#ffffff", 3))
    add(e, ellipse(1721, 306, 22, 22, "gray", "#343a40", 2))

    add(e, arrow(109, 317, 180, 317))
    add(e, text(95, 265, 95, 34, "StoreBegin", 13, "#495057"))
    add(e, arrow(500, 317, 680, 317))
    add(e, text(505, 255, 170, 52,
                "StoreEnd\n[transfer Completed]", 13, "#495057"))
    add(e, arrow(1040, 317, 1300, 317))
    add(e, text(1045, 245, 250, 65,
                "TryMarkDeleting [refCnt == 0]\n或 TryMarkEvicting\n[leaseTimeout <= now]", 12, "#495057"))
    add(e, arrow(1620, 317, 1710, 317))
    add(e, text(1605, 255, 120, 48, "ShardMetadata::\nDelete", 12, "#495057"))

    # READY self-loop for operations that do not change EntryStatus.
    add(e, polyarrow([(780, 200), (780, 145), (950, 145), (950, 200)],
                     "LoadBegin / LoadEnd / Exist", (865, 142)))

    # Dump failure follows a separate lower lane to keep the success path clean.
    add(e, polyarrow([(340, 415), (340, 570), (1460, 570), (1460, 415)],
                     "MetadataManager::Delete [Dump failed]", (900, 565), color=COLORS["red"][0]))
    save("04_entry_state_v1", 1800, 760, e)


def metadata_structure():
    e = []
    add(e, text(40, 20, 1340, 45, "MetadataManager、ShardMetadata 与 BufferManager 的结构关系", 26))
    add(e, rect(390, 90, 360, 100, "MetadataManager\nshards_[1024]", "blue", font=21))
    add(e, rect(950, 90, 360, 100, "BufferManager\npools_", "orange", font=21))
    add(e, arrow(750, 140, 950, 140, "bufferManager_"))
    add(e, rect(60, 290, 280, 250, "ShardMetadata[0]\n\nmetadata_\nperiodicEvictor_\ndeepEvictor_\nmtx_", "green", align="left"))
    add(e, rect(410, 290, 280, 250, "ShardMetadata[i]\n\nmetadata_\nperiodicEvictor_\ndeepEvictor_\nmtx_", "green", align="left"))
    add(e, rect(760, 290, 280, 250, "ShardMetadata[1023]\n\nmetadata_\nperiodicEvictor_\ndeepEvictor_\nmtx_", "green", align="left"))
    add(e, arrow(470, 190, 200, 290, "shards_[0]"))
    add(e, arrow(570, 190, 550, 290, "ShardIdx(key)"))
    add(e, arrow(670, 190, 900, 290, "shards_[1023]"))
    add(e, rect(1090, 300, 250, 220, "BufferPool(size A)\nBufferPool(size B)\n...\nMemoryRegions()", "orange", font=18))
    add(e, arrow(1130, 190, 1215, 300, "pools_[size]"))
    add(e, rect(80, 740, 260, 100, "TtlEvictionPolicy", "purple"))
    add(e, rect(420, 740, 260, 100, "PosEvictionPolicy", "purple"))
    add(e, rect(760, 740, 280, 100, "EntryPtr\nshared_ptr<Entry>", "gray"))
    add(e, arrow(190, 540, 210, 740, "periodicEvictor_"))
    add(e, arrow(550, 540, 550, 740, "deepEvictor_"))
    add(e, arrow(900, 540, 900, 740, "metadata_[key]"))
    save("05_metadata_structure_v1", 1400, 1010, e)


def communication_sequence():
    e = []
    add(e, text(40, 15, 1400, 45, "控制请求、数据传输与响应回写时序", 26))
    participants = [
        (120, "DramStore"),
        (430, "TcpMessageChannel"),
        (740, "TaskWorker"),
        (1050, "TransportManager"),
        (1360, "CompletionPoller"),
    ]
    for x, name in participants:
        add(e, rect(x - 110, 90, 220, 65, name, "blue" if name != "DramStore" else "green", font=17))
        add(e, line(x, 155, x, 770, dashed=True))
    add(e, arrow(120, 220, 430, 220, "Send Metadata（控制面）"))
    add(e, arrow(430, 290, 740, 290, "RequestTaskPtr"))
    add(e, arrow(740, 370, 1050, 370, "ExecuteAsync(Operation)"))
    add(e, arrow(1050, 440, 120, 440, "Dump Read / Load Write（数据面）"))
    add(e, arrow(1050, 510, 1360, 510, "TransferHandle"))
    add(e, arrow(1360, 585, 1050, 585, "GetStatus(handle)"))
    add(e, arrow(1360, 660, 1050, 660, "ExecuteAsync(response Write)"))
    add(e, arrow(1050, 725, 120, 725, "ResponseStatus + packed results"))
    save("06_communication_sequence_v1", 1500, 820, e)


def request_execution_pipeline():
    e = []
    add(e, text(40, 15, 1500, 45, "DramPool 请求执行线程与两级 SPSC 队列", 26))
    lanes = [
        (90, "DramPoolServer::RequestReceiveLoop", "blue"),
        (300, "TaskWorker::Run", "green"),
        (510, "CompletionPoller::Run", "orange"),
    ]
    for y, name, color in lanes:
        add(e, rect(30, y, 1500, 160, "", color, dashed=True))
        add(e, text(50, y + 15, 340, 32, name, 19, COLORS[color][0], "left"))
    add(e, rect(420, 135, 230, 70, "Receive + UnpackRequest", "blue"))
    add(e, rect(730, 135, 190, 70, "requestQueue_", "gray"))
    add(e, rect(1010, 135, 260, 70, "队列满：等待后重试", "yellow"))
    add(e, arrow(650, 170, 730, 170, "TryPush"))
    add(e, arrow(920, 170, 1010, 170, "失败"))
    add(e, arrow(1120, 215, 850, 215, "等待后重试"))
    add(e, rect(420, 345, 220, 70, "TryPop(RequestTaskPtr)", "green"))
    add(e, rect(720, 345, 250, 70, "ProcessDump/Load/Lookup", "green"))
    add(e, rect(1050, 345, 210, 70, "completionQueue_", "gray"))
    add(e, arrow(835, 205, 530, 345, "消费"))
    add(e, arrow(640, 380, 720, 380))
    add(e, arrow(970, 380, 1050, 380, "Push"))
    add(e, rect(420, 555, 230, 70, "FillPendingWindow", "orange"))
    add(e, rect(735, 555, 220, 70, "pending_ deque", "gray"))
    add(e, rect(1040, 555, 260, 70, "PollPendingCompletions", "orange"))
    add(e, arrow(1155, 415, 535, 555, "TryPop"))
    add(e, arrow(650, 590, 735, 590))
    add(e, arrow(955, 590, 1040, 590))
    save("07_request_execution_pipeline_v1", 1580, 710, e)


def completion_state():
    e = []
    add(e, text(40, 18, 1320, 45, "CompletionRecord.stage 推进过程", 26))
    add(e, rect(60, 200, 260, 110, "PollDataTransfer\nGetStatus(data_handle)", "blue", font=19))
    add(e, diamond(400, 185, 250, 140, "数据传输到达终态？", "yellow"))
    add(e, rect(745, 200, 250, 110, "SettleDataTransfer\nStoreEnd/LoadEnd/Delete", "green", font=17))
    add(e, rect(1090, 200, 250, 110, "SubmitResponse\nAllocate + PackResponse", "purple", font=17))
    add(e, diamond(1085, 420, 260, 145, "响应提交成功？", "yellow"))
    add(e, rect(740, 440, 250, 105, "PollResponseTransfer\nGetStatus(response_handle)", "orange", font=17))
    add(e, rect(400, 440, 240, 105, "释放 local_resp_slot\n移除 CompletionRecord", "gray", font=17))
    add(e, arrow(320, 255, 400, 255))
    add(e, arrow(650, 255, 745, 255, "是"))
    add(e, arrow(525, 325, 190, 390, "否：下一轮继续轮询"))
    add(e, arrow(995, 255, 1090, 255))
    add(e, arrow(1215, 310, 1215, 420))
    add(e, arrow(1085, 492, 990, 492, "是"))
    add(e, arrow(1215, 565, 1215, 645, "NoSpace：保留并重试"))
    add(e, rect(1060, 645, 310, 80, "CompletionRecord保留在pending_", "yellow"))
    add(e, arrow(740, 492, 640, 492, "响应到达终态"))
    save("08_completion_poller_state_v1", 1420, 780, e)


def gc_decision():
    e = []
    add(e, text(40, 15, 1300, 45, "Entry::TryMarkEvicting 淘汰资格判断", 26))
    add(e, rect(530, 80, 260, 70, "EvictionPolicy选择EntryPtr", "blue"))
    add(e, diamond(515, 200, 290, 125, "status == READY？", "yellow"))
    add(e, diamond(515, 390, 290, 125, "refCnt == 0？", "yellow"))
    add(e, diamond(515, 580, 290, 125, "leaseTimeout <= now？", "yellow"))
    add(e, rect(530, 770, 260, 85, "status = DELETING\n返回 true", "green"))
    add(e, rect(970, 300, 260, 90, "返回 false\n本轮不淘汰", "red"))
    add(e, arrow(660, 150, 660, 200))
    add(e, arrow(660, 325, 660, 390, "是"))
    add(e, arrow(805, 262, 970, 330, "否"))
    add(e, arrow(660, 515, 660, 580, "是"))
    add(e, arrow(805, 452, 970, 345, "否"))
    add(e, arrow(660, 705, 660, 770, "是"))
    add(e, arrow(805, 642, 970, 365, "否"))
    save("09_gc_eviction_decision_v1", 1340, 920, e)


def validate():
    for path in ROOT.glob("*_v1.excalidraw"):
        raw = path.read_text(encoding="utf-8")
        obj = json.loads(raw)
        assert obj["type"] == "excalidraw"
        assert obj["version"] == 2
        assert "???" not in raw
        assert obj["elements"]
    for path in ROOT.glob("*_v1.svg"):
        raw = path.read_text(encoding="utf-8")
        assert raw.startswith("<svg")
        assert "???" not in raw


if __name__ == "__main__":
    drampool_constructor_v2()
    drampool_constructor_v3()
    internal_architecture()
    core_data_path()
    entry_state()
    metadata_structure()
    communication_sequence()
    request_execution_pipeline()
    completion_state()
    gc_decision()
    validate()
