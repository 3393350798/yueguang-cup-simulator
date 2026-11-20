const build=new URLSearchParams(location.search).get('v')||Date.now();
const [{default:config},{initTrace},{initTrials},{initIncidents},{initHelpBundle}]=await Promise.all(
  ['scene','trace','trials','incidents','help-bundle'].map(name=>import('./'+name+'.js?v='+encodeURIComponent(build)))
);
const elements=new Map();
const $=id=>{
  if(!elements.has(id))elements.set(id,document.getElementById(id));
  return elements.get(id);
};
const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value;};
const colors={red:'红色',yellow:'黄色',blue:'蓝色'};
const hardware=config.robot;
const deviceNames={front:'前距',left:'左距',right:'右距',rear:'后距'};
$('step-badge').textContent=`${config.step_ms??32} ms / 周期`;
const trace=initTrace(config.step_ms??32,showIncidentFrame);
const incidents=initIncidents({stepMs:config.step_ms??32,field:config.field_revision,open:trace.openIncident});
$('hardware-summary').textContent=`${hardware.name} · 底盘 ${hardware.body.length_mm} × ${hardware.body.width_mm} mm · 轮径 ${hardware.wheels.radius_mm*2} mm · 轮距 ${hardware.wheels.track_mm} mm · 红外 ${hardware.ir.positions_mm.length} 路 · 超声波 ${hardware.sonar.sensors.length} 路 · 摄像头 ${hardware.camera.width} × ${hardware.camera.height}`;
const model=hardware.model||{kind:'parametric'};
const claw=config.gripper||{kind:'none',jaws:[],lift:null};
for(const id of ['grab','release'])$(id).disabled=!claw.jaws.length;
for(const id of ['lift','lower'])$(id).disabled=!claw.lift;
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
const help={manual:'按住方向按钮驾驶，松开停车。Webots 中用 W/A/S/D 驾驶，E 切换开合，Q 切换升降，空格停车。',member:'在 member_algorithm.py 中填写自己的算法。模板初始为停车，保存后点击重载算法。',firmware:'先用 scripts/import-hardware.bat 导入固件并绑定管脚。点击开始后同步执行MCU；暂停冻结MCU，重置复位MCU。'};
const uartInput=$('uart-input'), uartFormat=$('uart-format');
uartFormat.onchange=()=>{uartInput.placeholder=uartFormat.value==='hex'?'例如 A5 00 FF':'串口文本';uartInput.setCustomValidity('');schedulePaint();};
uartInput.oninput=()=>uartInput.setCustomValidity('');
$('uart-send').onclick=()=>{
  let value=uartInput.value+'\n';
  if(uartFormat.value==='hex'){
    const hex=uartInput.value.replace(/[\s,]+/g,'');
    if(!/^(?:[0-9a-f]{2})+$/i.test(hex)){
      uartInput.setCustomValidity('每个字节需要两位十六进制数字，例如 A5 00 FF。');
      uartInput.reportValidity();
      return;
    }
    value=hex.match(/../g).map(pair=>parseInt(pair,16));
  }
  send('uart',{value});uartInput.value='';
};
let state=null,path=[],socket=null,pending=[],paintFrame=0;
let paintedMode, paintedPose, paintedBlock, paintedColor, paintedPathEnd, mapDirty=true;
let previewFrame, appliedPreview, cameraVisible=false, eventKey='';
let panelSession, panelTick=-1;
const robotName=new URLSearchParams(location.search).get('name')||'Moonlight';
const mapCanvas=$('map'), mapContext=mapCanvas.getContext('2d');
const incidentCanvas=$('incident-map'), incidentContext=incidentCanvas.getContext('2d');
let incidentFrame, incidentVisible=false;
const camera=$('camera');
const irCells=[], sonarCells=new Map();
const base=new URL('track.png',location.href);
base.searchParams.set('v',build);
mapCanvas.style.backgroundImage='url("'+base.href+'")';
incidentCanvas.style.backgroundImage=mapCanvas.style.backgroundImage;
new IntersectionObserver(([entry])=>{
  incidentVisible=entry.isIntersecting;
  if(incidentVisible&&incidentFrame)drawIncidentMap();
}).observe(incidentCanvas);
new IntersectionObserver(([entry])=>{
  cameraVisible=entry.isIntersecting;
  if(cameraVisible)schedulePaint();
}).observe(camera.parentElement);
function send(command,extra={}){
  const payload=JSON.stringify({command,...extra});
  if(socket?.readyState===1)socket.send(`robot:${robotName}:${payload}`);
  else pending.push(payload);
}
const trials=initTrials(send,incidents,initHelpBundle(config,incidents),
  config.tolerance_cases??[],config.robot?.imperfections?.light_gain??1);
