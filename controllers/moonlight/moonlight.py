"""Webots 场景桥接与练习面板。会员算法在 member_algorithm.py。"""
from pathlib import Path
import base64
import importlib.util
import json
import math
import os
import sys
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
CONFIG=json.loads((ROOT/'scene.json').read_text(encoding='utf-8'))
DT=0.032
R=CONFIG['wheel_radius']

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
motors=[sim.getDevice(f'{side}_motor') for side in ('left','right')]
encoders=[sim.getDevice(f'{side}_encoder') for side in ('left','right')]
ir=[sim.getDevice(f'ir{i}') for i in range(7)]
sonar={name:sim.getDevice(f'sonar_{name}') for name in ('front','left','right')}
camera=sim.getDevice('camera')
for motor in motors:
    motor.setPosition(float('inf'))
    motor.setVelocity(0)
for device in encoders+ir+list(sonar.values())+[camera]:device.enable(32)
sim.getKeyboard().enable(32)

state=dict(mode='manual',running=False,group=2,color='red',carrying=False,
           elapsed=0.0,collision=False,grasped=False,placed=None,parked=False,aligned=False,
           message='选择模式后开始；会员模板初始为停车。',events=[])
manual=[0.0,0.0]
actuated=[0.0,0.0]
last_encoder=[0.0,0.0]
still=0.0
algorithm=None
last_image=None
tick=0
last_collision_time=-10
keyboard_driving=False

def log(text):
    state['message']=text
    state['events']=(state['events']+[f"{state['elapsed']:.1f}s · {text}"])[-8:]
    print('EVENT',text,flush=True)

def pose():
    p=car.getPosition();o=car.getOrientation()
    return [p[0],p[1],math.atan2(o[3],o[0])]

def grip_position():
    p=pose()
    return [p[0]+.12*math.cos(p[2]),p[1]+.12*math.sin(p[2]),.085]

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
        if math.dist(grip_position()[:2],block.getPosition()[:2])<.05:
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
    global still,last_encoder,algorithm
    if group is not None:state['group']=max(1,int(group))
    if color in ('red','yellow','blue'):state['color']=color
    translation.setSFVec3f(CONFIG['start']+[.032])
    rotation.setSFRotation([0,0,1,0]);car.resetPhysics()
    block_position.setSFVec3f(CONFIG['pickup']+[.035]);block.resetPhysics()
    block_color.setSFColor({'red':[.9,.025,.04],'yellow':[1,.78,.015],'blue':[.025,.14,.9]}[state['color']])
    tag_texture.setMFString(0,f"textures/tag{state['group']%2}.png")
    for motor in motors:motor.setVelocity(0)
    actuated[:]=[0,0];manual[:]=[0,0];last_encoder=[d.getValue() for d in encoders]
    state.update(running=False,carrying=False,elapsed=0,collision=False,grasped=False,
                 placed=None,parked=False,aligned=False,events=[],message='场景已重置')
    still=0
    if state['mode']=='member':
        algorithm=load_algorithm()

def receive(message):
    global algorithm
    data=json.loads(message)
    command=data.get('command')
    if command=='start':state['running']=True
    elif command=='pause':state['running']=False;manual[:]=[0,0]
    elif command=='reset':reset(data.get('group'),data.get('color'))
    elif command=='mode':
        state['mode']=data['value'];state['running']=False;manual[:]=[0,0]
        if state['mode']=='member':
            algorithm=load_algorithm()
        log('模式已切换，点击开始运行')
    elif command=='reload':
        algorithm=load_algorithm()
        log('算法已重载；状态已重新初始化')
    elif command=='drive':
        manual[:]=data['value'];state['running']=True;state['mode']='manual'
    elif command=='gripper':gripper(data['value'])
    elif command=='snapshot':
        destination=ROOT/'output';destination.mkdir(exist_ok=True)
        raw=np.frombuffer(camera.getImage(),np.uint8).reshape((240,320,4))[:,:,:3]
        cv2.imencode('.png',raw)[1].tofile(str(destination/f'camera-{tick}.png'))
        log('摄像头原图已保存至 output 文件夹')
    elif command=='channel':
        a,b=CONFIG['channel']
        translation.setSFVec3f([b[0]-.09,(a[1]+b[1])/2,.032])
        rotation.setSFRotation([0,0,1,math.pi]);car.resetPhysics()
        state['running']=False;actuated[:]=[0,0]
        log('已放到超声波区域入口，车头朝左')

capture=os.environ.get('YUEGUANG_CAPTURE_DIR')
while sim.step(32)!=-1:
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
    current_encoder=[d.getValue() for d in encoders]
    wheel_speed=[(a-b)*R/DT for a,b in zip(current_encoder,last_encoder)]
    last_encoder=current_encoder
    image=np.frombuffer(camera.getImage(),np.uint8).reshape((240,320,4))[:,:,:3].copy()
    # 原始图像供会员自行识别，约每 0.2 秒更新面板。
    if tick%6==0:
        ok,jpg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,70])
        last_image=base64.b64encode(jpg).decode() if ok else None
    sensors=dict(ir=[max(0,min(1,d.getValue()/1000)) for d in ir],
                 sonar={k:d.getValue() for k,d in sonar.items()},
                 encoder=dict(zip(('left','right'),current_encoder)),
                 speed=dict(zip(('left','right'),wheel_speed)),
                 carrying=state['carrying'],vision={})
    command=dict(left=0,right=0)
    if state['running']:
        state['elapsed']+=DT
        try:
            if state['mode']=='manual':command=dict(zip(('left','right'),manual))
            elif algorithm:
                sensors['vision']=algorithm.vision(image)
                command=algorithm.step(sensors,DT)
            gripper(command.get('gripper'))
        except Exception as error:
            state['running']=False;log(f'算法错误，已停车：{error}');traceback.print_exc()
    for i,name in enumerate(('left','right')):
        target=max(-.25,min(.25,float(command.get(name,0)))) if state['running'] else 0
        actuated[i]+=(target-actuated[i])*DT/(.12+DT)
        motors[i].setVelocity(actuated[i]/R)
    if state['carrying']:
        block_position.setSFVec3f(grip_position());block.setVelocity([0]*6)
    elif tick%8==0:state['placed']=place_result()
    p=pose();finish=CONFIG['finish']
    contact_centers=[(0,.0625),(0,-.0625),(-.055,0)]
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
    if tick%6==0:
        payload=dict(state,sensors=sensors,pose=p,image=last_image,
                     target_side='left' if state['group']%2==0 else 'right')
        sim.wwiSendText(json.dumps(payload,ensure_ascii=True,separators=(',',':')))
    if capture and tick==30:
        destination=Path(capture);destination.mkdir(parents=True,exist_ok=True)
        sim.exportImage(str(destination/'scene.png'),100)
        cv2.imencode('.png',image)[1].tofile(str(destination/'camera.png'))
        (destination/'sensors.json').write_text(json.dumps(sensors,indent=2),encoding='utf-8')
    if os.environ.get('YUEGUANG_AUTOCLOSE') and sim.getTime()>float(os.environ['YUEGUANG_AUTOCLOSE']):
        if capture:
            (Path(capture)/'result.json').write_text(json.dumps(dict(state,pose=pose()),ensure_ascii=False,indent=2),encoding='utf-8')
        sim.simulationQuit(0)

