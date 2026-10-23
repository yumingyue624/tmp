from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT.parent / 'generate_figures.py'
scope = {'__file__': str(source)}
exec(compile(source.read_text(encoding='utf-8').split("f=Figure('01-architecture'")[0], str(source), 'exec'), scope)
scope['ROOT'] = ROOT
Figure, C = scope['Figure'], scope['C']
X = [170, 580, 1010, 1450]


def start(name, title, subtitle, height):
    f = Figure(name, 1650, height, title, subtitle)
    for x, label, color in zip(X, ['调用方 / RealClient', 'C++ Client', 'KVClientBackend / SDK', '原生 Master + TE'], ['gray', 'blue', 'green', 'amber']):
        f.box(x-145, 145, 290, 65, label, color, 20)
        f.arrow([(x, 210), (x, height-110)], color, dashed=True, head=False)
    return f


def msg(f, a, b, y, label, color='blue', reply=False):
    left, right = sorted([X[a], X[b]])
    f.text(left+10, y-37, right-left-20, 30, label, 19, C[color])
    f.arrow([(X[a], y), (X[b], y)], color, dashed=reply)


def note(f, x, y, w, text, color='gray', h=78):
    f.box(x, y, w, h, text, color, 19)


def frame(f, y, h, label):
    # Transparent frame preserves participant lifelines and messages.
    f.arrow([(30,y),(1600,y),(1600,y+h),(30,y+h),(30,y)], 'gray', dashed=True, head=False)
    f.text(45, y+8, 1510, 28, label, 19, C['gray'], align='left')


f = start('03-dual-put-sequence', '双写时序：提交外部 → 原生同步写入 → Wait → 返回',
          '设计调用链；实线为调用，虚线为返回；灰框表示条件分支或安全约束。', 1870)
msg(f, 0, 1, 280, 'Put / BatchPut(key(s), slices, config)')
note(f, 330, 315, 500, '参数与布局检查；同 key 前序操作先收敛', 'blue', 65)
note(f, 70, 405, 1490, '前置条件：源已就绪且保持不变，内存已预注册；RealClient 已完成 IPC 导入 / 地址转换\n本次调用不临时注册、不复制 payload；多 slices 按序组成对象，检查长度、对齐和请求上限', h=90)
msg(f, 1, 2, 555, 'Put / BatchPut(key(s), slices)', 'green')
note(f, 835, 585, 390, '查已注册句柄，转换请求\n提交 StoreAsync / BatchStoreAsync', 'green', 85)
msg(f, 2, 1, 725, 'OperationHandle 或提交错误', 'green', True)
note(f, 70, 755, 1490, '提交失败仍执行原生写；若部分请求已派发，必须返回可追踪句柄\nSDK TaskId 由 backend 封装；同步 backend 可返回已完成句柄', h=85)
msg(f, 1, 3, 910, '原生 Put / BatchPut', 'amber')
note(f, 1220, 940, 380, 'PutStart 分配副本\nTE 传输 → PutEnd 提交', 'amber', 90)
note(f, 820, 940, 360, '外部任务继续执行\n与原生传输重叠', 'green', 90)
msg(f, 3, 1, 1085, '原生逐项结果 / 异常', 'amber', True)
frame(f, 1120, 240, 'alt  已获得外部句柄：即使原生失败 / 异常，也必须等待外部收敛')
msg(f, 1, 2, 1210, 'OperationHandle::Wait()', 'green')
msg(f, 2, 1, 1285, '外部逐项结果 / 超时状态', 'green', True)
f.text(70, 1320, 1480, 28, 'else  无句柄且无在途任务：记录提交错误，不调用 Wait', 19, C['gray'], align='left')
note(f, 70, 1400, 1490, '返回门槛：两路都已停止访问源；Wait 超时不等于传输停止，不能仅删除任务后返回\nASU 达 maxErrorCount 可触发断链，仍须确认无在途访问；停止保证须按设备 / 协议验证', h=90)
note(f, 330, 1530, 1000, '按原始下标汇合结果：返回原生结果，外部失败记录降级\n原生失败、外部成功：保留外部对象，不自动回滚删除', 'blue', 90)
msg(f, 1, 0, 1690, '返回原生结果；调用方此时才可复用源', 'blue', True)
f.text(65, 1780, 1520, 55, '设备不允许两路并发访问时须串行或禁用；原生 staging buffer 不自动具备外部注册资格。', 19, C['gray'])
f.export()

