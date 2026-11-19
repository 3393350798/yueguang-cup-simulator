const incidentNames={
  wall_contact:'撞到挡板',block_dropped:'意外掉块',member_error:'会员程序异常',firmware_error:'固件执行异常'
};
const stageNames={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const colorNames={red:'红色',yellow:'黄色',blue:'蓝色'};
const modes=['manual','member','firmware'];
const storeKey='moonlight-incident-clips-v1';
const format='moonlight-incident-v1';
const beforeSeconds=5, afterSeconds=3, maxClips=4, maxSamples=2002, maxMarks=12;
const maxStored=2*1024*1024;
const fields=['rules-2000','practice-legacy','custom'];
const steps=[4,8,16,32];

const number=(n,limit=1e6)=>Number.isFinite(n)&&Math.abs(n)<=limit?Number(n.toFixed(6)):null;
const vector=(values,size)=>{
  if(!Array.isArray(values)||values.length!==size)return null;
  const safe=values.map(n=>number(n));
  return safe.every(n=>n!==null)?safe:null;
};

function safeSample(row){
  if(!row||number(row.t)===null||row.t<0)return null;
  const pose=vector(row.pose,3), block=vector(row.block_position,3);
  if(!pose||!block)return null;
  const sonar={};
  let custom=0;
  // Discard custom sensor names; preserve their readings under numbered channels.
  for(const [key,value] of Object.entries(row.sonar||{}).slice(0,16)){
    if(number(value,100)===null)continue;
    const known=['front','left','right','rear'].includes(key)||/^测距 [1-9][0-9]?$/.test(key);
    const label=known?key:'测距 '+(++custom);
    sonar[label]=number(value,100);
  }
  const grip=row.grip||{};
  return {
    t:number(row.t),mode:modes.includes(row.mode)?row.mode:'manual',running:row.running===true,
    pose,block_position:block,sonar,
    pwm:vector(row.pwm,2),speed:vector(row.speed,2),encoder:vector(row.encoder,2),
    ir:Array.isArray(row.ir)&&row.ir.length<=32?vector(row.ir,row.ir.length):null,
    grip:{holding:grip.holding===true,closing:grip.closing===true,raising:grip.raising===true,
      airborne:grip.airborne===true,touching:number(grip.touching,32),clearance_mm:number(grip.clearance_mm,2000)}
  };
}

function safeClip(value){
  if(value?.format!==format||!fields.includes(value.field)||!steps.includes(value.step_ms)||
      !Array.isArray(value.samples)||!value.samples.length||value.samples.length>maxSamples||
      !Array.isArray(value.marks)||!value.marks.length||value.marks.length>maxMarks||
      !Number.isInteger(value.created)||value.created<0||value.created>8.64e15||
      !Object.hasOwn(stageNames,value.stage)||!Object.hasOwn(colorNames,value.color))return null;
  const samples=value.samples.map(safeSample);
  if(samples.some((s,i)=>!s||(i>0&&s.t<=samples[i-1].t)))return null;
  const marks=[];
  for(const mark of value.marks){
    if(!mark||!Object.hasOwn(incidentNames,mark.kind)||number(mark.t)===null||
        mark.t<samples[0].t||mark.t>samples.at(-1).t||mark.t<(marks.at(-1)?.t??0))return null;
    marks.push({kind:mark.kind,t:number(mark.t)});
  }
  return {format,field:value.field,step_ms:value.step_ms,stage:value.stage,color:value.color,
    created:value.created,partial:value.partial===true,marks,samples};
}

export function initIncidents({stepMs,field,open}){
  const $=id=>document.getElementById('incident-'+id);
  const items=$('items'), input=$('file'), status=$('status');
  // A live round key links the reports on this page to their own clips.
  // Never add the controller session or round identity to persisted/exported clips.
  const clipEpochs=new WeakMap();
  let clips=[], recent=[], epoch, active=null, storing=true, revision=0;
  const readClips=()=>{
    const raw=localStorage.getItem(storeKey)||'[]';
    if(raw.length>maxStored)return [];
    const list=JSON.parse(raw);
    return Array.isArray(list)?list.slice(0,maxClips).map(safeClip).filter(Boolean):[];
  };
  try{clips=readClips();}catch{storing=false;}
  const setStatus=text=>{status.textContent=text;};
  const persist=()=>{
    try{
      let serialized=JSON.stringify(clips);
      // Storage limits vary; remove the oldest clip first.
      while(serialized.length>maxStored&&clips.length>1){clips.pop();serialized=JSON.stringify(clips);}
      localStorage.setItem(storeKey,serialized);storing=true;
    }
    catch{storing=false;}
  };
  function redraw(){
    revision++;
    $('count').textContent=clips.length?String(clips.length):'';
    const rows=clips.map(clip=>{
      const item=document.createElement('li'),button=document.createElement('button'),time=document.createElement('time');
      button.type='button';button.textContent=clip.marks.map(m=>incidentNames[m.kind]).filter((name,i,all)=>all.indexOf(name)===i).join('、');
      button.onclick=()=>show(clip);
      time.dateTime=new Date(clip.created).toISOString();
      time.textContent=new Date(clip.created).toLocaleString('zh-CN',{hour12:false});
      const details=document.createElement('span');
      const length=(clip.samples.at(-1).t-clip.samples[0].t).toFixed(1);
      details.textContent=stageNames[clip.stage]+' · '+colorNames[clip.color]+' · '+length+' 秒'+
        (clip===active?.clip?' · 正在截取':clip.partial?' · 提前结束':'');
      item.append(button,time,details);
      return item;
    });
    items.replaceChildren(...rows);
    setStatus(!storing?'浏览器存储不可用，本页内仍可回放和下载。':clips.length?'':'暂无失败片段');
  }
  function show(clip){
    if(clip.field!==field){setStatus('片段的赛场版本与当前赛场不一致。');return;}
    if(clip===active?.clip){setStatus('正在截取事件后的片段，完成后可回放。');return;}
    open(clip,incidentNames);
  }
  function finish(complete=false){
    if(!active)return;
    active.clip.partial=!complete;
    active=null;
    persist();
    redraw();
  }
  function begin(mark,meta){
    const samples=recent.map(safeSample).filter(Boolean);
    if(!samples.length)return;
    const clip={format,field,step_ms:stepMs,stage:meta.stage,color:meta.color,
      created:Date.now(),partial:true,marks:[mark],samples};
    clipEpochs.set(clip,epoch);
    clips.unshift(clip);clips=clips.slice(0,maxClips);
    active={clip,until:mark.t+afterSeconds};
    persist();
    redraw();
  }
  $('import').onclick=()=>input.click();
  input.onchange=async()=>{
    const file=input.files[0];input.value='';
    if(!file)return;
    try{
      if(file.size>2*1024*1024)throw Error();
      const clip=safeClip(JSON.parse(await file.text()));
      if(!clip)throw Error();
      show(clip);
    }catch{setStatus('无法打开：文件不是有效的失败片段。');}
  };
  window.addEventListener('pagehide',()=>{if(active)persist();});
  redraw();
  return {
    get revision(){return revision;},
    hasRound(key){return clips.some(clip=>clip!==active?.clip&&clipEpochs.get(clip)===key);},
    openRound(key){
      const clip=clips.find(clip=>clip!==active?.clip&&clipEpochs.get(clip)===key);
      if(clip)show(clip);
    },
    receive(nextEpoch,batch,meta){
      if(nextEpoch!==epoch){finish();recent=[];epoch=nextEpoch;}
      let oldest=0;
      for(const row of batch||[]){
        if(!Number.isFinite(row.t)||row.t<0||
           (recent.length&&row.t<=recent.at(-1).t))continue;
        recent.push(row);
        while(oldest<recent.length&&recent[oldest].t<row.t-beforeSeconds-1e-8)oldest++;
        if(active&&row.t>active.until+1e-8)finish(true);
        if(active){
          const safe=safeSample(row);
          if(safe&&active.clip.samples.length<maxSamples)active.clip.samples.push(safe);
        }
        for(const kind of row.incidents||[]){
          if(!Object.hasOwn(incidentNames,kind))continue;
          const mark={kind,t:row.t};
          if(!active){
            if(oldest){recent.splice(0,oldest);oldest=0;}
            begin(mark,meta);continue;
          }
          const marks=active.clip.marks;
          if(marks.length<maxMarks&&!marks.some(m=>m.kind===kind&&mark.t-m.t<.5))marks.push(mark);
        }
        if(active&&row.t>=active.until-1e-8)finish(true);
      }
      if(oldest)recent.splice(0,oldest);
    },
    disconnect:()=>finish()
  };
}
