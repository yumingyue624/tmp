from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT.parent / 'generate_figures.py'
scope = {'__file__': str(source)}
exec(compile(source.read_text(encoding='utf-8').split("f=Figure('01-architecture'")[0],
             str(source), 'exec'), scope)
scope['ROOT'] = ROOT
Figure, C = scope['Figure'], scope['C']


def lanes(f):
    for x, title, color in [(60, 'Client：编排与结果汇合', 'blue'),
                             (610, 'ExternalStoreBackend / KVClient', 'green'),
                             (1160, '原生路径：Master + TE', 'amber')]:
        f.box(x, 145, 500, 65, title, color, 22)


f = Figure('03-dual-put-detailed', 1720, 1700,
           '双写：外部先提交，原生同步执行，返回前收敛两路访问',
           '适用于 Put / BatchPut；批量结果按原始 key 下标汇合，单对象独立实现。')
lanes(f)
f.box(60, 250, 500, 110, '入口校验与同 key 排序\n前序操作收敛后再开始本次双写', 'blue', 22)
f.box(610, 250, 1050, 110, '源数据已就绪且保持不变；相关内存已提前注册\nRealClient 完成 IPC 导入和地址转换；本次 Put 不临时注册、不复制 payload', 'gray', 22)
f.arrow([(310, 360), (310, 420)], 'blue')
f.box(60, 420, 500, 110, '调用 backend Put / BatchPut\n传入完整 key 与每个对象的 slices', 'blue', 22)
f.arrow([(560, 475), (610, 475)], 'green')
f.box(610, 420, 500, 110, '查注册句柄，转换 SDK 请求\n异步提交，返回 OperationHandle', 'green', 22)
f.box(1160, 420, 500, 110, '提交失败不覆盖原生结果\n部分已派发：必须保留可追踪句柄', 'gray', 22)
f.arrow([(310, 530), (310, 590)], 'blue')
f.box(60, 590, 500, 110, '执行原生同步 Put / BatchPut\n两个 BatchPut 重载只编排一次', 'blue', 22)
f.arrow([(560, 645), (580, 645), (580, 570), (1140, 570), (1140, 645), (1160, 645)], 'amber')
f.box(1160, 590, 500, 110, 'PutStart 分配 → TE 传输\n→ PutEnd 提交；记录逐项结果', 'amber', 22)
f.box(610, 590, 500, 110, '外部传输与原生路径重叠\n两路使用调用方源 buffer', 'green', 22)
f.arrow([(310, 700), (310, 760)], 'blue')
f.box(60, 760, 500, 110, '无论原生成败，收敛外部任务\n原生异常也不能绕过清理', 'blue', 22)
f.arrow([(560, 815), (610, 815)], 'green')
f.box(610, 760, 500, 110, '对已获得的句柄执行 Wait\n逐项记录外部完成结果', 'green', 22)
f.box(1160, 760, 500, 110, '未获得句柄且无在途任务\n记录外部提交失败，无需 Wait', 'gray', 22)
f.arrow([(310, 870), (310, 930)], 'blue')
f.box(60, 930, 500, 140, '返回门槛：两路停止访问源\n超时 ≠ 停止传输\n失败 / 超时仍需取得停止依据', 'blue', 22)
f.box(610, 930, 1050, 140, 'Wait 超时不能只移除任务后返回；MR 引用也不能阻止调用方复用 payload\nASU 达 maxErrorCount 触发断链，仍须确认清理后无在途访问\n停止访问保证及有界返回须按设备 / 协议验证', 'gray', 22)
f.arrow([(310, 1070), (310, 1140)], 'blue')
f.box(60, 1140, 1600, 155, '结果汇合：返回原生逐项结果，外部结果用于诊断 / 降级\n原生成、外部败：返回原生成功；原生败、外部成：返回原生失败，保留外部对象\n外部失败不自动回滚删除；筛选、重排后仍按原始 key 下标还原结果', 'blue', 23)
f.box(60, 1360, 1600, 130, '布局与设备边界\n多 slices 按序构成同一对象，检查总长度、协议对齐与请求上限；不越界补齐\n设备组合不允许并发访问时需串行或禁用；原生 staging 不等于外部可用注册源', 'gray', 22)
f.text(60, 1560, 1600, 70, '安全约束：只有确认两路均不再访问源，调用方才能修改、复用或释放 buffer。', 24, C['ink'])
f.export()

