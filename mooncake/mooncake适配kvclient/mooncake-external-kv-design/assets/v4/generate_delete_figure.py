from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = ROOT.parent / 'generate_figures.py'
scope = {'__file__': str(source)}
exec(compile(source.read_text(encoding='utf-8').split("f=Figure('01-architecture'")[0],
             str(source), 'exec'), scope)
scope['ROOT'] = ROOT
Figure, C = scope['Figure'], scope['C']

f=Figure('04-delete',1400,930,'04  删除流程与失败分支',
         '先判断原生删除结果，再决定是否执行外部 Remove；每个 key 独立汇合。')
f.box(500,165,360,105,'同 key 前序操作收敛\n执行原生 Remove','blue',23)
f.arrow([(680,270),(680,320),(260,320),(260,455)],'green')
f.arrow([(680,270),(680,455)],'amber')
f.arrow([(680,270),(680,320),(1100,320),(1100,455)],'gray')
f.text(75,365,170,60,'成功 /\n明确 NotFound',20,C['green'])
f.text(705,365,190,60,'租约 / 权限等\n明确拒绝',20,C['amber'])
f.text(1120,365,240,60,'Timeout /\n结果不确定',20,C['gray'])
f.box(80,455,360,115,'执行外部 Remove\nWait 并汇合','green',23)
f.box(500,455,360,115,'不删除外部\n返回原生错误','amber',23)
f.box(920,455,360,115,'不盲删外部\n先确认原生结果','gray',23)
f.arrow([(260,570),(260,680)],'green')
f.box(80,680,360,115,'外部成功 / 不存在：成功\n外部失败：删除部分失败','green',21)
f.rect(500,680,780,115,'gray',True)
f.text(525,704,730,67,'Master 未记录对象，不代表外部没有对象。\n删除重试不得误删后来重建的对象。',23,align='left')
f.text(65,855,1270,35,'原生淘汰不透传删除；外部未确认删除时，不报告完整删除成功。',22,align='left')
f.export()
