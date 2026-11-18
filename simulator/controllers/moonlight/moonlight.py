"""Webots 场景桥接与练习面板。会员算法在 member_algorithm.py。"""
from pathlib import Path
import base64
import importlib.util
import json
import math
import os
import sys
import time
import traceback
# Webots R2025a 的 Windows 启动器可能以错误编码传递中文安装目录。
# 用已找到的 controller 包位置恢复真实运行时路径，再加载其 DLL。
controller_spec=importlib.util.find_spec('controller')
os.environ['WEBOTS_HOME']=str(Path(controller_spec.origin).parents[4])
if sys.platform=='win32':
    dll_directory=os.add_dll_directory(str(Path(os.environ['WEBOTS_HOME'])/'msys64/mingw64/bin'))
from controller import Supervisor
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from firmware_bridge import FirmwareBridge
from imperfections import Imperfections
from vision_link import VisionLink
CONFIG=json.loads((ROOT/'scene.json').read_text(encoding='utf-8'))
STEP=int(CONFIG.get('step_ms',32))
DT=STEP/1000
PREVIEW_TICKS=192//STEP
R=CONFIG['wheel_radius']
HW=CONFIG['robot']
Z=CONFIG['robot_origin_z']
MAX_SPEED=HW['wheels']['max_speed_m_s']
RESPONSE=-math.expm1(-DT/HW['wheels']['response_s']) if HW['wheels']['response_s']>0 else 1
MODEL=HW.get('model',{'kind':'parametric'})
IMPORTED=MODEL['kind']=='urdf'
WHEEL_NAMES=[MODEL[side+'_joint'] if IMPORTED else side+'_motor' for side in ('left','right')]
WHEEL_SIGNS=[MODEL.get(side+'_sign',1) if IMPORTED else 1 for side in ('left','right')]

def angle(v):
    return (v+math.pi)%(2*math.pi)-math.pi