function connectionStatus(message, warning=false){
  const badge=$('connection');
  if(badge.textContent!==message)badge.textContent=message;
  badge.classList.toggle('warn',warning);
}
function connect(){
  const url=new URL(location.href);url.protocol=location.protocol==='https:'?'wss:':'ws:';
  url.pathname=url.pathname.substring(0,url.pathname.indexOf('/robot_windows/')+1);url.search='';url.hash='';
  socket=new WebSocket(url);
  socket.onopen=()=>{
    connectionStatus('等待场景数据');
    socket.send('robot:'+robotName+':init robot window');
    for(const p of pending)socket.send('robot:'+robotName+':'+p);
    pending=[];
  };
  socket.onmessage=event=>{
    if(typeof event.data!=='string')return;
    for(const line of event.data.split('\n')){
      if(!line.startsWith('robot:'))continue;
      const packet=JSON.parse(line.slice(6));
      if(packet.name!==robotName)continue;
      if(packet.message.startsWith('{'))receive(JSON.parse(packet.message));
    }
  };
  socket.onclose=()=>{
    incidents.disconnect();
    trials.disconnect();
    connectionStatus(state?'场景已断开 · 刷新重连':'场景未连通 · 刷新重试',true);
  };
}
function schedulePaint(){
  if(!paintFrame)paintFrame=requestAnimationFrame(()=>{
    paintFrame=0;
    if(state)render(state);
  });
}
function receive(data){
  // Webots can relay the same update more than once when multiple clients connect.
  if(data.panel_session){
    if(data.panel_session!==panelSession){panelSession=data.panel_session;panelTick=-1;path=[];mapDirty=true;}
    if(data.panel_tick<=panelTick)return;
    panelTick=data.panel_tick;
  }
  if(!state){$('group').value=data.group;$('color').value=data.color;}
  if(data.trace_epoch!==state?.trace_epoch){path=[];mapDirty=true;}
  state=data;
  // Consume every batch even if several arrive before the next browser paint.
  const epoch=data.panel_session?data.panel_session+':'+data.trace_epoch:data.trace_epoch;
  trace.receive(epoch,data.trace);
  incidents.receive(epoch,data.trace,{stage:data.trial?.stage??'full',color:data.color,timedEnd:data.round_result?.trace_end_t});
  trials.receive(data,epoch);
  if(data.image)previewFrame=data.image;
  if(!path.length||Math.hypot(data.pose[0]-path.at(-1)[0],data.pose[1]-path.at(-1)[1])>.006)path.push(data.pose);
  if(path.length>2500)path.shift();
  schedulePaint();
}
function render(data){
  setText($('step-badge'),(config.step_ms??32)+' ms / 周期'+(data.running&&Number.isFinite(data.rate)?' · '+data.rate.toFixed(2)+'×':''));
  if(socket.readyState===1)connectionStatus(data.running?'运行中':'已连接 · 待运行');
  setText($('timer'),`${String(Math.floor(data.elapsed/60)).padStart(2,'0')}:${(data.elapsed%60).toFixed(1).padStart(4,'0')}`);
  setText($('message'),data.message);
  if(data.mode!==paintedMode){
    paintedMode=data.mode;
    $('mode').value=data.mode;
    setText($('mode-help'),help[data.mode]);
    setText($('reload'),data.mode==='firmware'?'重载固件与视觉':'重载算法');
    $('firmware-card').hidden=data.mode!=='firmware';
  }
  if(data.mode==='firmware'){
    const fw=data.firmware||{};
    setText($('firmware-status'),fw.pc?`PC ${fw.pc} · MCU ${fw.virtual_s.toFixed(3)} s · PWM 左 ${fw.left.toFixed(3)} / 右 ${fw.right.toFixed(3)} · 超声触发 ${fw.sonar_triggers}`:'点击开始加载固件');
    setText($('uart-output'),(uartFormat.value==='hex'?fw.uart_hex:fw.uart_text)||'');
  }
  setText($('target'),(data.target_side==='left'?'左侧':'右侧')+' · '+colors[data.color]);
  setText($('grasp'),data.carrying?'夹持中':data.grasped?'已完成':'等待');
  const grip=data.claw;
  if(grip){
    const details=[`接触 ${grip.touching} / ${claw.contact_links?.length??0}`,grip.airborne?`离地 ${grip.clearance_mm.toFixed(1)} mm`:'物块未离地'];
    setText($('claw-status'),claw.kind==='none'?'当前装配体未绑定夹爪':details.join(' · '));
  }
  setText($('placed'),data.placed?(data.placed.correct?'正确目标圈':'其他放置圈')+` / ${data.placed.offset_mm} mm`:'尚未进圈');
  setText($('parking'),data.aligned?'已摆正并停稳':data.parked?'已到达停稳':'等待');
  for(const [name,value] of Object.entries(data.sensors.sonar)){
    if(!sonarCells.has(name)){
      const row=document.createElement('div'),label=document.createElement('span'),reading=document.createElement('strong');
      label.textContent=deviceNames[name]||name;row.append(label,reading);$('sonars').append(row);
      sonarCells.set(name,reading);
    }
    setText(sonarCells.get(name),data.sensors.sonar_no_echo?'无回波（模拟）':(value*100).toFixed(1)+' cm');
  }
  for(const [i,value] of data.sensors.ir.entries()){
    if(!irCells[i]){
      const node=document.createElement('div');
      $('ir').append(node);irCells.push({node});
    }
    const cell=irCells[i],shade=Math.round(value*235);
    if(cell.shade!==shade){cell.shade=shade;cell.node.style.backgroundColor=`rgb(${shade},${shade},${shade})`;}
    const color=value>.5?'#263b45':'#fff';
    if(cell.color!==color){cell.color=color;cell.node.style.color=color;}
    setText(cell.node,value.toFixed(2));
  }
  for(const name of ['left','right'])setText($('wheel-'+name),(name==='left'?'左轮 ':'右轮 ')+data.sensors.speed[name].toFixed(3)+' m/s');
  setText($('collision'),data.collision?'超声波区域有碰撞记录':'区域内无碰撞记录');
  $('collision').classList.toggle('warn',data.collision);
  for(const [name,value] of Object.entries(data.sensors.joints||{})){
    const field=$('joint-value-'+name);
    if(field)setText(field,' 实测 '+value.toFixed(3));
  }
  const nextKey=JSON.stringify(data.events);
  if(eventKey!==nextKey){
    eventKey=nextKey;
    $('events').replaceChildren(...data.events.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  }
  if(cameraVisible&&previewFrame&&previewFrame!==appliedPreview){
    if(!appliedPreview){camera.style.display='block';$('camera-empty').hidden=true;}
    appliedPreview=previewFrame;
    camera.src='data:image/jpeg;base64,'+previewFrame;
  }
  const moved=(a,b)=>Boolean(a)!==Boolean(b)||(a&&b&&Math.hypot(a[0]-b[0],a[1]-b[1])>.001);
  if(mapDirty||moved(data.pose,paintedPose)||moved(data.block_position,paintedBlock)||
     Math.abs(data.pose[2]-paintedPose[2])>.01||paintedPathEnd!==path.at(-1)||data.color!==paintedColor)draw();
}
function draw(){
  mapDirty=false;
  paintedPose=state?.pose;paintedBlock=state?.block_position;paintedColor=state?.color;paintedPathEnd=path.at(-1);
  paintMap(mapContext,mapCanvas.width,state,path);
}
function showIncidentFrame(sample,clip){
  const view=$('incident-view');
  view.hidden=!sample;
  incidentFrame=sample?{sample,color:clip.color}:null;
  if(!sample)return;
  const [x,y,angle]=sample.pose,block=sample.block_position,grip=sample.grip;
  setText($('incident-position'),`车体 ${x.toFixed(3)}, ${y.toFixed(3)} m · ${(angle*180/Math.PI).toFixed(1)}°｜物块 ${block[0].toFixed(3)}, ${block[1].toFixed(3)} m`);
  setText($('incident-grip'),`夹爪 ${grip.closing?'合拢':'张开'}／${grip.raising?'抬升':'下降'} · ${grip.holding?'夹持中':'未夹持'} · 离地 ${grip.clearance_mm?.toFixed(1)??'—'} mm`);
  if(incidentVisible)drawIncidentMap();
}
function drawIncidentMap(){
  const {sample,color}=incidentFrame;
  paintMap(incidentContext,incidentCanvas.width,{pose:sample.pose,block_position:sample.block_position,color},[]);
}
function paintMap(ctx,size,snapshot,route){
  ctx.clearRect(0,0,size,size);
  const p=point=>[(point[0]/config.floor_size+.5)*size,(.5-point[1]/config.floor_size)*size];
  ctx.strokeStyle='#667b91';ctx.lineWidth=5;
  for(const [x0,y0,x1,y1] of config.walls){ctx.beginPath();ctx.moveTo(...p([x0,y0]));ctx.lineTo(...p([x1,y1]));ctx.stroke();}
  if(route.length){ctx.strokeStyle='#1caa91a0';ctx.lineWidth=2;ctx.beginPath();route.forEach((v,i)=>{const c=p(v);i?ctx.lineTo(...c):ctx.moveTo(...c);});ctx.stroke();}
  if(snapshot){
    const c=p(snapshot.pose),scale=size/config.floor_size/1000;
    ctx.save();ctx.translate(...c);ctx.rotate(-snapshot.pose[2]);ctx.scale(scale,scale);
    const b=hardware.body,w=hardware.wheels;
    ctx.fillStyle='#133b4d';ctx.fillRect(-b.length_mm/2,-b.width_mm/2,b.length_mm,b.width_mm);
    ctx.fillStyle='#465765';for(const y of [-w.track_mm/2,w.track_mm/2])ctx.fillRect(-w.radius_mm,y-w.width_mm/2,w.radius_mm*2,w.width_mm);
    if(claw.kind==='parametric'){
      ctx.strokeStyle='#f6ab3d';ctx.lineWidth=6;ctx.beginPath();ctx.moveTo(b.length_mm/2,0);ctx.lineTo(claw.reach_mm,0);ctx.stroke();
    }
    ctx.fillStyle='#3bc2aa';for(const position of hardware.ir.positions_mm){ctx.beginPath();ctx.arc(position[0],-position[1],3,0,Math.PI*2);ctx.fill();}
    ctx.fillStyle='#278fc3';ctx.fillRect(hardware.camera.position_mm[0]-5,-hardware.camera.position_mm[1]-5,10,10);ctx.restore();
  }
  if(snapshot?.block_position){
    const color={red:'#df263e',yellow:'#dac226',blue:'#2460ec'}[snapshot.color];
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(...p(snapshot.block_position),5,0,Math.PI*2);ctx.fill();
  }
}
draw();
for(const id of ['start','reload','snapshot','channel'])$(id).onclick=()=>send(id);
$('pause').onclick=trials.pause;
$('reset').onclick=()=>{path=[];mapDirty=true;send('reset',{group:Number($('group').value),color:$('color').value,stage:'full'});};
$('mode').onchange=()=>{send('mode',{value:$('mode').value});$('mode-help').textContent=help[$('mode').value];};
for(const button of document.querySelectorAll('[data-drive]')){
  button.onpointerdown=event=>{button.setPointerCapture(event.pointerId);send('drive',{value:button.dataset.drive.split(',').map(Number)});};
  button.onpointerup=button.onpointercancel=()=>send('drive',{value:[0,0]});
}
for(const value of ['grab','release','lift','lower'])$(value).onclick=()=>send('gripper',{value});
window.addEventListener('blur',()=>{if(state?.mode==='manual')send('drive',{value:[0,0]});});
connect();
