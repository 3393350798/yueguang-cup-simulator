const colors=['#087e73','#b34829','#2366ac','#a0326e','#736113','#5549a1','#337123','#955429'];
const sides=['左','右'];
const modes={manual:'手动',member:'会员算法',firmware:'STM32'};
const distances={front:'前距',left:'左距',right:'右距',rear:'后距'};
const windowSeconds=10;
const maxSamples=120000;

const specs={
  pwm:{title:'电机 PWM · 带方向 %',range:[-100,100],values:row=>row.pwm?.map(value=>100*value),names:sides,digits:1},
  speed:{title:'实际轮速 · m/s',min:.05,symmetric:true,values:row=>row.speed,names:sides,digits:3},
  encoder:{title:'轮编码器角度 · rad',min:1,values:row=>row.encoder,names:sides,digits:2},
  ir:{title:'红外 · 0 黑 / 1 白',range:[0,1],values:row=>row.ir,digits:2},
  sonar:{title:'超声波距离 · cm',min:10,values:(row,keys)=>keys.map(key=>100*row.sonar?.[key]),digits:1}
};

function bound(rows,time,upper=false){
  let a=0,b=rows.length;
  while(a<b){
    const middle=(a+b)>>1, value=rows[middle].t;
    if(value<time||(upper&&value===time))a=middle+1;
    else b=middle;
  }
  return a;
}

function compact(value,digits){
  return value.toFixed(digits).replace(/(\.\d*?[1-9])0+$/,'$1').replace(/\.0+$/,'');
}

function plot(item,rows,start,end){
  const {canvas,legend,key}=item,spec=specs[key],ctx=canvas.getContext('2d');
  const width=canvas.clientWidth,height=canvas.clientHeight;
  if(!width||!height)return;
  const dpr=window.devicePixelRatio||1;
  if(canvas.width!==Math.round(width*dpr)||canvas.height!==Math.round(height*dpr)){
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
  }
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,width,height);
  const left=45,right=width-10,top=10,bottom=height-20;
  const keys=key==='sonar'?[...new Set(rows.flatMap(row=>Object.keys(row.sonar||{})))]:[];
  const values=rows.map(row=>spec.values(row,keys)||[]);
  const count=values.reduce((max,v)=>Math.max(max,v.length),0);
  let low=0,high=spec.min||1;
  if(spec.range)[low,high]=spec.range;
  else for(const v of values)for(const n of v)if(Number.isFinite(n)){
    low=Math.min(low,n);high=Math.max(high,n);
  }
  if(spec.symmetric){high=Math.max(-low,high);low=-high;}
  const x=t=>left+(t-start)/(end-start)*(right-left);
  // Keep boundary values (e.g. IR = 0 or 1) away from the canvas edge.
  const y=n=>bottom-2-(n-low)/(high-low)*(bottom-top-4);
  ctx.font='10px ui-monospace, monospace';
  ctx.fillStyle='#506b79';
  ctx.strokeStyle='#dde7eb';
  ctx.textAlign='right';
  for(const n of [low,(high+low)/2,high]){
    const yy=y(n);
    ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.stroke();
    ctx.fillText(compact(n,spec.digits),left-5,yy+3);
  }
  ctx.textAlign='center';
  for(let tick=0;tick<=4;tick++){
    const t=start+(end-start)*tick/4, xx=x(t);
    ctx.beginPath();ctx.moveTo(xx,top);ctx.lineTo(xx,bottom);ctx.stroke();
    ctx.textAlign=tick===0?'left':tick===4?'right':'center';
    ctx.fillText(t.toFixed(2)+' s',xx,height-5);
  }
  if(!count){
    ctx.textAlign='center';
    ctx.fillText(rows.length?(key==='pwm'?'当前时段无固件 PWM':'当前时段无信号'):'等待采样',(left+right)/2,(top+bottom)/2-4);
    legend.replaceChildren();
    return;
  }
  const selected=values.at(-1), label=[];
  for(let i=0;i<count;i++){
    const color=colors[i%colors.length];
    ctx.strokeStyle=color;
    ctx.lineWidth=1.5;
    ctx.setLineDash(i>=colors.length?[3,2]:[]);
    ctx.beginPath();
    let previous;
    for(let j=0;j<rows.length;j++){
      const value=values[j][i];
      if(!Number.isFinite(value)){previous=undefined;continue;}
      const px=x(rows[j].t), py=y(value);
      if(previous===undefined)ctx.moveTo(px,py);
      else{
        if(key==='pwm')ctx.lineTo(px,y(previous));
        ctx.lineTo(px,py);
      }
      previous=value;
    }
    ctx.stroke();
    ctx.setLineDash([]);
    const node=document.createElement('span'),swatch=document.createElement('i');
    swatch.style.background=color;
    const name=spec.names?.[i]??(key==='ir'?'IR'+i:distances[keys[i]]||keys[i]);
    const value=Number.isFinite(selected[i])?selected[i].toFixed(spec.digits):'—';
    node.append(swatch,name+' '+value);
    label.push(node);
    if(Number.isFinite(selected[i])){
      ctx.fillStyle=color;
      ctx.beginPath();ctx.arc(x(rows.at(-1).t),y(selected[i]),2,0,Math.PI*2);ctx.fill();
    }
  }
  ctx.lineWidth=1;
  legend.replaceChildren(...label);
}