f = Figure('04-delete-detailed', 1720, 1760,
           '显式删除：两种 force 均先提交外部删除，原生侧按租约处理',
           '适用于 Remove / BatchRemove；原生缓存淘汰不触发外部删除。')
lanes(f)
f.box(60, 250, 500, 110, '输入 key(s)、force（默认 false）\n同 key 前序写入先收敛', 'blue', 22)
f.box(610, 250, 1050, 110, 'Query / GetReplicaList 通过 GrantLease 授予短期读租约\nforce 仅控制原生租约检查；KVClient DeleteAsync 没有 force 参数', 'gray', 22)
f.arrow([(310, 360), (310, 420)], 'blue')
f.box(60, 420, 500, 110, '先调用 backend Remove / BatchRemove\nforce=true / false 均执行', 'blue', 22)
f.arrow([(560, 475), (610, 475)], 'green')
f.box(610, 420, 500, 110, '转换 key，提交 DeleteAsync\n返回句柄；部分提交也须可追踪', 'green', 22)
f.box(1160, 420, 500, 110, 'false 不跳过外部删除\n避免默认 Remove 残留外部缓存', 'gray', 22)
f.arrow([(310, 530), (310, 590)], 'blue')
f.box(60, 590, 500, 110, '再执行原生同步 Remove(key, force)\n记录原生结果', 'blue', 22)
f.arrow([(560, 645), (580, 645), (580, 570), (1140, 570), (1140, 645), (1160, 645)], 'amber')
f.box(1160, 590, 500, 110, 'Master 执行原生删除\n按 force 决定是否检查读租约', 'amber', 22)
f.box(610, 590, 500, 110, '外部删除可能已经完成\n不能依据原生拒绝结果撤回', 'green', 22)
f.arrow([(1410, 700), (1410, 755)], 'amber')
f.arrow([(1410, 730), (310, 730), (310, 755)], 'amber')
f.arrow([(860, 730), (860, 755)], 'amber')
f.box(60, 755, 500, 150, 'force=false，存在未过期租约\n原生拒绝：OBJECT_HAS_LEASE\n外部仍执行，可能形成部分删除', 'amber', 22)
f.box(610, 755, 500, 150, 'force=false，无有效租约\n正常执行原生删除\n不存在则记录明确 NotFound', 'amber', 22)
f.box(1160, 755, 500, 150, 'force=true\n跳过租约检查，执行原生删除\n可能影响在途原生读取', 'amber', 22)
f.arrow([(310, 905), (310, 965)], 'blue')
f.arrow([(860, 905), (860, 935), (310, 935)], 'blue', head=False)
f.arrow([(1410, 905), (1410, 935), (860, 935)], 'blue', head=False)
f.box(60, 965, 500, 115, '原生成功、拒绝、失败或异常\n均需收敛已提交的外部任务', 'blue', 22)
f.arrow([(560, 1022), (610, 1022)], 'green')
f.box(610, 965, 500, 115, '有句柄则 Wait，记录逐项结果\n无句柄且无在途任务则记录提交失败', 'green', 21)
f.box(1160, 965, 500, 115, '超时保留可追踪状态\n任务记录删除 ≠ 底层访问停止', 'gray', 22)
f.box(60, 1140, 1600, 205, '逐 key 汇合结果\n原生已删 / 明确不存在 + 外部已删 / 明确不存在 → 幂等成功\n原生因租约 / 权限拒绝 → 返回原生错误；外部已删则记录部分删除\n原生成功但外部失败 / 未确认 → 返回删除失败 / 部分失败\n原生超时或结果不确定 → 保留逐侧状态，不报告完整成功', 'blue', 23)
f.box(60, 1410, 1600, 150, '读删与重试边界\n外部读删并发安全由存储端保证；force=true 不允许回收仍被访问的资源\n原生拒绝不代表两侧均未删除；重试须防止迟到删除误删重建对象\n对象代际 / 幂等保护未落实前，不对结果未知的删除进行盲重试', 'gray', 22)
f.text(60, 1630, 1600, 70, '观测要求：记录原生 / 外部状态、部分删除和未确认任务；批量结果保持原始下标。', 23, C['ink'])
f.export()
