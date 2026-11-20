const stages=['full','pickup','channel','parking'];
const expected=[2,1].flatMap(group=>['red','yellow','blue'].map(color=>[group,color]));
const events={wall_contact:'碰撞挡板',block_dropped:'意外掉块',member_error:'会员程序异常',firmware_error:'固件异常'};
const int=(n,min,max)=>Number.isInteger(n)&&n>=min&&n<=max;
const keys=(obj,list)=>obj&&typeof obj==='object'&&!Array.isArray(obj)&&
  Object.keys(obj).length===list.length&&list.every(key=>Object.hasOwn(obj,key));

export function cleanPlan(raw,limit,mode){
  if(!raw||typeof raw!=='object'||Array.isArray(raw)||Object.keys(raw).some(k=>!['sonar','uart'].includes(k))||
    !int(limit,1,180)||!['member','firmware'].includes(mode))throw new Error('定时故障设置不完整。');
  const plan={};
  for(const k of ['sonar','uart']){
    if(!Object.hasOwn(raw,k))continue;
    const entry=raw[k];
    const allowed=['start_ms','duration_ms'];
    if(k==='uart'){
      if(mode!=='firmware')throw new Error('请切换到 STM32 固件模式，或关闭虚拟 UART 故障。');
      allowed.push('action');
      if(entry?.action==='delay')allowed.push('delay_ms');
    }
    if(!keys(entry,allowed)||!int(entry.start_ms,0,limit*1000)||!int(entry.duration_ms,64,limit*1000)||
      entry.start_ms+entry.duration_ms>limit*1000)throw new Error('故障窗口至少 0.064 秒，且须完全位于本轮时长内。');
    const item={start_ms:entry.start_ms,duration_ms:entry.duration_ms};
    if(k==='uart'){
      if(!['drop','delay'].includes(entry.action))throw new Error('请选择受支持的虚拟 UART 故障。');
      item.action=entry.action;
      if(entry.action==='delay'){
        if(!int(entry.delay_ms,1,3000))throw new Error('追加延迟须为 1～3000 毫秒的整数。');
        item.delay_ms=entry.delay_ms;
      }
    }
    plan[k]=item;
  }
  return plan;
}

export function settingsFromPanel(get,limit,mode,sonarAvailable){
  const ms=name=>{
    const input=get(name);
    const raw=input.valueAsNumber;
    if(!Number.isFinite(raw)||Math.abs(raw*1000-Math.round(raw*1000))>1e-6)
      throw new Error('请填写精确到毫秒的开始时间和持续时间。');
    return Math.round(raw*1000);
  };
  const raw={};
  if(get('fault-sonar').checked){
    if(!sonarAvailable)throw new Error('当前小车没有配置超声波。');
    raw.sonar={start_ms:ms('sonar-start'),duration_ms:ms('sonar-length')};
  }
  const action=get('fault-uart').value;
  if(action){
    raw.uart={start_ms:ms('uart-start'),duration_ms:ms('uart-length'),action};
    if(action==='delay')raw.uart.delay_ms=get('uart-delay').valueAsNumber;
  }
  return cleanPlan(raw,limit,mode);
}

export function samePlan(a,b,limit,mode){
  try{return JSON.stringify(cleanPlan(a,limit,mode))===JSON.stringify(cleanPlan(b,limit,mode));}
  catch{return false;}
}

function evidence(value){
  const counters=['sonar_samples','uart_affected_bytes','uart_dropped_bytes','uart_pending_bytes','uart_overflow_bytes'];
  const instants=['sonar_first_ms','uart_first_ms'];
  if(!value||value.version!==1||(value.scene_signature!==null&&
    (typeof value.scene_signature!=='string'||!/^[a-f0-9]{64}$/.test(value.scene_signature)))||
    !counters.every(k=>int(value[k],0,Number.MAX_SAFE_INTEGER))||!instants.every(k=>value[k]===null||int(value[k],0,180000)))return null;
  const e=value.first_event;
  if(e!==null&&(!e||!Object.hasOwn(events,e.kind)||!int(e.elapsed_ms,0,180032)||!Array.isArray(e.position_mm)||
      e.position_mm.length!==2||!e.position_mm.every(n=>int(n,-1e9,1e9))))return null;
  return value;
}

