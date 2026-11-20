const stages=['full','pickup','channel','parking'];
const modes=['member','firmware'];
const colors=['red','yellow','blue'];
const outcomes=['passed','failed','invalid','unrun'];
const integer=(n,min,max)=>Number.isSafeInteger(n)&&n>=min&&n<=max;
const line=(text,max)=>typeof text==='string'&&text.length>0&&text.length<=max;
const array=(v,length)=>Array.isArray(v)&&v.length===length&&v.every(Number.isFinite);
const signature=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const parameters=['lateral_mm','block_y_mm','light_scale'];

export function observedAtStart(v){
  return array(v?.start_pose_mm_deg,3)&&array(v?.block_xy_mm,2)&&
    ['intensity','ambientIntensity'].every(k=>Number.isFinite(v?.light?.[k]));
}

export function readToleranceReport(report){
  if(!report||![1,2].includes(report.version)||!['running','complete','cancelled'].includes(report.status)||
    !stages.includes(report.stage)||!modes.includes(report.mode)||!colors.includes(report.color)||
    !integer(report.group,1,Number.MAX_SAFE_INTEGER)||!integer(report.limit_s,1,180)||
    !Array.isArray(report.cases)||report.cases.length<1||report.cases.length>20)return null;
  const ids=new Set();
  for(const item of report.cases){
    const p=item?.preset;
    if(typeof p?.id!=='string'||!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(p.id)||ids.has(p.id)||!line(p.label,120)||
      !parameters.every(k=>Number.isFinite(p[k]))||p.light_scale<=0||!outcomes.includes(item.status)||
      (item.first_unmet!==null&&!line(item.first_unmet,120))||
      (item.first_unmet_stage!==null&&![...stages.slice(1),'execution'].includes(item.first_unmet_stage)))return null;
    if(valid(item)&&(!observedAtStart(item.observed)||!line(item.first_unmet,120)||
      (item.status==='passed'?item.first_unmet_stage!==null:!stages.slice(1).includes(item.first_unmet_stage))))return null;
    if(report.version===2&&item.scene_signature!==null&&!signature(item.scene_signature))return null;
    ids.add(p.id);
  }
  return report;
}

const valid=item=>item&&['passed','failed'].includes(item.status);
const conditions={
  stage:'赛段不同',mode:'控制模式不同',group:'组号不同',color:'物块颜色不同',limit_s:'每轮秒数不同'
};

function observedConflict(a,b){
  const distance=(x,y)=>Math.hypot(x[0]-y[0],x[1]-y[1]);
  const angle=Math.abs(a.start_pose_mm_deg[2]-b.start_pose_mm_deg[2])%360;
  const positionLimit=0.5+1e-9;
  if(distance(a.start_pose_mm_deg,b.start_pose_mm_deg)>positionLimit||Math.min(angle,360-angle)>positionLimit)
    return '实际起点相差超过 0.5 mm 或 0.5°';
  if(distance(a.block_xy_mm,b.block_xy_mm)>positionLimit)return '实际物块位置相差超过 0.5 mm';
  if(['intensity','ambientIntensity'].some(k=>Math.abs(a.light[k]-b.light[k])>0.0001+1e-9))
    return '实际主光源光强相差超过 0.0001';
  return null;
}

export function compareTolerance(before,now){
  const conflict=Object.keys(conditions).find(k=>before[k]!==now[k]);
  const prior=new Map(before.cases.map(x=>[x.preset.id,x]));
  const latest=new Map(now.cases.map(x=>[x.preset.id,x]));
  const ids=new Set([...latest.keys(),...prior.keys()]);
  const result={version:1,historical_status:before.status,current_status:now.status,
    comparable:0,excluded:0,historical_passed:0,current_passed:0,regressions:0,improvements:0,
    changed_failures:0,cases:[]};
  for(const id of ids){
    const a=prior.get(id),b=latest.get(id);
    const summary=item=>item?{status:item.status,first_unmet_stage:item.first_unmet_stage,first_unmet:item.first_unmet}:null;
    const row={id,label:(b||a).preset.label,historical:summary(a),current:summary(b),change:null,exclusion:null};
    if(conflict)row.exclusion=conditions[conflict];
    else if(!a||!b)row.exclusion='缺少对应预设';
    else if(parameters.some(k=>a.preset[k]!==b.preset[k]))row.exclusion='扰动参数不同';
    else if(a.status==='unrun'||b.status==='unrun')row.exclusion='存在未完成轮';
    else if(!valid(a)||!valid(b))row.exclusion='存在无效轮';
    else if(before.version<2||now.version<2||!signature(a.scene_signature)||!signature(b.scene_signature))
      row.exclusion='缺少场景配置指纹';
    else if(a.scene_signature!==b.scene_signature)row.exclusion='场地、车模或仿真配置不同';
    else row.exclusion=observedConflict(a.observed,b.observed);
    if(row.exclusion)result.excluded++;
    else{
      result.comparable++;
      const passedA=a.status==='passed',passedB=b.status==='passed';
      if(passedA)result.historical_passed++;
      if(passedB)result.current_passed++;
      if(passedA&&!passedB){row.change='regressed';result.regressions++;}
      else if(!passedA&&passedB){row.change='improved';result.improvements++;}
      else if(!passedA&&(a.first_unmet_stage!==b.first_unmet_stage||a.first_unmet!==b.first_unmet)){
        row.change='failure_changed';result.changed_failures++;
      }else row.change='same';
    }
    result.cases.push(row);
  }
  return result;
}

const statusNames={passed:'通过',failed:'未通过',invalid:'无效',unrun:'未完成'};
const changeNames={regressed:'通过 → 未通过',improved:'未通过 → 通过',
  failure_changed:'首个未通过项变化',same:'判定未变'};
function verdict(item){
  return item?statusNames[item.status]+(item.first_unmet&&['failed','invalid'].includes(item.status)?' · '+item.first_unmet:''):'无此工况';
}

export function showToleranceComparison(result,nodes){
  const {summary,rows}=nodes;
  if(!result){
    summary.textContent='已载入历史容差报告。请用同一设置运行当前方案。';
    rows.replaceChildren();
    return;
  }
  const n=result.comparable;
  summary.textContent=n
    ?'可比工况 '+n+' 项：通过 历史 '+result.historical_passed+'/'+n+' → 当前 '+result.current_passed+'/'+n
      +'；退步 '+result.regressions+'，改善 '+result.improvements+'，首个未通过项变化 '+result.changed_failures
      +'。排除 '+result.excluded+' 项。'
    :'没有可比工况；排除 '+result.excluded+' 项。原因见下表。';
  const content=result.cases.map(item=>{
    const tr=document.createElement('tr');
    for(const text of [item.label,verdict(item.historical),verdict(item.current),
      item.exclusion?'不计入：'+item.exclusion:changeNames[item.change]]){
      const td=document.createElement('td');td.textContent=text;tr.append(td);
    }
    if(item.change==='regressed')tr.cells[3].className='regression-bad';
    return tr;
  });
  rows.replaceChildren(...content);
}
