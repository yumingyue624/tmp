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
    def marker_id(color: str) -> str:
        return "arrow_" + "".join(ch for ch in color if ch.isalnum())

    arrow_colors = sorted({
        e["strokeColor"]
        for e in elements
        if e["type"] == "arrow" and e.get("endArrowhead")
    })
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
            marker = (
                f' marker-end="url(#{marker_id(stroke)})"'
                if kind == "arrow" and e.get("endArrowhead")
                else ""
            )
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
    markers = "".join(
        f'<marker id="{marker_id(color)}" markerWidth="10" markerHeight="10" '
        f'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
        f'<path d="M0,0 L0,6 L9,3 z" fill="{color}"/></marker>'
        for color in arrow_colors
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<defs>{markers}</defs>'
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
    add(e, text(40, 18, 1800, 45, "DramPool 元数据管理", 28))

    # Manager: one routing surface, two ownership directions.
    add(e, rect(60, 300, 360, 270, "", "blue"))
    add(e, text(90, 322, 300, 40, "MetadataManager", 23, COLORS["blue"][0]))
    add(e, line(90, 380, 390, 380, COLORS["blue"][0]))
    add(e, text(100, 398, 280, 145,
                "shards_[1024]\nbufferManager_\ndefaultEvictRatio_\n\nShardIdx(key) → ShardMetadata[i]",
                16, "#495057", "left"))

    # The shard array is shown once; only the selected shard is expanded.
    add(e, rect(500, 90, 800, 710, "", "green", dashed=True))
    add(e, text(530, 105, 600, 36,
                "shards_ : array<unique_ptr<ShardMetadata>, 1024>",
                19, COLORS["green"][0], "left"))
    add(e, rect(550, 165, 170, 70, "ShardMetadata[0]", "gray", font=15))
    add(e, rect(770, 155, 190, 90, "ShardMetadata[i]", "green", font=17))
    add(e, text(985, 175, 70, 50, "···", 24, "#868e96"))
    add(e, rect(1080, 165, 180, 70, "ShardMetadata[1023]", "gray", font=14))
    add(e, arrow(420, 420, 500, 420))

    add(e, arrow(865, 245, 865, 300, "selected"))
    add(e, rect(550, 300, 700, 450, "", "green", dashed=True))
    add(e, text(580, 315, 360, 36, "ShardMetadata[i]", 21, COLORS["green"][0], "left"))
    add(e, rect(590, 375, 620, 55,
                "mtx_ : RwLock    ·    leaseTime_ : milliseconds", "gray", font=15))
    add(e, rect(590, 470, 620, 115,
                "metadata_ : unordered_map<BlockId, EntryPtr>\nkey₀ → EntryPtr    keyᵢ → EntryPtr    keyₙ → EntryPtr",
                "green", font=16))
    add(e, rect(590, 635, 285, 80,
                "periodicEvictor_\nunique_ptr<EvictionPolicy>", "purple", font=14))
    add(e, rect(925, 635, 285, 80,
                "deepEvictor_\nunique_ptr<EvictionPolicy>", "purple", font=14))

    # Main index and both policy indexes share ownership of the same Entry.
    add(e, line(1275, 520, 1275, 770, "#868e96"))
    add(e, line(1210, 525, 1275, 525, "#868e96"))
    add(e, line(732, 715, 732, 770, "#868e96"))
    add(e, line(1067, 715, 1067, 770, "#868e96"))
    add(e, line(732, 770, 1275, 770, "#868e96"))
    add(e, arrow(1275, 555, 1320, 555))
    add(e, rect(1320, 510, 160, 90, "EntryPtr\nshared_ptr<Entry>", "gray", font=13))

    # Entry is the bridge from metadata indexes to the allocated Host slot.
    add(e, arrow(1480, 555, 1510, 555))
    add(e, rect(1510, 245, 320, 420, "", "gray"))
    add(e, text(1540, 265, 260, 40, "Entry", 23, COLORS["gray"][0]))
    add(e, line(1540, 320, 1800, 320, COLORS["gray"][0]))
    add(e, text(1550, 340, 240, 100,
                "key : BlockId\nshard : uint32_t\nsize : size_t\nbuffer : Buffer",
                16, "#495057", "left"))
    add(e, line(1540, 455, 1800, 455, "#adb5bd"))
    add(e, text(1550, 470, 240, 80,
                "lock : Spinlock\nstatus · refCnt · leaseTimeout",
                15, "#495057", "left"))
    add(e, line(1540, 570, 1800, 570, "#adb5bd"))
    add(e, text(1550, 585, 240, 52,
                "lifeTimeout · position", 15, "#495057", "left"))

    # Buffer ownership is separate from metadata ownership.
    add(e, rect(60, 790, 360, 230, "", "orange"))
    add(e, text(90, 810, 300, 40, "BufferManager", 22, COLORS["orange"][0]))
    add(e, line(90, 865, 390, 865, COLORS["orange"][0]))
    add(e, text(100, 880, 280, 120,
                "pools_ : map<size,\n  unique_ptr<BufferPool>>\nmemoryRegions_\nAllocate(size) / Free(size, slot)",
                14, "#495057", "left"))
    add(e, arrow(240, 570, 240, 790, "bufferManager_ (reference)"))

    add(e, rect(1280, 815, 550, 205, "", "orange", dashed=True))
    add(e, text(1310, 832, 470, 36, "BufferPool(size) · Host DRAM", 20, COLORS["orange"][0], "left"))
    for idx, label in enumerate(["slot 0", "slot 1", "···", "slot n"]):
        add(e, rect(1320 + idx * 120, 900, 95, 70, label,
                    "orange" if label != "···" else "gray", font=14))
    add(e, arrow(420, 920, 1280, 920, "pools_[size] (unique_ptr)"))
    add(e, arrow(1670, 665, 1670, 815, "buffer.addr / slot / length"))

    save("05_metadata_management_v1", 1880, 1080, e)


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
    add(e, arrow(120, 220, 430, 220, "Send Request（控制面）"))
    add(e, arrow(430, 290, 740, 290, "RequestTaskPtr"))
    add(e, arrow(740, 370, 1050, 370, "ExecuteAsync(Operation)"))
    add(e, arrow(1050, 440, 120, 440, "Dump Read / Load Write（数据面）"))
    add(e, arrow(1050, 510, 1360, 510, "TransferHandle"))
    add(e, arrow(1360, 585, 1050, 585, "GetStatus(handle)"))
    add(e, arrow(1360, 660, 1050, 660, "ExecuteAsync(response Write)"))
    add(e, arrow(1050, 725, 120, 725, "ResponseStatus + packed results"))
    save("06_communication_sequence_v2", 1500, 820, e)


def request_execution_pipeline():
    e = []

    def thread_card(x, y, w, h, title, entry, details, stop_flag, color):
        stroke, fill = COLORS[color]
        shape = base("rectangle", x, y, w, h, stroke, fill)
        shape["strokeWidth"] = 2
        add(e, shape)
        if h <= 150:
            add(e, text(x + 18, y + 7, w - 36, 27, title, 18, stroke, "left"))
            add(e, line(x + 18, y + 42, x + w - 18, y + 42, stroke))
            add(e, text(x + 18, y + 48, w - 36, 27, entry, 15, "#343a40", "left"))
            add(e, text(x + 18, y + 76, w - 36, 22, details, 12, "#495057", "left"))
            add(e, text(x + 18, y + h - 25, w - 36, 18, stop_flag, 11, "#868e96", "left"))
        else:
            add(e, text(x + 18, y + 12, w - 36, 34, title, 18, stroke, "left"))
            add(e, line(x + 18, y + 56, x + w - 18, y + 56, stroke))
            add(e, text(x + 18, y + 67, w - 36, 38, entry, 15, "#343a40", "left"))
            add(e, text(x + 18, y + 108, w - 36, 52, details, 13, "#495057", "left"))
            add(e, text(x + 18, y + h - 38, w - 36, 25, stop_flag, 12, "#868e96", "left"))

    add(e, text(45, 20, 1910, 45, "DramPoolServer 线程模型", 27))
    add(e, rect(30, 85, 1940, 915, "", "gray", dashed=True))
    add(e, text(60, 100, 360, 34, "DramPoolServer", 20, COLORS["gray"][0], "left"))

    # Request processing path: each queue has exactly one producer and one consumer.
    add(e, rect(70, 155, 1860, 510, "", "blue", dashed=True))
    add(e, text(100, 172, 430, 34, "请求处理路径", 19, COLORS["blue"][0], "left"))

    thread_card(
        105, 275, 275, 205,
        "requestReceiverThread_",
        "RequestReceiveLoop()",
        "Receive → UnpackRequest\n→ RequestTask",
        "stop: requestReceiverStop_",
        "purple",
    )
    add(e, spsc_queue(
        440, 235, 300, 280,
        "requestQueue_\nRequestQueue",
        "R", "RequestTaskPtr", "purple",
    ))
    thread_card(
        800, 275, 280, 205,
        "taskWorkerThread_",
        "TaskWorkerLoop()\n→ TaskWorker::Run()",
        "TryPop → ProcessOneRequest\n→ CompletionRecord",
        "stop: taskWorkerStop_",
        "blue",
    )
    add(e, spsc_queue(
        1140, 235, 300, 280,
        "completionQueue_\nCompletionQueue",
        "C", "CompletionRecord", "orange",
    ))
    thread_card(
        1500, 265, 330, 225,
        "completionPollerThread_",
        "CompletionPollerLoop()\n→ CompletionPoller::Run()",
        "FillPendingWindow → pending_\n→ PollPendingCompletions",
        "stop: completionPollerStop_",
        "orange",
    )

    add(e, arrow(380, 377, 440, 377, "TryPush", COLORS["purple"][0]))
    add(e, arrow(740, 377, 800, 377, "TryPop", COLORS["purple"][0]))
    add(e, arrow(1080, 377, 1140, 377, "Push", COLORS["orange"][0]))
    add(e, arrow(1440, 377, 1500, 377, "TryPop", COLORS["orange"][0]))

    # The GC loop is an independent timed path and bypasses both request queues.
    add(e, rect(70, 710, 1860, 225, "", "green", dashed=True))
    add(e, text(100, 727, 430, 34, "定时维护路径", 19, COLORS["green"][0], "left"))
    thread_card(
        180, 775, 320, 125,
        "gcThread_",
        "GCThreadLoop()",
        "独立于请求处理路径",
        "stop: gcThreadStop_",
        "green",
    )
    add(e, rect(
        670, 785, 430, 105,
        "stopWaitCv_.wait_for\n(g_config.gcIntervalMs)",
        "gray", font=17,
    ))
    add(e, rect(
        1300, 785, 420, 105,
        "metadataManager_->PerformEvict()",
        "green", font=18,
    ))
    add(e, arrow(500, 837, 670, 837, "wait", COLORS["green"][0]))
    add(e, arrow(1100, 837, 1300, 837, "timeout", COLORS["green"][0]))

    save("07_request_execution_pipeline_v2", 2000, 1040, e)


def completion_state():
    e = []

    def state_card(x, y, number, title, entry, lines, color):
        stroke, fill = COLORS[color]
        shape = base("rectangle", x, y, 430, 270, stroke, fill)
        shape["strokeWidth"] = 3
        add(e, shape)
        add(e, ellipse(x + 22, y + 18, 52, 52, color, fill_override=stroke, stroke_width=2))
        add(e, text(x + 22, y + 18, 52, 52, str(number), 21, "#ffffff"))
        add(e, text(x + 92, y + 18, 310, 42, title, 22, stroke, "left"))
        add(e, text(x + 92, y + 59, 310, 28, entry, 14, "#495057", "left"))
        add(e, line(x + 24, y + 102, x + 406, y + 102, stroke))
        add(e, text(x + 36, y + 118, 358, 118, lines, 15, "#343a40", "left"))

    def queue_strip(x, y, w, title, footer, prefix, color):
        add(e, rect(x, y, w, 150, "", color, dashed=True))
        add(e, text(x + 16, y + 8, w - 32, 34,
                    title, 15, COLORS[color][0]))
        slot_w = 68
        gap = 12
        start_x = x + (w - (4 * slot_w + 3 * gap)) / 2
        for index in range(4):
            sx = start_x + index * (slot_w + gap)
            label = f"{prefix}{index}" if index < 3 else "empty"
            add(e, rect(sx, y + 48, slot_w, 55, label,
                        color if index < 3 else "gray", font=11))
        add(e, text(x + 16, y + 112, w - 32, 27,
                    footer, 11, "#495057"))

    def pending_window(x, y, w):
        add(e, rect(x, y, w, 180, "", "orange", dashed=True))
        add(e, text(x + 16, y + 7, w - 32, 32,
                    "pending_ · deque<CompletionRecord> · g_config.pollerPendingDepth = 64",
                    14, COLORS["orange"][0]))
        slot_w = 20
        slot_h = 15
        gap_x = 5
        gap_y = 5
        grid_w = 16 * slot_w + 15 * gap_x
        start_x = x + (w - grid_w) / 2
        start_y = y + 44
        stage_colors = ["blue", "purple", "orange", "blue", "orange", "purple"]
        for row in range(4):
            for col in range(16):
                index = row * 16 + col
                color = stage_colors[index % len(stage_colors)] if index < 56 else "gray"
                stroke, fill = COLORS[color]
                cell = base(
                    "rectangle",
                    start_x + col * (slot_w + gap_x),
                    start_y + row * (slot_h + gap_y),
                    slot_w,
                    slot_h,
                    stroke,
                    fill,
                )
                cell["strokeWidth"] = 1
                add(e, cell)
            add(e, text(start_x - 40, start_y + row * (slot_h + gap_y) - 2, 32, 19,
                        str(row * 16), 9, "#868e96", "right"))
            add(e, text(start_x + grid_w + 8, start_y + row * (slot_h + gap_y) - 2, 32, 19,
                        str(row * 16 + 15), 9, "#868e96", "left"))

        legend_y = y + 133
        legend = [
            ("blue", "PollDataTransfer"),
            ("purple", "SubmitResponse"),
            ("orange", "PollResponseTransfer"),
            ("gray", "empty"),
        ]
        legend_x = x + 38
        for color, label in legend:
            stroke, fill = COLORS[color]
            cell = base("rectangle", legend_x, legend_y, 18, 18, stroke, fill)
            cell["strokeWidth"] = 1
            add(e, cell)
            add(e, text(legend_x + 25, legend_y - 4, 140, 26,
                        label, 10, stroke, "left"))
            legend_x += 170
        add(e, text(x + 16, y + 154, w - 32, 20,
                    "有效位置的颜色表示 record.stage，灰色位置为空",
                    10, "#495057"))

    add(e, text(45, 20, 2090, 45, "CompletionRecord.stage 状态转换", 28))

    # TaskWorker owns record creation. completionQueue_ is outside both thread-local frames.
    add(e, rect(100, 70, 540, 180, "", "blue", dashed=True))
    add(e, text(125, 78, 180, 28,
                "TaskWorker", 17, COLORS["blue"][0], "left"))
    add(e, rect(140, 110, 460, 110,
                "TaskWorker\n创建 CompletionRecord\nstage = PollDataTransfer\n或 SubmitResponse",
                "blue", font=13))

    # The SPSC queue is the only structure spanning the two thread roles.
    queue_strip(
        170, 275, 400,
        "completionQueue_ · CompletionQueue",
        "SPSC · TaskWorker → CompletionPoller",
        "C", "purple",
    )

    # CompletionPoller owns both pending_ and the complete state machine.
    add(e, rect(45, 470, 2090, 910, "", "orange", dashed=True))
    add(e, text(70, 482, 230, 30,
                "CompletionPoller", 17, COLORS["orange"][0], "left"))
    add(e, rect(220, 535, 300, 110,
                "CompletionPoller\nFillPendingWindow()",
                "orange", font=16))
    pending_window(620, 500, 760)

    add(e, arrow(370, 220, 370, 275, "Push", COLORS["purple"][0]))
    add(e, arrow(370, 425, 370, 535, "TryPop", COLORS["purple"][0]))
    add(e, arrow(520, 590, 620, 590, "emplace_back", COLORS["orange"][0]))

    add(e, text(75, 700, 690, 34,
                "PollPendingCompletions()：推进 pending_ 中的 CompletionRecord",
                19, COLORS["gray"][0], "left"))

    state_card(
        100, 820, 1,
        "PollDataTransfer",
        "PollDataTransfer(record)",
        "GetStatus(data_handle)\nWaiting：保留在 pending_\n终态：SettleDataTransfer()\n发布/删除 Dump，释放 Load 引用",
        "blue",
    )
    state_card(
        740, 820, 2,
        "SubmitResponse",
        "SubmitResponse(record)",
        "flagBufferPool_.Allocate()\nPackResponse(results)\nExecuteAsync(response Write)",
        "purple",
    )
    state_card(
        1380, 820, 3,
        "PollResponseTransfer",
        "PollResponseTransfer(record)",
        "GetStatus(response_handle)\n\nWaiting：保留 local_resp_slot\n终态：释放响应 Slot",
        "orange",
    )

    # A record may enter pending_ at either of the first two stages.
    add(e, rect(175, 735, 280, 65,
                "Dump/Load · 有数据 transfer 任务\n初始 stage = PollDataTransfer",
                "blue", font=11))
    add(e, arrow(315, 800, 315, 820, color=COLORS["blue"][0]))
    add(e, rect(815, 735, 280, 65,
                "Lookup · 无数据 transfer 任务\n初始 stage = SubmitResponse",
                "purple", font=11))
    add(e, arrow(955, 800, 955, 820, color=COLORS["purple"][0]))

    # Main state axis.
    add(e, arrow(530, 955, 740, 955, color=COLORS["blue"][0]))
    add(e, text(535, 877, 200, 58,
                "data_handle 到达终态\nSettleDataTransfer()",
                13, COLORS["blue"][0]))
    add(e, rect(540, 985, 190, 58,
                "stage =\nSubmitResponse", "purple", font=12))

    add(e, arrow(1170, 955, 1380, 955, color=COLORS["purple"][0]))
    add(e, text(1175, 877, 200, 58,
                "响应 Write 提交成功\n获得 response_handle",
                13, COLORS["purple"][0]))
    add(e, rect(1180, 985, 190, 58,
                "stage =\nPollResponseTransfer", "orange", font=12))

    # Waiting/NoSpace retain the record in its current stage.
    add(e, polyarrow(
        [(220, 1090), (220, 1190), (410, 1190), (410, 1090)],
        color=COLORS["blue"][0]))
    add(e, text(225, 1191, 180, 48,
                "Waiting\n下轮继续轮询", 13, COLORS["blue"][0]))

    add(e, polyarrow(
        [(845, 1090), (845, 1190), (1010, 1190), (1010, 1090)],
        color=COLORS["purple"][0]))
    add(e, text(835, 1191, 185, 48,
                "flagBufferPool_ NoSpace\n保留记录，下轮重试", 13, COLORS["purple"][0]))

    add(e, polyarrow(
        [(1500, 1090), (1500, 1190), (1690, 1190), (1690, 1090)],
        color=COLORS["orange"][0]))
    add(e, text(1505, 1191, 180, 48,
                "Waiting\n下轮继续轮询", 13, COLORS["orange"][0]))

    # Terminal exits are kept outside the three-stage axis.
    add(e, arrow(1080, 1090, 1080, 1265, color=COLORS["red"][0]))
    add(e, rect(905, 1265, 350, 85,
                "响应提交失败\n释放已有资源并从 pending_ 移除",
                "red", font=14))

    add(e, arrow(1810, 955, 2070, 955, color=COLORS["orange"][0]))
    add(e, ellipse(2070, 930, 50, 50, "gray", fill_override="#495057", stroke_width=2))
    add(e, text(1835, 870, 225, 48,
                "response_handle 到达终态\nReleaseResponseBuffer()",
                12, COLORS["orange"][0]))
    add(e, text(1885, 990, 205, 42,
                "从 pending_ 移除", 13, "#495057"))

    save("08_completion_poller_state_v6", 2180, 1410, e)


def gc_decision():
    e = []
    add(e, text(40, 18, 1820, 45, "Entry::TryMarkEvicting() 淘汰保护判定", 28))

    add(e, rect(280, 90, 1540, 700, "", "gray", dashed=True))
    add(e, text(305, 102, 420, 34, "Entry::lock critical section", 18, "#868e96", "left"))
    add(e, rect(1220, 120, 530, 58,
                "SpinLockGuard guard(lock) · check and transition atomically",
                "gray", font=14))

    add(e, rect(45, 260, 190, 100,
                "EvictionPolicy\nEntryPtr candidate", "blue", font=16))

    # The success path stays on one horizontal axis.
    add(e, diamond(330, 230, 250, 150,
                   "status ==\nEntryStatus::READY", "yellow", font=16))
    add(e, diamond(650, 230, 250, 150, "refCnt == 0", "yellow", font=17))
    add(e, diamond(970, 230, 250, 150, "leaseTimeout <= now", "yellow", font=16))
    add(e, rect(1290, 250, 230, 110, "status = DELETING", "green", font=18))
    add(e, rect(1600, 250, 160, 110, "return true", "green", font=18))

    add(e, arrow(235, 310, 330, 310))
    add(e, arrow(580, 305, 650, 305, "true"))
    add(e, arrow(900, 305, 970, 305, "true"))
    add(e, arrow(1220, 305, 1290, 305, "true"))
    add(e, arrow(1520, 305, 1600, 305))

    # Every rejected condition has one explicit reason and joins one exit rail.
    add(e, rect(330, 500, 250, 95,
                "status != READY\n未发布或已进入删除", "red", font=15))
    add(e, rect(650, 500, 250, 95,
                "refCnt != 0\n仍有在途 Load", "red", font=15))
    add(e, rect(970, 500, 250, 95,
                "leaseTimeout > now\nLease 保护尚未结束", "red", font=15))
    add(e, arrow(455, 380, 455, 500))
    add(e, text(465, 420, 65, 28, "false", 13, "#c92a2a", "left"))
    add(e, arrow(775, 380, 775, 500))
    add(e, text(785, 420, 65, 28, "false", 13, "#c92a2a", "left"))
    add(e, arrow(1095, 380, 1095, 500))
    add(e, text(1105, 420, 65, 28, "false", 13, "#c92a2a", "left"))

    add(e, line(455, 595, 455, 690, COLORS["red"][0]))
    add(e, line(775, 595, 775, 690, COLORS["red"][0]))
    add(e, line(1095, 595, 1095, 690, COLORS["red"][0]))
    add(e, line(455, 690, 1420, 690, COLORS["red"][0]))
    add(e, arrow(1420, 690, 1480, 690, color=COLORS["red"][0]))
    add(e, rect(1480, 635, 230, 110,
                "return false\n本轮不淘汰", "red", font=17))

    save("09_gc_eviction_decision_v2", 1880, 850, e)


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
