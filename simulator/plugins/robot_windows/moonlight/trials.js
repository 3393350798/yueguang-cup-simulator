const names={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const colors={red:'红块',yellow:'黄块',blue:'蓝块'};
const required=['stage','mode','group','color','elapsed','wall_hits','sonar_triggers','falls'];
const uses=(round,stage)=>round.stage==='full'||round.stage===stage;
const scenario=r=>(r.group%2===0?'偶数组':'奇数组')+' · '+(colors[r.color]||'未记录颜色');

function firstUnmet(r){
  if(r.reason==='error')return r.mode==='firmware'?'固件执行异常':'会员程序异常';
  if(uses(r,'pickup')){
    if(!r.grasped)return '抓放：未稳定提起';
    if(r.falls>0)return '抓放：夹持中掉块';
    if(!r.placed)return '抓放：物块未放入圈内';
    if(!r.placed.correct){
      if(r.placed.side!==(r.group%2===0?'left':'right'))return '抓放：放置侧别错误';
      if(r.placed.color!==r.color)return '抓放：放置颜色错误';
      return '抓放：未放对圈';
    }
  }
  if(uses(r,'channel')){
    if(!r.crossed)return '通道：未完整通过';
    if(r.wall_hits>0)return '通道：碰到挡板';
  }
  if(uses(r,'parking')){
    if(!r.parked)return '停车：未停稳';
    if(!r.aligned)return '停车：未摆正';
  }
  return '适用检查均通过';
}

export function initTrials(send,incidents){
  const $=id=>document.getElementById('trial-'+id);
  const value=id=>document.getElementById(id).value;
  const field=(id,min,max)=>Math.max(min,Math.min(max,Math.round(Number($(id).value))||min));
  const settings=()=>{
    const group=Math.round(Number(value('group')));
    return {stage:$('stage').value,group:Number.isSafeInteger(group)&&group>0?group:1,color:value('color')};
  };
  let current,batch,completedEpoch,clipRevision,baseline=[],notice='';
  const records=[];
  // Report JSON stays separate from replay JSON and browser-only lookup keys.
  const recordEpochs=new WeakMap();
  const add=r=>{
    const tr=document.createElement('tr');
    const condition=Number.isInteger(r.case_index)?' · 工况 '+r.case_index+'/6':'';
    const items=[(r.label||'历史')+' · '+names[r.stage]+' · '+scenario(r)+condition,
      firstUnmet(r),
      uses(r,'pickup')?(r.grasped?'提起':'未提起')+(r.placed?.correct?'／放对圈 '+r.placed.offset_mm+' mm':r.placed?'／其他圈':'')+'／掉落 '+r.falls:'—',
      uses(r,'channel')?(r.crossed?'通过':'未通过')+'／碰撞 '+r.wall_hits+'／触发 '+r.sonar_triggers:'—',
      uses(r,'parking')?(r.aligned?'摆正':r.parked?'停稳':'未停稳')+(r.parking_angle_deg==null?'':'／'+r.parking_angle_deg+'°'):'—',
      r.elapsed.toFixed(2)+' s'];
    for(const item of items){const td=document.createElement('td');td.textContent=item;tr.append(td);}
    const key=recordEpochs.get(r);
    if(key!==undefined&&incidents.hasRound(key)){
      const button=document.createElement('button');
      button.className='small';button.type='button';button.textContent='查看本轮失败片段';
      button.onclick=()=>incidents.openRound(key);
      tr.cells[1].append(document.createElement('br'),button);
    }
    return tr;
  };
  function totals(rows){
    const rates=(filter,test)=>{
      const group=rows.filter(filter);
      return group.length?group.filter(test).length+'/'+group.length:'—';
    };
    return '抓取 '+rates(r=>uses(r,'pickup'),r=>r.grasped)
      +' · 放对圈 '+rates(r=>uses(r,'pickup'),r=>r.placed?.correct)
      +' · 通道无碰撞通过 '+rates(r=>uses(r,'channel'),r=>r.crossed&&!r.wall_hits)
      +' · 停稳摆正 '+rates(r=>uses(r,'parking'),r=>r.aligned);
  }
  function updateProgress(){
    const active=batch?((batch.kind==='matrix'?'固定工况':'重复同一工况')+
      ' · 已记录 '+batch.index+' / '+batch.cases.length+' · 正在测试 '+scenario(batch.cases[batch.index])+'。 '):'';
    $('batch').textContent=batch?.kind==='repeat'?'停止多轮':'开始多轮';
    $('matrix').textContent=batch?.kind==='matrix'?'停止固定工况':'固定工况（6 轮）';
    $('batch').disabled=batch?.kind==='matrix';
    $('matrix').disabled=batch?.kind==='repeat';
    $('finish').disabled=Boolean(batch||!current?.trial||current.round_result||current.resetting);
    $('summary').textContent=active+(notice?notice+' ':'')
      +(records.length?'当前 '+totals(records):'尚无记录')
      +(baseline.length?'；历史 '+totals(baseline):'');
  }
  function redraw(){
    clipRevision=incidents.revision;
    $('rows').replaceChildren(...[...records,...baseline].map(add));
    $('export').disabled=!records.length;
    updateProgress();
  }
  function abort(message,pause=false){
    batch=null;
    notice=message;
    if(pause)send('pause');
    updateProgress();
  }
  function position(){
    if(batch)abort('多轮已停止。');
    send('reset',settings());
  }
  function resetCase(state){
    batch.expected=state.trial.id+1;
    batch.started=false;
    batch.ran=false;
    send('reset',batch.cases[batch.index]);
  }
  function startBatch(kind){
    if(batch){abort('多轮已停止；已完成的记录可以导出。',true);return;}
    if(!current?.trial)notice='请先连接场景。';
    else if(current.mode!==value('mode'))notice='控制方式正在切换，请稍后开始。';
    else if(current.mode==='manual')notice='请先选择「会员算法」或「STM32 固件」。';
    else notice='';
    if(notice){updateProgress();return;}
    const plan={...settings(),limit:field('limit',1,180)};
    const cases=kind==='matrix'?[2,1].flatMap(group=>Object.keys(colors).map(color=>({...plan,group,color})))
      :Array.from({length:field('count',1,20)},()=>plan);
    batch={kind,index:0,cases,label:$('label').value,mode:current.mode,session:current.panel_session};
    notice='';
    resetCase(current);
    updateProgress();
  }
  function record(r,epoch,plan){
    completedEpoch=epoch;
    const row={...r,label:plan?.label??$('label').value};
    if(plan?.kind==='matrix'){
      row.suite='parity-colors';
      row.case_index=plan.index+1;
      row.limit_s=plan.cases[plan.index].limit;
      row.first_unmet=firstUnmet(row);
    }
    recordEpochs.set(row,epoch);
    records.push(row);
  }
  function finishCase(r,state,epoch){
    record(r,epoch,batch);
    if(++batch.index<batch.cases.length)resetCase(state);
    else{
      notice=batch.kind==='matrix'?'固定工况已完成（6 轮）。':'多轮已完成。';
      batch=null;
    }
    redraw();
  }
  $('position').onclick=position;
  $('finish').onclick=()=>send('finish_round');
  $('batch').onclick=()=>startBatch('repeat');
  $('matrix').onclick=()=>startBatch('matrix');
  $('export').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify({format:'moonlight-trials',version:1,records},null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='moonlight-trials.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('import').onclick=()=>$('file').click();
  $('file').onchange=async()=>{
    const file=$('file').files[0];$('file').value='';if(!file)return;
    try{
      if(file.size>5*1024*1024)throw new Error('文件超过 5 MB');
      const data=JSON.parse(await file.text());
      if(data.format!=='moonlight-trials'||data.version!==1||!Array.isArray(data.records)||data.records.length>2000||
        data.records.some(r=>!r||required.some(k=>!Object.hasOwn(r,k))||!Number.isFinite(r.elapsed)||!Object.hasOwn(names,r.stage)))
        throw new Error('格式不符');
      baseline=data.records;notice='';redraw();
    }catch(error){notice='无法打开对照报告：'+error.message;updateProgress();}
  };
  return {
    receive(state,epoch){
      current=state;
      if(!state.trial)return;
      const stageText=names[state.trial.stage]+(state.resetting?' · 等待停稳':'');
      if($('current').textContent!==stageText)$('current').textContent=stageText;
      const disabled=Boolean(batch||state.round_result||state.resetting);
      if($('finish').disabled!==disabled)$('finish').disabled=disabled;
      if(clipRevision!==incidents.revision)redraw();
      if(!batch){
        if(state.round_result&&epoch!==completedEpoch){record(state.round_result,epoch);redraw();}
        return;
      }
      if(state.mode!==batch.mode||state.panel_session!==batch.session){abort('运行环境已切换，多轮已停止。');return;}
      if(state.trial.id<batch.expected)return;
      const plan=batch.cases[batch.index];
      if(state.trial.id!==batch.expected||['stage','group','color'].some(k=>state.trial[k]!==plan[k])){
        abort('摆位条件已改变，多轮已停止。');return;
      }
      if(state.round_error){
        if(batch.kind==='matrix'){
          if(epoch!==completedEpoch)finishCase({...state.trial,reason:'error'},state,epoch);
        }else abort('多轮已停止：'+state.round_error);
        return;
      }
      if(state.round_result&&epoch!==completedEpoch){
        finishCase(state.round_result,state,epoch);return;
      }
      if(state.running)batch.ran=true;
      if(batch.ran&&!state.running){
        abort('多轮已暂停；已完成的记录可以导出。');return;
      }
      if(!batch.started&&!state.resetting){batch.started=true;send('start');}
    },
    disconnect(){
      current=null;
      if(batch)abort('场景已断开，多轮已停止。');
      redraw();
    }
  };
}
