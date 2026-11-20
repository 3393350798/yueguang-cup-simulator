"""Webots 场景桥接与练习面板。会员算法在 member_algorithm.py。"""
from pathlib import Path
import base64
from hashlib import file_digest, sha256
import importlib.util
import json
import math
import os
import sys
import time
import traceback
from uuid import uuid4
import xml.etree.ElementTree as ET
from runtime_log import CHOICES, RuntimeLog, choice, project_version

ROOT=Path(__file__).resolve().parents[2]
ALGORITHM_PATH=ROOT/'controllers/moonlight/member_algorithm.py'
diagnostics=RuntimeLog(ROOT.parent)
diagnostics.install_exception_hook()
PROJECT_VERSION=project_version(ROOT.parent)
diagnostics.event('startup_begin',project_version=PROJECT_VERSION)
# Webots R2025a 的 Windows 启动器可能以错误编码传递中文安装目录。
# 用已找到的 controller 包位置恢复真实运行时路径，再加载其 DLL。
controller_spec=importlib.util.find_spec('controller')
os.environ['WEBOTS_HOME']=str(Path(controller_spec.origin).parents[4])
if sys.platform=='win32':
    dll_directory=os.add_dll_directory(str(Path(os.environ['WEBOTS_HOME'])/'msys64/mingw64/bin'))
from controller import Supervisor
import cv2
import numpy as np

sys.path.insert(0,str(ROOT/'tools'))
from firmware_bridge import FirmwareBridge, diagnostic_reason
from imperfections import Imperfections
from vision_link import VisionLink
from physical_gripper import PhysicalGripper
from stage_trial import StageTrial, STAGES, starting_pose
from timed_faults import RoundFaults, parse_plan
diagnostics.phase='scene_config'
CONFIG=json.loads((ROOT/'scene.json').read_text(encoding='utf-8'))
STEP=int(CONFIG.get('step_ms',32))
DT=STEP/1000
PANEL_TICKS=64//STEP
CAMERA_PREVIEW_TICKS=192//STEP
FIRMWARE_VISION_TICKS=192//STEP
R=CONFIG['wheel_radius']
HW=CONFIG['robot']
Z=CONFIG['robot_origin_z']
MAX_SPEED=HW['wheels']['max_speed_m_s']
RESPONSE=-math.expm1(-DT/HW['wheels']['response_s']) if HW['wheels']['response_s']>0 else 1
CALIBRATION=HW.get('calibration',{})
IR_CAL=CALIBRATION.get('ir',[])
SONAR_CAL=CALIBRATION.get('sonar',{})
MODEL=HW.get('model',{'kind':'parametric'})
IMPORTED=MODEL['kind']=='urdf'
WHEEL_NAMES=[MODEL[side+'_joint'] if IMPORTED else side+'_motor' for side in ('left','right')]
WHEEL_SIGNS=[MODEL.get(side+'_sign',1) if IMPORTED else 1 for side in ('left','right')]
diagnostics.event('config_ready',step_ms=STEP,physics_step_ms=CONFIG.get('physics_step_ms',STEP),
                  field_revision=choice(CONFIG.get('field_revision','practice-legacy'),'field_revision','custom'),
                  robot_kind=choice(MODEL['kind'],'robot_kind','unknown'),
                  gripper_kind=CONFIG.get('gripper',{}).get('kind','none'))

def angle(v):
    return (v+math.pi)%(2*math.pi)-math.pi

def load_algorithm():
    previous_phase=diagnostics.phase
    diagnostics.phase='algorithm_load'
    spec=importlib.util.spec_from_file_location('practice_algorithm',ALGORITHM_PATH)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance=module.Algorithm()
    record('algorithm_loaded')
    diagnostics.phase=previous_phase
    return instance

diagnostics.phase='webots_setup'
sim=Supervisor()
car=sim.getSelf()
block=sim.getFromDef('BLOCK')
light=sim.getFromDef('PRACTICE_LIGHT')
light_fields={name:light.getField(name) for name in ('intensity','ambientIntensity')} if light else {}
baseline_light={name:field.getSFFloat() for name,field in light_fields.items()}
finish_half_size=[side/2 for side in CONFIG.get('finish_inner_size',[.205,.205])]
channel_tl,channel_br=CONFIG['channel']
wall_half_width=CONFIG.get('wall_thickness',.012)/2
wall_lines=CONFIG.get('walls',[[channel_tl[0],y,channel_br[0],y]
                              for y in (channel_br[1]-wall_half_width,channel_tl[1]+wall_half_width)])
