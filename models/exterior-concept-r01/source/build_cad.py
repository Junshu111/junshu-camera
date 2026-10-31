from pathlib import Path
import sys, json, math
sys.path.insert(0,'/Applications/FreeCAD.app/Contents/Resources/lib')
import FreeCAD as A, Part
V=A.Vector
R=Path(__file__).resolve().parents[1]
P={'body_width':78.0,'body_length':125.0,'body_height':42.0,'vertical_corner_radius':7.5,'rim_edge_radius':3.5,
   'screen_glass_width':66.8,'screen_glass_length':113.8,'screen_corner_radius':5.1,
   'lens_outer_diameter':17.4,'lens_center_height':22.0,'knob_diameter':13.8,'knob_pitch':19.0,
   'lever_side_y':-38.0,'lever_center_height':26.0,'hatch_length':49.0,'hatch_height':25.0}
(R/'source/parameters.json').write_text(json.dumps(P,indent=2))
doc=A.newDocument('Reference_Camera_R01')
info=doc.addObject('App::FeaturePython','DesignScope')
for key,val in P.items():
 info.addProperty('App::PropertyLength',key,'Reference dimensions');setattr(info,key,val)
info.addProperty('App::PropertyString','Scope','Design note');info.Scope='Exterior reconstruction R01. Dimensions inferred, not measured. Internal hardware and manufacturing not designed.'
info.addProperty('App::PropertyString','Axes','Design note');info.Axes='X width; Y length; Z up. Lens front Y-, controls X-, hatch X+, rear slot Y+.'
objects=[]; meshes={}; qa=[]
def add(name,label,s,mat):
 if name.startswith(('ControlPlate','Knob','Lever')):
  s=s.copy();s.rotate(V(0,0,0),V(0,0,1),180)
 if name in ('DoorShadow','SideDoor','DoorFingerRecess'):
  s=s.copy();s.rotate(V(0,0,0),V(0,1,0),180);s.translate(V(0,0,37))
 s=s.removeSplitter()
 assert s.isValid(),name+' invalid'
 assert len(s.Solids)==1,(name,len(s.Solids))
 o=doc.addObject('PartDesign::Feature',name);o.Label=label;o.Shape=s
 o.addProperty('App::PropertyString','MaterialRole','Appearance');o.MaterialRole=mat
 objects.append(o)
 vv,ff=s.tessellate(.05)
 meshes[name]={'label':label,'material':mat,'vertices_mm':[[p.x,p.y,p.z] for p in vv],'faces_tri':ff}
 bb=s.BoundBox
 qa.append({'name':name,'valid':s.isValid(),'closed':s.isClosed(),'solids':len(s.Solids),'volume_mm3':s.Volume,
            'bounds_mm':[[bb.XMin,bb.YMin,bb.ZMin],[bb.XMax,bb.YMax,bb.ZMax]]})
 return s
def rounded(w,l,h,r,z=0):
 s=Part.makeBox(w,l,h,V(-w/2,-l/2,z))
 ee=[e for e in s.Edges if e.BoundBox.ZLength>h-.001]
 return s.makeFillet(r,ee)
def move(s,xyz):
 s=s.copy();s.translate(V(*xyz));return s
def rotate(s,axis,deg):
 s=s.copy();s.rotate(V(0,0,0),V(*axis),deg);return s
def xpanel(length,height,depth,r,x,y,z):
 # local X becomes global Y, local Y becomes Z, local Z becomes X.
 s=rounded(length,height,depth,r)
 s=rotate(s,(1,1,1),120)
 return move(s,(x,y,z))
def ypanel(width,height,depth,r,x,y,z):
 s=rounded(width,height,depth,r)
 s=rotate(s,(1,0,0),90) # local extrusion becomes -Y
 return move(s,(x,y,z))