def load_algorithm():
    path=ROOT/'controllers/moonlight/member_algorithm.py'
    spec=importlib.util.spec_from_file_location('practice_algorithm',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Algorithm()

sim=Supervisor()
car=sim.getSelf()
block=sim.getFromDef('BLOCK')
tag_texture=sim.getFromDef('TAG_TEXTURE').getField('url')
block_color=sim.getFromDef('BLOCK_MATERIAL').getField('baseColor')
translation=car.getField('translation')
rotation=car.getField('rotation')
block_position=block.getField('translation')
motors=[sim.getDevice(name) for name in WHEEL_NAMES]
encoders=[sim.getDevice(name+'_sensor') if IMPORTED else sim.getDevice(side+'_encoder') for name,side in zip(WHEEL_NAMES,('left','right'))]
joint_motors={j['name']:sim.getDevice(j['name']) for j in MODEL.get('joints',[]) if j['name'] not in WHEEL_NAMES}
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
last_image=None
tick=0
last_collision_time=-10
keyboard_driving=False
trace_epoch=0
trace_start=sim.getTime()
trace=[]
last_view_wall=time.monotonic()
active_steps=0
vision_link=None

def log(text):
    state['message']=text
    state['events']=(state['events']+[f"{state['elapsed']:.1f}s · {text}"])[-8:]
    print('EVENT',text,flush=True)


if port:=int(HW.get('vision_link_port',0)):
    try:
        vision_link=VisionLink(port)
        log(f'本机图像与串口接口：127.0.0.1:{port}')
    except OSError as error:
        log(f'本机图像与串口接口未启动：{error}')


def queue_uart(value, at=None):
    if state['mode']=='firmware':
        effects.send(state['elapsed'] if at is None else at,
                     value.encode('utf-8') if isinstance(value,str) else bytes(value))

def pose():
    p=car.getPosition();o=car.getOrientation()
    return [p[0],p[1],math.atan2(o[3],o[0])]

def grip_position():
    p=pose()
    reach=HW['gripper']['reach_mm']/1000
    return [p[0]+reach*math.cos(p[2]),p[1]+reach*math.sin(p[2]),HW['gripper']['carry_height_mm']/1000]

def place_result():
    p=block.getPosition()
    nearest=min(CONFIG['circles'],key=lambda c:math.dist(p[:2],c['center']))
    distance=math.dist(p[:2],nearest['center'])
    if distance<=.02125:
        correct=(nearest['side']==('left' if state['group']%2==0 else 'right') and nearest['color']==state['color'])
        return dict(side=nearest['side'],color=nearest['color'],correct=correct,offset_mm=round(distance*1000,1))
    return None

def gripper(action):
    if action=='grab' and not state['carrying']:
        if math.dist(grip_position()[:2],block.getPosition()[:2])<HW['gripper']['grab_distance_mm']/1000:
            state['carrying']=True;state['grasped']=True;state['placed']=None
            block.resetPhysics();log('已夹取物块')
        else:log('夹爪与物块距离较远，请继续接近')
    elif action=='release' and state['carrying']:
        state['carrying']=False
        p=grip_position();p[2]=.036
        block_position.setSFVec3f(p);block.resetPhysics()
        state['placed']=place_result()
        log('已释放：'+('目标圈正确' if state['placed'] and state['placed']['correct'] else '可查看最终放置位置'))

def reset(group=None,color=None):
    global still,last_encoder,algorithm,firmware,firmware_status,effects
    global trace_epoch,trace_start
    if firmware:firmware.close()
    firmware=None;firmware_status={}
    effects=Imperfections(HW.get('imperfections',{}))
    trace_epoch+=1
    trace_start=sim.getTime()
    trace.clear()
    if group is not None:state['group']=max(1,int(group))
    if color in ('red','yellow','blue'):state['color']=color
    translation.setSFVec3f(CONFIG['start']+[Z])
    rotation.setSFRotation([0,0,1,0]);car.resetPhysics()
    block_position.setSFVec3f(CONFIG['pickup']+[.035]);block.resetPhysics()
    block_color.setSFColor({'red':[.9,.025,.04],'yellow':[1,.78,.015],'blue':[.025,.14,.9]}[state['color']])
    tag_texture.setMFString(0,f"textures/tag{state['group']%2}.png")
    for motor in motors:motor.setVelocity(0)
    for motor in joint_motors.values():motor.setPosition(0)
    actuated[:]=[0,0];manual[:]=[0,0];last_encoder=[d.getValue()*sign for d,sign in zip(encoders,WHEEL_SIGNS)]
    state.update(running=False,carrying=False,elapsed=0,collision=False,grasped=False,
                 placed=None,parked=False,aligned=False,events=[],message='场景已重置')
    still=0
    if state['mode'] in ('member','firmware'):
        algorithm=load_algorithm()

def receive(message):
    global algorithm,firmware,firmware_status
    data=json.loads(message)
    command=data.get('command')
    if command=='start':
        if state['mode']=='firmware' and firmware is None:
            firmware=FirmwareBridge()
            log('STM32固件已加载，开始同步执行')
        state['running']=True
    elif command=='pause':state['running']=False;manual[:]=[0,0]
    elif command=='reset':reset(data.get('group'),data.get('color'))
    elif command=='mode':
        if firmware:firmware.close()
        firmware=None;firmware_status={};effects.queue.clear()
        state['mode']=data['value'];state['running']=False;manual[:]=[0,0]
        if state['mode'] in ('member','firmware'):
            algorithm=load_algorithm()
        log('模式已切换，点击开始运行')
    elif command=='reload':
        algorithm=load_algorithm()
        if firmware:firmware.close()
        firmware=None;firmware_status={};effects.queue.clear()
        state['running']=False
        log('程序已重载；点击开始运行，固件将从复位状态执行')
    elif command=='uart':queue_uart(data['value'])
    elif command=='drive':
        if firmware:
            firmware.close();firmware=None;firmware_status={};effects.queue.clear()
        manual[:]=data['value'];state['running']=True;state['mode']='manual'
    elif command=='gripper':gripper(data['value'])
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
        a,b=CONFIG['channel']
        translation.setSFVec3f([b[0]-CONFIG['body_size'][0]/2-.015,(a[1]+b[1])/2,Z])
        rotation.setSFRotation([0,0,1,math.pi]);car.resetPhysics()
        state['running']=False;actuated[:]=[0,0]
        log('已放到超声波区域入口，车头朝左')

capture=os.environ.get('YUEGUANG_CAPTURE_DIR')
while sim.step(STEP)!=-1:
    tick+=1
    while True:
        message=sim.wwiReceiveText()
        if message is None:break
        if not message.startswith('{'):continue
        try:receive(message)
        except Exception as error:
            state['running']=False;log(f'算法或命令错误：{error}');traceback.print_exc()
    key=sim.getKeyboard().getKey()
    if key!=-1 and state['mode']=='manual':
        keyboard_driving=True
        keys=[]
        while key!=-1:keys.append(key);key=sim.getKeyboard().getKey()
        manual[:]=[0,0]
        if ord('W') in keys:manual[:]=[.12,.12]
        if ord('S') in keys:manual[:]=[-.10,-.10]
        if ord('A') in keys:manual[:]=[-.07,.07]
        if ord('D') in keys:manual[:]=[.07,-.07]
        if ord('E') in keys:gripper('release' if state['carrying'] else 'grab')
        if ord(' ') in keys:state['running']=False
        else:state['running']=True
    elif keyboard_driving:
        manual[:]=[0,0];keyboard_driving=False
    current_encoder=[d.getValue()*sign for d,sign in zip(encoders,WHEEL_SIGNS)]
    wheel_speed=[(a-b)*R/DT for a,b in zip(current_encoder,last_encoder)]
    last_encoder=current_encoder
    preview=tick%PREVIEW_TICKS==0
    image=None
    link_frame=vision_link is not None and tick%(64//STEP)==0
    if preview or link_frame or (state['running'] and state['mode']=='member') or (capture and tick==960//STEP):
        pixels=camera.getImage()
        if pixels:
            image=np.frombuffer(pixels,np.uint8).reshape((IMAGE_HEIGHT,IMAGE_WIDTH,4))[:,:,:3].copy()
    if preview and image is not None:
        ok,jpg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,70])
        last_image=base64.b64encode(jpg).decode() if ok else None
    if link_frame and image is not None:
        ok,png=cv2.imencode('.png',image)
        if ok:
            vision_link.camera(png.tobytes(),round(sim.getTime(),6))
    if vision_link:
        queue_uart(vision_link.receive(bool(state['running'] and state['mode']=='firmware' and firmware)))
    sensors=dict(ir=[effects.infrared(d.getValue()/1000) for d in ir],
                 sonar={k:effects.sonar(d.getValue(),HW['sonar']['range_mm']/1000) for k,d in sonar.items()},
                 encoder=dict(zip(('left','right'),current_encoder)),
                 speed=dict(zip(('left','right'),wheel_speed)),
                 joints={name:sensor.getValue() for name,sensor in joint_sensors.items()},
                 carrying=state['carrying'],vision={})
    command=dict(left=0,right=0)
    pwm,uart_in,uart_out=None,[],[]
    if state['running']:
        cycle_start=state['elapsed']
        state['elapsed']+=DT
        active_steps+=1
        try:
            if state['mode']=='manual':command=dict(zip(('left','right'),manual))
            elif state['mode']=='firmware':
                if preview and image is not None:
                    sensors['vision']=algorithm.vision(image)
                    queue_uart(sensors['vision'].get('uart_tx',''),cycle_start)
                # UART input waits for the next MCU sampling boundary.
                uart_data=effects.receive(cycle_start)
                uart_in=list(uart_data)
                firmware_status=firmware.step(sensors,DT,uart_data)
                firmware_status['uart_dropped']=effects.dropped
                pwm=[firmware_status[side] for side in ('left','right')]
                uart_out=firmware_status['uart']
                if vision_link and uart_out:
                    vision_link.transmit(uart_out)
                command={side:firmware_status[side]*MAX_SPEED for side in ('left','right')}
                command['gripper']=firmware_status.get('gripper')
            elif algorithm:
                if image is not None:
                    sensors['vision']=algorithm.vision(image)
                command=algorithm.step(sensors,DT)
            gripper(command.get('gripper'))
        except Exception as error:
            state['running']=False;log(f'程序错误，已停车：{error}');traceback.print_exc()
    for i,name in enumerate(('left','right')):
        target=MAX_SPEED*effects.motor(i,float(command.get(name,0))/MAX_SPEED) if state['running'] else 0
        actuated[i]+=(target-actuated[i])*RESPONSE
        motors[i].setVelocity(max(-motors[i].getMaxVelocity(),min(motors[i].getMaxVelocity(),actuated[i]/R*WHEEL_SIGNS[i])))
    if state['running']:
        for name,position in command.get('joints',{}).items():joint_motors[name].setPosition(float(position))
    if state['carrying']:
        block_position.setSFVec3f(grip_position());block.setVelocity([0]*6)
    elif tick%(256//STEP)==0:state['placed']=place_result()
    p=pose();finish=CONFIG['finish']
    contact_centers=[(0,CONFIG['axle_length']/2),(0,-CONFIG['axle_length']/2),(HW['caster']['x_mm']/1000,0)]
    def contact_inside(local):
        x=p[0]+local[0]*math.cos(p[2])-local[1]*math.sin(p[2])
        y=p[1]+local[0]*math.sin(p[2])+local[1]*math.cos(p[2])
        return abs(x-finish[0])<=.1025 and abs(y-finish[1])<=.1025
    in_finish=all(contact_inside(point) for point in contact_centers)
    still=still+DT if in_finish and max(abs(v) for v in wheel_speed)<.005 else 0
    state['parked']=still>=3
    state['aligned']=False
    if state['parked']:
        yaw_error=abs(angle(p[2]-round(p[2]/(math.pi/2))*(math.pi/2)))
        width,height=CONFIG['body_size']
        ext_x=(abs(math.cos(p[2]))*width+abs(math.sin(p[2]))*height)/2
        ext_y=(abs(math.sin(p[2]))*width+abs(math.cos(p[2]))*height)/2
        state['aligned']=(abs(p[0]-finish[0])+ext_x<=.1025 and abs(p[1]-finish[1])+ext_y<=.1025 and yaw_error<=math.radians(10))
    for contact in car.getContactPoints(True):
        point=contact.point
        a,b=CONFIG['channel']
        if point[2]>.015 and a[0]-.025<point[0]<b[0]+.025 and b[1]-.025<point[1]<a[1]+.025:
            state['collision']=True
            if state['elapsed']-last_collision_time>1:
                log('超声波区域发生接触');last_collision_time=state['elapsed']
            break
    trace.append(dict(t=round(sim.getTime()-trace_start,6), mode=state['mode'], running=state['running'],
                      pwm=pwm, speed=wheel_speed, encoder=current_encoder, ir=sensors['ir'],
                      sonar=sensors['sonar'], uart_in=uart_in, uart_out=uart_out, pose=p))
    if preview:
        now=time.monotonic()
        rate=active_steps*DT/max(now-last_view_wall,.001)
        last_view_wall=now
        active_steps=0
        payload=dict(state,sensors=sensors,pose=p,image=last_image,firmware=firmware_status,
                     trace=trace,trace_epoch=trace_epoch,rate=rate,
                     target_side='left' if state['group']%2==0 else 'right')
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

if firmware:firmware.close()
if vision_link:vision_link.close()