walls=[(line,sim.getFromDef(f'WALL_{i}')) for i,line in enumerate(wall_lines)]
wall_ids={node.getId() for _,node in walls if node is not None}
tag_texture=sim.getFromDef('TAG_TEXTURE').getField('url')
block_color=sim.getFromDef('BLOCK_MATERIAL').getField('baseColor')
translation=car.getField('translation')
rotation=car.getField('rotation')
block_position=block.getField('translation')
block_rotation=block.getField('rotation')
GRIP=CONFIG.get('gripper',dict(kind='none',jaws=[],lift=None,contact_links=[]))
GRIP_ACTUATORS=[*GRIP['jaws'], *([GRIP['lift']] if GRIP['lift'] else [])]
motors=[sim.getDevice(name) for name in WHEEL_NAMES]
encoders=[sim.getDevice(name+'_sensor') if IMPORTED else sim.getDevice(side+'_encoder') for name,side in zip(WHEEL_NAMES,('left','right'))]
joint_names={j['name'] for j in MODEL.get('joints',[]) if j['name'] not in WHEEL_NAMES}
joint_names.update(actuator['joint'] for actuator in GRIP_ACTUATORS)
joint_motors={name:sim.getDevice(name) for name in sorted(joint_names)}
joint_sensors={name:sim.getDevice(name+'_sensor') for name in joint_motors}
ir=[sim.getDevice(f'ir{i}') for i in range(len(HW['ir']['positions_mm']))]
sonar={s['name']:sim.getDevice('sonar_'+s['name']) for s in HW['sonar']['sensors']}
camera=sim.getDevice('camera')
IMAGE_WIDTH,IMAGE_HEIGHT=camera.getWidth(),camera.getHeight()
for motor in motors:
    motor.setPosition(float('inf'))
    motor.setVelocity(0)
for device in encoders+ir+list(sonar.values())+list(joint_sensors.values()):device.enable(STEP)
camera.enable(32)
sim.getKeyboard().enable(STEP)
claw=PhysicalGripper(GRIP,joint_motors,sim,car,block,CONFIG.get('floor_z',-.005),DT)
grip_keys=set()
trial=StageTrial('full',CONFIG)
round_id=0
round_result=None
ready_at=0
round_limit=None
round_error=None
round_faults=None
round_tolerance=None

state=dict(mode='manual',running=False,group=2,color='red',carrying=False,
           elapsed=0.0,collision=False,grasped=False,placed=None,parked=False,aligned=False,
           message='选择模式后开始；会员模板初始为停车。',events=[])
manual=[0.0,0.0]
actuated=[0.0,0.0]
last_encoder=[0.0,0.0]
still=0.0
algorithm=None
firmware=None
firmware_status={}
effects=Imperfections(HW.get('imperfections',{}))
tick=0
last_collision_time=-10
keyboard_driving=False
trace_epoch=0
trace_start=sim.getTime()
trace=[]
incident_flags=set()
last_view_wall=time.monotonic()
active_steps=0
panel_rate=0.0
panel_session=uuid4().hex[:8]
vision_link=None

def update_log_context():
    diagnostics.mode=state['mode']
    diagnostics.sim_ms=round(sim.getTime()*1000)
    diagnostics.round=round_id

def record(name, **fields):
    update_log_context()
    diagnostics.event(name, **fields)

def report_error(error):
    if diagnostics.phase.startswith('firmware_'):
        incident_flags.add('firmware_error')
    elif diagnostics.phase in ('algorithm_load','member_vision','member_step'):
        incident_flags.add('member_error')
    elif diagnostics.phase=='actuator' and state['mode'] in ('member','firmware'):
        incident_flags.add(state['mode']+'_error')
    if round_faults:
        for kind in ('firmware_error','member_error'):
            if kind in incident_flags:
                round_faults.observe(kind,round_elapsed_ms(),pose())
    update_log_context()
    reason=diagnostic_reason(error) if diagnostics.phase.startswith('firmware_') else None
    line=None
    if diagnostics.phase in ('algorithm_load','member_vision','member_step'):
        if type(error) in (SyntaxError,IndentationError,TabError) and error.filename==str(ALGORITHM_PATH):
            line=error.lineno
        frame=BaseException.__getattribute__(error,'__traceback__')
        while frame is not None:
            if frame.tb_frame.f_code.co_filename==str(ALGORITHM_PATH):
                line=frame.tb_lineno
            frame=frame.tb_next
    diagnostics.error(error,reason=reason,member_line=line)

def log(text):
    state['message']=text
    state['events']=(state['events']+[f"{state['elapsed']:.1f}s · {text}"])[-8:]
    print('EVENT',text,flush=True)