W,L,H=P['body_width'],P['body_length'],P['body_height']
body=rounded(W,L,H,7.5)
edges=[e for e in body.Edges if e.BoundBox.ZLength<.001]
body=body.makeFillet(3.5,edges)
bottom=body.common(Part.makeBox(100,150,2.3,V(-50,-75,0)))
body=body.cut(Part.makeBox(100,150,2.55,V(-50,-75,0)))
body=body.cut(rounded(68.5,115.5,5,5.8,40.6))
body=body.cut(Part.makeCylinder(8.85,5,V(0,-64,22),V(0,1,0)))
body=body.cut(rotate(xpanel(64.8,20.8,4,10.25,38.4,-10,23),(0,0,1),180))
body=body.cut(move(rotate(xpanel(50,26,3,3.6,-40,-24,18.5),(0,1,0),180),(0,0,37)))
body=body.cut(ypanel(5.8,10.8,4,2.85,-25,64,8.5))
add('Housing','01 · 米白圆角机身',body,'ivory')
add('BottomCover','02 · 底部细缝与底板',bottom,'ivory')
add('BottomGasket','03 · 底缝暗线',rounded(73,120,.18,6.3,2.34),'rubber')
rim=rounded(68.15,115.15,1.0,5.65,40.7).cut(rounded(66.9,113.9,2,5.15,40.3))
add('DisplayRim','04 · 屏幕细金属边',rim,'silver')
glass=rounded(66.8,113.8,.7,5.1,40.95)
add('DisplayGlass','05 · 顶部黑色玻璃',glass,'display')
# Front, optical concentric rings: shallow recess instead of a protruding lens barrel.
axis=V(0,1,0)
ring=Part.makeCylinder(8.7,1.25,V(0,-62.65,22),axis).cut(Part.makeCylinder(7.65,2,V(0,-63,22),axis))
add('LensTrim','06 · 镜头银色边圈',ring,'silver')
barrel=Part.makeCylinder(7.6,2.9,V(0,-62.6,22),axis).cut(Part.makeCylinder(5.6,3.4,V(0,-63,22),axis))
add('LensBarrel','07 · 镜头黑色内圈',barrel,'black')
inner=Part.makeCylinder(5.65,.7,V(0,-61.1,22),axis).cut(Part.makeCylinder(4.7,1,V(0,-61.2,22),axis))
add('LensInner','08 · 内光圈',inner,'black')
glasslens=Part.makeSphere(6.1,V(0,-55.9,22)).common(Part.makeCylinder(4.7,2.2,V(0,-62.1,22),axis))
add('OpticalGlass','09 · 透镜表面',glasslens,'lens')
add('LensDarkCore','10 · 镜头底层',Part.makeCylinder(4.7,.4,V(0,-59.9,22),axis),'black')
# Three independently editable knurled control knobs.
add('ControlPlate','11 · 三旋钮凹入饰板',xpanel(64,20,0.75,9.9,38.5,-10,23),'satin')
for i,y in enumerate([-29.,-10.,9.],1):
 add('KnobBase'+str(i),'12-'+str(i)+' · 旋钮底座',Part.makeCylinder(7.25,.75,V(39.2,y,23),V(1,0,0)),'black')
 pts=[]
 for k in range(160):
  ang=2*math.pi*k/160;rad=6.9 if k%4 in (1,2) else 6.65
  pts.append(V(rad*math.cos(ang),rad*math.sin(ang),0))
 shape=Part.Face(Part.Wire(Part.makePolygon(pts+[pts[0]]).Edges)).extrude(V(0,0,3.2))
 shape=move(rotate(shape,(0,1,0),90),(39.8,y,23))
 add('KnobFluted'+str(i),'13-'+str(i)+' · 旋钮细齿边',shape,'silver')
 cap=Part.makeCylinder(6.35,.75,V(42.8,y,23),V(1,0,0))
 cap=cap.makeFillet(.3,cap.Edges)
 add('KnobCap'+str(i),'14-'+str(i)+' · 旋钮面',cap,'silver')
# Lever, pivot and orange tip, same side as the knobs.
add('LeverFoot','15 · 拨杆圆座',Part.makeCylinder(5.6,.85,V(38.9,38,26),V(1,0,0)),'satin')
add('LeverSocket','16 · 拨杆金属座',Part.makeCone(4.7,3.4,2.1,V(39.5,38,26),V(1,0,0)),'silver')
add('LeverBall','17 · 拨杆球头',Part.makeSphere(3.15,V(41.4,38,26)),'silver')
dv=V(.45,.55,-.70);dv.normalize();start=V(41.4,38,26)
add('LeverStem','18 · 金属拨杆',Part.makeCylinder(1.8,15.5,start,dv),'silver')
tip=Part.makeCylinder(3.8,8.6,start+dv*13.3,dv)
tip=tip.makeFillet(.85,tip.Edges)
add('LeverOrangeGrip','19 · 橙色握柄',tip,'orange')
# Opposite side access cover with a small recessed finger slot.
add('DoorShadow','20 · 侧盖细缝',xpanel(49.8,25.8,.45,3.5,-38.5,-24,18.5),'rubber')
door=xpanel(49,25,.8,3.2,-39,-24,18.5)
door=door.cut(xpanel(4.4,9.6,2,2.15,-39.5,-2.6,18.5))
add('SideDoor','21 · 齐平侧盖',door,'ivory')
add('DoorFingerRecess','22 · 侧盖指扣内凹',xpanel(4,9.2,.25,1.95,-38.15,-2.6,18.5),'black')
slotrim=ypanel(5.65,10.65,.4,2.8,-25,62.55,8.5).cut(ypanel(4.55,9.55,1,2.25,-25,62.8,8.5))
add('RearSlotRim','23 · 后端竖向开口边',slotrim,'silver')
add('RearSlotDark','24 · 后端竖向开口内侧',ypanel(4.5,9.5,.4,2.2,-25,61.5,8.5),'black')
doc.recompute()
doc.saveAs(str(R/'cad/Junshu_Reference_Camera_R01.FCStd'))
Part.export(objects,str(R/'cad/Junshu_Reference_Camera_R01.step'))
(R/'source/cad-meshes.json').write_text(json.dumps(meshes,ensure_ascii=False,separators=(',',':')))
volume=sum(o.Shape.Volume for o in objects)
reimport=Part.read(str(R/'cad/Junshu_Reference_Camera_R01.step'))
report={'parts':qa,'part_count':len(objects),'all_valid':all(q['valid'] and q['closed'] for q in qa),
 'step_reimport_valid':reimport.isValid(),'step_solids':len(reimport.Solids),'volume_before_mm3':volume,'volume_after_mm3':reimport.Volume,
 'scope':'External appearance only. Dimensions inferred. Not a manufacture-ready case; internal fit not verified.'}
assert reimport.isValid() and len(reimport.Solids)==len(objects)
assert abs(volume-reimport.Volume)<.1
(R/'checks/cad-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('CAD_READY',len(objects),volume)