export function faultNotes(row){
  const v=evidence(row.fault_run);
  if(!v)return [];
  const notes=[];
  if(Object.hasOwn(v.plan||{},'sonar')){
    notes.push(v.sonar_first_ms===null?'超声波窗口未执行':`超声波无回波 ${(v.sonar_first_ms/1000).toFixed(3)} s 起 · ${v.sonar_samples} 个采样周期`);
  }
  if(Object.hasOwn(v.plan||{},'uart')){
    const action=v.plan.uart?.action==='delay'?'延迟':'断流';
    notes.push(v.uart_first_ms===null?`UART ${action}窗口未执行`:
      `UART ${action} ${(v.uart_first_ms/1000).toFixed(3)} s 起 · ${v.uart_affected_bytes} B`+
      (v.uart_affected_bytes===0?'（窗口内无输入）':'')+
      (v.uart_pending_bytes?` · 结束时尚待送达 ${v.uart_pending_bytes} B`:'')+
      (v.uart_overflow_bytes?` · 队列满丢弃 ${v.uart_overflow_bytes} B`:''));
  }
  if(v.first_event){
    const e=v.first_event;
    notes.push(`首个运行事件：${events[e.kind]} · ${(e.elapsed_ms/1000).toFixed(3)} s · (${e.position_mm.join(', ')}) mm`);
  }
  return notes;
}

function validRow(r){
  if(r?.suite!=='parity-colors'||!int(r.matrix_run,1,Number.MAX_SAFE_INTEGER)||!int(r.case_index,1,6)||
    !stages.includes(r.stage)||!int(r.limit_s,1,180)||!int(r.step_ms,1,1000)||typeof r.field_revision!=='string'||
    !Number.isFinite(r.elapsed)||r.elapsed<0||r.elapsed>180.032||!['time','error'].includes(r.reason)||
    !['grasped','crossed','parked','aligned'].every(k=>typeof r[k]==='boolean')||
    !['falls','wall_hits'].every(k=>int(r[k],0,1000000)))return false;
  const [group,color]=expected[r.case_index-1], fault=evidence(r.fault_run);
  return r.group===group&&r.color===color&&fault&&fault.scene_signature!==null&&samePlan(fault.plan,fault.plan,r.limit_s,r.mode);
}

function incompatible(a,b){
  if(a.stage!==b.stage)return '赛段不同';
  if(a.mode!==b.mode)return '控制模式不同';
  if(a.limit_s!==b.limit_s)return '每轮秒数不同';
  if(a.field_revision!==b.field_revision||a.step_ms!==b.step_ms)return '场地或仿真步长不同';
  if(!samePlan(a.fault_run.plan,b.fault_run.plan,a.limit_s,a.mode))return '定时故障不同';
  if(a.fault_run.scene_signature!==b.fault_run.scene_signature)return '仿真版本、车模或仿真配置不同';
  return '';
}

function latestComplete(records){
  const groups=new Map();
  for(const row of records){
    if(row?.suite!=='parity-colors'||!int(row.matrix_run,1,Number.MAX_SAFE_INTEGER))continue;
    if(!groups.has(row.matrix_run))groups.set(row.matrix_run,[]);
    groups.get(row.matrix_run).push(row);
  }
  for(const group of [...groups.values()].reverse()){
    if(group.length!==6||!group.every(validRow)||new Set(group.map(r=>r.case_index)).size!==6||
      group.some(r=>incompatible(group[0],r)))continue;
    return [...group].sort((a,b)=>a.case_index-b.case_index);
  }
  return null;
}

function passed(r,stage){
  if(stage==='pickup')return r.grasped&&r.falls===0&&r.placed?.correct===true;
  if(stage==='channel')return r.crossed&&r.wall_hits===0;
  if(stage==='parking')return r.parked&&r.aligned;
  return false;
}

export function compareLatest(current,history){
  const old=latestComplete(history), fresh=latestComplete(current);
  if(!old||!fresh)return {message:'需使用当前版本完整跑完两套可比的固定工况。旧版、取消或缺轮的记录仍可查看。'};
  const issue=incompatible(old[0],fresh[0]);
  if(issue)return {message:`本次不计算变化：${issue}。请用同一设置重新运行。`};
  const selected=fresh[0].stage==='full'?stages.slice(1):[fresh[0].stage];
  const rows=fresh.map((now,i)=>{
    const before=old[i];
    const oldPassed=before.reason!=='error'&&selected.every(s=>passed(before,s));
    const newPassed=now.reason!=='error'&&selected.every(s=>passed(now,s));
    const regressions=before.reason==='error'||now.reason==='error'?[]:selected.filter(s=>passed(before,s)&&!passed(now,s));
    const improvements=before.reason==='error'||now.reason==='error'?[]:selected.filter(s=>!passed(before,s)&&passed(now,s));
    return {before,now,oldPassed,newPassed,regressions,improvements};
  });
  const regressions=rows.filter(r=>(r.oldPassed&&!r.newPassed)||r.regressions.length).length;
  const improvements=rows.filter(r=>(!r.oldPassed&&r.newPassed)||r.improvements.length).length;
  return {message:`对照最近完整六轮：历史通过 ${rows.filter(r=>r.oldPassed).length}/6 → 当前通过 ${rows.filter(r=>r.newPassed).length}/6；出现退步 ${regressions} 轮，出现改善 ${improvements} 轮。`,rows};
}
