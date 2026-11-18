const names={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const required=['stage','mode','group','color','elapsed','wall_hits','sonar_triggers','falls'];

export function initTrials(send){
  const $=id=>document.getElementById('trial-'+id);
  const value=id=>document.getElementById(id).value;
  const field=(id,min,max)=>Math.max(min,Math.min(max,Math.round(Number($(id).value))||min));
  const settings=()=>({stage:$('stage').value,group:Number(value('group')),color:value('color')});
  let current,batch,completedId,baseline=[];
  const records=[];
  const add=r=>{
    const tr=document.createElement('tr');
    const items=[r.label+' · '+names[r.stage],
      (r.grasped?'提起':'未提起')+(r.placed?.correct?'／放对圈 '+r.placed.offset_mm+' mm':r.placed?'／其他圈':'')+'／掉落 '+r.falls,
      (r.crossed?'通过':'未通过')+'／碰撞 '+r.wall_hits+'／触发 '+r.sonar_triggers,
      (r.aligned?'摆正':r.parked?'停稳':'未停稳')+(r.parking_angle_deg==null?'':'／'+r.parking_angle_deg+'°'),
      r.elapsed.toFixed(2)+' s'];
    for(const item of items){const td=document.createElement('td');td.textContent=item;tr.append(td);}
    return tr;
  };
  function totals(rows){
    const rates=(filter,test)=>{
      const group=rows.filter(filter);
      return group.length?group.filter(test).length+'/'+group.length:'—';
    };
    return '抓取 '+rates(r=>['full','pickup'].includes(r.stage),r=>r.grasped)
      +' · 放对圈 '+rates(r=>['full','pickup'].includes(r.stage),r=>r.placed?.correct)
      +' · 通道无碰撞通过 '+rates(r=>['full','channel'].includes(r.stage),r=>r.crossed&&!r.wall_hits)
      +' · 停稳摆正 '+rates(r=>['full','parking'].includes(r.stage),r=>r.aligned);
  }
  function redraw(){
    $('rows').replaceChildren(...[...records,...baseline].map(add));
    $('export').disabled=!records.length;
    $('summary').textContent=(batch?'多轮运行中 · '+(batch.total-batch.remaining)+' / '+batch.total+'。 ':'')
      +(records.length?'当前 '+totals(records):'尚无记录')
      +(baseline.length?'；历史 '+totals(baseline):'');
  }
  function position(){
    if(batch){batch=null;$('batch').textContent='开始多轮';redraw();}
    send('reset',settings());
  }
  $('position').onclick=position;
  $('finish').onclick=()=>send('finish_round');
  $('batch').onclick=()=>{
    if(batch){batch=null;send('pause');$('batch').textContent='开始多轮';redraw();return;}
    if(!current||current.mode==='manual'){$('summary').textContent='请先选择「会员算法」或「STM32 固件」。';return;}
    const total=field('count',1,20);
    batch={total,remaining:total,label:$('label').value,mode:current.mode,waiting:current.trial.id,started:false,ran:false,
      settings:{...settings(),limit:field('limit',1,180)}};
    $('batch').textContent='停止多轮';
    send('reset',batch.settings);
    redraw();
  };
  $('export').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify({format:'moonlight-trials',version:1,records},null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='moonlight-trials.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('import').onclick=()=>$('file').click();
  $('file').onchange=async()=>{
    const file=$('file').files[0];$('file').value='';if(!file)return;
    try{
      const data=JSON.parse(await file.text());
      if(data.format!=='moonlight-trials'||data.version!==1||!Array.isArray(data.records)||
        data.records.some(r=>!r||required.some(k=>!Object.hasOwn(r,k))||!Number.isFinite(r.elapsed)||!Object.hasOwn(names,r.stage)))
        throw new Error('格式不符');
      baseline=data.records;redraw();
    }catch(error){$('summary').textContent='无法打开对照报告：'+error.message;}
  };
  return state=>{
    current=state;
    if(!state.trial)return;
    $('current').textContent=names[state.trial.stage]+(state.resetting?' · 等待停稳':'');
    $('finish').disabled=Boolean(state.round_result)||state.resetting;
    if(batch && state.mode!==batch.mode){
      batch=null;$('batch').textContent='开始多轮';redraw();
    }
    if(batch?.started&&state.round_error){
      batch=null;$('batch').textContent='开始多轮';redraw();
      $('summary').textContent='多轮已停止：'+state.round_error;
    }
    if(batch?.started&&state.running)batch.ran=true;
    if(batch?.ran&&!state.running&&!state.round_result){
      batch=null;$('batch').textContent='开始多轮';redraw();
      $('summary').textContent='多轮已暂停；已完成的记录可以导出。';
    }
    if(batch&&!batch.started&&state.trial.id!==batch.waiting&&!state.resetting){
      batch.started=true;send('start');
    }
    if(state.round_result&&state.round_result.id!==completedId){
      completedId=state.round_result.id;
      records.push({...state.round_result,label:batch?.label??$('label').value});
      if(batch && --batch.remaining>0){
        batch.waiting=state.trial.id;batch.started=false;batch.ran=false;send('reset',batch.settings);
      }else{
        batch=null;$('batch').textContent='开始多轮';
      }
      redraw();
    }
  };
}
