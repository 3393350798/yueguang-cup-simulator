"""生成教学赛场、纹理与路径。仅重建场景时需要 Pillow。"""
from pathlib import Path
import json
import math
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'worlds' / 'textures'
ASSETS.mkdir(parents=True, exist_ok=True)
SCALE = 0.0027
SIZE = 2.0

def xy(point):
    return [(point[0] - 437.5) * SCALE, (415 - point[1]) * SCALE]

def bezier(a, b, c, d, n=45):
    return [tuple((1-t)**3*a[j]+3*(1-t)**2*t*b[j]+3*(1-t)*t*t*c[j]+t**3*d[j]
                  for j in range(2)) for t in [i/n for i in range(1, n+1)]]

path = [(195, 670), (555, 670)]
def curve(b, c, d):
    path.extend(bezier(path[-1], b, c, d))

curve((595,670),(580,635),(609,650))
curve((635,668),(622,624),(648,618))
curve((678,617),(704,596),(691,570))
curve((678,548),(678,514),(704,483))
curve((735,449),(727,403),(690,370))
curve((664,350),(659,395),(638,383))
curve((611,372),(623,351),(662,331))
curve((699,316),(716,299),(716,242))
path += [(716,202),(402,202),(402,280)]
curve((402,321),(429,337),(470,337))
curve((507,337),(540,371),(540,420))
curve((540,451),(560,475),(598,475))
path += [(598,534),(519,534)]
entry_index = len(path)-1
path += [(363,534),(208,534),(325,416),(195,416),(195,190)]

points = [xy(p) for p in path]
circles = []
for row, py in [('right',202-0.25/SCALE), ('left',202+0.25/SCALE)]:
    for color, px in [('red',565-0.15/SCALE),('yellow',565),('blue',565+0.15/SCALE)]:
        circles.append(dict(side=row,color=color,center=xy((px,py))))
config = dict(floor_size=SIZE, line_width=0.0225, wheel_radius=0.027,
              axle_length=0.125, body_size=[0.15,0.13], start=xy((195,670)),
              finish=xy((195,190)), pickup=xy((632,202)), marker=xy((565,202)),
              tag=xy((715,141)), circles=circles,
              channel=[xy((363,475)),xy((519,594))],
              tag_family='tag36h11', tag_size=0.08,
              geometry_note='教学近似布局；以正式 DWG 更新坐标，标签/挡板布置为练习配置')
