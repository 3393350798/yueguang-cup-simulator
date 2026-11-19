"""Run short stationary grip-only trials using Webots contact physics."""
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import traceback

from physical_gripper import PhysicalGripper


ROOT = Path(__file__).resolve().parents[2]
# A Windows launcher may transcode a Chinese install path. Derive it from
# the controller package that Webots placed on this process's module path.
controller_spec = importlib.util.find_spec('controller')
os.environ['WEBOTS_HOME'] = str(Path(controller_spec.origin).parents[4])
if sys.platform == 'win32':
    dll_directory = os.add_dll_directory(str(Path(os.environ['WEBOTS_HOME'])/'msys64/mingw64/bin'))
from controller import Supervisor


def read(name):
    return json.loads((ROOT/name).read_text(encoding='utf-8'))


def publish(name, data):
    target = ROOT/name
    pending = target.with_suffix('.tmp')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    # On Windows the progress UI may be in the middle of reading a file.
    if name == 'progress.json':
        try:
            pending.replace(target)
        except PermissionError:
            return
    else:
        pending.replace(target)


def yaw_tilt(yaw, pitch):
    """Convert Rz(yaw) * Ry(pitch) to the axis/angle required by Webots."""
    sa, ca = math.sin(yaw/2), math.cos(yaw/2)
    sb, cb = math.sin(pitch/2), math.cos(pitch/2)
    axis = [-sa*sb, ca*sb, sa*cb]
    magnitude = math.hypot(*axis)
    return [0, 0, 1, 0] if magnitude<1e-12 else [*(n/magnitude for n in axis), 2*math.atan2(magnitude,ca*cb)]


