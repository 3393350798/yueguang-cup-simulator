"""Measure assembly behavior in a small private Webots fixture."""
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import traceback

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
# Derive the native DLL directory from the controller package Webots loaded;
# native launchers may misencode a Chinese installation directory.
controller_spec = importlib.util.find_spec('controller')
os.environ['WEBOTS_HOME'] = str(Path(controller_spec.origin).parents[4])
if sys.platform == 'win32':
    dll_directory = os.add_dll_directory(str(Path(os.environ['WEBOTS_HOME'])/'msys64/mingw64/bin'))
from controller import Supervisor


def read(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))


def publish(name, value):
    path = ROOT/name
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    try:
        temp.replace(path)
    except PermissionError:
        if name != 'progress.json':
            raise


def finite(value, places=6):
    return round(value, places) if math.isfinite(value) else None


def check(key, label, status, detail, **observed):
    return dict(item=key, label=label, status=status, detail=detail, observed=observed)


def caption(stage):
    publish('progress.json', {'stage': stage})


def read_only_field(node, name, proto=False):
    if node:
        return node.getBaseNodeField(name) if proto else node.getField(name)


def endpoint(sim, motor, proto):
    device = sim.getFromDevice(motor._tag)
    joint = device.getParentNode() if device else None
    field = read_only_field(joint, 'endPoint', proto)
    return field.getSFNode() if field else None


def yaw(ori):
    return math.atan2(ori[3], ori[0])


def drive_test(bench, names, signs):
    car = bench.car
    results = []
    for i, (name, sign) in enumerate(zip(names, signs)):
        key, label = ('wheel_left', '左轮正向') if i==0 else ('wheel_right', '右轮正向')
        motor = bench.wheels[i]
        encoder = motor.getPositionSensor() if motor else None
        bench.reset()
        if not motor or sign not in (-1, 1):
            results.append(check(key, label, 'unobserved', '找不到此轮电机，或正向符号没有设为 ±1。', motor=name))
            continue
        if encoder:
            encoder.enable(bench.step)
        bench.advance(.16)
        start = car.getPosition()
        heading = yaw(car.getOrientation())
        angle0 = encoder.getValue() if encoder else math.nan
        velocity = min(2.0, motor.getMaxVelocity()*.45)
        if not math.isfinite(velocity) or velocity<=0:
            results.append(check(key, label, 'unobserved', '虚拟电机没有可用的速度上限。', motor=name))
            continue
        motor.setVelocity(sign*velocity)
        bench.advance(max(.75, .90/velocity), max_seconds=3)
        end = car.getPosition()
        turn = yaw(car.getOrientation())-heading
        turn = math.degrees(math.atan2(math.sin(turn),math.cos(turn)))
        forward = 1000*((end[0]-start[0])*math.cos(heading)+(end[1]-start[1])*math.sin(heading))
        turn_sign = -1 if i==0 else 1
        raw = encoder.getValue()-angle0 if encoder else math.nan
        bench.brake()
        status = 'pass'
        if not math.isfinite(forward) or not math.isfinite(turn):
            status, detail = 'unobserved', '未获得稳定的小车姿态。'
        elif forward < -2:
            status, detail = 'review', f'车体后移 {-forward:.1f} mm；先核对电机正向符号。'
        elif forward <= 2:
            status, detail = 'review', '车体未明显前移；核对轮轴、碰撞几何和轮胎接地。'
        elif turn*turn_sign < -.8:
            status, detail = 'review', f'车体前移 {forward:.1f} mm，但偏航方向相反；核对左右轮绑定。'
        elif turn*turn_sign <= .8:
            status, detail = 'review', '单轮前移但偏航不明显；核对左右轮碰撞与接地。'
        elif encoder and (not math.isfinite(raw) or raw*sign < .05):
            status, detail = 'review', '车体方向符合，但编码器未观测到同向增量。'
        else:
            detail = f'车体前移 {forward:.1f} mm，偏航 {turn:+.1f}°'+('。' if encoder else '；未观测到编码器。')
        results.append(check(key, label, status, detail,
                             motor=name, configured_sign=sign,
                             command_rad_s=finite(sign*velocity), forward_mm=finite(forward, 2),
                             yaw_deg=finite(turn, 2), encoder_delta_rad=finite(raw),
                             expected_turn='right' if i==0 else 'left'))
    return results