diagnostics.phase='vision_link'
if port:=int(HW.get('vision_link_port',0)):
    try:
        vision_link=VisionLink(port)
        log(f'本机图像与串口接口：127.0.0.1:{port}')
        record('vision_link_ready',vision_link=True)
    except OSError as error:
        log(f'本机图像与串口接口未启动：{error}')
        report_error(error)

diagnostics.phase='scene_config'
try:
    firmware_settings=json.loads((ROOT/'firmware.json').read_text(encoding='utf-8-sig'))
    configured=bool(firmware_settings.get('image')) if isinstance(firmware_settings,dict) else False
except (OSError,UnicodeError,ValueError) as error:
    report_error(error)
    configured=False

def scene_signature(firmware_config=None, firmware_board=None):
    # Candidate firmware images and member code may differ between runs.
    setup={key:value for key,value in (firmware_config or {}).items() if key not in ('image','renode')}
    data=json.dumps([PROJECT_VERSION,CONFIG,setup,firmware_board],sort_keys=True,ensure_ascii=True,separators=(',',':'))
    return sha256(data.encode('ascii')).hexdigest()


member_scene_signature=scene_signature()


def tolerance_assets():
    # Scene parameters alone do not detect a replaced mesh at the same path.
    paths={'worlds/moonlight.wbt', 'worlds/textures/track.png',
           'worlds/textures/tag0.png', 'worlds/textures/tag1.png'}
    if source:=MODEL.get('source'):
        paths.add(source)
        if IMPORTED:
            urdf=ROOT/source
            for mesh in ET.parse(urdf).iter('mesh'):
                paths.add((Path(source).parent/mesh.attrib['filename']).as_posix())
    if source:=(CONFIG.get('board',{}).get('model') or {}).get('source'):
        paths.add(source)
    hashes={}
    for name in sorted(paths):
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('场景资源不在工程目录内。')
        with path.open('rb') as asset:
            hashes[name]=file_digest(asset,'sha256').hexdigest()
    return hashes


try:
    tolerance_scene_assets=tolerance_assets()
except (OSError, ValueError, KeyError, ET.ParseError):
    tolerance_scene_assets=None


def tolerance_signature(settings=None,board=None):
    if tolerance_scene_assets is None:
        return None
    setup=json.dumps([scene_signature(settings,board),tolerance_scene_assets],
                     sort_keys=True,ensure_ascii=True,separators=(',',':'))
    return sha256(setup.encode('ascii')).hexdigest()


def round_elapsed_ms():
    return round(state['elapsed']*1000)


def trial_report(position):
    report=trial.report(state,position)
    if round_faults:
        report['fault_run']=round_faults.report()
    if round_tolerance is not None:
        report['tolerance_run']=round_tolerance.copy()
    return report


def queue_uart(value, at=None):
    if state['mode']=='firmware':
        effects.send(state['elapsed'] if at is None else at,
                     value.encode('utf-8') if isinstance(value,str) else bytes(value))


def calibrated_ir(index,value):
    if index<len(IR_CAL):
        calibration=IR_CAL[index]
        value=calibration['black']+(calibration['white']-calibration['black'])*value
    return effects.infrared(value)


def calibrated_sonar(name,value):
    if name in SONAR_CAL:
        calibration=SONAR_CAL[name]
        value=calibration['gain']*value+calibration['offset_mm']/1000
    return effects.sonar(value,HW['sonar']['range_mm']/1000)


def pose():
    p=car.getPosition();o=car.getOrientation()
    return [p[0],p[1],math.atan2(o[3],o[0])]

def place_result():
    p=block.getPosition()
    orientation=block.getOrientation()
    half_height=CONFIG.get('block',{}).get('height_mm',70)/2000
    base_center=[p[0]-half_height*orientation[2],p[1]-half_height*orientation[5]]
    nearest=min(CONFIG['circles'],key=lambda c:math.dist(base_center,c['center']))
    distance=math.dist(base_center,nearest['center'])
    if distance<=nearest.get('inner_radius',.02125):
        correct=(nearest['side']==('left' if state['group']%2==0 else 'right') and nearest['color']==state['color'])
        return dict(side=nearest['side'],color=nearest['color'],correct=correct,offset_mm=round(distance*1000,1))
    return None


def touches_channel_wall():
    contacts=car.getContactPoints(True)
    if any(contact.node_id in wall_ids for contact in contacts):
        return True
    for (x0,y,x1,_),node in walls:
        if node is None:
            continue
        near=[contact.point for contact in contacts
              if contact.point[2]>CONFIG.get('floor_z',-.005)+.003
              and min(x0,x1)-.005<=contact.point[0]<=max(x0,x1)+.005
              and abs(contact.point[1]-y)<=wall_half_width+.004]
        if near:
            # Webots may report a contact's owning node instead of the other participant.
            points=node.getContactPoints()
            if any(math.dist(point.point,p)<.0015 for point in points for p in near):
                return True
    return False

