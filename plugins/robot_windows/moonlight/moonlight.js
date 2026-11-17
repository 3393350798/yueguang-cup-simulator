import config from './scene.js';
const $=id=>document.getElementById(id);
const colors={red:'红色',yellow:'黄色',blue:'蓝色'};
const help={manual:'按住方向按钮驾驶，松开停车。在 Webots 画面中也可使用 W/A/S/D，E 夹取/释放，空格停车。',member:'在 member_algorithm.py 中填写自己的算法。模板初始为停车，保存后点击重载算法。'};
let state=null,path=[],socket=null,pending=[];
const image=new Image();
const base=new URL('track.png',location.href);
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
  $('target').textContent=(data.target_side==='left'?'左侧':'右侧')+' · '+colors[data.color];
  $('grasp').textContent=data.carrying?'夹持中':data.grasped?'已完成':'等待';
  $('placed').textContent=data.placed?(data.placed.correct?'正确目标圈':'其他放置圈')+` / ${data.placed.offset_mm} mm`:'尚未进圈';
  $('parking').textContent=data.aligned?'已摆正并停稳':data.parked?'已到达停稳':'等待';
  for(const [name,value] of Object.entries(data.sensors.sonar))$('sonar-'+name).textContent=(value*100).toFixed(1)+' cm';
  $('ir').replaceChildren(...data.sensors.ir.map(value=>{const d=document.createElement('div');d.style.background=`rgb(${Math.round(value*235)},${Math.round(value*235)},${Math.round(value*235)})`;d.style.color=value>.5?'#263b45':'#fff';d.textContent=value.toFixed(2);return d;}));
  for(const name of ['left','right'])$('wheel-'+name).textContent=(name==='left'?'左轮 ':'右轮 ')+data.sensors.speed[name].toFixed(3)+' m/s';
  $('collision').textContent=data.collision?'超声波区域有碰撞记录':'区域内无碰撞记录';
  $('collision').classList.toggle('warn',data.collision);
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
    const c=p(state.pose);ctx.save();ctx.translate(...c);ctx.rotate(-state.pose[2]);ctx.fillStyle='#133b4d';ctx.fillRect(-.075/config.floor_size*size,-.065/config.floor_size*size,.15/config.floor_size*size,.13/config.floor_size*size);ctx.fillStyle='#f6ab3d';ctx.fillRect(.075/config.floor_size*size,-3,.048/config.floor_size*size,6);ctx.fillStyle='#3bc2aa';ctx.fillRect(-10,-10,15,20);ctx.restore();
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