def ir_test(bench, positions):
    sim = bench.sim
    bench.reset()
    sensors = [sim.getDevice(f'ir{i}') for i in range(len(positions))]
    if not sensors:
        return [check('ir_none', '红外读数', 'unobserved', '当前车模没有给体检绑定红外。')]
    for device in sensors:
        if device:
            device.enable(bench.step)
    floor = sim.getFromDef('ASSEMBLY_FLOOR').getField('baseColor')

    def measure(color):
        floor.setSFColor([color]*3)
        bench.advance(.20)
        rows = []
        for _ in range(4):
            bench.advance(.04)
            rows.append([device.getValue() if device else math.nan for device in sensors])
        return [statistics.median(values) if all(map(math.isfinite,values)) else None for values in zip(*rows)]

    black = measure(.005)
    white = measure(1.0)
    results = []
    for i, (position, dark, bright) in enumerate(zip(positions, black, white)):
        delta = bright-dark if dark is not None and bright is not None else None
        if delta is None:
            status, detail = 'unobserved', '没有读取到黑白地面的这路红外值。'
        elif delta >= 120:
            status, detail = 'pass', f'黑 {dark:.0f}，白 {bright:.0f}；白比黑高 {delta:.0f}。'
        elif delta < -120:
            status, detail = 'review', f'黑 {dark:.0f}，白 {bright:.0f}；响应极性与参考相反。'
        else:
            status, detail = 'review', f'黑 {dark:.0f}，白 {bright:.0f}；对比不足，核对安装高度、遮挡和地面。'
        results.append(check(f'ir{i}',f'红外 {i}',status,detail, position_mm=position,
                             black=finite(dark) if dark is not None else None,
                             white=finite(bright) if bright is not None else None,
                             contrast=finite(delta) if delta is not None else None))
    return results


def gripper_devices(bench, config):
    joints = config['jaws'] + ([config['lift']] if config['lift'] else [])
    motors = {entry['joint']: bench.sim.getDevice(entry['joint']) for entry in joints}
    sensors = {name: motor.getPositionSensor() if motor else None for name, motor in motors.items()}
    for sensor in sensors.values():
        if sensor:
            sensor.enable(bench.step)
    bench.advance(.15)
    return joints, motors, sensors


def move_gripper(bench, joints, motors, sensors, goals, observe=None):
    duration = .65
    for name, target in goals.items():
        motor, sensor = motors[name], sensors[name]
        if not motor:
            continue
        speed = motor.getMaxVelocity()
        if not math.isfinite(speed) or speed <= 0:
            continue
        motor.setVelocity(speed)
        motor.setPosition(target)
        current = sensor.getValue() if sensor else math.nan
        if math.isfinite(current):
            duration = max(duration, abs(target-current)/speed+.45)
    bench.advance(duration, observe, max_seconds=10)
    results = []
    for name, target in goals.items():
        motor, sensor = motors[name], sensors[name]
        value = sensor.getValue() if sensor else math.nan
        row = next(entry for entry in joints if entry['joint'] == name)
        span = abs(row['open']-row['closed']) if 'open' in row else abs(row['raise']-row['lower'])
        tolerance = max(.0005, span*.06)
        state = ('unobserved' if not math.isfinite(value) or not motor else
                 'pass' if abs(value-target)<=tolerance else 'unobserved' if duration>10 else 'review')
        results.append(dict(joint=name, target=finite(target), measured=finite(value), status=state,
                            tolerance=finite(tolerance), unit='m' if motor and motor.getType()==motor.LINEAR else 'rad'))
    return results


