from pathlib import Path
import json
import math
from html import escape
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = 'C:/Windows/Fonts/msyh.ttc'
C = {'ink': '#243746', 'blue': '#315b8a', 'blue_bg': '#edf3fa',
     'green': '#28705f', 'green_bg': '#edf7f2', 'amber': '#93651f',
     'amber_bg': '#fff6e7', 'gray': '#657583', 'gray_bg': '#f5f7f9'}


class Figure:
    def __init__(self, name, width, height, title, subtitle):
        self.name, self.width, self.height = name, width, height
        self.elements, self.shapes, self.labels = [], [], []
        self.text(50, 26, width-100, 44, title, 30, align='left')
        self.text(50, 80, width-100, 38, subtitle, 20, C['gray'], align='left')

    def base(self, typ, x, y, w, h, **kw):
        n = len(self.elements)+1
        e = dict(id=f'{self.name}_{n}', type=typ, x=x, y=y, width=w, height=h,
                 angle=0, strokeColor=C['ink'], backgroundColor='transparent',
                 fillStyle='solid', strokeWidth=2, strokeStyle='solid', roughness=0,
                 opacity=100, groupIds=[], frameId=None, roundness=None, seed=n*103,
                 version=1, versionNonce=n*109, isDeleted=False, boundElements=None,
                 updated=1, link=None, locked=False)
        e.update(kw)
        self.elements.append(e)
        return e

    def text(self, x, y, w, h, value, size=23, color=None, align='center'):
        e = self.base('text', x,y,w,h, text=value, originalText=value,
                      fontSize=size, fontFamily=3, textAlign=align, verticalAlign='top',
                      autoResize=False, lineHeight=1.3, containerId=None,
                      strokeColor=color or C['ink'])
        self.labels.append(e)
        return e

    def rect(self, x,y,w,h, color='gray', dashed=False):
        return self.base('rectangle',x,y,w,h,strokeColor=C[color],
                         backgroundColor=C[color+'_bg'],
                         strokeStyle='dashed' if dashed else 'solid')

    def box(self,x,y,w,h,label,color='blue',size=23):
        self.rect(x,y,w,h,color)
        lines=label.count('\n')+1
        th=lines*size*1.3
        self.text(x+12,y+(h-th)/2,w-24,th,label,size,C[color])

    def arrow(self, points, color='gray', dashed=False, head=True):
        xs,ys=zip(*points)
        x,y=min(xs),min(ys)
        return self.base('arrow' if head else 'line',x,y,max(xs)-x,max(ys)-y,
                         points=[[px-x,py-y] for px,py in points],
                         startArrowhead=None, endArrowhead='arrow' if head else None,
                         startBinding=None,endBinding=None,
                         strokeColor=C[color],strokeStyle='dashed' if dashed else 'solid')

    def export(self):
        for e in self.labels:
            font=ImageFont.truetype(FONT,e['fontSize'])
            for line in e['text'].splitlines():
                assert font.getlength(line) <= e['width']-2, (self.name,line,e['width'])
            assert len(e['text'].splitlines())*e['fontSize']*1.3 <= e['height']+1
            assert 0 <= e['x'] and e['x']+e['width'] <= self.width
            assert 0 <= e['y'] and e['y']+e['height'] <= self.height
        # All labels occupy separate boxes; containment in the intended shape is allowed.
        for i,a in enumerate(self.labels):
            for b in self.labels[i+1:]:
                iw=min(a['x']+a['width'],b['x']+b['width'])-max(a['x'],b['x'])
                ih=min(a['y']+a['height'],b['y']+b['height'])-max(a['y'],b['y'])
                assert iw <= 0 or ih <= 0, (self.name,a['text'],b['text'])
        scene=dict(type='excalidraw',version=2,source='https://excalidraw.com',
                   elements=self.elements,appState={'viewBackgroundColor':'#ffffff'},files={})
        out=ROOT/(self.name+'.excalidraw')
        if out.exists():
            old=json.loads(out.read_text(encoding='utf-8'))
            assert all(e['id'].startswith(self.name+'_') and e.get('version')==1
                       for e in old['elements']), f'Preserve edited diagram: {out}'
        out.write_text(json.dumps(scene,ensure_ascii=True,indent=2),encoding='utf-8')
        image=Image.new('RGB',(self.width,self.height),'white')
        draw=ImageDraw.Draw(image)
        svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" viewBox="0 0 {self.width} {self.height}">',
             '<rect width="100%" height="100%" fill="white"/>']
        for e in self.elements:
            x,y,w,h=e['x'],e['y'],e['width'],e['height']
            color=e['strokeColor']
            if e['type']=='rectangle':
                draw.rectangle((x,y,x+w,y+h),fill=e['backgroundColor'])
                if e['strokeStyle']=='dashed':
                    self.dash(draw,(x,y),(x+w,y),color)
                    self.dash(draw,(x+w,y),(x+w,y+h),color)
                    self.dash(draw,(x+w,y+h),(x,y+h),color)
                    self.dash(draw,(x,y+h),(x,y),color)
                else:
                    draw.rectangle((x,y,x+w,y+h),outline=color,width=2)
                dash=' stroke-dasharray="9 7"' if e['strokeStyle']=='dashed' else ''
                svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{e["backgroundColor"]}" stroke="{color}" stroke-width="2"{dash}/>')
            elif e['type']=='text':
                font=ImageFont.truetype(FONT,e['fontSize'])
                for j,line in enumerate(e['text'].splitlines()):
                    linew=font.getlength(line)
                    tx=x if e['textAlign']=='left' else x+(w-linew)/2
                    ty=y+j*e['fontSize']*1.3
                    draw.text((tx,ty),line,font=font,fill=color,anchor='lt')
                    anchor='start' if e['textAlign']=='left' else 'middle'
                    sx=x if anchor=='start' else x+w/2
                    svg.append(f'<text x="{sx}" y="{ty+e["fontSize"]}" font-family="Microsoft YaHei, sans-serif" font-size="{e["fontSize"]}" text-anchor="{anchor}" fill="{color}">{escape(line)}</text>')
            else:
                pts=[(x+px,y+py) for px,py in e['points']]
                for a,b in zip(pts,pts[1:]):
                    if e['strokeStyle']=='dashed': self.dash(draw,a,b,color)
                    else: draw.line([a,b],fill=color,width=2)
                dash=' stroke-dasharray="9 7"' if e['strokeStyle']=='dashed' else ''
                coords=' '.join(f'{px},{py}' for px,py in pts)
                svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
                if e.get('endArrowhead'):
                    a,b=pts[-2:]
                    ang=math.atan2(b[1]-a[1],b[0]-a[0])
                    wings=[(b[0]-12*math.cos(ang+t),b[1]-12*math.sin(ang+t)) for t in (-.43,.43)]
                    draw.line([wings[0],b,wings[1]],fill=color,width=2)
                    svg.append(f'<polyline points="{wings[0][0]},{wings[0][1]} {b[0]},{b[1]} {wings[1][0]},{wings[1][1]}" fill="none" stroke="{color}" stroke-width="2"/>')
        image.save(ROOT/(self.name+'.png'))
        (ROOT/(self.name+'.svg')).write_text('\n'.join(svg+['</svg>']),encoding='utf-8')
        print(self.name, len(self.elements),'elements: layout checks passed')

    @staticmethod
    def dash(draw,a,b,color):
        length=math.dist(a,b)
        if not length:return
        for k in range(0,int(length),16):
            u,v=k/length,min(k+9,length)/length
            draw.line([(a[0]+(b[0]-a[0])*u,a[1]+(b[1]-a[1])*u),
                       (a[0]+(b[0]-a[0])*v,a[1]+(b[1]-a[1])*v)],fill=color,width=2)