function uartLines(rows){
  const lines=[];
  for(let i=rows.length-1;i>=0&&lines.length<24;i--){
    const row=rows[i];
    for(const [field,label] of [['uart_out','MCU→电脑'],['uart_in','电脑→MCU']]){
      const bytes=row[field];
      if(!Array.isArray(bytes)||!bytes.length)continue;
      const head=bytes.slice(0,32).map(n=>Number(n).toString(16).padStart(2,'0').toUpperCase()).join(' ');
      lines.push(row.t.toFixed(3)+' s  '+label+'  '+head+(bytes.length>32?' … ('+bytes.length+' B)':''));
    }
  }
  return lines.reverse().join('\n');
}

export function initTrace(stepMs){
  const $=id=>document.getElementById('trace-'+id);
  let epoch, live=[], capture, replay, index=0, recording=false, frame=0, plots=[];
  let status='尚未录制';
  const createPlots=()=>{
    const group=$('group').value;
    plots=(group==='drive'?['pwm','speed','encoder']:['ir','sonar']).map(key=>{
      const root=document.createElement('section'),title=document.createElement('h3');
      const canvas=document.createElement('canvas'),legend=document.createElement('div');
      root.className='trace-plot';
      title.textContent=specs[key].title;
      canvas.setAttribute('role','img');
      canvas.setAttribute('aria-label',specs[key].title+'随仿真时间变化');
      legend.className='trace-legend';
      root.append(title,canvas,legend);
      return {root,canvas,legend,key};
    });
    $('plots').replaceChildren(...plots.map(p=>p.root));
  };
  const draw=()=>{
    const all=replay?replay.samples:live;
    const sampleIndex=replay?index:all.length-1;
    const at=all[sampleIndex];
    const end=at?.t??0;
    const start=at?Math.max(all[0].t,end-windowSeconds):0;
    const rows=at?all.slice(bound(all,start),sampleIndex+1):[];
    for(const graph of plots)plot(graph,rows,start,Math.max(end,start+.5));
    $('source').textContent=replay?'回放 · '+replay.step_ms+' ms':'实时 · '+stepMs+' ms';
    $('status').textContent=recording?'录制中 · '+capture.samples.length+' 点':status;
    $('record').textContent=recording?'停止录制':'开始录制';
    $('record').setAttribute('aria-pressed',String(recording));
    $('record').disabled=Boolean(replay);
    $('export').disabled=!(replay||capture)?.samples.length;
    $('transport').hidden=!replay;
    $('play').textContent=frame?'暂停':'播放';
    if(replay){
      $('position').max=replay.samples.length-1;
      $('position').value=index;
      $('position').setAttribute('aria-valuetext',at.t.toFixed(3)+' 秒');
    }
    const pose=at?.pose;
    const position=Array.isArray(pose)&&pose.length>=3&&pose.every(Number.isFinite)
      ?' · '+pose[0].toFixed(3)+', '+pose[1].toFixed(3)+' m · '+(pose[2]*180/Math.PI).toFixed(1)+'°':'';
    $('time').textContent=at?end.toFixed(3)+' s · '+(modes[at.mode]||'—')+' · '+(at.running?'运行':'暂停')+position:'等待采样';
    $('uart').textContent=uartLines(rows)||'当前时间窗无收发';
  };
  const stopRecording=(reason='已录制')=>{
    if(!recording)return;
    recording=false;
    status=reason+' · '+capture.samples.length+' 点';
  };
  const stopPlayback=()=>{
    if(frame)clearTimeout(frame);
    frame=0;
  };
  $('record').onclick=()=>{
    if(recording)stopRecording();
    else{
      capture={format:'moonlight-trace',version:1,step_ms:stepMs,samples:[]};
      recording=true;
    }
    draw();
  };
  $('export').onclick=()=>{
    const data=replay||capture;
    if(!data?.samples.length)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'}));
    const link=document.createElement('a');
    link.href=url;
    link.download='moonlight-trace-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('import').onclick=()=>$('file').click();
  $('file').onchange=async()=>{
    const file=$('file').files[0];
    $('file').value='';
    if(!file)return;
    try{
      const data=JSON.parse(await file.text());
      if(data?.format!=='moonlight-trace'||data.version!==1||!Number.isFinite(data.step_ms)||data.step_ms<=0||
        !Array.isArray(data.samples)||!data.samples.length||
        data.samples.some((row,i)=>!Number.isFinite(row?.t)||(i>0&&row.t<data.samples[i-1].t)||
          ['pwm','speed','encoder','ir'].some(k=>row[k]!=null&&(!Array.isArray(row[k])||!row[k].every(Number.isFinite))))){
        throw new Error('录制格式无效');
      }
      stopRecording();stopPlayback();
      replay=data;index=0;
      status='已打开录制 · '+replay.samples.length+' 点';
    }catch(error){
      status='无法打开录制：'+error.message;
    }
    draw();
  };
  $('position').oninput=()=>{
    stopPlayback();index=Number($('position').value);draw();
  };
  $('play').onclick=()=>{
    if(frame){stopPlayback();draw();return;}
    if(index===replay.samples.length-1)index=0;
    const first=replay.samples[index].t, begun=performance.now();
    const advance=()=>{
      index=bound(replay.samples,first+(performance.now()-begun)/1000,true)-1;
      frame=index<replay.samples.length-1?setTimeout(advance,50):0;
      draw();
    };
    advance();
  };
  $('live').onclick=()=>{
    stopPlayback();
    replay=null;
    status=capture?.samples.length?'已录制 · '+capture.samples.length+' 点':'尚未录制';
    draw();
  };
  $('group').onchange=()=>{createPlots();draw();};
  window.addEventListener('resize',draw);
  createPlots();draw();
  return (nextEpoch,batch)=>{
    if(!batch?.length)return;
    if(epoch!==undefined&&nextEpoch!==epoch){live=[];stopRecording('场景重置 · 录制已结束');}
    epoch=nextEpoch;
    live.push(...batch);
    live=live.slice(bound(live,live.at(-1).t-windowSeconds));
    if(recording){
      capture.samples.push(...batch.slice(0,maxSamples-capture.samples.length));
      if(capture.samples.length===maxSamples)stopRecording('达到录制上限 · 录制已结束');
    }
    if(!replay)draw();
  };
}