def camera_test(bench, distances, floor_z, config):
    sim = bench.sim
    bench.reset()
    cam = sim.getDevice('camera')
    if not cam:
        return [check('camera', '相机标靶', 'unobserved', '当前车模未绑定相机。')]
    target = sim.getFromDef('ASSEMBLY_TARGET').getField('translation')
    width, height = cam.getWidth(), cam.getHeight()

    def capture(filename):
        bench.advance(.16)
        raw = cam.getImage()
        if not raw:
            return None
        img = Image.frombytes('RGB', (width, height), raw, 'raw', 'BGRX')
        img.save(ROOT/filename)
        return np.asarray(img, dtype=np.int16)

    joints, motors, sensors = gripper_devices(bench, config)
    lift = config['lift']
    stages = [('low','低位','lower'), ('high','高位','raise')] if lift else [('fixed','固定高度',None)]
    results = []
    cam.enable(bench.step)
    for stage, stage_name, lift_goal in stages:
        caption('正在采集相机标靶：'+stage_name+'…')
        goals = {entry['joint']:entry['open'] for entry in config['jaws']}
        if lift:
            goals[lift['joint']] = lift[lift_goal]
        positions = move_gripper(bench, joints, motors, sensors, goals) if goals else []
        at_goal = all(row['status']=='pass' for row in positions)
        empty_name = f'camera-{stage}-empty.png'
        target.setSFVec3f([0,0,-3])
        before = capture(empty_name) if at_goal else None
        for i, distance in enumerate(distances, 1):
            for key, label, y_mm in [('center','中央',0), ('left','左侧',110), ('right','右侧',-110)]:
                item, heading = f'camera_{stage}_{i}_{key}', f'{stage_name} {distance:g} mm {label}'
                evidence = dict(lift_stage=stage, joint_feedback=positions, target_slot=key,
                                resolution=[width,height], target_position_mm=[distance,y_mm,75],
                                reference_size_mm=[100,100], visible_px=None, bbox_px=None,
                                touches_frame_edge=None, frame=None, empty_frame=empty_name if before is not None else None)
                if before is None:
                    detail = ('未确认夹爪达到该姿态，无法比较此组画面。' if not at_goal
                              else '没有取得可用于比较的空景原图。')
                    results.append(check(item,heading,'unobserved',detail,**evidence))
                    continue
                target.setSFVec3f([distance/1000, y_mm/1000, floor_z+.075])
                name = f'camera-{stage}-{i:02}-{key}.png'
                after = capture(name)
                if after is None:
                    results.append(check(item,heading,'unobserved','没有取到这个位置的相机原图。',**evidence))
                    continue
                evidence['frame'] = name
                red, green, blue = (after[:,:,k] for k in range(3))
                magenta = (red>=80) & (blue>=65) & (red-green>=30) & (blue-green>=25)
                difference = np.abs(after-before).max(axis=2)>=35
                ys, xs = np.nonzero(magenta & difference)
                evidence['visible_px'] = int(len(xs))
                enough = max(12, math.ceil(width*height*.0002))
                if len(xs):
                    extent = [int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())]
                    evidence['bbox_px'] = extent
                    evidence['touches_frame_edge'] = (extent[0]==0 or extent[1]==0 or
                                                      extent[2]==width-1 or extent[3]==height-1)
                if len(xs)>=enough and np.ptp(xs)+1>=3 and np.ptp(ys)+1>=3:
                    edge = evidence['touches_frame_edge']
                    status = 'review' if edge else 'pass'
                    detail = f'看到标靶（{len(xs)} 像素）'+('，到达画面边缘。' if edge else '；请结合原图检查局部遮挡。')
                else:
                    status, detail = 'review', '未看清标靶；检查视野边界、相机方向和车体遮挡。'
                results.append(check(item,heading,status,detail,**evidence))
    target.setSFVec3f([0,0,-3])
    cam.disable()
    return results


def gripper_test(bench, config, floor, imported):
    if not config['jaws']:
        return [check('grip','夹爪开合','unobserved','当前车模未配置夹爪开合关节。'),
                check('lift','夹爪升降','unobserved','当前车模未配置升降关节。'),
                check('interference','车内接触','unobserved','没有可运行的夹爪开合动作。')]
    bench.reset()
    joints, motors, sensors = gripper_devices(bench, config)
    moved_ids = set()
    for motor in motors.values():
        node = endpoint(bench.sim,motor,imported) if motor else None
        if node:
            moved_ids.add(node.getId())
    for key in config['contact_defs']:
        node = bench.car.getFromProtoDef(key) if imported else bench.sim.getFromDef(key)
        if node:
            moved_ids.add(node.getId())
    stages = [('lower_open','低位开爪','open','lower'),
              ('lower_closed','低位合爪','closed','lower')]
    if config['lift']:
        stages.extend([('raised_closed','高位合爪','closed','raise'),
                       ('raised_open','高位开爪','open','raise')])
    stages.append(('return','回到低位开爪','open','lower'))
    rows, contact_stages = [], []

    def contacts():
        # Only a horizontal plane and an out-of-view non-colliding marker are
        # present. Contact points > 3mm above the plane cannot be floor contacts.
        return [p for p in bench.car.getContactPoints(True) if p.point[2]>floor+.003]

    for key, label, jaw_pos, lift_pos in stages:
        goals = {entry['joint']:entry[jaw_pos] for entry in config['jaws']}
        if config['lift']:
            goals[config['lift']['joint']] = config['lift'][lift_pos]
        peak = linked = 0
        height = None

        def tick():
            nonlocal peak, linked, height
            points = contacts()
            peak = max(peak,len(points))
            linked = max(linked,sum(point.node_id in moved_ids for point in points))
            if points:
                sample_height = max(p.point[2]-floor for p in points)*1000
                height = max(height or 0,sample_height)

        checks = move_gripper(bench, joints, motors, sensors, goals, tick)
        rows.append((key,label,checks))
        contact_stages.append(dict(stage=key,label=label,peak_contact_points=peak,
                                   peak_gripper_link_points=linked, max_height_mm=finite(height,1) if height is not None else None))

    result = []
    for names, key, label in [
        ({entry['joint'] for entry in config['jaws']}, 'grip', '夹爪开合'),
        ({config['lift']['joint']} if config['lift'] else set(), 'lift', '夹爪升降'),
    ]:
        matched = [dict(stage=row[0],**test) for row in rows for test in row[2] if test['joint'] in names]
        if not matched:
            result.append(check(key,label,'unobserved','当前车模未配置升降关节。'))
            continue
        problem = next((test for test in matched if test['status']=='review'),None)
        missing = next((test for test in matched if test['status']=='unobserved'),None)
        if problem:
            state, detail = 'review', f"{problem['joint']} 有阶段未到达目标；核对关节限位、推力和干涉。"
        elif missing:
            state, detail = 'unobserved', f"{missing['joint']} 缺少有效反馈，或所需行程超过体检时间。"
        else:
            state, detail = 'pass', '各阶段的位置反馈均达到设定目标。'
        result.append(check(key,label,state,detail,joints=matched))

    reported = [row for row in contact_stages if row['peak_contact_points']]
    if reported:
        attributed = [row['label'] for row in reported if row['peak_gripper_link_points']]
        message = ('发现夹爪相关的车内碰撞点（'+'、'.join(attributed)+'）。' if attributed else
                   '发现高于参考地面的车内接触点；仿真未能定位到具体夹爪连杆。')
        status = 'review'
    elif any(row['status']!='pass' for row in result if row['item']=='grip' or config['lift']):
        status, message = 'unobserved', '行程未完整走过，不能据此排除干涉。'
    else:
        status, message = 'pass', '这些动作中未观测到车内接触；仅覆盖仿真碰撞几何。'
    result.append(check('interference','车内接触',status,message,phases=contact_stages,
                        floor_filter_mm=3, tracked_gripper_links=len(moved_ids)))
    return result