f=Figure('01-architecture',1500,900,'01  接入位置与模块职责','实线表示调用；虚线框表示请求执行进程。外部对象不登记到 Master。')
f.rect(40,225,960,610,'gray',True)
f.text(450,242,520,36,'直接模式：推理进程 / Dummy 模式：Real 进程',20,align='left')
f.box(80,130,330,65,'应用 / DummyClient','gray')
f.box(80,350,330,115,'C++ Client\n双写与结果汇合','blue')
f.box(600,310,340,105,'原生对象路径\nMaster RPC / TE 传输','blue')
f.box(1120,155,320,95,'Master 服务\n目录 / 副本 / 租约','blue',22)
f.box(1120,310,320,105,'原生内存池','blue')
f.box(600,505,340,120,'KVClientBackend\n实现 ExternalStoreBackend','green',21)
f.box(600,705,340,95,'完整 KVClient SDK\nDHT / 注册 / 协议 / 完成','green',22)
f.box(1120,705,320,95,'外部 KV 存储','green')
f.arrow([(245,195),(245,350)],'gray')
f.text(90,285,135,32,'C++ / IPC',18)
f.arrow([(410,385),(490,385),(490,363),(600,363)],'blue')
f.arrow([(940,335),(1025,335),(1025,202),(1120,202)],'blue')
f.arrow([(940,363),(1120,363)],'blue')
f.text(1028,380,85,32,'TE 传输',20,C['blue'])
f.arrow([(410,430),(500,430),(500,565),(600,565)],'green')
f.text(335,490,155,62,'接口调用\n不经过 TE',20,C['green'])
f.arrow([(770,625),(770,705)],'green')
f.text(790,650,170,30,'SDK 公共 API',18,C['green'])
f.arrow([(940,752),(1120,752)],'green')
f.text(960,710,145,30,'请求 / 响应',19,C['green'])
f.text(70,850,1370,34,'Backend 是可替换实现：换成 DPUBackend 时，Client 仍调用同一组对象接口。',21,align='left')
f.export()

