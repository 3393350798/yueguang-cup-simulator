"""根据 robot.json 生成教学场景与会员小车。"""
from pathlib import Path
import json
import math
from PIL import Image, ImageDraw, ImageFont
from hardware_import import read_json
from imported_robot import assembly, board_shape
from competition_block import block_node

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'worlds' / 'textures'
ASSETS.mkdir(parents=True, exist_ok=True)
SCALE = 0.0027
SIZE = 2.0
robot = json.loads((ROOT/'robot.json').read_text(encoding='utf-8-sig'))
body, wheels = robot['body'], robot['wheels']
length, width, height = [body[k]/1000 for k in ('length_mm','width_mm','height_mm')]
origin_z = body['clearance_mm']/1000 + height/2
model = robot.get('model', {'kind': 'parametric'})
if model['kind'] == 'urdf':
    origin_z = model['origin_height_mm']/1000
board = read_json(ROOT/'board.json')
radius, wheel_width, track = [wheels[k]/1000 for k in ('radius_mm','width_mm','track_mm')]

def meters(values):return [x/1000 for x in values]

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
config = dict(floor_size=SIZE, line_width=0.0225, wheel_radius=radius,
              axle_length=track, body_size=[length,max(width,track+wheel_width)],
              robot=robot, board=board, robot_origin_z=origin_z, start=xy((195,670)),
              finish=xy((195,190)), pickup=xy((632,202)), marker=xy((565,202)),
              tag=xy((715,141)), circles=circles,
              channel=[xy((363,475)),xy((519,594))],
              tag_family='tag36h11', tag_size=0.08,
              block=dict(base_diameter_mm=56,top_diameter_mm=36,height_mm=70,base_height_mm=8,neck_height_mm=10,mass_kg=0.04),
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
parts.append(block_node(bx,by))
sx,sy=config['start']
if model['kind'] == 'urdf':
    parts[0] = parts[0].replace('#VRML_SIM R2025a utf8', '#VRML_SIM R2025a utf8\nEXTERNPROTO "../protos/imported/MemberRobot.proto"')
    parts.append(assembly(robot, [sx,sy], origin_z, board))
    (ROOT/'worlds'/'moonlight.wbt').write_text('\n'.join(parts)+'\n',encoding='utf-8')
    print('Generated scene with member URDF assembly')
    raise SystemExit(0)
if model['kind'] == 'mesh':
    chassis = f'''Shape {{ appearance {material('0.08 0.16 0.25')} geometry Mesh {{ url [ {json.dumps('../'+model['source'],ensure_ascii=False)} ] }} }}'''
else:
    chassis = f'''Shape {{ appearance {material('0.08 0.16 0.25')} geometry Box {{ size {v([length,width,height])} }} }}'''
parts.append(f'''DEF CAR Robot {{ translation {sx} {sy} {origin_z}
name "Moonlight" controller "moonlight" supervisor TRUE window "moonlight"
children [
{chassis}
Transform {{ translation {-length*.1} 0 {height/2+.008} children [ Shape {{ appearance {material('0.1 0.68 0.63')} geometry Box {{ size {v([length*.5,width*.8,.016])} }} }} ] }}
''')
parts.append(board_shape(board))
g=robot['gripper'];reach=g['reach_mm']/1000;opening=g['opening_mm']/1000
for position,size in [([(length/2+reach)/2,0,0],[reach-length/2,.012,.012]),
                      ([reach,0,0],[.008,opening+.008,.012]),
                      ([reach,opening/2,0],[.03,.008,.018]),
                      ([reach,-opening/2,0],[.03,.008,.018])]:
    parts.append(f'Transform {{ translation {v(position)} children [ Shape {{ appearance {material("0.98 0.59 0.18")} geometry Box {{ size {v(size)} }} }} ] }}')
c=robot['camera']
parts.append(f'''Transform {{ translation {v(meters(c['position_mm']))} rotation 0 0 1 {math.radians(c['yaw_deg'])} children [
Camera {{ rotation 0 1 0 {math.radians(c['pitch_deg'])} name "camera" width {c['width']} height {c['height']} fieldOfView {math.radians(c['fov_deg'])} near 0.01 far 3 }} ] }}''')
for side,y in [('left',track/2),('right',-track/2)]:
    parts.append(f'''HingeJoint {{ jointParameters HingeJointParameters {{ axis 0 1 0 anchor 0 {y} {radius-origin_z} }}
device [ RotationalMotor {{ name "{side}_motor" maxVelocity {wheels['max_speed_m_s']/radius} maxTorque {wheels['max_torque_nm']} sound "" }} PositionSensor {{ name "{side}_encoder" }} ]
endPoint Solid {{ name "{side}_wheel" translation 0 {y} {radius-origin_z} rotation 1 0 0 -1.5707963 contactMaterial "wheel"
children [ Shape {{ appearance {material('0.12 0.13 0.16')} geometry Cylinder {{ radius {radius} height {wheel_width} }} }} ]
boundingObject Cylinder {{ radius {radius} height {wheel_width} }} physics Physics {{ density -1 mass {wheels['mass_kg']} }}
}} }}''')
cr=robot['caster']['radius_mm']/1000;cx=robot['caster']['x_mm']/1000
parts.append(f'''Solid {{ name "caster" translation {cx} 0 {cr-origin_z} children [ Shape {{ appearance {material('0.7 0.73 0.78')} geometry Sphere {{ radius {cr} }} }} ] boundingObject Sphere {{ radius {cr} }} physics Physics {{ density -1 mass 0.01 }} contactMaterial "caster" }}''')
for i,position in enumerate(robot['ir']['positions_mm']):
    p=meters(position);scale=(origin_z+p[2])/.012
    parts.append(f'''DistanceSensor {{ translation {v(p)} rotation 0 1 0 1.5707963 name "ir{i}" type "infra-red" lookupTable [ 0 1000 0, {.03*scale} 1000 0, {.045*scale} 0 0, {.12*scale} 0 0 ] numberOfRays 1 }}''')
s=robot['sonar'];distance=s['range_mm']/1000
for device in s['sensors']:
    parts.append(f'''DistanceSensor {{ translation {v(meters(device['position_mm']))} rotation 0 0 1 {math.radians(device['yaw_deg'])} name "sonar_{device['name']}" type "sonar" lookupTable [ 0 0 0, {distance} {distance} 0 ] numberOfRays 5 aperture {math.radians(s['aperture_deg'])} }}''')
parts.append(f''' ] boundingObject Box {{ size {v([length,width,height])} }} physics Physics {{ density -1 mass {body['mass_kg']} }}
}}''')
(ROOT/'worlds'/'moonlight.wbt').write_text('\n'.join(parts)+'\n',encoding='utf-8')
print('Generated worlds/moonlight.wbt from robot.json')