def observe_gripper():
    observed=claw.observe()
    if observed['holding'] and not state['carrying']:
        state['grasped']=True
        log('夹爪已将物块提离地面')
        record('gripper_holding')
    elif state['carrying'] and not observed['holding']:
        log('夹爪已失去夹持')
        record('gripper_released')
    state['carrying']=observed['holding']
    state['placed']=place_result() if (
        state['grasped'] and not observed['touching'] and observed['settled'] and observed['upright']
    ) else None
    return observed

def close_firmware(reason):
    global firmware,firmware_status
    if firmware is not None:
        previous_phase=diagnostics.phase
        diagnostics.phase='firmware_stop'
        firmware.close()
        record('firmware_closed',reason=reason)
        diagnostics.phase=previous_phase
    firmware=None;firmware_status={}


def tolerance_case(key,stage,limit,fault_plan):
    if key is None:
        return None
    if (fault_plan is not None or state['mode'] not in ('member','firmware') or
            type(limit) is not int or not 1<=limit<=180):
        raise ValueError('容差练习需要程序模式、指定用时，且不与定时故障叠加。')
    if not baseline_light or not any(baseline_light.values()):
        raise ValueError('容差练习需要启用赛场灯光；请重新生成场景并核对灯光配置。')
    for case in CONFIG.get('tolerance_cases',[]):
        if case['id']==key and (stage in ('full','pickup') or not case['block_y_mm']):
            return case
    raise ValueError('当前赛段不支持此容差工况。')


def set_practice_light(scale):
    for name,field in light_fields.items():
        level=baseline_light[name]*scale
        field.setSFFloat(min(1,level) if name=='ambientIntensity' else level)


def observe_tolerance_start():
    x,y,yaw=pose()
    return dict(start_pose_mm_deg=[round(x*1000,2),round(y*1000,2),round(math.degrees(yaw),2)],
                block_xy_mm=[round(v*1000,2) for v in block.getPosition()[:2]],
                light={name:round(field.getSFFloat(),5) for name,field in light_fields.items()})


def reset(group=None,color=None,stage=None,limit=None,fault_plan=None,tolerance_id=None):
    global still,last_encoder,algorithm,effects
    global trace_epoch,trace_start
    global trial,round_id,round_result,ready_at,last_collision_time,round_limit,round_error,round_faults,round_tolerance
    diagnostics.phase='round_reset'
    stage=stage if stage in STAGES else trial.stage
    case=tolerance_case(tolerance_id,stage,limit,fault_plan)
    plan=None if fault_plan is None else parse_plan(fault_plan,limit,state['mode'],bool(sonar))
    close_firmware('reset')
    if round_tolerance is not None or case is not None:
        set_practice_light(case['light_scale'] if case is not None else 1)
    round_tolerance=None if case is None else dict(version=1,case=case,limit_s=limit,observed=None,
        scene_signature=tolerance_signature() if state['mode']=='member' else None)
    effects=Imperfections(HW.get('imperfections',{}))
    trace_epoch+=1
    trace_start=sim.getTime()
    trace.clear()
    incident_flags.clear()
    if group is not None:state['group']=max(1,int(group))
    if color in ('red','yellow','blue'):state['color']=color
    start=starting_pose(CONFIG,stage)
    block_xy=CONFIG['pickup'].copy()
    if case is not None:
        lateral=case['lateral_mm']/1000
        start[0]-=lateral*math.sin(start[2])
        start[1]+=lateral*math.cos(start[2])
        block_xy[1]+=case['block_y_mm']/1000
    trial=StageTrial(stage,CONFIG,start)
    round_id+=1
    round_result=None
    round_error=None
    round_limit=max(1,min(180,float(limit))) if limit is not None else None
    round_faults=RoundFaults(plan,member_scene_signature if state['mode']=='member' else None) if plan is not None else None
    ready_at=sim.getTime()+.8
    last_collision_time=-10
    car.loadState('practice-ready')
    x,y,yaw=trial.start
    translation.setSFVec3f([x,y,Z])
    rotation.setSFRotation([0,0,1,yaw]);car.resetPhysics()
    block_position.setSFVec3f(block_xy+[CONFIG.get('floor_z',-.005)+.035])
    block_rotation.setSFRotation([0,0,1,0]);block.resetPhysics()
    block_color.setSFColor({'red':[.9,.025,.04],'yellow':[1,.78,.015],'blue':[.025,.14,.9]}[state['color']])
    tag_texture.setMFString(0,f"textures/tag{state['group']%2}.png")
    for motor in motors:
        # Webots restores position control when loading the saved vehicle state.
        motor.setPosition(float('inf'))
        motor.setVelocity(0)
    for motor in joint_motors.values():motor.setPosition(0)
    claw.reset()
    actuated[:]=[0,0];manual[:]=[0,0];last_encoder=[d.getValue()*sign for d,sign in zip(encoders,WHEEL_SIGNS)]
    state.update(running=False,carrying=False,elapsed=0,collision=False,grasped=False,
                 placed=None,parked=False,aligned=False,events=[],message='已就位：'+STAGES[trial.stage])
    still=0
    record('round_reset',stage=trial.stage)
    if state['mode'] in ('member','firmware'):
        algorithm=load_algorithm()