def run(sim):
    settings, scene = read('bench.json'), read('scene.json')
    car, block = sim.getSelf(), sim.getFromDef('BLOCK')
    grip, robot = scene['gripper'], scene['robot']
    floor = scene['floor_z']
    step_ms = int(sim.getBasicTimeStep())
    dt = step_ms/1000
    model = robot.get('model', {})
    imported = model.get('kind') == 'urdf'
    wheel_names = [model[side+'_joint'] if imported else side+'_motor' for side in ('left','right')]
    wheels = [sim.getDevice(name) for name in wheel_names]
    grip_motors = {item['joint']:sim.getDevice(item['joint']) for item in [*grip['jaws'],grip['lift']]}
    if not all([*wheels,*grip_motors.values()]):
        raise ValueError('夹爪或车轮设备未生成，请检查硬件配置。')
    claw = PhysicalGripper(grip,grip_motors,sim,car,block,floor,dt)
    car_t, car_r = car.getField('translation'), car.getField('rotation')
    block_t, block_r = block.getField('translation'), block.getField('rotation')
    mass = block.getField('physics').getSFNode().getField('mass')
    props = sim.getFromDef('BENCH_WORLD').getField('contactProperties')
    friction = props.getMFNode(0).getField('coulombFriction')
    home = [0,0,scene['robot_origin_z']]
    block_t.setSFVec3f([-.6,0,floor+.04])
    car_t.setSFVec3f(home)
    car_r.setSFRotation([0,0,1,0])

    def brake():
        for wheel in wheels:
            wheel.setPosition(float('inf'))
            wheel.setVelocity(0)

    def advance(seconds, observe=None):
        for _ in range(math.ceil(seconds/dt)):
            if sim.step(step_ms) == -1:
                raise RuntimeError('Webots 在测试完成前关闭。')
            if observe is not None:
                observe(claw.observe())

    def travel_time(joints, keys):
        times = [abs(j[keys[0]]-j[keys[1]])/max(.00001,grip_motors[j['joint']].getMaxVelocity()) for j in joints]
        time = max(times)
        if time > 12:
            raise ValueError('夹爪单项开合或升降的预计行程超过 12 秒，请检查关节速度。')
        return time

    close_time = travel_time(grip['jaws'],('open','closed'))+.35
    lift_time = travel_time([grip['lift']],('lower','raise'))+.35
    brake()
    advance(max(close_time,lift_time,.5))
    # The saved state is a resting, open assembly. Release/lift targets are
    # reissued after resetting physics; no block is attached or moved mid-trial.
    car.saveState('bench-open')
    if imported:
        pads = [pad.getCenterOfMass() for pad in claw.pads]
        car_origin = car.getPosition()
        default_center = [sum(p[axis] for p in pads)/len(pads)-car_origin[axis] for axis in (0,1)]
    else:
        default_center = [grip['reach_mm']/1000,0]
    center = [default_center[i] if settings[key] is None else settings[key]/1000
              for i,key in enumerate(('center_x_mm','center_y_mm'))]
    if any(abs(pad)>.8 for pad in center):
        raise ValueError('抓取中心超出测试台范围，请填写小车坐标系内的中心。')
    results = []
    for case in settings['cases']:
        car.loadState('bench-open')
        car.resetPhysics()
        brake()
        claw.reset()
        friction.setMFFloat(0, case['friction'])
        mass.setSFFloat(case['mass_g']/1000)
        location = [center[0]+case['x_mm']/1000,center[1]+case['y_mm']/1000]
        yaw, tilt = (math.radians(case[key]) for key in ('yaw_deg','tilt_deg'))
        lowest = min(z*math.cos(tilt)-radius*abs(math.sin(tilt))
                     for z,radius in ((-.035,.028),(-.027,.028),(.025,.018),(.035,.018)))
        block_t.setSFVec3f([*location,floor-lowest+.001])
        block_r.setSFRotation(yaw_tilt(yaw,tilt))
        block.resetPhysics()
        advance(.35)
        pre_grasp_position = block.getPosition()
        grasp_tilt = round(math.degrees(math.acos(max(-1,min(1,block.getOrientation()[8])))),1)
        claw.command('grab')
        advance(close_time)
        closed_position = block.getPosition()
        # Measure a sustained grip, with the shared two-pad detector, rather
        # than equating a bounce/temporary lift with successful holding.
        claw.command('lift')
        lifted, dropped, holding, peak = False, False, False, 0.0

        def observe_lift(row):
            nonlocal lifted, dropped, holding, peak
            peak = max(peak,row['clearance_mm'])
            holding = row['holding']
            dropped |= lifted and not holding
            lifted |= holding

        advance(lift_time+.6,observe_lift)
        held_at_release = holding
        claw.command('release')
        release_observation = None
        stable_tail = 0.0

        def observe_release(row):
            nonlocal release_observation, stable_tail
            release_observation = row
            velocity = block.getVelocity()
            # A sleeping Webots body can stop emitting contact points. The
            # bottom clearance and velocities remain available even then.
            at_floor = -3 <= row['clearance_mm'] <= 3
            still = math.hypot(*velocity[:3]) < .015 and math.hypot(*velocity[3:]) < .12
            if at_floor and still and row['touching'] == 0:
                stable_tail += dt
            else:
                stable_tail = 0.0

        advance(close_time+.8, observe_release)
        landed = held_at_release and stable_tail >= .2
        final = block.getPosition()
        landing_delta = [1000*(final[i]-location[i]) for i in (0,1)]
        results.append(dict(case, lifted=lifted, dropped=dropped, held_at_release=held_at_release,
                            max_clearance_mm=peak, landing_stable=landed,
                            landing_stable_ms=round(stable_tail*1000,1) if held_at_release else None,
                            landing_upright=release_observation['upright'] if landed else None,
                            landing_x_mm=round(landing_delta[0],1) if landed else None,
                            landing_y_mm=round(landing_delta[1],1) if landed else None,
                            landing_offset_mm=round(math.hypot(*landing_delta),1) if landed else None,
                            final_offset_mm=round(math.hypot(*landing_delta),1),
                            pre_grasp_shift_mm=round(1000*math.dist(pre_grasp_position[:2],location),1),
                            closing_shift_mm=round(1000*math.dist(closed_position[:2],pre_grasp_position[:2]),1),
                            pre_grasp_tilt_deg=grasp_tilt))
        publish('progress.json',{'completed':len(results)})
    counts = dict(trials=len(results),lifted=sum(row['lifted'] for row in results),
                  dropped=sum(row['dropped'] for row in results),held_at_release=sum(row['held_at_release'] for row in results),
                  landing_stable=sum(row['landing_stable'] for row in results))
    grouped = {}
    for row in results:
        params = {k:v for k,v in settings['cases'][0].items() if k!='repeat'}
        key = tuple(row[k] for k in params)
        if key not in grouped:
            grouped[key] = dict(zip(params,key),trials=0,lifted=0,dropped=0,held_at_release=0,landing_stable=0)
        part = grouped[key]
        part['trials'] += 1
        for name in ('lifted','dropped','held_at_release','landing_stable'):
            part[name] += int(row[name])
    publish('report.json',dict(format='moonlight-gripper-bench',version=1,design_sha256=settings['design_sha256'],
                               model_kind=grip['kind'],physics_step_ms=step_ms,
                               center_mm=[round(value*1000,2) for value in center],
                               summary=counts,groups=list(grouped.values()),trials=results))


if __name__ == '__main__':
    sim = Supervisor()
    try:
        run(sim)
    except Exception as error:
        traceback.print_exc()
        publish('report.json',{'error':str(error)})
        sim.simulationQuit(1)
    else:
        sim.simulationQuit(0)
