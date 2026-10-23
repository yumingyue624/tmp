from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT.parent / 'generate_figures.py'
scope = {'__file__': str(source)}
exec(compile(source.read_text(encoding='utf-8').split("f=Figure('01-architecture'")[0], str(source), 'exec'), scope)
scope['ROOT'] = ROOT
Figure, C = scope['Figure'], scope['C']
C.update(red='#ad3434', red_bg='#fff0f0')
X = [170, 570, 970, 1370, 1830]


def start(name, title, subtitle, height):
    f = Figure(name, 2040, height, title, subtitle)
    for x, label, color in zip(X, ['调用方 / RealClient', 'C++ Client', 'KVClientBackend', 'KVClient SDK', '原生 Master + TE'], ['gray', 'blue', 'green', 'green', 'amber']):
        f.box(x-150, 145, 300, 60, label, color, 21)
        f.arrow([(x,205),(x,height-90)], color, dashed=True, head=False)
    return f


def msg(f,a,b,y,label,color='blue',reply=False):
    lo,hi=sorted([X[a],X[b]])
    f.text(lo+8,y-32,hi-lo-16,28,label,18,C[color])
    f.arrow([(X[a],y),(X[b],y)],color,dashed=reply)


def branch(f,y,h,label,left=430,right=1580):
    f.arrow([(left,y),(right,y),(right,y+h),(left,y+h),(left,y)],'gray',dashed=True,head=False)
    f.text(left+12,y+9,right-left-24,28,label,19,C['gray'],align='left')


def sep(f,y,label,left=430,right=1580):
    f.arrow([(left,y),(right,y)],'gray',dashed=True,head=False)
    f.text(left+12,y+8,right-left-24,28,label,19,C['gray'],align='left')


def local(f,a,y,label,color='blue',w=360):
    # A self-message makes error handling part of the sequence, rather than a note box.
    x=X[a]
    f.arrow([(x,y),(x+65,y),(x+65,y+32),(x,y+32)],color)
    f.text(x+80,y,w,52,label,18,C[color],align='left')


def export_expanded(f):
    # Insert space for Client preparation, backend conversion, and handle construction.
    for e in f.elements:
        y=e['y']
        if e['type'] == 'line' and y == 205:
            e['height'] += 370
            e['points'][-1][1] += 370
        else:
            e['y'] += (130 if y >= 313 else 0) + (170 if y >= 388 else 0) + (70 if y >= 573 else 0)
            if e['type'] == 'line' and y == 455:
                e['height'] += 70
                for point in e['points']:
                    if point[1] > 0:
                        point[1] += 70
    f.height += 370
    f.export()


f=start('03-dual-put-sequence-split','双写时序：外部提交失败与执行失败分别处理',
        '实线：调用；虚线：返回；红色：错误路径；alt / opt：条件执行。Put 与 BatchPut 采用相同编排。',2040)
msg(f,0,1,270,'Put / BatchPut')
local(f,1,305,'校验 key / slices、对象总长\n同 key 排序；保留原始下标','blue',500)
msg(f,1,2,345,'Put / BatchPut','green')
local(f,2,375,'tenant + 字符串 key → SDK key\n查注册句柄，构造 KVBuffer','green',560)
msg(f,2,3,420,'StoreAsync / BatchStoreAsync','green')
branch(f,455,430,'alt  SDK 接受请求（含可追踪的部分提交）')
msg(f,3,2,540,'TaskId','green',True)
local(f,2,560,'以 TaskId 构造 OperationHandle\n保存 SDK 上下文与批量下标','green',560)
msg(f,2,1,605,'OperationHandle','green',True)
sep(f,635,'else  提交失败，且没有在途任务')
msg(f,3,2,710,'提交错误','red',True)
msg(f,2,1,775,'提交错误（无句柄）','red',True)
local(f,1,830,'记录外部失败\n继续原生写入','red',350)
msg(f,1,4,945,'原生 Put / BatchPut：分配 → 传输 → 提交','amber')
msg(f,4,1,1020,'原生逐项结果 / 错误','amber',True)
branch(f,1060,500,'opt  已获得 OperationHandle：原生失败 / 异常也必须执行 Wait')
msg(f,1,2,1140,'OperationHandle::Wait()','green')
msg(f,2,3,1210,'Wait(TaskId, timeout)','green')
msg(f,3,2,1280,'任务结果 / 超时','green',True)
branch(f,1310,180,'opt  外部执行失败或超时',left=790,right=1570)
local(f,2,1370,'失败：保存逐项错误\n超时：确认访问停止后才收敛','red',450)
msg(f,2,1,1535,'外部逐项结果','green',True)
local(f,1,1610,'按 key 汇合两侧结果\n至少一端成功即该 key 成功','blue',680)
msg(f,1,0,1740,'返回逐 key 合并结果','blue',True)
f.box(70,1810,1880,105,'返回前，两路必须停止访问源 buffer；Wait 超时不等于底层传输停止。\n原生失败但外部成功：保留外部对象；批量结果按原始 key 下标还原。','gray',22)
export_expanded(f)