f = start('04-delete-sequence', '删除时序：外部先提交，force 仅控制原生租约检查',
          'Remove / BatchRemove；原生淘汰不触发外部删除；各 key 分别记录结果。', 2110)
msg(f, 0, 1, 280, 'Remove(key, force) / BatchRemove')
note(f, 330, 315, 500, '同 key 前序写先收敛，再开始删除', 'blue', 65)
msg(f, 1, 2, 445, 'Remove / BatchRemove：两种 force 均调用', 'green')
note(f, 835, 475, 390, '转换 key，提交 DeleteAsync\nSDK 删除接口没有 force 参数', 'green', 85)
msg(f, 2, 1, 620, 'OperationHandle 或提交错误', 'green', True)
note(f, 70, 655, 1490, '外部先提交，可能在原生检查前完成，不能依据原生拒绝撤回\nforce=false 也调用外部删除，避免默认 Remove 成功后仍残留外部缓存；部分提交必须可追踪', h=85)
msg(f, 1, 3, 810, '原生 Remove(key, force) / BatchRemove', 'amber')
frame(f, 850, 405, 'alt  原生侧按 force 与读租约处理（Query / GetReplicaList 通过 GrantLease 授予短租约）')
note(f, 1200, 905, 380, 'force=false，租约未过期\n拒绝删除：OBJECT_HAS_LEASE', 'amber', 78)
msg(f, 3, 1, 1025, '返回租约拒绝；外部仍已提交', 'amber', True)
f.arrow([(45, 1050), (1585, 1050)], 'gray', dashed=True, head=False)
note(f, 1200, 1070, 380, 'force=false，无有效租约\n正常删除，或明确 NotFound', 'amber', 75)
f.arrow([(45, 1160), (1585, 1160)], 'gray', dashed=True, head=False)
note(f, 1200, 1173, 380, 'force=true：跳过租约检查\n执行删除，可能影响在途读取', 'amber', 75)
msg(f, 3, 1, 1320, '原生逐项结果 / 错误 / 超时', 'amber', True)
frame(f, 1360, 220, 'alt  已获得外部句柄：原生成功、拒绝或异常，均须收敛已提交任务')
msg(f, 1, 2, 1440, 'OperationHandle::Wait()', 'green')
msg(f, 2, 1, 1510, '外部逐项结果 / 超时状态', 'green', True)
f.text(70, 1543, 1480, 28, 'else  无句柄且无在途任务：记录外部提交错误', 19, C['gray'], align='left')
note(f, 70, 1615, 1490, '结果汇合\n两侧已删 / 明确不存在：幂等成功；原生拒绝：返回原生错误，外部已删则记录部分删除\n原生已删但外部失败 / 未确认：删除失败或部分失败；原生结果不确定：保留逐侧状态', 'blue', 115)
msg(f, 1, 0, 1800, '返回汇合结果，不将部分删除报告为完整成功', 'blue', True)
note(f, 70, 1850, 1490, '外部在途读取安全由存储端保证；force=true 不允许提前回收仍被访问的资源\n超时不等于底层停止；保留未确认任务，重试需防止迟到删除误删重建对象', h=90)
f.text(65, 2000, 1520, 55, '批量处理保留原始下标；对象代际 / 幂等保护未落实前，不对未知结果进行盲重试。', 19, C['gray'])
f.export()
