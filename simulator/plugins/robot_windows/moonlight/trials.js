const query=new URL(import.meta.url).search;
const [{settingsFromPanel,samePlan,compareLatest,faultNotes},{observedAtStart,readToleranceReport,compareTolerance,showToleranceComparison}]=
  await Promise.all(['fault-regression','tolerance-compare'].map(name=>import('./'+name+'.js'+query)));
const names={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const colors={red:'红块',yellow:'黄块',blue:'蓝块'};
const required=['stage','mode','group','color','elapsed','wall_hits','sonar_triggers','falls'];
const uses=(round,stage)=>round.stage==='full'||round.stage===stage;
const scenario=r=>(r.group%2===0?'偶数组':'奇数组')+' · '+(colors[r.color]||'未记录颜色');

function outcome(r){
  const fail=(stage,label)=>({stage,label});
  if(r.reason==='error')return fail('execution',r.mode==='firmware'?'固件执行异常':'会员程序异常');
  if(uses(r,'pickup')){
    if(!r.grasped)return fail('pickup','抓放：未稳定提起');
    if(r.falls>0)return fail('pickup','抓放：夹持中掉块');
    if(!r.placed)return fail('pickup','抓放：物块未放入圈内');
    if(!r.placed.correct){
      if(r.placed.side!==(r.group%2===0?'left':'right'))return fail('pickup','抓放：放置侧别错误');
      if(r.placed.color!==r.color)return fail('pickup','抓放：放置颜色错误');
      return fail('pickup','抓放：未放对圈');
    }
  }
  if(uses(r,'channel')){
    if(!r.crossed)return fail('channel','通道：未完整通过');
    if(r.wall_hits>0)return fail('channel','通道：碰到挡板');
  }
  if(uses(r,'parking')){
    if(!r.parked)return fail('parking','停车：未停稳');
    if(!r.aligned)return fail('parking','停车：未摆正');
  }
  return {stage:null,label:'适用检查均通过'};
}
const firstUnmet=r=>outcome(r).label;

function toleranceMatches(evidence,preset,limit){
  return evidence?.version===1&&evidence.limit_s===limit&&
    Object.keys(preset).every(k=>evidence.case?.[k]===preset[k]);
}

function toleranceSummary(run,rows){
  const cases=run.cases.map(preset=>{
    const row=rows.find(r=>r.suite==='tolerance'&&r.tolerance_run_id===run.id&&r.tolerance_run?.case?.id===preset.id);
    if(!row)return {preset,status:'unrun',first_unmet_stage:null,first_unmet:null,observed:null,scene_signature:null};
    const result=outcome(row), observed=row.tolerance_run.observed;
    const valid=row.reason==='time'&&observedAtStart(observed);
    const status=valid?(result.stage?'failed':'passed'):'invalid';
    return {preset,status,first_unmet_stage:valid?result.stage:'execution',
      first_unmet:valid||row.reason==='error'?result.label:'未完整运行或缺少开跑观测',
      observed,scene_signature:row.tolerance_run.scene_signature??null};
  });
  const passed=cases.filter(c=>c.status==='passed').length, valid=cases.filter(c=>['passed','failed'].includes(c.status)).length;
  return {version:2,run_id:run.id,status:run.status,label:run.label,stage:run.stage,mode:run.mode,
    group:run.group,color:run.color,limit_s:run.limit_s,cases,valid,passed,
    invalid:cases.filter(c=>c.status==='invalid').length,unrun:cases.filter(c=>c.status==='unrun').length,
    pass_rate:valid?passed/valid:null};
}

export function initTrials(send,incidents,help,presets=[],lightGain=1,history){
  const $=id=>document.getElementById('trial-'+id);
  const value=id=>document.getElementById(id).value;
  const field=(id,min,max)=>Math.max(min,Math.min(max,Math.round(Number($(id).value))||min));
  const settings=()=>{
    const group=Math.round(Number(value('group')));
    return {stage:$('stage').value,group:Number.isSafeInteger(group)&&group>0?group:1,color:value('color')};
  };
  let current,batch,completedEpoch,orphanedEpoch,clipRevision,baseline=[],notice='',matrixSerial=0,toleranceSerial=0,lastTolerance=null;
  let baselineTolerance=null,toleranceComparison=null;
  const records=[];
  // Report JSON stays separate from replay JSON and browser-only lookup keys.
  const recordEpochs=new WeakMap();
  const supportData=new WeakMap();
  const add=r=>{
    const tr=document.createElement('tr');
    const condition=r.suite==='tolerance'?' · 容差：'+(r.tolerance_run?.case?.label||'未记录')
      :Number.isInteger(r.case_index)?' · 工况 '+r.case_index+'/6':'';
    const items=[(r.label||'历史')+' · '+names[r.stage]+' · '+scenario(r)+condition,
      firstUnmet(r),
      uses(r,'pickup')?(r.grasped?'提起':'未提起')+(r.placed?.correct?'／放对圈 '+r.placed.offset_mm+' mm':r.placed?'／其他圈':'')+'／掉落 '+r.falls:'—',
      uses(r,'channel')?(r.crossed?'通过':'未通过')+'／碰撞 '+r.wall_hits+'／触发 '+r.sonar_triggers:'—',
      uses(r,'parking')?(r.aligned?'摆正':r.parked?'停稳':'未停稳')+(r.parking_angle_deg==null?'':'／'+r.parking_angle_deg+'°'):'—',
      r.elapsed.toFixed(2)+' s'];
    for(const item of items){const td=document.createElement('td');td.textContent=item;tr.append(td);}
    for(const message of faultNotes(r)){
      const note=document.createElement('small');note.className='fault-round-note';note.textContent=message;
      tr.cells[1].append(note);
    }
    if(r.suite==='tolerance'&&r.reason!=='time'){
      const note=document.createElement('small');note.className='fault-round-note';note.textContent='无效轮，不计入容差通过率';
      tr.cells[1].append(note);
    }
    const key=recordEpochs.get(r);
    if(key!==undefined&&incidents.hasRound(key)){
      const button=document.createElement('button');
      button.className='small';button.type='button';button.textContent='查看本轮失败片段';
      button.onclick=()=>incidents.openRound(key);
      tr.cells[1].append(document.createElement('br'),button);
    }
    if(supportData.has(r)){
      const button=document.createElement('button');
      button.className='small';button.type='button';button.textContent='导出本轮求助包';
      button.onclick=()=>help.open(supportData.get(r),()=>incidents.exportRound(key));
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
    const labels={matrix:'固定工况',repeat:'重复同一工况',tolerance:'容差练习'};
    const active=batch?labels[batch.kind]+' · 已记录 '+batch.index+' / '+batch.cases.length+
      ' · 正在测试 '+(batch.kind==='tolerance'?lastTolerance.cases[batch.index].label:scenario(batch.cases[batch.index]))+'。 ':'';
    $('batch').textContent=batch?.kind==='repeat'?'停止多轮':'开始多轮';
    $('matrix').textContent=batch?.kind==='matrix'?'停止固定工况':'固定工况（6 轮）';
    $('tolerance').textContent=batch?.kind==='tolerance'?'停止容差练习':'开始容差练习';
    $('batch').disabled=Boolean(batch&&batch.kind!=='repeat');
    $('matrix').disabled=Boolean(batch&&batch.kind!=='matrix');
    $('tolerance').disabled=Boolean(batch&&batch.kind!=='tolerance');
    $('finish').disabled=Boolean(batch||!current?.trial||current.round_result||current.resetting);
    const ordinary=records.filter(r=>r.suite!=='tolerance'), old=baseline.filter(r=>r.suite!=='tolerance');
    $('summary').textContent=active+(notice?notice+' ':'')
      +(ordinary.length?'当前 '+totals(ordinary):records.length?'容差记录见下表':'尚无记录')
      +(old.length?'；历史 '+totals(old):'');
    const r=lastTolerance?toleranceSummary(lastTolerance,records):null;
    if(r){
      const pass=r.valid?`${r.passed}/${r.valid}（${(100*r.pass_rate).toFixed(1)}%）`:'—（无有效轮）';
      const status={running:'进行中',complete:'已完成',cancelled:'已停止'}[r.status];
      $('tolerance-summary').textContent=`${status} · 通过 ${pass} · 无效 ${r.invalid} · 未完成 ${r.unrun}`;
    }
    $('tolerance-comparison').hidden=!baselineTolerance;
    toleranceComparison=baselineTolerance&&r?compareTolerance(baselineTolerance,r):null;
    if(baselineTolerance)showToleranceComparison(toleranceComparison,{
      summary:$('tolerance-comparison-summary'),rows:$('tolerance-comparison-rows')
    });
    $('export').disabled=!records.length&&!lastTolerance;
  }
  function showRegression(){
    $('regression').hidden=!baseline.length||(baselineTolerance&&!baseline.some(r=>r.suite==='parity-colors'));
    if($('regression').hidden)return;
    const result=compareLatest(records,baseline);
    $('regression-summary').textContent=result.message;
    const rows=(result.rows||[]).map(({before,now,oldPassed,newPassed,regressions,improvements})=>{
      const tr=document.createElement('tr');
      const delta=[];
      if(oldPassed&&!newPassed)delta.push('通过 → 未通过');
      if(!oldPassed&&newPassed)delta.push('未通过 → 通过');
      if(regressions.length)delta.push('退步：'+regressions.map(s=>names[s]).join('、'));
      if(improvements.length)delta.push('改善：'+improvements.map(s=>names[s]).join('、'));
      const labels=[names[now.stage]+' · '+scenario(now),firstUnmet(before),firstUnmet(now),delta.join('；')||'判定未变'];
      for(const label of labels){
        const cell=document.createElement('td');
        cell.textContent=label;tr.append(cell);
      }
      if(oldPassed&&!newPassed||regressions.length)tr.cells[3].className='regression-bad';
      return tr;
    });
    $('regression-rows').replaceChildren(...rows);
  }
  function redraw(){
    clipRevision=incidents.revision;
    $('rows').replaceChildren(...[...records,...baseline].map(add));
    showRegression();
    updateProgress();
  }
  function abort(message,pause=false){
    const ending=batch;
    batch=null;
    notice=message;
    if(ending?.kind==='tolerance'){
      lastTolerance.status='cancelled';
      if(pause)send('end_tolerance',{round_id:ending.expected});
    }else if(pause)send('pause');
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
    if(batch){abort(batch.kind==='tolerance'?'容差练习已停止；已完成的记录可以导出。':'多轮已停止；已完成的记录可以导出。',true);return;}
    if(!current?.trial)notice='请先连接场景。';
    else if(current.mode!==value('mode'))notice='控制方式正在切换，请稍后开始。';
    else if(current.mode==='manual')notice='请先选择「会员算法」或「STM32 固件」。';
    else if(kind==='tolerance'&&(!presets.length||!lightGain))notice='容差练习需要当前版场景和非零灯光，请检查配置并重新启动。';
    else notice='';
    if(notice){updateProgress();return;}
    const plan={...settings(),limit:field('limit',1,180)};
    if(kind==='matrix'){
      try{plan.fault_plan=settingsFromPanel($,plan.limit,current.mode,Object.keys(current.sensors?.sonar||{}).length>0);}
      catch(error){notice=error.message;updateProgress();return;}
    }
    let cases;
    if(kind==='tolerance'){
      const selected=presets.filter(c=>uses(plan,'pickup')||!c.block_y_mm);
      lastTolerance={id:++toleranceSerial,status:'running',label:$('label').value,stage:plan.stage,mode:current.mode,
        group:plan.group,color:plan.color,limit_s:plan.limit,cases:selected};
      cases=selected.map(c=>({...plan,tolerance_id:c.id}));
    }else if(kind==='matrix')cases=[2,1].flatMap(group=>Object.keys(colors).map(color=>({...plan,group,color})));
    else cases=Array.from({length:field('count',1,20)},()=>plan);
    batch={kind,index:0,cases,label:$('label').value,mode:current.mode,session:current.panel_session,
      historyId:current.panel_session+':batch:'+String(current.trial.id+1),
      matrixSerial:kind==='matrix'?++matrixSerial:null};
    notice='';
    resetCase(current);
    updateProgress();
  }
  function record(r,epoch,plan){
    completedEpoch=epoch;
    const {trace_end_t,...result}=r;
    const row={...result,label:plan?.label??$('label').value};
    if(plan?.kind==='matrix'){
      row.suite='parity-colors';
      row.case_index=plan.index+1;
      row.limit_s=plan.cases[plan.index].limit;
      row.matrix_run=plan.matrixSerial;
      row.first_unmet=firstUnmet(row);
    }else if(plan?.kind==='tolerance'){
      row.suite='tolerance';
      row.tolerance_run_id=lastTolerance.id;
      row.tolerance_index=plan.index+1;
      row.limit_s=plan.cases[plan.index].limit;
      row.first_unmet=firstUnmet(row);
      row.first_unmet_stage=outcome(row).stage;
    }
    if(row.reason==='time'&&!current?.round_error&&outcome(row).stage)
      incidents.finishTimeout(epoch,trace_end_t,row.elapsed,row);
    recordEpochs.set(row,epoch);
    const verdict=current?.round_error?firstUnmet({...row,reason:'error'}):firstUnmet(row);
    const snapshot=help.capture(row,current,verdict);
    supportData.set(row,snapshot);
    history.record(epoch,{batch:plan?.historyId??epoch,kind:plan?.kind??'single',index:(plan?.index??0)+1,
      total:plan?.cases.length??1,label:String(row.label).slice(0,50),group:row.group},snapshot,
      incidents.snapshotRound(epoch));
    records.push(row);
  }
  function finishCase(r,state,epoch){
    record(r,epoch,batch);
    if(++batch.index<batch.cases.length)resetCase(state);
    else{
      if(batch.kind==='tolerance'){
        notice='容差练习已结束，恢复常规摆位。';
        lastTolerance.status='complete';
        send('end_tolerance',{round_id:batch.expected});
      }else notice=batch.kind==='matrix'?'固定工况已完成（6 轮）。':'多轮已完成。';
      batch=null;
    }
    redraw();
  }
  $('position').onclick=position;
  $('finish').onclick=()=>send('finish_round');
  $('batch').onclick=()=>startBatch('repeat');
  $('matrix').onclick=()=>startBatch('matrix');
  $('tolerance').onclick=()=>startBatch('tolerance');
  function syncFaultInputs(){
    for(const id of ['sonar-start','sonar-length'])$(id).disabled=!$('fault-sonar').checked;
    for(const id of ['uart-start','uart-length'])$(id).disabled=!$('fault-uart').value;
    $('uart-delay').disabled=$('fault-uart').value!=='delay';
  }
  $('fault-sonar').onchange=$('fault-uart').onchange=syncFaultInputs;
  syncFaultInputs();
  $('export').onclick=()=>{
    const report={format:'moonlight-trials',version:1,records};
    if(lastTolerance)report.tolerance_report=toleranceSummary(lastTolerance,records);
    if(toleranceComparison)report.tolerance_comparison=toleranceComparison;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
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
      const tolerance=data.tolerance_report==null?null:readToleranceReport(data.tolerance_report);
      if(data.tolerance_report!=null&&!tolerance)throw new Error('容差报告格式不符');
      baseline=data.records;baselineTolerance=tolerance;notice='';redraw();
      if(tolerance)$('tolerance-comparison').closest('details').open=true;
    }catch(error){notice='无法打开对照报告：'+error.message;updateProgress();}
  };
  return {
    pause(){
      if(batch)abort(batch.kind==='tolerance'?'容差练习已停止；已完成的记录可以导出。':'多轮已暂停；已完成的记录可以导出。',true);
      else send('pause');
    },
    receive(state,epoch){
      const initial=current==null;
      current=state;
      if(!state.trial)return;
      const stageText=names[state.trial.stage]+(state.resetting?' · 等待停稳':'');
      if($('current').textContent!==stageText)$('current').textContent=stageText;
      const disabled=Boolean(batch||state.round_result||state.resetting);
      if($('finish').disabled!==disabled)$('finish').disabled=disabled;
      if(clipRevision!==incidents.revision)redraw();
      if(!batch){
        // Reopening this page does not resume an old automatic batch or
        // create a second record for its already stopped final round.
        if(initial){
          if(state.round_result)completedEpoch=epoch;
          if(state.running)orphanedEpoch=epoch;
        }
        const r=state.round_result;
        if(r&&!r.tolerance_run&&epoch!==completedEpoch&&!(r.reason==='time'&&epoch===orphanedEpoch)){
          record(r,epoch);redraw();
        }
        return;
      }
      if(state.mode!==batch.mode||state.panel_session!==batch.session){abort('运行环境已切换，多轮已停止。');return;}
      if(state.trial.id<batch.expected)return;
      const plan=batch.cases[batch.index];
      if(state.trial.id!==batch.expected||['stage','group','color'].some(k=>state.trial[k]!==plan[k])){
        abort('摆位条件已改变，多轮已停止。');return;
      }
      if(batch.kind==='matrix'&&!samePlan(state.trial.fault_run?.plan,plan.fault_plan,plan.limit,batch.mode)){
        abort('场景未确认本轮定时故障设置，固定工况已停止。',true);return;
      }
      if(batch.kind==='tolerance'&&!toleranceMatches(state.trial.tolerance_run,lastTolerance.cases[batch.index],plan.limit)){
        abort('场景未确认本轮容差工况，已停止。',true);return;
      }
      if(state.round_error){
        if(batch.kind==='matrix'||batch.kind==='tolerance'){
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