(ROOT/'scene.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

im = Image.new('RGB',(2048,2048),'white')
draw=ImageDraw.Draw(im)
def pix(p):
    return ((p[0]/SIZE+0.5)*2048,(0.5-p[1]/SIZE)*2048)
def line(poly,width=0.0225,fill='black'):
    draw.line([pix(p) for p in poly],fill=fill,width=round(width/SIZE*2048),joint='curve')
def box(center,side,width=0.0225):
    x,y=center;h=side/2
    line([(x-h,y-h),(x+h,y-h),(x+h,y+h),(x-h,y+h),(x-h,y-h)],width)

line(points[:entry_index+1])
line(points[entry_index+1:])
# 区域轮廓为浅色虚线，贯穿区域的循迹线留空。
a,b=config['channel'];x0,x1=a[0],b[0];y0,y1=b[1],a[1]
for y in [y0,y1]:
    x=x0
    while x<x1:
        line([(x,y),(min(x+.025,x1),y)],.004,'#c1c9d4');x+=.045
line([(x0,y0),(x0,y1)],.0225)
for circle in circles:
    cx,cy=pix(circle['center']);r=.04375/SIZE*2048
    draw.ellipse([cx-r,cy-r,cx+r,cy+r],fill={'red':'#f02b37','yellow':'#eed609','blue':'#2460ec'}[circle['color']])
    r=.02125/SIZE*2048
    draw.ellipse([cx-r,cy-r,cx+r,cy+r],fill='white')
box(config['start'],.2275)
box(config['finish'],.2275)
box(config['pickup'],.060,.009)
mx,my=config['marker'];line([(mx,my-.08),(mx,my+.08)])
for key,letter in [('start','S'),('finish','F')]:
    draw.text(pix(config[key]),letter,fill='#334957',font=ImageFont.load_default(size=56),anchor='mm')
im.save(ASSETS/'track.png')
window_assets=ROOT/'plugins/robot_windows/moonlight'
window_assets.mkdir(parents=True,exist_ok=True)
im.save(window_assets/'track.png')
(window_assets/'scene.js').write_text('export default '+json.dumps(config,ensure_ascii=False,indent=2)+';\n',encoding='utf-8')

import cv2
dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
for tag_id in (0,1):
    marker=cv2.aruco.generateImageMarker(dictionary,tag_id,256)
    tag=Image.new('RGB',(320,320),'white')
    tag.paste(Image.fromarray(marker).convert('RGB'),(32,32))
    tag=tag.resize((512,512),Image.Resampling.NEAREST)
    tag.save(ASSETS/f'tag{tag_id}.png')

def v(values):return ' '.join(f'{x:.7g}' for x in values)
def material(color):return f'PBRAppearance {{ baseColor {color} roughness 0.85 metalness 0 }}'
def solid_box(name,center,size,color,rotation='0 0 1 0',bounding=True):
    return f'''DEF {name} Solid {{ name "{name}" translation {v(center)} rotation {rotation}
children [ Shape {{ appearance {material(color)} geometry DEF {name}_GEOM Box {{ size {v(size)} }} }} ]
{'boundingObject USE '+name+'_GEOM' if bounding else ''}
}}'''

parts=['''#VRML_SIM R2025a utf8
WorldInfo { basicTimeStep 32 coordinateSystem "ENU" contactProperties [ ContactProperties { material1 "wheel" coulombFriction [ 1 ] bumpSound "" rollSound "" slideSound "" } ContactProperties { bumpSound "" rollSound "" slideSound "" } ] }
Viewpoint { orientation -0.57735027 0.57735027 0.57735027 2.0943951 position 0 0 6.1 fieldOfView 0.7 }
Background { skyColor [ 0.91 0.94 0.97 ] }
DirectionalLight { direction -0.3 -0.4 -1 intensity 1.3 ambientIntensity 0.65 castShadows TRUE }
DEF FLOOR Solid {
  translation 0 0 -0.005
  children [ Shape { appearance PBRAppearance {
    baseColorMap ImageTexture { url [ "textures/track.png" ] filtering 5 }
    roughness 1 metalness 0
  } geometry Plane { size 2 2 } } ]
  boundingObject Plane { size 2 2 }
}
''']
for i,y in enumerate([y0-.006,y1+.006]):
    parts.append(solid_box(f'WALL_{i}',[(x0+x1)/2,y,.065],[x1-x0,.012,.14],'0.34 0.42 0.54'))
# 立牌图案平面默认位于局部 XY，将法线转向南侧。
tx,ty=config['tag']
parts.append(f'''DEF TAG Solid {{ name "tag" translation {tx} {ty} 0.125 rotation 1 0 0 1.5707963
children [ Shape {{ appearance PBRAppearance {{ baseColorMap DEF TAG_TEXTURE ImageTexture {{ url [ "textures/tag0.png" ] }} roughness 1 metalness 0 }} geometry Plane {{ size 0.1 0.1 }} }} ] }}''')
bx,by=config['pickup']
parts.append(f'''DEF BLOCK Solid {{ name "block" translation {bx} {by} 0.035
children [ Shape {{ appearance DEF BLOCK_MATERIAL PBRAppearance {{ baseColor 0.9 0.05 0.08 roughness 0.7 metalness 0 }} geometry Cylinder {{ radius 0.028 height 0.07 }} }} ]
boundingObject Cylinder {{ radius 0.028 height 0.07 }} physics Physics {{ density -1 mass 0.04 }}
}}''')
sx,sy=config['start']
parts.append(f'''DEF CAR Robot {{ translation {sx} {sy} 0.032
name "Moonlight" controller "moonlight" supervisor TRUE window "moonlight"
children [
Shape {{ appearance {material('0.08 0.16 0.25')} geometry Box {{ size 0.15 0.105 0.045 }} }}
Transform {{ translation -0.015 0 0.034 children [ Shape {{ appearance {material('0.1 0.68 0.63')} geometry Box {{ size 0.075 0.085 0.022 }} }} ] }}
Transform {{ translation 0.095 0 0 children [ Shape {{ appearance {material('0.98 0.59 0.18')} geometry Box {{ size 0.06 0.012 0.012 }} }} ] }}
Transform {{ translation 0.12 0.028 0 children [ Shape {{ appearance {material('0.98 0.59 0.18')} geometry Box {{ size 0.012 0.055 0.018 }} }} ] }}
Transform {{ translation 0.12 -0.028 0 children [ Shape {{ appearance {material('0.98 0.59 0.18')} geometry Box {{ size 0.012 0.055 0.018 }} }} ] }}
Camera {{ translation 0.05 0 0.075 rotation 0 1 0 0.15 name "camera" width 320 height 240 fieldOfView 1.25 near 0.01 far 3 }}
''')
for side,y in [('left',.0625),('right',-.0625)]:
    parts.append(f'''HingeJoint {{ jointParameters HingeJointParameters {{ axis 0 1 0 anchor 0 {y} -0.005 }}
device [ RotationalMotor {{ name "{side}_motor" maxVelocity 18 maxTorque 0.12 sound "" }} PositionSensor {{ name "{side}_encoder" }} ]
endPoint Solid {{ name "{side}_wheel" translation 0 {y} -0.005 rotation 1 0 0 -1.5707963 contactMaterial "wheel"
children [ Shape {{ appearance {material('0.12 0.13 0.16')} geometry Cylinder {{ radius 0.027 height 0.014 }} }} ]
boundingObject Cylinder {{ radius 0.027 height 0.014 }} physics Physics {{ density -1 mass 0.06 }}
}} }}''')
parts.append(f'''Solid {{ name "caster" translation -0.055 0 -0.017 children [ Shape {{ appearance {material('0.7 0.73 0.78')} geometry Sphere {{ radius 0.013 }} }} ] boundingObject Sphere {{ radius 0.013 }} physics Physics {{ density -1 mass 0.01 }} contactMaterial "caster" }}''')
for i,y in enumerate([.036,.024,.012,0,-.012,-.024,-.036]):
    parts.append(f'''DistanceSensor {{ translation 0.062 {y} -0.02 rotation 0 1 0 1.5707963 name "ir{i}" type "infra-red" lookupTable [ 0 1000 0, 0.03 1000 0, 0.045 0 0, 0.12 0 0 ] numberOfRays 1 }}''')
for name,angle in [('front',0),('left',math.pi/2),('right',-math.pi/2)]:
    parts.append(f'''DistanceSensor {{ translation 0.03 0 0.055 rotation 0 0 1 {angle} name "sonar_{name}" type "sonar" lookupTable [ 0 0 0, 1.5 1.5 0 ] numberOfRays 5 aperture 0.35 }}''')
parts.append(''' ] boundingObject Box { size 0.15 0.105 0.045 } physics Physics { density -1 mass 0.6 }
}''')
(ROOT/'worlds'/'moonlight.wbt').write_text('\n'.join(parts)+'\n',encoding='utf-8')
print('Generated worlds/moonlight.wbt, scene.json and textures')

