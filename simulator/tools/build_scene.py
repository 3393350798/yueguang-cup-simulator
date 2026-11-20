"""根据 robot.json 生成教学场景与会员小车。"""
from pathlib import Path
import json
import math
from PIL import Image, ImageDraw
from hardware_import import read_json
from imported_robot import assembly, board_shape
from competition_block import block_node
from gripper_model import parametric_gripper, resolve_parametric_gripper
from imperfections import Imperfections

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'worlds' / 'textures'
ASSETS.mkdir(parents=True, exist_ok=True)
FLOOR_Z = -.005
robot = json.loads((ROOT/'robot.json').read_text(encoding='utf-8-sig'))
step_ms = robot.get('step_ms', 32)
if step_ms not in (4, 8, 16, 32):
    raise ValueError('step_ms 应为 4、8、16 或 32。')
imp_config = robot.get('imperfections', {})
Imperfections(imp_config)
light_gain = imp_config.get('light_gain', 1)
if not 0 <= light_gain <= 3:
    raise ValueError('light_gain 应在 0 到 3 之间。')
body, wheels = robot['body'], robot['wheels']
length, width, height = [body[k]/1000 for k in ('length_mm','width_mm','height_mm')]
origin_z = body['clearance_mm']/1000 + height/2
model = robot.get('model', {'kind': 'parametric'})
if model['kind'] == 'urdf':
    from imported_robot import assembly_gripper
    origin_z = model['origin_height_mm']/1000
    gripper = assembly_gripper(robot)
else:
    gripper = resolve_parametric_gripper(robot)
physics_step_ms = min(step_ms,4) if gripper['kind'] != 'none' else step_ms
block_spec = dict(base_diameter_mm=56, top_diameter_mm=36, height_mm=70,
                  base_height_mm=8, neck_height_mm=10, mass_kg=.04, floor_friction=.5)
for key in ('mass_kg', 'floor_friction'):
    block_spec[key] = float(robot.get('block', {}).get(key, block_spec[key]))
if not (math.isfinite(block_spec['mass_kg']) and block_spec['mass_kg'] > 0
        and math.isfinite(block_spec['floor_friction']) and block_spec['floor_friction'] >= 0):
    raise ValueError('物块质量必须大于零，地面摩擦系数必须为有限的非负数。')
board = read_json(ROOT/'board.json')
radius, wheel_width, track = [wheels[k]/1000 for k in ('radius_mm','width_mm','track_mm')]

def meters(values):return [x/1000 for x in values]

field = read_json(ROOT/'assets'/'competition_field.json')
layout = field['layout_mm']
SIZE = field['size_mm']/1000

def field_point(point):
    return [coordinate/1000 - SIZE/2 for coordinate in point]

channel = [field_point(point) for point in layout['channel']]
(channel_left, channel_top), (channel_right, channel_bottom) = channel
# The walls follow the full centerlines of the two long sides of the printed frame.
walls = [[channel_left,y,channel_right,y] for y in (channel_top,channel_bottom)]

# Published practice-only offsets. Publish the same table to the controller
# and panel so a plan is selected by ID instead of taking arbitrary offsets.
tolerance_cases = [
    dict(id='nominal', label='基准摆位', lateral_mm=0, block_y_mm=0, light_scale=1),
    dict(id='start-left', label='起步左偏 10 mm', lateral_mm=10, block_y_mm=0, light_scale=1),
    dict(id='start-right', label='起步右偏 10 mm', lateral_mm=-10, block_y_mm=0, light_scale=1),
    dict(id='block-up', label='物块场地 Y +8 mm', lateral_mm=0, block_y_mm=8, light_scale=1),
    dict(id='block-down', label='物块场地 Y −8 mm', lateral_mm=0, block_y_mm=-8, light_scale=1),
    dict(id='light-dim', label='灯光 ×0.75', lateral_mm=0, block_y_mm=0, light_scale=.75),
    dict(id='light-bright', label='灯光 ×1.25', lateral_mm=0, block_y_mm=0, light_scale=1.25),
]

config = dict(step_ms=step_ms, physics_step_ms=physics_step_ms,
              field_revision='rules-2000', floor_size=SIZE, line_width=layout['line_width']/1000,
              wheel_radius=radius, axle_length=track, body_size=[length,max(width,track+wheel_width)],
              robot=robot, board=board, robot_origin_z=origin_z, floor_z=FLOOR_Z,
              gripper=gripper, **{key:field_point(layout[key])
                                  for key in ('start','finish','pickup','marker','tag')},
              finish_inner_size=[layout['finish_inner_size']/1000]*2,
              circles=[dict(side=circle['side'],color=circle['color'],
                            center=field_point(circle['center']),
                            inner_radius=layout['circle_inner_diameter']/2000)
                       for circle in layout['circles']],
              channel=channel, walls=walls,
              wall_thickness=.012, wall_height=.14,
              tag_family='tag36h11', tag_size=0.08,
              block=block_spec, tolerance_cases=tolerance_cases,
              geometry_note='2000 × 2000 mm 赛规定尺版；挡板沿超声波区黑框长边全长布置')
