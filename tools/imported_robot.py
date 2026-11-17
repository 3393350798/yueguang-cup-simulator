"""将会员装配体与比赛传感器接入同一Webots控制器。"""
import json
import math
from hardware_import import build_urdf_model


def numbers(values):
    return ' '.join(f'{value:.7g}' for value in values)


def sensors(robot, origin_z):
    c = robot['camera']
    nodes = [f'''Transform {{ translation {numbers([v/1000 for v in c['position_mm']])} rotation 0 0 1 {math.radians(c['yaw_deg'])} children [
Camera {{ rotation 0 1 0 {math.radians(c['pitch_deg'])} name "camera" width {c['width']} height {c['height']} fieldOfView {math.radians(c['fov_deg'])} near 0.01 far 3 }} ] }}''']
    for i, position in enumerate(robot['ir']['positions_mm']):
        p = [v/1000 for v in position]
        scale = (origin_z+p[2])/.012
        nodes.append(f'''DistanceSensor {{ translation {numbers(p)} rotation 0 1 0 1.5707963 name "ir{i}" type "infra-red" lookupTable [ 0 1000 0, {.03*scale} 1000 0, {.045*scale} 0 0, {.12*scale} 0 0 ] numberOfRays 1 }}''')
    sonar = robot['sonar']
    distance = sonar['range_mm']/1000
    for device in sonar['sensors']:
        nodes.append(f'''DistanceSensor {{ translation {numbers([v/1000 for v in device['position_mm']])} rotation 0 0 1 {math.radians(device['yaw_deg'])} name "sonar_{device['name']}" type "sonar" lookupTable [ 0 0 0, {distance} {distance} 0 ] numberOfRays 5 aperture {math.radians(sonar['aperture_deg'])} }}''')
    return '\n'.join(nodes)


def board_shape(board):
    model = board.get('model')
    if not model:
        return ''
    url = json.dumps('../'+model['source'], ensure_ascii=False)
    return f'''Transform {{ translation {numbers([v/1000 for v in board['position_mm']])} children [ Shape {{ appearance PBRAppearance {{ baseColor 0.06 0.45 0.2 roughness 0.8 metalness 0 }} geometry Mesh {{ url [ {url} ] }} }} ] }}'''


def assembly(robot, start, origin_z, board):
    model = robot['model']
    build_urdf_model(model)
    return f'''DEF CAR MemberRobot {{ translation {numbers([*start, origin_z])}
name "Moonlight" controller "moonlight" supervisor TRUE window "moonlight"
toolSlot [ {sensors(robot, origin_z)} {board_shape(board)} ]
}}'''