f=start('04-delete-sequence-split','删除时序：外部均执行，原生按 force 检查租约',
        '实线：调用；虚线：返回；红色：错误路径。原生缓存淘汰不触发此流程。',2470)
msg(f,0,1,270,'Remove(key, force) / BatchRemove')
local(f,1,305,'校验 key，保留 force 与原始下标\n等待同 key 前序写完成','blue',540)
msg(f,1,2,345,'Remove / BatchRemove','green')
local(f,2,375,'沿用 Put 的 tenant / key 映射\n构造 SDK 删除 key 列表','green',560)
msg(f,2,3,420,'DeleteAsync(keys)','green')
branch(f,455,345,'alt  SDK 接受请求：force=true / false 均提交外部删除')
msg(f,3,2,540,'TaskId','green',True)
local(f,2,560,'以 TaskId 构造 OperationHandle\n保存 SDK 上下文与批量下标','green',560)
msg(f,2,1,605,'OperationHandle','green',True)
sep(f,635,'else  提交失败，且没有在途任务')
msg(f,3,2,710,'提交错误','red',True)
msg(f,2,1,775,'提交错误（无句柄）','red',True)
msg(f,1,4,875,'原生 Remove(key, force) / BatchRemove','amber')
branch(f,910,425,'alt  force=false 且存在未过期读租约',left=430,right=1990)
msg(f,4,1,995,'OBJECT_HAS_LEASE：原生拒绝，外部仍已提交','red',True)
sep(f,1030,'else  force=false 且无有效租约',left=430,right=1990)
msg(f,4,1,1120,'正常删除结果 / 明确 NotFound / 其他错误','amber',True)
sep(f,1160,'else  force=true：跳过租约检查',left=430,right=1990)
msg(f,4,1,1260,'正常删除结果 / 明确 NotFound / 其他错误','amber',True)
branch(f,1380,490,'opt  已获得 OperationHandle：原生拒绝 / 失败也必须执行 Wait')
msg(f,1,2,1460,'OperationHandle::Wait()','green')
msg(f,2,3,1530,'Wait(TaskId, timeout)','green')
msg(f,3,2,1600,'任务结果 / 超时','green',True)
branch(f,1630,160,'opt  外部执行失败或超时',left=790,right=1570)
local(f,2,1680,'保留逐项错误 / 未确认状态\n不将外部失败报告为删除成功','red',470)
msg(f,2,1,1840,'外部逐项结果','green',True)
local(f,1,1930,'汇合两侧结果（见下表）','blue',680)
msg(f,1,0,2040,'返回汇合结果','blue',True)
f.box(70,2100,1880,175,'两侧已删 / 明确不存在 → 幂等成功\n原生租约拒绝 → 返回原生错误；外部已删则记录部分删除\n原生已删但外部失败 / 未确认 → 删除失败 / 部分失败\n原生结果不确定 → 保留逐侧状态，不报告完整成功','blue',22)
f.text(70,2320,1880,65,'KVClient 无 force 参数；force 仅作用于原生租约检查。超时任务须安全收敛，未知结果不盲重试。',21,C['gray'])
export_expanded(f)