(ROOT/'scene.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

# Render CAD ink at higher resolution first to preserve edges and hollow regions.
resolution = 4096
scale = resolution/field['size_mm']
def pixel(point):
    return (point[0]*scale,(field['size_mm']-point[1])*scale)

im = Image.new('RGB',(resolution,resolution),'white')
for shape in field['shapes']:
    mask = Image.new('1',im.size)
    draw = ImageDraw.Draw(mask)
    for polygon in shape['polygons']:
        draw.polygon([pixel(point) for point in polygon['outer']],fill=1)
        for hole in polygon['holes']:
            draw.polygon([pixel(point) for point in hole],fill=0)
    im.paste(shape['fill'],(0,0),mask)
im = im.resize((2048,2048),Image.Resampling.LANCZOS)
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
WorldInfo {
  basicTimeStep STEP_MS coordinateSystem "ENU"
  contactProperties [
    ContactProperties { material1 "grip_pad" material2 "competition_block" coulombFriction [ GRIP_FRICTION ] bounce 0 softCFM 0.0001 bumpSound "" rollSound "" slideSound "" }
    ContactProperties { material1 "competition_block" material2 "default" coulombFriction [ FLOOR_FRICTION ] bounce 0 bumpSound "" rollSound "" slideSound "" }
    ContactProperties { material1 "wheel" coulombFriction [ 1 ] bumpSound "" rollSound "" slideSound "" }
    ContactProperties { bumpSound "" rollSound "" slideSound "" }
  ]
}
Viewpoint { orientation -0.57735027 0.57735027 0.57735027 2.0943951 position 0 0 6.1 fieldOfView 0.7 }
Background { skyColor [ 0.91 0.94 0.97 ] }
DEF PRACTICE_LIGHT DirectionalLight { direction -0.3 -0.4 -1 intensity LIGHT_INTENSITY ambientIntensity LIGHT_AMBIENT castShadows TRUE }
DEF FLOOR Solid {
  translation 0 0 FLOOR_Z
  children [ Shape { appearance PBRAppearance {
    baseColorMap ImageTexture { url [ "textures/track.png" ] filtering 5 }
    roughness 1 metalness 0
  } geometry Plane { size FLOOR_SIZE FLOOR_SIZE } } ]
  boundingObject Plane { size FLOOR_SIZE FLOOR_SIZE }
}
'''.replace('STEP_MS', str(physics_step_ms))
   .replace('GRIP_FRICTION', f'{gripper.get("friction", .85):.7g}')
   .replace('FLOOR_FRICTION', f'{block_spec["floor_friction"]:.7g}')
   .replace('FLOOR_Z', f'{FLOOR_Z:.7g}')
   .replace('FLOOR_SIZE', f'{SIZE:.7g}')
   .replace('LIGHT_INTENSITY', f'{1.3 * light_gain:g}')
   .replace('LIGHT_AMBIENT', f'{min(1, 0.65 * light_gain):g}')]
wall_height = config['wall_height']
for i,(x0,y0,x1,y1) in enumerate(config['walls']):
    center = [(x0+x1)/2,(y0+y1)/2,FLOOR_Z+wall_height/2]
    dimensions = [math.hypot(x1-x0,y1-y0),config['wall_thickness'],wall_height]
    parts.append(solid_box(f'WALL_{i}',center,dimensions,'0.34 0.42 0.54',
                           f'0 0 1 {math.atan2(y1-y0,x1-x0):.7g}'))
# 立牌图案平面默认位于局部 XY，将法线转向南侧。
tx,ty=config['tag']
parts.append(f'''DEF TAG Solid {{ name "tag" translation {tx} {ty} 0.125 rotation 1 0 0 1.5707963
children [ Shape {{ appearance PBRAppearance {{ baseColorMap DEF TAG_TEXTURE ImageTexture {{ url [ "textures/tag0.png" ] }} roughness 1 metalness 0 }} geometry Plane {{ size 0.1 0.1 }} }} ] }}''')
bx,by=config['pickup']
parts.append(block_node(bx,by,block_spec['mass_kg']))
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
parts.append(parametric_gripper(robot,gripper,origin_z,FLOOR_Z))
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
# A fixed chassis center of mass lets a heavier or longer custom gripper
# shift the complete vehicle's weight distribution in the physics engine.
default_com_x = -18.35 if gripper['kind'] == 'parametric' else -6
center_of_mass = body.get('center_of_mass_mm', [default_com_x,0,0])
if (not isinstance(center_of_mass, list) or len(center_of_mass) != 3
        or not all(isinstance(axis, (int,float)) and math.isfinite(axis) for axis in center_of_mass)):
    raise ValueError('车身重心必须填写三个有限的坐标值（mm）。')
parts.append(f''' ] boundingObject Box {{ size {v([length,width,height])} }} physics Physics {{
  density -1 mass {body['mass_kg']} centerOfMass [ {v(meters(center_of_mass))} ]
}}
}}''')
(ROOT/'worlds'/'moonlight.wbt').write_text('\n'.join(parts)+'\n',encoding='utf-8')
print('Generated worlds/moonlight.wbt from robot.json')