f=Figure('02-registration',1540,970,'02  注册与操作分离','注册按分配生命周期复用；每次 Put / Get 不重新注册。')
f.rect(40,160,1460,345,'gray',True)
f.text(65,175,600,34,'冷路径：初始化 / 显式注册 / 扩容',23,align='left')
f.box(80,235,280,78,'直接模式\n本进程有效内存','blue',22)
f.box(80,380,280,78,'Dummy 模式\n导出 IPC 共享句柄','blue',22)
f.box(430,380,280,78,'Real 导入\n取得本进程地址','blue',22)
f.box(800,280,280,120,'Client / RealClient\nTE + SDK 提前注册','green',22)
f.box(1180,280,280,120,'BufferRegistry\n区域 → SDK / MRHandle','green',21)
f.arrow([(360,274),(745,274),(745,315),(800,315)],'blue')
f.arrow([(360,419),(430,419)],'blue')
f.arrow([(710,419),(755,419),(755,365),(800,365)],'blue')
f.arrow([(1080,340),(1180,340)],'green')
f.rect(40,555,1460,325,'gray',True)
f.text(65,570,600,34,'热路径：复用已注册区域',23,align='left')
f.box(80,690,300,95,'Mooncake Slice\nptr + size','blue',22)
f.box(535,690,360,95,'Backend 查注册表\n校验设备 / 范围 / context','green',21)
f.box(1110,690,350,95,'构造 SDK 请求\nMRHandle + 有效数据范围','green',21)
f.arrow([(380,738),(535,738)],'green')
f.arrow([(895,738),(1110,738)],'green')
f.arrow([(1320,400),(1320,640),(715,640),(715,690)],'green',True)
f.text(930,602,300,30,'复用预注册记录',20,C['green'])
f.text(70,910,1400,34,'撤销顺序：停止新引用 → 等待在途访问结束 → 注销 MR → 关闭 IPC 映射。',21,align='left')
f.export()

f=Figure('03-dual-put',1520,1000,'03  双写时序：先外部提交，再原生 Put，最后 Wait','图示异步 backend；同步 backend 返回已完成的 OperationHandle，调用顺序相同。')
centers=[190,565,940,1315]
names=['引擎 / Connector','C++ Client','原生路径','ExternalStoreBackend']
colors=['gray','blue','blue','green']
for x,name,c in zip(centers,names,colors):
    f.box(x-145,150,290,70,name,c,21)
    f.arrow([(x,220),(x,890)],c,True,False)

def message(x1,x2,y,label,color='blue',dashed=False):
    f.arrow([(x1,y),(x2,y)],color,dashed)
    left,right=min(x1,x2),max(x1,x2)
    # White backing prevents intermediate lifelines from striking labels.
    font=ImageFont.truetype(FONT,20)
    width=font.getlength(label)+20
    f.base('rectangle',(left+right-width)/2,y-44,width,35,
           strokeColor='#ffffff',backgroundColor='#ffffff',strokeWidth=0)
    f.text(left+12,y-42,right-left-24,32,label,20,C[color])

f.rect(45,245,1420,75,'amber',True)
f.text(70,260,1370,40,'前置条件：源数据已就绪；相关内存已完成预注册。',24,C['amber'])
message(190,565,380,'① BatchPut(keys, slices)')
message(565,1315,465,'② 外部 BatchPut：启动任务','green')
message(1315,565,535,'返回 OperationHandle','green',True)
message(565,940,610,'③ 原生 BatchPut')
message(940,565,680,'原生逐项结果','blue',True)
message(565,1315,755,'④ OperationHandle.Wait()','green')
message(1315,565,825,'外部逐项结果；访问结束','green',True)
message(565,190,900,'⑤ 返回原生 Put 结果','blue',True)
f.text(65,945,1390,34,'原生失败也要等待已启动的外部任务；两路均停止访问后，调用方才能复用源内存。',21,align='left')
f.export()
