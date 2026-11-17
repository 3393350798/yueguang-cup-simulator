const {default:config}=await import('./scene.js?v='+Date.now());
const $=id=>document.getElementById(id);
const colors={red:'红色',yellow:'黄色',blue:'蓝色'};
const hardware=config.robot;
const deviceNames={front:'前距',left:'左距',right:'右距',rear:'后距'};
$('hardware-summary').textContent=`${hardware.name} · 底盘 ${hardware.body.length_mm} × ${hardware.body.width_mm} mm · 轮径 ${hardware.wheels.radius_mm*2} mm · 轮距 ${hardware.wheels.track_mm} mm · 红外 ${hardware.ir.positions_mm.length} 路 · 超声波 ${hardware.sonar.sensors.length} 路 · 摄像头 ${hardware.camera.width} × ${hardware.camera.height}`;
const model=hardware.model||{kind:'parametric'};
$('hardware-summary').textContent+=' · '+({urdf:'URDF装配体',mesh:'导入底盘几何',parametric:'参数车模'}[model.kind]);
$('board-summary').textContent=`${config.board.mcu} · ${config.board.netlist?'已导入网表':'未导入网表'} · ${Object.keys(config.board.bindings).length} 项引脚对应 · 支持Renode固件模式`;
for(const joint of model.joints||[]){
  if([model.left_joint,model.right_joint].includes(joint.name))continue;
  $('joint-card').hidden=false;
  const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),button=document.createElement('button'),value=document.createElement('span');
  label.textContent=joint.name+' ';input.type='number';input.value='0';input.step='0.05';input.style.width='85px';input.setAttribute('aria-label',joint.name+'目标位置');
  if(joint.limits?.lower!==undefined)input.min=joint.limits.lower;
  if(joint.limits?.upper!==undefined)input.max=joint.limits.upper;
  button.textContent='设置';button.onclick=()=>send('joint',{name:joint.name,value:Number(input.value)});
  value.id='joint-value-'+joint.name;label.append(input);row.append(label,button,value);$('joint-controls').append(row);
}
document.querySelector('.camera').style.aspectRatio=`${hardware.camera.width}/${hardware.camera.height}`;
const help={manual:'按住方向按钮驾驶，松开停车。在 Webots 画面中也可使用 W/A/S/D，E 夹取/释放，空格停车。',member:'在 member_algorithm.py 中填写自己的算法。模板初始为停车，保存后点击重载算法。',firmware:'先用 import-hardware.bat 导入固件并绑定管脚。点击开始后同步执行MCU；暂停冻结MCU，重置复位MCU。'};
$('uart-send').onclick=()=>{send('uart',{value:$('uart-input').value+'\n'});$('uart-input').value='';};
let state=null,path=[],socket=null,pending=[];
const image=new Image();
const base=new URL('track.png',location.href);
base.searchParams.set('v',Date.now());
image.src=base.href;
function send(command,extra={}){
  const payload=JSON.stringify({command,...extra});
  if(socket?.readyState===1)socket.send(`robot:${new URLSearchParams(location.search).get('name')||'Moonlight'}:${payload}`);
  else pending.push(payload);
}
function connect(){
  const url=new URL(location.href);url.protocol=location.protocol==='https:'?'wss:':'ws:';
  url.pathname=url.pathname.substring(0,url.pathname.indexOf('/robot_windows/')+1);url.search='';url.hash='';
  socket=new WebSocket(url);
  socket.onopen=()=>{socket.send('robot:'+ (new URLSearchParams(location.search).get('name')||'Moonlight') +':init robot window');for(const p of pending)socket.send('robot:'+(new URLSearchParams(location.search).get('name')||'Moonlight')+':'+p);pending=[];};
  socket.onmessage=event=>{
    if(typeof event.data!=='string')return;
    for(const line of event.data.split('\n')){
      if(!line.startsWith('robot:'))continue;
      const packet=JSON.parse(line.slice(6));
      if(packet.name!==(new URLSearchParams(location.search).get('name')||'Moonlight'))continue;
      if(packet.message.startsWith('{'))render(JSON.parse(packet.message));
    }
  };
  socket.onclose=()=>{$('connection').textContent='场景已断开';};
}
function render(data){
  if(!state){$('group').value=data.group;$('color').value=data.color;}
  if(data.elapsed<(state?.elapsed||0))path=[];
  state=data;
  $('connection').textContent=data.running?'运行中':'已连接 · 待运行';
  $('timer').textContent=`${String(Math.floor(data.elapsed/60)).padStart(2,'0')}:${(data.elapsed%60).toFixed(1).padStart(4,'0')}`;
  $('message').textContent=data.message;
  $('mode').value=data.mode;
  $('mode-help').textContent=help[data.mode];
  $('reload').textContent=data.mode==='firmware'?'重载固件与视觉':'重载算法';
  $('firmware-card').hidden=data.mode!=='firmware';
  const fw=data.firmware||{};
  $('firmware-status').textContent=fw.pc?`PC ${fw.pc} · MCU ${fw.virtual_s.toFixed(3)} s · PWM 左 ${fw.left.toFixed(3)} / 右 ${fw.right.toFixed(3)} · 超声触发 ${fw.sonar_triggers}`:'点击开始加载固件';
  $('uart-output').textContent=fw.uart_text||'';
  $('target').textContent=(data.target_side==='left'?'左侧':'右侧')+' · '+colors[data.color];
  $('grasp').textContent=data.carrying?'夹持中':data.grasped?'已完成':'等待';
  $('placed').textContent=data.placed?(data.placed.correct?'正确目标圈':'其他放置圈')+` / ${data.placed.offset_mm} mm`:'尚未进圈';
  $('parking').textContent=data.aligned?'已摆正并停稳':data.parked?'已到达停稳':'等待';
  $('sonars').replaceChildren(...Object.entries(data.sensors.sonar).map(([name,value])=>{const d=document.createElement('div'),label=document.createElement('span'),v=document.createElement('strong');label.textContent=deviceNames[name]||name;v.textContent=(value*100).toFixed(1)+' cm';d.append(label,v);return d;}));
  $('ir').replaceChildren(...data.sensors.ir.map(value=>{const d=document.createElement('div');d.style.background=`rgb(${Math.round(value*235)},${Math.round(value*235)},${Math.round(value*235)})`;d.style.color=value>.5?'#263b45':'#fff';d.textContent=value.toFixed(2);return d;}));
  for(const name of ['left','right'])$('wheel-'+name).textContent=(name==='left'?'左轮 ':'右轮 ')+data.sensors.speed[name].toFixed(3)+' m/s';
  $('collision').textContent=data.collision?'超声波区域有碰撞记录':'区域内无碰撞记录';
  $('collision').classList.toggle('warn',data.collision);
  for(const [name,value] of Object.entries(data.sensors.joints||{}))$('joint-value-'+name).textContent=' 实测 '+value.toFixed(3);
  $('events').replaceChildren(...data.events.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  if(data.image){$('camera').src='data:image/jpeg;base64,'+data.image;$('camera').style.display='block';$('camera-empty').style.display='none';}
  if(!path.length||Math.hypot(data.pose[0]-path.at(-1)[0],data.pose[1]-path.at(-1)[1])>.006)path.push(data.pose);
  if(path.length>2500)path.shift();draw();
}
function draw(){
  const canvas=$('map'),ctx=canvas.getContext('2d'),size=canvas.width;
  ctx.clearRect(0,0,size,size);ctx.fillStyle='#fff';ctx.fillRect(0,0,size,size);
  if(image.complete)ctx.drawImage(image,0,0,size,size);
  if(!config)return;
  const p=point=>[(point[0]/config.floor_size+.5)*size,(.5-point[1]/config.floor_size)*size];
  const a=p(config.channel[0]),b=p(config.channel[1]);
  ctx.strokeStyle='#667b91';ctx.lineWidth=5;
  for(const y of [a[1],b[1]]){ctx.beginPath();ctx.moveTo(a[0],y);ctx.lineTo(b[0],y);ctx.stroke();}
  if(path.length){ctx.strokeStyle='#1caa91a0';ctx.lineWidth=2;ctx.beginPath();path.forEach((v,i)=>{const c=p(v);i?ctx.lineTo(...c):ctx.moveTo(...c);});ctx.stroke();}
  if(state){
    const c=p(state.pose),scale=size/config.floor_size/1000;
    ctx.save();ctx.translate(...c);ctx.rotate(-state.pose[2]);ctx.scale(scale,scale);
    const b=hardware.body,w=hardware.wheels;
    ctx.fillStyle='#133b4d';ctx.fillRect(-b.length_mm/2,-b.width_mm/2,b.length_mm,b.width_mm);
    ctx.fillStyle='#465765';for(const y of [-w.track_mm/2,w.track_mm/2])ctx.fillRect(-w.radius_mm,y-w.width_mm/2,w.radius_mm*2,w.width_mm);
    ctx.strokeStyle='#f6ab3d';ctx.lineWidth=8;ctx.beginPath();ctx.moveTo(b.length_mm/2,0);ctx.lineTo(hardware.gripper.reach_mm,0);ctx.stroke();
    ctx.fillStyle='#3bc2aa';for(const position of hardware.ir.positions_mm){ctx.beginPath();ctx.arc(position[0],-position[1],3,0,Math.PI*2);ctx.fill();}
    ctx.fillStyle='#278fc3';ctx.fillRect(hardware.camera.position_mm[0]-5,-hardware.camera.position_mm[1]-5,10,10);ctx.restore();
  }
  for(const [name,text] of [['start','S'],['finish','F']]){const c=p(config[name]);ctx.fillStyle='#344d5f';ctx.font='600 16px sans-serif';ctx.textAlign='center';ctx.fillText(text,c[0],c[1]+5);}
}
image.onload=draw;
for(const id of ['start','pause','reload','snapshot','channel'])$(id).onclick=()=>send(id);
$('reset').onclick=()=>{path=[];send('reset',{group:Number($('group').value),color:$('color').value});};
$('mode').onchange=()=>{send('mode',{value:$('mode').value});$('mode-help').textContent=help[$('mode').value];};
for(const button of document.querySelectorAll('[data-drive]')){
  button.onpointerdown=event=>{button.setPointerCapture(event.pointerId);send('drive',{value:button.dataset.drive.split(',').map(Number)});};
  button.onpointerup=button.onpointercancel=()=>send('drive',{value:[0,0]});
}
for(const value of ['grab','release'])$(value).onclick=()=>send('gripper',{value});
window.addEventListener('blur',()=>{if(state?.mode==='manual')send('drive',{value:[0,0]});});
connect();
