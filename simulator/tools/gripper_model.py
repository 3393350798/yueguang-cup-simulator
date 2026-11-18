"""生成带碰撞体的双指夹爪与竖直升降机构。"""
import math


GRIPPER_DEFAULTS = {
    'reach_mm': 120,
    'opening_mm': 56,
    'closed_gap_mm': 24,
    'finger_length_mm': 40,
    'finger_thickness_mm': 7,
    'finger_height_mm': 9,
    'jaw_center_height_mm': 70,
    'lift_travel_mm': 45,
    'jaw_speed_mm_s': 26,
    'lift_speed_mm_s': 60,
    'jaw_force_n': 2,
    'lift_force_n': 6,
    'finger_mass_kg': .014,
    'carriage_mass_kg': .045,
    'friction': .85,
}


def resolve_parametric_gripper(robot):
    saved = robot.get('gripper', {})
    if saved.get('kind') == 'none':
        return dict(kind='none', jaws=[], lift=None, contact_links=[], contact_defs=[], reach_mm=0, friction=.85)
    geometry = {key: float(saved.get(key, default)) for key, default in GRIPPER_DEFAULTS.items()}
    if not all(math.isfinite(value) and value >= 0 for value in geometry.values()):
        raise ValueError('夹爪尺寸、质量、速度、力和摩擦系数必须是有限的非负数。')
    allow_zero = {'closed_gap_mm', 'lift_travel_mm', 'friction', 'jaw_force_n'}
    if any(value == 0 for key, value in geometry.items() if key not in allow_zero):
        raise ValueError('爪指尺寸、质量、机构速度与升降推力必须大于零。')
    if geometry['closed_gap_mm'] >= geometry['opening_mm']:
        raise ValueError('夹爪最大开口必须大于闭合间隙。')
    if geometry['jaw_center_height_mm'] <= geometry['finger_height_mm']/2:
        raise ValueError('夹爪降低时，爪指下沿必须高于地面。')
    if geometry['reach_mm']-geometry['finger_length_mm']/2 <= robot['body']['length_mm']/2:
        raise ValueError('夹爪伸出距离不足，爪指与车身重叠。')
    travel = (geometry['opening_mm']-geometry['closed_gap_mm'])/2000
    names = ['gripper_left', 'gripper_right']
    lift = {'joint':'gripper_lift', 'lower':0, 'raise':geometry['lift_travel_mm']/1000}
    return dict(geometry, kind='parametric', jaws=[dict(joint=name, open=0, closed=travel) for name in names],
                lift=lift if lift['raise'] else None, contact_links=names, contact_defs=[name.upper() for name in names])


def numbers(values):
    return ' '.join(f'{value:.7g}' for value in values)


def slider(name, axis, travel, velocity, force, endpoint):
    # 用同一组行程限制关节及电机，避免控制目标超过机械止挡。
    return f'''SliderJoint {{
  jointParameters JointParameters {{ axis {numbers(axis)} minStop 0 maxStop {travel:.7g} dampingConstant 0.01 }}
  device [
    LinearMotor {{ name "{name}" minPosition 0 maxPosition {travel:.7g} maxVelocity {velocity:.7g} maxForce {force:.7g} sound "" }}
    PositionSensor {{ name "{name}_sensor" }}
  ]
  endPoint {endpoint}
}}'''


def parametric_gripper(robot, gripper, origin_z, floor_z):
    if gripper['kind'] == 'none':
        return ''
    reach = gripper['reach_mm']/1000
    z = floor_z+gripper['jaw_center_height_mm']/1000-origin_z
    gap = gripper['opening_mm']/1000
    length = gripper['finger_length_mm']/1000
    thickness = gripper['finger_thickness_mm']/1000
    height = gripper['finger_height_mm']/1000
    front = robot['body']['length_mm']/2000
    carriage_x = reach-length/2-.010
    beam = numbers([.010, gap+2*thickness, .011])
    lift = gripper['lift']
    orange = 'PBRAppearance { baseColor 0.98 0.52 0.13 roughness 0.8 metalness 0 }'
    pad = 'PBRAppearance { baseColor 0.16 0.19 0.21 roughness 1 metalness 0 }'
    fingers = []
    for jaw, sign in zip(gripper['jaws'], (1, -1)):
        name = jaw['joint']
        y = sign*(gap+thickness)/2
        key = name.upper()
        finger_size = numbers([length, thickness, height])
        # 与物块接触的爪指是独立刚体，夹持只依赖碰撞和摩擦。
        finger = f'''DEF {key} Solid {{
          name "{name}" translation {numbers([reach-carriage_x if lift else reach, y, 0 if lift else z])}
          contactMaterial "grip_pad"
          children [ Shape {{ appearance {pad} geometry DEF {key}_GEOM Box {{ size {finger_size} }} }} ]
          boundingObject USE {key}_GEOM
          physics Physics {{ density -1 mass {gripper['finger_mass_kg']:.7g} }}
        }}'''
        fingers.append(slider(name, (0,-sign,0), jaw['closed'], gripper['jaw_speed_mm_s']/1000,
                              gripper['jaw_force_n'], finger))
    beam_shape = f'Shape {{ appearance {orange} geometry DEF GRIPPER_CARRIAGE_GEOM Box {{ size {beam} }} }}'
    if lift:
        carriage = f'''DEF GRIPPER_LIFT Solid {{
          name "gripper_lift" translation {carriage_x:.7g} 0 {z:.7g}
          children [ {beam_shape} {' '.join(fingers)} ]
          boundingObject USE GRIPPER_CARRIAGE_GEOM
          physics Physics {{ density -1 mass {gripper['carriage_mass_kg']:.7g} }}
        }}'''
        carriage = slider(lift['joint'], (0,0,1), lift['raise'], gripper['lift_speed_mm_s']/1000,
                          gripper['lift_force_n'], carriage)
    else:
        carriage = f'''Solid {{
          name "gripper_mount" translation {carriage_x:.7g} 0 {z:.7g}
          children [ {beam_shape} ]
          boundingObject USE GRIPPER_CARRIAGE_GEOM
        }} {' '.join(fingers)}'''
    mast_height = max(.025,(gripper['lift_travel_mm']+gripper['jaw_center_height_mm'])/1000)
    mast_size = numbers([.008,.014,mast_height])
    return f'''Pose {{
      translation {front+.005:.7g} 0 {floor_z+mast_height/2+.005-origin_z:.7g}
      children [ Shape {{ appearance {orange} geometry Box {{ size {mast_size} }} }} ]
    }}
    {carriage}'''
