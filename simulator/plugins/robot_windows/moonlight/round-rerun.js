const {cleanPlan}=await import('./fault-regression.js'+new URL(import.meta.url).search);

const modes=['member','firmware'];
const stages=['full','pickup','channel','parking'];
const colors=['red','yellow','blue'];
const integer=(value,min,max)=>Number.isSafeInteger(value)&&value>=min&&value<=max;
const signature=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const presetId=value=>typeof value==='string'&&/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value);
const presetKeys=['lateral_mm','block_y_mm','light_scale'];
const finite=value=>Number.isFinite(value)&&Math.abs(value)<=1e9;
const coordinates=(value,length)=>Array.isArray(value)&&value.length===length&&value.every(finite);
const lightKeys=['intensity','ambientIntensity'];

function observation(value){
  if(!coordinates(value?.start_pose_mm_deg,3)||!coordinates(value.block_xy_mm,2)||
    !value.light||typeof value.light!=='object'||Array.isArray(value.light))return null;
  const lighting=Object.keys(value.light);
  if(lighting.length&&(lighting.length!==lightKeys.length||lightKeys.some(k=>!finite(value.light[k]))))return null;
  return {start_pose_mm_deg:[...value.start_pose_mm_deg],block_xy_mm:[...value.block_xy_mm],
    light:lighting.length?Object.fromEntries(lightKeys.map(k=>[k,value.light[k]])):{}};
}

function cleanPreset(raw){
  if(!presetId(raw?.id)||!presetKeys.every(k=>Number.isFinite(raw[k]))||raw.light_scale<=0)return null;
  return {id:raw.id,...Object.fromEntries(presetKeys.map(k=>[k,raw[k]]))};
}

export function matchesPreset(a,b){
  return Boolean(a&&b&&a.id===b.id&&presetKeys.every(k=>a[k]===b[k]));
}

// A separate local allowlist: none of these fields are part of the
// portable help snapshot or the trial report JSON.
export function cleanRerunPlan(raw,snapshot,group){
  const scenario=snapshot?.scenario;
  if(raw?.version!==1||!modes.includes(scenario?.mode)||!stages.includes(scenario.stage)||
    !colors.includes(scenario.color)||snapshot.result?.termination!=='time'||
    !integer(group,1,Number.MAX_SAFE_INTEGER)||(group%2?'odd':'even')!==scenario.group_parity||
    !integer(raw.limit_s,1,180)||!signature(raw.scene_signature)||typeof raw.external_uart!=='boolean')return null;
  const observed=observation(raw.observed);
  if(!observed)return null;
  const tolerance=raw.tolerance===null?null:cleanPreset(raw.tolerance);
  if(raw.tolerance!==null&&(!tolerance||raw.fault_plan!==null))return null;
  try{
    const fault=raw.fault_plan===null?null:cleanPlan(raw.fault_plan,raw.limit_s,scenario.mode);
    return {version:1,limit_s:raw.limit_s,scene_signature:raw.scene_signature,observed,
      fault_plan:fault,tolerance,external_uart:raw.external_uart};
  }catch{return null;}
}

export function replayEvidence(row,evidence,snapshot){
  if(evidence?.version!==1||evidence.mode!==row.mode||evidence.interrupted!==false||evidence.rejection!==null)return null;
  return cleanRerunPlan({...evidence,fault_plan:row.fault_run?.plan??null,
    tolerance:row.tolerance_run?.case??null},snapshot,row.group);
}

export function unavailableReplay(result){
  if(result?.termination==='manual')return '手动结束的轮次无法自动复跑。';
  if(result?.termination==='error')return '执行异常的轮次没有完整的可复跑条件。';
  return '这轮未保存完整的开跑条件，无法自动复跑。';
}

export function rejectionMessage(reason){
  return ({
    scene_signature:'场地、车模、仿真版本或固件外设配置已改变。',
    start_pose:'实际车体起点相差超过 0.5 mm 或 0.5°。',
    block_pose:'实际物块位置相差超过 0.5 mm。',
    light:'实际主光源设置不同。',
    manual_input:'开跑前收到了手动控制。',
    missing_observation:'场景没有返回可核对的开跑观测。'
  })[reason]||'场景未确认历史的开跑条件。';
}

export function compareRerun(original,current,replay){
  const before=original.snapshot.result, now=current.result;
  const verdict=r=>[r.first_unmet,Number.isFinite(r.elapsed_s)?r.elapsed_s.toFixed(2)+' s':null].filter(Boolean).join(' · ');
  const pair=`历史：${verdict(before)}；本次：${verdict(now)}。`;
  if(now.termination==='error')return '本次程序或固件执行异常，不计算变化。'+pair;
  if(!replay||replay.scene_signature!==original.replay.scene_signature)
    return '本次未取得同条件下的完整结果，不计算变化。'+pair;
  if(original.replay.external_uart||replay.external_uart)
    return '有外部串口输入，内容和时序未复现，仅展示两次结果。'+pair;
  const stage=original.snapshot.scenario.stage;
  const checks=[
    ['pickup','抓放',r=>r.grasped&&r.falls===0&&r.placement?.correct===true],
    ['channel','通道',r=>r.crossed&&r.wall_hits===0],
    ['parking','停车',r=>r.parked&&r.aligned]
  ].filter(([id])=>stage==='full'||id===stage);
  const changes=checks.flatMap(([,label,passed])=>Boolean(passed(before))===Boolean(passed(now))?[]:
    [label+(passed(now)?'：未通过 → 通过':'：通过 → 未通过')]);
  return (changes.length?changes.join('；')+'。':
    before.first_unmet!==now.first_unmet?'首个未通过项变化。':'判定未变。')+pair;
}