class Fixture:
    def __init__(self, sim, scene, names):
        self.sim = sim
        self.car = sim.getSelf()
        self.step = int(sim.getBasicTimeStep())
        self.dt = self.step/1000
        self.wheels = [sim.getDevice(name) for name in names]
        self.brake()
        self.car.getField('translation').setSFVec3f([0,0,scene['robot_origin_z']])
        self.car.getField('rotation').setSFRotation([0,0,1,0])
        self.car.resetPhysics()
        self.advance(.48)
        self.car.saveState('assembly-home')

    def brake(self):
        for motor in self.wheels:
            if motor:
                motor.setPosition(float('inf'))
                motor.setVelocity(0)

    def reset(self):
        self.brake()
        self.car.loadState('assembly-home')
        self.car.resetPhysics()
        self.advance(.22)

    def advance(self, elapsed, observe=None, max_seconds=10):
        for _ in range(math.ceil(min(max_seconds,elapsed)/self.dt)):
            if self.sim.step(self.step)==-1:
                raise RuntimeError('体检期间 Webots 已关闭。')
            if observe:
                observe()


def run(sim):
    scene, settings = read('scene.json'), read('assembly_settings.json')
    robot = scene['robot']
    model = robot.get('model', {})
    kind = model.get('kind','parametric')
    imported = kind=='urdf'
    sides = ('left','right')
    names = [model.get(side+'_joint', '') if imported else side+'_motor' for side in sides]
    signs = [model.get(side+'_sign',1) if imported else 1 for side in sides]
    floor = scene['floor_z']
    bench = Fixture(sim, scene, names)
    checks = []
    if not settings['camera_only']:
        caption('正在检查车轮方向和编码器…')
        checks.extend(drive_test(bench, names, signs))
        caption('正在采集黑白地面的红外读数…')
        checks.extend(ir_test(bench, robot['ir']['positions_mm']))
    checks.extend(camera_test(bench, settings['target_distances_mm'], floor, scene['gripper']))
    if not settings['camera_only']:
        caption('正在检查开合、升降和车内接触…')
        checks.extend(gripper_test(bench,scene['gripper'],floor,imported))
    publish('report.json',dict(format='moonlight-assembly-check', version=2, model_kind=kind,
                               physics_step_ms=bench.step, camera_mount=robot.get('camera'), checks=checks))


if __name__=='__main__':
    sim = Supervisor()
    try:
        run(sim)
    except Exception as error:
        traceback.print_exc()
        publish('report.json',{'error':str(error)})
        sim.simulationQuit(1)
    else:
        sim.simulationQuit(0)
