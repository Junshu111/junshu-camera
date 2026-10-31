"""R4 logical 480×800 native Qt artwork. No reference photos or simulated values."""
import math
from PyQt5.QtCore import Qt,QRectF,QPointF
from PyQt5.QtGui import QColor,QFont,QFontDatabase,QFontMetricsF,QPainterPath,QPen

WHITE='#f5f5f7';MUTED='#a9a9b0';YELLOW='#ffd60a';RED='#ff6961'

class Artist:
    def __init__(self,painter,targets=None,dx=0):self.p=painter;self.targets=targets if targets is not None else {};self.dx=dx
    def rect(self,xy,fill=None,radius=0,outline=None,width=1):
        x1,y1,x2,y2=xy
        self.p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        self.p.setPen(QPen(QColor(outline),width) if outline else Qt.NoPen)
        self.p.drawRoundedRect(QRectF(x1,y1,x2-x1,y2-y1),radius,radius)
    def text(self,x,y,text,size=22,color=WHITE,bold=False,align='left'):
        f=QFontDatabase.systemFont(QFontDatabase.GeneralFont);f.setPixelSize(round(size));f.setBold(bold);self.p.setFont(f)
        fm=QFontMetricsF(f);b=fm.tightBoundingRect(str(text));w=fm.horizontalAdvance(str(text))
        left=x if align=='left' else x-w if align=='right' else x-w/2
        self.p.setPen(QColor(color));self.p.drawText(QPointF(left,y-(b.top()+b.bottom())/2),str(text))
    def line(self,points,color=WHITE,width=2):
        self.p.setPen(QPen(QColor(color),width,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        for a,b in zip(points,points[1:]):self.p.drawLine(QPointF(*a),QPointF(*b))
    def circle(self,x,y,r,fill=None,outline=None,width=1):
        self.p.setBrush(QColor(fill) if fill else Qt.NoBrush);self.p.setPen(QPen(QColor(outline),width) if outline else Qt.NoPen)
        self.p.drawEllipse(QRectF(x-r,y-r,2*r,2*r))
    def target(self,key,rect):self.targets[key]=(rect[0]+self.dx,rect[1],rect[2]+self.dx,rect[3])
    def glass(self,xy,alpha=175,radius=16):self.rect(xy,QColor(12,12,14,alpha),radius)
    def icon(self,kind,x,y,color=WHITE,size=24):
        s=size/24
        def line(coords,width=2):self.line([(x+a*s,y+b*s) for a,b in coords],color,width*s)
        if kind=='check':line([(-9,0),(-2,7),(11,-8)],2.4)
        elif kind=='back':line([(5,-9),(-4,0),(5,9)])
        elif kind=='next':line([(-4,-8),(4,0),(-4,8)])
        elif kind in ('plus','minus'):
            line([(-7,0),(7,0)])
            if kind=='plus':line([(0,-7),(0,7)])
        elif kind=='more':
            for a in (-8,0,8):self.circle(x+a*s,y,2.5*s,color)
        elif kind=='grid':
            for i in (-1,1):
                for j in (-1,1):self.rect((x+i*6*s-3*s,y+j*6*s-3*s,x+i*6*s+3*s,y+j*6*s+3*s),None,1,color,1.5)
        elif kind=='trash':
            line([(-7,-5),(-6,10),(6,10),(7,-5)]);line([(-9,-7),(9,-7)]);line([(-4,-7),(-4,-10),(4,-10),(4,-7)])
            line([(-2,-2),(-2,6)],1.4);line([(3,-2),(3,6)],1.4)
        elif kind=='lock':
            self.rect((x-7*s,y-1*s,x+7*s,y+10*s),None,2,color,1.7)
            path=QPainterPath();path.moveTo(x-4*s,y-1*s);path.cubicTo(x-5*s,y-13*s,x+5*s,y-13*s,x+4*s,y-1*s)
            self.p.setPen(QPen(QColor(color),1.7*s));self.p.setBrush(Qt.NoBrush);self.p.drawPath(path)
        elif kind in ('aspect','format','photo'):
            self.rect((x-10*s,y-8*s,x+10*s,y+8*s),None,2,color,1.6)
            if kind=='photo':line([(-8,6),(-3,-1),(2,4),(5,0),(9,6)],1.2)
            elif kind=='format':line([(-6,-3),(6,-3)],1.3);line([(-6,2),(4,2)],1.3)
        elif kind in ('clock','play'):
            self.circle(x,y,10*s,None,color,1.7)
            if kind=='clock':line([(0,-6),(0,1),(5,4)],1.7)
            else:line([(-2,-5),(5,0),(-2,5),(-2,-5)],1.7)
        elif kind in ('brightness','sun'):
            self.circle(x,y,5*s,None,color,1.6)
            for i in range(8):
                t=i*math.pi/4;line([(8*math.cos(t),8*math.sin(t)),(11*math.cos(t),11*math.sin(t))],1.4)
        elif kind=='sound':
            line([(-9,-3),(-5,-3),(1,-8),(1,8),(-5,3),(-9,3),(-9,-3)],1.6)
            line([(5,-6),(8,-3),(8,3),(5,6)],1.5)
        elif kind=='power':
            self.circle(x,y+1*s,9*s,None,color,1.8);line([(0,-12),(0,0)],2.8)
        elif kind=='rotate':
            self.circle(x,y,8*s,None,color,1.6);line([(5,-10),(10,-10),(10,-5)],1.6)
        elif kind=='warning':
            line([(0,-11),(-11,9),(11,9),(0,-11)],1.8);line([(0,-4),(0,2)],2);self.circle(x,y+6*s,1*s,color)
    def button(self,key,label,xy,primary=False,danger=False):
        self.rect(xy,'#e9edef' if primary else '#2c2c2e',14)
        self.text((xy[0]+xy[2])/2,(xy[1]+xy[3])/2,label,20,'#161718' if primary else RED if danger else WHITE,align='center')
        self.target(key,xy)
    def toggle(self,x,y,on):
        self.rect((x,y,x+58,y+32),'#e1e3e6' if on else '#4b4b50',16)
        self.circle(x+(42 if on else 16),y+16,12,'#18181b' if on else WHITE)
    def image(self,image,xy,contain=True,radius=0):
        if image is None:return
        x,y,w,h=xy;self.p.save()
        if radius:
            clip=QPainterPath();clip.addRoundedRect(QRectF(x,y,w,h),radius,radius);self.p.setClipPath(clip)
        scale=min(w/image.width(),h/image.height()) if contain else max(w/image.width(),h/image.height())
        tw,th=image.width()*scale,image.height()*scale
        if not contain:self.p.setClipRect(QRectF(x,y,w,h),Qt.IntersectClip)
        self.p.drawImage(QRectF(x+(w-tw)/2,y+(h-th)/2,tw,th),image);self.p.restore()

def shutter_label(us):
    if us>=1000000:return '{:g} s'.format(us/1000000)
    return '1/{} s'.format(max(1,round(1000000/us)))

def draw_info(a,w):
    if w.info_amount<=0:return
    s=w.effective_settings;y=19.25-44*(1-w.info_amount)
    a.text(24,y,'JPG  {}  {}'.format(s.ratio,'M' if s.exposure_mode=='manual' else 'AUTO'),13)
    value=w.battery.get('percent');color=RED if value is not None and value<=15 else WHITE
    a.text(422,y,'—' if value is None else str(value)+'%',13,color,align='right')
    a.rect((432,y-7.25,456,y+7.25),None,3,color,1.3);a.rect((457,y-3.25,459,y+3.25),color,1)
    if value is not None:a.rect((435,y-4.25,435+18*value/100,y+4.25),color,1)
    if w.battery.get('charging'):a.line([(445,y-9.25),(441,y),(446,y),(442,y+9.25)],YELLOW,2.5)

def draw_controls(a,w):
    s=w.effective_settings;busy=w.busy
    a.glass((221,623,259,661),190,19);a.text(240,642,str(s.zoom)+'×',17,MUTED if busy else YELLOW,True,align='center')
    a.target('zoom',(212,614,268,670))
    if w.thumbnail is not None:a.image(w.thumbnail,(42,704,60,60),False,12)
    else:a.rect((42,704,102,764),'#202023',12);a.icon('photo',72,734,MUTED,26)
    a.rect((42,704,102,764),None,12,'#929296',.8);a.target('gallery',(42,704,102,764))
    disabled=busy or not w.live_ready
    a.circle(240,734,45,None,'#86868b' if disabled else WHITE,3);a.circle(240,734,37,'#47474b' if disabled else WHITE)
    if busy:
        a.p.setPen(QPen(QColor(WHITE),3));a.p.setBrush(Qt.NoBrush);a.p.drawArc(QRectF(222,716,36,36),int(-90*16),int(270*16))
    a.target('shutter',(195,689,285,779))
    a.glass((378,706,434,762),170,28);a.icon('more',406,734,MUTED if busy else WHITE,26);a.target('more',(378,706,434,762))
    if w.panel is None and w.panel_progress<.001:
        a.rect((470.5,379,473.5,421),'#dddddf',1.5);a.target('edge',(424,360,480,440))

def draw_histogram(a,w):
    if not w.effective_settings.histogram or not w.histogram_data:return
    from .framing import capture_rect
    x,y,fw,fh=capture_rect(w.effective_settings.ratio)
    x=x+fw-154;y=max(58,y+16)
    a.glass((x,y,x+138,y+88),175,12)
    values=w.histogram_data;peak=max(values) or 1
    for i,value in enumerate(values):
        bx=x+11+i*116/len(values);h=value/peak*55
        a.rect((bx,y+70-h,bx+max(.8,100/len(values)),y+70),WHITE)
    a.line([(x+10,y+72),(x+128,y+72)],'#89898e',.5);a.text(x+10,y+81,'Y',9,MUTED)

def draw_live(a,w):
    draw_info(a,w);draw_histogram(a,w);draw_controls(a,w)
    if w.effective_settings.ael:
        from .framing import capture_rect
        x,y,_,_=capture_rect(w.effective_settings.ratio);x+=16;y=max(58,y+16)
        a.glass((x,y,x+98,y+34),180,17);a.text(x+33,y+17,'AEL',17,YELLOW,True,align='center');a.icon('lock',x+73,y+17,YELLOW,18)
    if w.countdown_deadline is not None:
        remaining=max(1,w.countdown_remaining)
        a.text(240,365,str(remaining),82,WHITE,True,align='center');a.button('cancel_timer','取消自拍',(168,462,312,518))
    if w.busy:
        a.glass((136,59,344,113),205,27);a.text(240,86,'正在保存…' if w.capture.snapshot().phase=='saving' else '正在拍摄…',18,align='center')
    if w.panel is not None or w.panel_progress>.001:draw_panel(a,w)

def draw_panel(a,w):
    left=w.panel_left;dx=left-16
    a.p.save();a.p.translate(dx,0);b=Artist(a.p,a.targets,dx)
    b.glass((16,266.5,464,533.5),175,16);b.rect((6.5,379,9.5,421),'#dddddf',1.5)
    s=w.effective_settings;kind=w.panel or 'menu'
    if kind=='menu':
        for x in (16+448/3,16+896/3):b.line([(x,286.5),(x,513.5)],'#5b5b60',.6)
        b.line([(36,400),(444,400)],'#5b5b60',.6)
        cells=[('exposure','曝光方式','M' if s.exposure_mode=='manual' else 'AUTO'),('ev','EV','已锁定' if s.ael else '不可调' if s.exposure_mode=='manual' else '{:+.1f}'.format(s.ev)),('wb','白平衡','AUTO' if s.wb_mode=='auto' else str(s.wb_kelvin)+' K'),('hdr','色彩模式','HDR' if s.hdr else '正常'),('histogram','直方图','ON' if s.histogram else 'OFF'),('beauty','美颜','ON' if s.beauty else 'OFF')]
        for i,(key,label,value) in enumerate(cells):
            x=16+(i%3+.5)*448/3;y=309.5+(i//3)*133.5
            b.text(x,y,label,18,MUTED,align='center');b.text(x,y+41,value,25,YELLOW if key in ('hdr','histogram','beauty') and getattr(s,key) else MUTED if key=='ev' and (s.ael or s.exposure_mode=='manual') else WHITE,True,align='center')
            b.target('cell_'+key,(16+(i%3)*448/3,266.5+(i//3)*133.5,16+(i%3+1)*448/3,266.5+(i//3+1)*133.5))
    elif kind in ('exposure','wb'):
        b.text(32,300,'曝光方式' if kind=='exposure' else '白平衡',19,WHITE,True)
        labels=('自动','手动') if kind=='exposure' else ('自动','自定义');chosen=0 if (s.exposure_mode if kind=='exposure' else s.wb_mode)=='auto' else 1
        b.rect((212,274,452,330),QColor(80,80,84,100),12);b.rect((215+chosen*120,277,329+chosen*120,327),QColor(120,120,126,160),10)
        for i,label in enumerate(labels):b.text(272+i*120,302,label,18,WHITE if i==chosen else MUTED,align='center');b.target('mode_'+str(i),(212+i*120,274,332+i*120,330))
        if kind=='exposure':
            manual=s.exposure_mode=='manual'
            parameters=[('shutter_us','快门速度',shutter_label(s.shutter_us),128),('iso','ISO',str(s.iso),352)] if manual else [('iso_max','最高 ISO',str(s.iso_max),128),('slowest_us','最慢快门',shutter_label(s.slowest_us),352)]
            for key,label,value,x in parameters:
                b.text(x,354,label,17,MUTED,align='center')
                for d,sign in [(-72,-1),(72,1)]:
                    b.circle(x+d,402,20,'#343437');b.icon('minus' if sign<0 else 'plus',x+d,402,MUTED if s.ael else WHITE,20)
                    if not s.ael:b.target(key+('_minus' if sign<0 else '_plus'),(x+d-28,374,x+d+28,430))
                b.text(x,402,value,23,MUTED if s.ael else WHITE,align='center')
                if not s.ael:b.target('value_'+key,(x-40,374,x+40,430))
            if manual:b.text(32,492,'ISO 为参考标度',15,MUTED)
            else:
                b.icon('lock',40,490,YELLOW if s.ael else WHITE,20);b.text(60,490,'AEL',19,YELLOW if s.ael else WHITE);b.toggle(132,474,s.ael);b.target('ael',(116,462,202,518));b.text(222,490,'先解锁再调节' if s.ael else '跨拍保持',15,MUTED)
        else:
            manual=s.wb_mode=='manual';b.text(240,363,str(s.wb_kelvin)+' K' if manual else 'AUTO',31,align='center')
            if manual:
                for i in range(321):
                    t=i/320;b.line([(80+i,405),(80+i,412)],QColor(round(240-134*t),round(165+32*t),round(82+159*t)),1)
                x=80+(s.wb_kelvin-2800)/5200*320;x=max(80,min(400,x));b.circle(x,409,10,WHITE,'#151515',1.5)
                b.text(80,440,'低色温',13,MUTED);b.text(400,440,'高色温',13,MUTED,align='right');b.target('temperature',(60,382,420,438))
                for x,sign in [(56,-1),(136,1)]:b.circle(x,494,20,'#343437');b.icon('minus' if sign<0 else 'plus',x,494,WHITE,20);b.target('wb_'+('minus' if sign<0 else 'plus'),(x-28,466,x+28,522))
                b.text(203,494,'实时预览',15,MUTED)
            else:b.text(240,412,'相机自动调整白平衡',18,MUTED,align='center');b.text(32,494,'勾选确认并返回',15,MUTED)
    elif kind=='ev':
        b.text(32,300,'曝光补偿',19,WHITE,True);b.text(240,346,'{:+.1f} EV'.format(s.ev),34,align='center')
        for i in range(33):
            x=48+i*12;h=22 if i%8==0 else 11;b.line([(x,383),(x,383+h)],YELLOW if abs(i-16-s.ev*8)<.7 else '#b0b0b5',2 if i%8==0 else 1)
        for i,label in enumerate(['−2','−1','0','+1','+2']):b.text(48+i*96,428,label,15,MUTED,align='center')
        b.target('ev_slider',(32,374,448,438))
        for x,sign in [(56,-1),(136,1)]:b.circle(x,494,20,'#343437');b.icon('minus' if sign<0 else 'plus',x,494,WHITE,20);b.target('ev_'+('minus' if sign<0 else 'plus'),(x-28,466,x+28,522))
        b.text(203,494,'实时预览',15,MUTED)
    if kind!='menu':b.rect((396,466,452,522),'#e9edef',14);b.icon('check',424,494,'#161718',29);b.target('confirm_panel',(396,466,452,522))
    a.p.restore()

def sheet(a,title='',top=220,back='更多'):
    a.glass((0,top,480,824),166,26);a.rect((218,top+12,262,top+15),'#77777d',1.5)
    a.icon('back',26,top+53,YELLOW);a.text(44,top+53,back,18,YELLOW);a.target('back',(0,top+25,130,top+81))
    if title:a.text(260,top+53,title,23,WHITE,True,align='center')

def draw_more(a,w):
    draw_info(a,w);sheet(a,top=178,back='取景');s=w.settings
    if w.more_group==0:
        rows=[('ratio','照片比例',s.ratio,'aspect'),('format','文件格式','JPEG','format'),('timer','自拍延时','关闭' if not s.self_timer else str(s.self_timer)+' 秒','clock'),('review_setting','拍后回显','关闭' if not s.review_seconds else str(s.review_seconds)+' 秒','play'),('fullscreen','全屏取景','ON' if s.fullscreen else 'OFF','aspect'),('sound','提示音','ON' if s.sound else 'OFF','sound')]
    else:rows=[('brightness','屏幕亮度',str(s.brightness)+'%','brightness'),('orientation','照片方向',str(s.rotation)+'°'+(' · 镜像' if s.mirror else ' · 不镜像'),'rotate'),('power','电源与退出','','power')]
    for i,(key,label,value,icon) in enumerate(rows):
        y=268+i*70;a.icon(icon,43,y+34,WHITE,23);a.text(70,y+34,label,21);a.text(444 if key in ('fullscreen','sound') else 414,y+34,value,18,YELLOW if value=='ON' else MUTED,align='right')
        if key not in ('fullscreen','sound'):a.icon('next',440,y+34,'#7c7c83',18)
        a.line([(70,y+68),(452,y+68)],'#3a3a3d',.6);a.target(key,(16,y,464,y+68))
    if w.more_group==0:a.text(28,724,'ON 沉浸取景 · OFF 黑色拍摄区',15,MUTED)
    else:a.text(28,621,'关机和退出均需再次确认',18,MUTED)
    a.text(240,770,'下滑返回上一组' if w.more_group else '上滑查看设备设置',16,MUTED,align='center')
    a.rect((470,500 if w.more_group else 285,472,635 if w.more_group else 420),'#626268',1)

def draw_setting_page(a,w):
    draw_info(a,w);s=w.settings;page=w.page
    if page=='ratio':
        sheet(a,'照片比例',300)
        for i,label in enumerate(['3:4','1:1','16:9','9:16']):
            x=28+(i%2)*218;y=398+(i//2)*160;a.rect((x,y,x+206,y+145),QColor(65,65,69,100),18)
            if label==s.ratio:a.rect((x,y,x+206,y+145),None,18,YELLOW,2)
            rw,rh=map(int,label.split(':'));ratio=rw/rh;fw,fh=(70,70/ratio) if ratio>=1 else (70*ratio,70)
            a.rect((x+103-fw/2,y+53-fh/2,x+103+fw/2,y+53+fh/2),None,3,YELLOW if label==s.ratio else '#c4c4c8',2);a.text(x+103,y+116,label,23,YELLOW if label==s.ratio else WHITE,align='center');a.target('ratio_'+label,(x,y,x+206,y+145))
        a.text(240,760,'未压暗区域为最终照片范围',17,MUTED,align='center')
    elif page in ('format','timer','review_setting'):
        title={'format':'文件格式','timer':'自拍延时','review_setting':'拍后回显'}[page];sheet(a,title,300)
        values=[('JPEG','JPEG'),('RAW','RAW'),('JPEG+RAW','JPEG + RAW')] if page=='format' else [(str(n),'关闭' if n==0 else str(n)+' 秒') for n in ([0,3,5,10] if page=='timer' else [0,3,10])]
        chosen='JPEG' if page=='format' else str(s.self_timer if page=='timer' else s.review_seconds)
        for i,(key,label) in enumerate(values):
            y=390+i*72;a.rect((24,y,456,y+64),QColor(65,65,69,100),14);a.text(46,y+32,label,23,YELLOW if key==chosen else WHITE)
            if key==chosen:a.icon('check',423,y+32,YELLOW)
            a.target('choice_'+key,(24,y,456,y+64))
    elif page=='brightness':
        sheet(a,'屏幕亮度',360);a.icon('sun',240,481,YELLOW,62);a.text(240,557,str(s.brightness)+'%',49,align='center')
        a.line([(60,641),(420,641)],'#4a4a4e',7);x=60+(s.brightness-20)/80*360;a.line([(60,641),(x,641)],WHITE,7);a.circle(x,641,16,WHITE)
        a.text(60,688,'暗',18,MUTED);a.text(420,688,'亮',18,MUTED,align='right');a.text(240,753,'下次启动保留当前亮度',17,MUTED,align='center');a.target('brightness_slider',(40,613,440,669))
        if not w.hardware_caps.get('brightness'):a.text(240,706,'背光接口暂不可用',14,MUTED,align='center')
    elif page=='orientation':
        sheet(a,'照片方向',260);a.text(28,359,'顺时针旋转',21);a.rect((28,396,452,452),'#343437',13)
        for i,value in enumerate([0,90,180,270]):
            x=32+i*104
            if s.rotation==value:a.rect((x,400,x+104,448),'#636367',10)
            a.text(x+52,424,str(value)+'°',21,align='center');a.target('rotation_'+str(value),(x,396,x+104,452))
        a.text(28,512,'水平镜像',22);a.toggle(398,496,s.mirror);a.target('mirror',(28,480,460,552));a.line([(28,552),(452,552)],'#414145',1)
        a.text(28,609,'预览与照片同步变化',20);a.text(28,644,'屏幕保持竖屏',20);a.text(28,742,'当前：{}° · {}'.format(s.rotation,'镜像' if s.mirror else '不镜像'),17,MUTED)
    elif page=='power':
        sheet(a,'电源与退出',300);a.button('shutdown','正常关机',(28,418,452,486));a.button('exit','退出应用',(28,506,452,574));a.text(240,633,'选择后还需点击确认',18,MUTED,align='center');a.text(240,734,'退出应用会返回树莓派桌面',17,MUTED,align='center')

def draw_gallery(a,w):
    a.icon('back',26,42,YELLOW);a.text(44,42,'取景',20,YELLOW);a.target('gallery_back',(0,14,135,70));a.text(240,42,'已选 {} 张'.format(len(w.selected)) if w.select_mode else '照片',24,WHITE,True,align='center')
    if not w.photos:a.text(240,399,'正在读取…' if w.file_job else '还没有照片',27,align='center');return
    if w.gallery_mode=='grid':
        a.text(452,42,'取消' if w.select_mode else '选择',20,YELLOW,align='right');a.target('select_mode',(380,14,480,70));a.text(20,105,'照片',20,WHITE,True)
        if w.select_mode:a.text(452,105,'清空' if len(w.selected)==len(w.photos) else '全选',18,YELLOW,align='right');a.target('select_all',(370,77,480,133))
        start=w.gallery_scroll
        for index in range(start,min(len(w.photos),start+9)):
            local=index-start;x=16+(local%3)*152;y=140+(local//3)*160;record=w.photos[index];image=w.thumbnails.get(str(record.path))
            if image:a.image(image,(x,y,144,152),False,5)
            else:a.rect((x,y,x+144,y+152),'#252527',5)
            if w.select_mode:
                chosen=record.path in w.selected;a.circle(x+122,y+130,13,YELLOW if chosen else '#242426',YELLOW if chosen else WHITE,1.5)
                if chosen:a.icon('check',x+122,y+130,'#141414',16)
            a.target('photo_'+str(index),(x,y,x+144,y+152))
        if w.select_mode:
            if w.selected:
                a.button('delete_selected','删除',(28,706,208,768),danger=True)
                a.button('send','发送 {} 张'.format(len(w.selected)),(224,706,452,768),primary=True)
            else:a.rect((28,706,452,768),'#202023',14);a.text(240,737,'请选择照片',20,MUTED,align='center')
        else:a.text(240,746,'{} 张照片'.format(len(w.photos)),18,MUTED,align='center')
    else:
        a.p.save();a.p.setClipRect(QRectF(0,86,480,532));image=w.gallery_image
        if image:
            scale=min(480/image.width(),532/image.height())*w.view_zoom;fw,fh=image.width()*scale,image.height()*scale
            a.p.drawImage(QRectF(240-fw/2+w.pan_x,352-fh/2+w.pan_y,fw,fh),image)
        a.p.restore()
        if w.view_zoom>1.01:
            a.glass((140,93,340,147),210,27);a.text(240,120,'查看放大 {:.1f}×'.format(w.view_zoom),18,align='center');a.button('fit_photo','适合屏幕',(132,656,348,712))
        else:
            first=max(0,min(w.photo_index-3,len(w.photos)-7))
            for index in range(first,min(len(w.photos),first+7)):
                x=24+(index-first)*62;record=w.photos[index];image=w.thumbnails.get(str(record.path));a.image(image,(x,646,56,56),False,6)
                if index==w.photo_index:a.rect((x-2,644,x+58,704),None,8,YELLOW,2)
                a.target('film_'+str(index),(x-2,642,x+58,706))
        a.icon('grid',55,751,WHITE,26);a.target('grid',(22,717,88,785));a.text(160,751,'{} / {}'.format(w.photo_index+1,len(w.photos)),18,MUTED,align='center');a.button('send','发送',(252,722,360,780),primary=True);a.icon('trash',420,751,WHITE,28);a.target('delete_one',(386,717,454,785))

def draw_share(a,w):
    a.rect((0,0,480,800),'#09090b')
    a.icon('back',26,42,YELLOW);a.text(44,42,'照片',20,YELLOW);a.target('share_back_top',(0,14,135,70))
    a.text(240,42,'发送到手机',24,WHITE,True,align='center')
    a.text(240,103,'手机与相机连接同一 Wi-Fi',21,WHITE,True,align='center')
    a.text(240,136,'用手机相机扫码，在网页中选择并下载',16,MUTED,align='center')
    session=w.share.session
    if session is not None and w.share.qr is not None:
        image=w.share.qr
        # Exact native integer pixels, centered with its four-module quiet zone.
        a.p.drawImage((480-image.width())//2,168,image)
        status=session.status();seconds=max(0,int(status['seconds_left']))
        a.text(240,553,'{} 张原图 · {:02d}:{:02d} 后关闭'.format(status['count'],seconds//60,seconds%60),21,WHITE,True,align='center')
        if status['active_requests']:
            message='正在传送… 请保持此页面开启'
        elif status['downloads_finished']:
            message='已完成 {} 次传送，请在手机确认文件'.format(status['downloads_finished'])
        elif status['downloads_started']:
            message='传送已中断，可在手机重新下载'
        else:message='等待手机打开 · 扫码后还需点击下载'
        a.text(240,590,message,16,YELLOW,align='center')
        if w.share_address:a.text(240,622,w.share_address.interface+' · '+w.share_address.address,16,MUTED,align='center')
    elif w.share_error:
        a.circle(240,310,38,None,'#45454b',2);a.icon('warning',240,310,YELLOW,32)
        # Fixed small lines keep offline text legible on the 480px display.
        if 'Wi-Fi' in w.share_error:
            a.text(240,389,'暂时找不到可用的局域网',23,WHITE,True,align='center')
            a.text(240,429,'连接与手机相同的 Wi-Fi 后重试',18,MUTED,align='center')
        else:
            a.text(240,389,'发送暂未开启',23,WHITE,True,align='center')
            a.text(240,429,w.share_error[:25],16,MUTED,align='center')
        a.button('share_retry','重新检查网络',(112,491,368,551),primary=True)
    elif len(w.share_choices)>1 and w.share_address is None:
        a.text(240,230,'选择手机所在的网络',23,WHITE,True,align='center')
        for index,choice in enumerate(w.share_choices[:4]):
            a.button('share_address_'+str(index),choice.interface+' · '+choice.address,(28,280+76*index,452,340+76*index))
        a.text(240,618,'无法连接时返回此页换一个网络',16,MUTED,align='center')
    else:
        a.text(240,341,'正在准备照片…' if w.share.pending else '正在检查网络…',25,WHITE,True,align='center')
        a.text(240,385,'照片仍保留在相机中',18,MUTED,align='center')
    a.text(240,660,'下载到「文件 / 下载项」，解压后可存入照片',16,MUTED,align='center')
    a.text(240,686,'网页传送完成不代表已存入手机相册',15,MUTED,align='center')
    a.button('share_back','关闭发送并返回照片',(28,718,452,780))

def draw_review(a,w):
    a.image(w.gallery_image,(0,0,480,800),True);draw_info(a,w);draw_controls(a,w)
    a.glass((166,59,314,113),180,27);a.text(240,86,'已保存',19,align='center');a.target('end_review',(0,114,480,610))

def draw_status(a,w):
    error=w.page=='error';a.icon('warning' if error else 'power',240,260,YELLOW,64);a.text(240,359,'拍摄或保存失败' if error else '系统处理中',29,WHITE,True,align='center')
    lines=w.message.split('\n')
    for i,line in enumerate(lines[:6]):a.text(240,421+i*29,line[:28],17,MUTED,align='center')
    if error:a.button('ack_error','返回取景',(74,616,406,680),primary=True);a.text(240,725,'退出应用',19,YELLOW,align='center');a.target('exit',(160,697,320,753))
    else:a.text(240,650,'请等待系统正常结束',19,MUTED,align='center')

def draw_modal(a,w):
    a.rect((0,0,480,800),QColor(0,0,0,140));kind=w.modal
    delete=kind=='delete';height=340 if delete else 230;y=400-height/2
    a.rect((42,y,438,y+height),'#29292c',24)
    title={'delete':'是否确认删除','exit':'退出应用？','shutdown':'正常关机？','raw':'raw暂不支持','info':'提示'}.get(kind,'提示')
    a.text(240,y+43,title,25,WHITE,True,align='center')
    if delete and w.delete_plan:
        plan=w.delete_plan;a.text(240,y+86,'{} 张 · {:.2f} MB'.format(len(plan.paths),plan.total_bytes/1048576),18,align='center')
        for i,path in enumerate(plan.paths[w.delete_offset:w.delete_offset+4]):a.text(68,y+121+i*23,path.name,9,MUTED)
        if len(plan.paths)>4:a.text(68,y+213,'{}–{} / {} · 上下滑动查看清单'.format(w.delete_offset+1,min(len(plan.paths),w.delete_offset+4),len(plan.paths)),13,MUTED)
        a.text(68,y+243,'其余 {} 张及设置保留'.format(max(0,len(w.photos)-len(plan.paths))),16,MUTED)
    else:
        body={'exit':'保存完成后退出并返回桌面','shutdown':'等待照片和设置保存后正常关机','raw':'当前继续使用 JPEG 拍摄'}.get(kind,w.modal_body or '')
        for i,line in enumerate(body.split('\n')[:3]):a.text(240,y+99+i*26,line,17,MUTED,align='center')
    ay=y+height-70;a.line([(42,ay),(438,ay)],'#515155',.7)
    if kind in ('raw','info'):a.text(240,ay+35,'知道了',22,YELLOW,align='center');a.target('modal_cancel',(42,ay,438,ay+70))
    else:
        a.text(141,ay+35,'取消',22,YELLOW,align='center');a.text(339,ay+35,'删除' if delete else '确认',22,RED if delete else YELLOW,align='center');a.line([(240,ay),(240,ay+70)],'#515155',.7);a.target('modal_cancel',(42,ay,240,ay+70));a.target('modal_confirm',(240,ay,438,ay+70))