def finish_round(reason):
    global round_result
    if round_result is not None:
        return
    state['running']=False
    manual[:]=[0,0]
    round_result=dict(trial_report(pose()),id=round_id,reason=reason)
    if reason=='time':
        round_result['trace_end_t']=round(sim.getTime()-trace_start,6)
    if round_tolerance is not None:
        set_practice_light(1)
    log('本轮结果已记录')
    previous_phase=diagnostics.phase
    diagnostics.phase='round_end'
    record('round_finished',reason=reason,stage=trial.stage)
    diagnostics.phase=previous_phase


def receive(message):
    global algorithm,firmware,round_error,effects,round_faults
    data=json.loads(message)
    command=data.get('command')
    if command=='start':
        diagnostics.phase='panel_start'
        if sim.getTime()<ready_at:
            log('等待车体和物块停稳后开始')
            return
        if round_result is not None:
            log('本轮已结束，请先重新摆位')
            return
        round_error=None
        if round_tolerance is not None and round_tolerance['observed'] is None:
            round_tolerance['observed']=observe_tolerance_start()
        if state['mode']=='firmware' and firmware is None:
            diagnostics.phase='firmware_start'
            record('firmware_start_requested')
            if round_faults or round_tolerance is not None:
                # Hash the exact runtime setup passed to Renode, including live
                # edits made after the Webots scene was generated.
                settings=json.loads((ROOT/'firmware.json').read_text(encoding='utf-8-sig'))
                board=json.loads((ROOT/'board.json').read_text(encoding='utf-8-sig'))
                if round_faults:
                    round_faults.signature=scene_signature(settings,board)
                else:
                    round_tolerance['scene_signature']=tolerance_signature(settings,board)
                firmware=FirmwareBridge(settings,board)
            else:
                firmware=FirmwareBridge()
            log('STM32固件已加载，开始同步执行')
            record('firmware_started')
            diagnostics.phase='panel_start'
        if state['elapsed']==0:
            effects=Imperfections(HW.get('imperfections',{}))
        if not state['running']:
            state['running']=True
            record('run_started',stage=trial.stage)
    elif command=='pause':
        diagnostics.phase='panel_pause'
        if state['running']:
            record('run_paused',reason='manual',firmware_active=firmware is not None)
        if round_tolerance is not None:
            reset()
            return
        state['running']=False;manual[:]=[0,0]
        round_faults=None
    elif command=='reset':reset(data.get('group'),data.get('color'),data.get('stage'),data.get('limit'),
                                data.get('fault_plan'),data.get('tolerance_id'))
    elif command=='end_tolerance':
        if round_tolerance is not None and data.get('round_id')==round_id:
            reset()
    elif command=='finish_round':
        finish_round('manual')
    elif command=='mode':
        diagnostics.phase='panel_mode'
        if data['value'] not in CHOICES['mode']:
            raise ValueError('未知的控制模式。')
        close_firmware('switch_mode')
        effects.queue.clear()
        round_faults=None
        state['mode']=data['value'];state['running']=False;manual[:]=[0,0]
        record('mode_changed')
        if round_tolerance is not None:
            reset()
        elif state['mode'] in ('member','firmware'):
            algorithm=load_algorithm()
        log('模式已切换，点击开始运行')
    elif command=='reload':
        diagnostics.phase='panel_reload'
        if round_tolerance is not None:
            reset()
        else:
            algorithm=load_algorithm()
        close_firmware('reload')
        effects.queue.clear()
        round_faults=None
        state['running']=False
        log('程序已重载；点击开始运行，固件将从复位状态执行')
    elif command=='uart':queue_uart(data['value'])
    elif command=='drive':
        diagnostics.phase='panel_drive'
        if round_tolerance is not None:
            reset()
            return
        if round_result is not None or sim.getTime()<ready_at:
            return
        if state['elapsed']==0 and not state['running']:
            effects=Imperfections(HW.get('imperfections',{}))
        if firmware:
            close_firmware('manual_override');effects.queue.clear()
        previous_mode=state['mode']
        round_faults=None
        manual[:]=data['value'];state['running']=True;state['mode']='manual'
        if previous_mode!='manual':
            record('mode_changed',reason='manual_override')
    elif command=='gripper':
        action=data['value']
        if claw.command(action):
            log({'grab':'夹爪正在合拢','release':'夹爪正在张开','lift':'升降机构正在抬起','lower':'升降机构正在降下'}[action])
    elif command=='joint':
        joint_motors[data['name']].setPosition(float(data['value']))
        log('关节目标已更新：'+data['name'])
    elif command=='snapshot':
        pixels=camera.getImage()
        if pixels is None:
            log('请等待相机生成第一帧')
            return
        destination=ROOT/'output';destination.mkdir(exist_ok=True)
        raw=np.frombuffer(pixels,np.uint8).reshape((IMAGE_HEIGHT,IMAGE_WIDTH,4))[:,:,:3]
        cv2.imencode('.png',raw)[1].tofile(str(destination/f'camera-{tick}.png'))
        log('摄像头原图已保存至 simulator/output 文件夹')
    elif command=='channel':
        reset(stage='channel')

capture=os.environ.get('YUEGUANG_CAPTURE_DIR')
diagnostics.phase='webots_setup'
car.saveState('practice-ready')
diagnostics.phase='controller_ready'
record('controller_ready',ir_count=len(ir),sonar_count=len(sonar),stage=trial.stage,
       vision_link=vision_link is not None,firmware_configured=configured)
diagnostics.phase='simulation_step'
while sim.step(STEP)!=-1:
    tick+=1
    while True:
        message=sim.wwiReceiveText()
        if message is None:break
        if not message.startswith('{'):continue
        diagnostics.phase='panel_message'
        update_log_context()
        try:receive(message)
        except Exception as error:
            report_error(error)
            round_error=str(error)
            if round_tolerance is not None:
                set_practice_light(1)
            state['running']=False;log(f'算法或命令错误：{error}');traceback.print_exc()
    diagnostics.phase='simulation_step'
    key=sim.getKeyboard().getKey()
    if key!=-1 and state['mode']=='manual' and round_result is None and sim.getTime()>=ready_at:
        keyboard_driving=True
        keys=[]
        while key!=-1:keys.append(key);key=sim.getKeyboard().getKey()
        manual[:]=[0,0]
        if ord('W') in keys:manual[:]=[.12,.12]
        if ord('S') in keys:manual[:]=[-.10,-.10]
        if ord('A') in keys:manual[:]=[-.07,.07]
        if ord('D') in keys:manual[:]=[.07,-.07]
        if ord('E') in keys and ord('E') not in grip_keys:
            claw.command('release' if claw.closing else 'grab')
        if ord('Q') in keys and ord('Q') not in grip_keys:
            claw.command('lower' if claw.raised else 'lift')
        grip_keys={k for k in keys if k in (ord('E'),ord('Q'))}
        if ord(' ') in keys:state['running']=False
        else:state['running']=True
    elif keyboard_driving:
        manual[:]=[0,0];keyboard_driving=False;grip_keys.clear()
    current_encoder=[d.getValue()*sign for d,sign in zip(encoders,WHEEL_SIGNS)]
    wheel_speed=[(a-b)*R/DT for a,b in zip(current_encoder,last_encoder)]
    last_encoder=current_encoder
    firmware_vision=state['running'] and state['mode']=='firmware' and tick%FIRMWARE_VISION_TICKS==0
    panel_preview=tick%CAMERA_PREVIEW_TICKS==0
    panel_image=None
    image=None
    link_frame=vision_link is not None and tick%(64//STEP)==0
    if panel_preview or firmware_vision or link_frame or (state['running'] and state['mode']=='member') or (capture and tick==960//STEP):
        pixels=camera.getImage()
        if pixels:
            image=np.frombuffer(pixels,np.uint8).reshape((IMAGE_HEIGHT,IMAGE_WIDTH,4))[:,:,:3].copy()
    if panel_preview and image is not None:
        longest=max(IMAGE_WIDTH,IMAGE_HEIGHT)
        preview_image=image if longest<=480 else cv2.resize(image,
            (max(1,round(IMAGE_WIDTH*480/longest)),max(1,round(IMAGE_HEIGHT*480/longest))),interpolation=cv2.INTER_AREA)
        ok,jpg=cv2.imencode('.jpg',preview_image,[cv2.IMWRITE_JPEG_QUALITY,70])
        if ok:panel_image=base64.b64encode(jpg).decode('ascii')
    if link_frame and image is not None:
        ok,png=cv2.imencode('.png',image)
        if ok:
            vision_link.camera(png.tobytes(),round(sim.getTime(),6))
    if vision_link:
        queue_uart(vision_link.receive(bool(state['running'] and state['mode']=='firmware' and firmware)))
    diagnostics.phase='gripper'
    grasp=observe_gripper()
    diagnostics.phase='simulation_step'
    sensors=dict(ir=[calibrated_ir(i,d.getValue()/1000) for i,d in enumerate(ir)],
                 sonar={k:calibrated_sonar(k,d.getValue()) for k,d in sonar.items()},
                 encoder=dict(zip(('left','right'),current_encoder)),
                 speed=dict(zip(('left','right'),wheel_speed)),
                 joints={name:sensor.getValue() for name,sensor in joint_sensors.items()},
                 carrying=state['carrying'],vision={})
    sonar_fault=bool(round_faults and state['running'] and round_faults.sample_sonar(round_elapsed_ms()))
    if sonar_fault:
        # Keep existing numeric max-range convention for Python member programs.
        sensors['sonar']={key:HW['sonar']['range_mm']/1000 for key in sensors['sonar']}
        sensors['sonar_no_echo']=True
    command=dict(left=0,right=0)
    pwm,uart_in,uart_out=None,[],[]
    firmware_input=0
    if state['running']:
        cycle_start=state['elapsed']
        state['elapsed']+=DT
        active_steps+=1
        try:
            if state['mode']=='manual':command=dict(zip(('left','right'),manual))
            elif state['mode']=='firmware':
                if firmware_vision and image is not None:
                    diagnostics.phase='member_vision'
                    sensors['vision']=algorithm.vision(image)
                    queue_uart(sensors['vision'].get('uart_tx',''),cycle_start)
                # UART input waits for the next MCU sampling boundary.
                diagnostics.phase='firmware_step'
                uart_data=effects.receive(cycle_start)
                if round_faults:
                    uart_data=round_faults.virtual_uart_input(round(cycle_start*1000),uart_data)
                uart_in=list(uart_data)
                # The Renode sonar emits an echo for 0.02..4.0 meters. 0 is
                # reserved here for no pulse; the Python/UI view stays at max range.
                firmware_sensors=dict(sensors,sonar={key:0 for key in sensors['sonar']}) if sonar_fault else sensors
                firmware_status=firmware.step(firmware_sensors,DT,uart_data)
                # The panel trace shows max range for a fault. Preserve the
                # different value delivered to the firmware without duplicating sensors.
                firmware_input=2 if sonar_fault else 1
                firmware_status['uart_dropped']=effects.dropped
                pwm=[firmware_status[side] for side in ('left','right')]
                uart_out=firmware_status['uart']
                if vision_link and uart_out:
                    vision_link.transmit(uart_out)
                command={side:firmware_status[side]*MAX_SPEED for side in ('left','right')}
                command['gripper']=firmware_status.get('gripper')
                command['gripper_lift']=firmware_status.get('gripper_lift')
            elif algorithm:
                if image is not None:
                    diagnostics.phase='member_vision'
                    sensors['vision']=algorithm.vision(image)
                diagnostics.phase='member_step'
                command=algorithm.step(sensors,DT)
            diagnostics.phase='actuator'
            claw.command(command.get('gripper'))
            claw.command(command.get('gripper_lift'))
        except Exception as error:
            report_error(error)
            round_error=str(error)
            if round_tolerance is not None:
                set_practice_light(1)
            state['running']=False;log(f'程序错误，已停车：{error}');traceback.print_exc()
    diagnostics.phase='actuator'
    for i,name in enumerate(('left','right')):
        target=MAX_SPEED*effects.motor(i,float(command.get(name,0))/MAX_SPEED) if state['running'] else 0
        actuated[i]+=(target-actuated[i])*RESPONSE
        motors[i].setVelocity(max(-motors[i].getMaxVelocity(),min(motors[i].getMaxVelocity(),actuated[i]/R*WHEEL_SIGNS[i])))
    if state['running']:
        for name,position in command.get('joints',{}).items():joint_motors[name].setPosition(float(position))
    diagnostics.phase='simulation_step'
    p=pose();finish=CONFIG['finish']
    contact_centers=[(0,CONFIG['axle_length']/2),(0,-CONFIG['axle_length']/2),(HW['caster']['x_mm']/1000,0)]
    def contact_inside(local):
        x=p[0]+local[0]*math.cos(p[2])-local[1]*math.sin(p[2])
        y=p[1]+local[0]*math.sin(p[2])+local[1]*math.cos(p[2])
        return abs(x-finish[0])<=finish_half_size[0] and abs(y-finish[1])<=finish_half_size[1]
    in_finish=all(contact_inside(point) for point in contact_centers)
    still=still+DT if in_finish and max(abs(v) for v in wheel_speed)<.005 else 0
    state['parked']=still>=3
    state['aligned']=False
    if state['parked']:
        yaw_error=abs(angle(p[2]-round(p[2]/(math.pi/2))*(math.pi/2)))
        width,height=CONFIG['body_size']
        ext_x=(abs(math.cos(p[2]))*width+abs(math.sin(p[2]))*height)/2
        ext_y=(abs(math.sin(p[2]))*width+abs(math.cos(p[2]))*height)/2
        state['aligned']=(abs(p[0]-finish[0])+ext_x<=finish_half_size[0]
                          and abs(p[1]-finish[1])+ext_y<=finish_half_size[1] and yaw_error<=math.radians(10))
    diagnostics.phase='contact'
    wall_contact=touches_channel_wall()
    if wall_contact:
        if not state['collision']:
            incident_flags.add('wall_contact')
            record('wall_contact',stage=trial.stage)
            if round_faults and state['running']:
                round_faults.observe('wall_contact',round_elapsed_ms(),p)
        state['collision']=True
        if state['elapsed']-last_collision_time>1:
            log('超声波区域发生接触');last_collision_time=state['elapsed']
    diagnostics.phase='simulation_step'
    if round_result is None and sim.getTime()>=ready_at:
        previous_falls=trial.falls
        trial.update(p,grasp,wall_contact,firmware_status.get('sonar_triggers',0))
        if trial.falls>previous_falls:
            incident_flags.add('block_dropped')
            if round_faults:
                round_faults.observe('block_dropped',round_elapsed_ms(),p)
    if state['running'] and round_limit is not None and state['elapsed']+1e-9>=round_limit:
        finish_round('time')
    block_pose=block.getPosition()
    sample=dict(t=round(sim.getTime()-trace_start,6), mode=state['mode'], running=state['running'],
                pwm=pwm, speed=wheel_speed, encoder=current_encoder, ir=sensors['ir'],
                sonar=sensors['sonar'], uart_in=uart_in, uart_out=uart_out, pose=p,
                grip=grasp, block_position=block_pose)
    if firmware_input:
        sample['firmware_input']=firmware_input
    if incident_flags:
        sample['incidents']=sorted(incident_flags)
        incident_flags.clear()
    trace.append(sample)
    if tick%PANEL_TICKS==0:
        now=time.monotonic()
        if panel_preview:
            panel_rate=active_steps*DT/max(now-last_view_wall,.001)
            last_view_wall=now
            active_steps=0
        payload=dict(state,sensors=sensors,pose=p,firmware=firmware_status,panel_session=panel_session,panel_tick=tick,
                     project_version=PROJECT_VERSION,
                     trace=trace,trace_epoch=trace_epoch,rate=panel_rate,claw=grasp,block_position=block_pose,
                     trial=dict(trial_report(p),id=round_id),round_result=round_result,
                     resetting=sim.getTime()<ready_at,round_error=round_error,
                     target_side='left' if state['group']%2==0 else 'right')
        if panel_image is not None:payload['image']=panel_image
        sim.wwiSendText(json.dumps(payload,ensure_ascii=True,separators=(',',':')))
        trace.clear()
    if capture and tick==960//STEP:
        destination=Path(capture);destination.mkdir(parents=True,exist_ok=True)
        sim.exportImage(str(destination/'scene.png'),100)
        cv2.imencode('.png',image)[1].tofile(str(destination/'camera.png'))
        (destination/'sensors.json').write_text(json.dumps(sensors,indent=2),encoding='utf-8')
    if os.environ.get('YUEGUANG_AUTOCLOSE') and sim.getTime()>float(os.environ['YUEGUANG_AUTOCLOSE']):
        if capture:
            (Path(capture)/'result.json').write_text(json.dumps(dict(state,pose=pose()),ensure_ascii=False,indent=2),encoding='utf-8')
        sim.simulationQuit(0)

diagnostics.phase='shutdown'
close_firmware('shutdown')
if vision_link:vision_link.close()
diagnostics.event('shutdown',reason='shutdown')

