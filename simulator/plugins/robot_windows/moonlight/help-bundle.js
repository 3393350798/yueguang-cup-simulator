const {storedZip,readDiagnostic}=await import('./help-zip.js'+new URL(import.meta.url).search);

const enumValue=(value,choices)=>choices.includes(value)?value:null;
const integer=(value,lo,hi)=>Number.isInteger(value)&&value>=lo&&value<=hi?value:null;
const number=(value,lo,hi)=>Number.isFinite(value)&&value>=lo&&value<=hi?Number(value.toFixed(6)):null;
const flag=value=>typeof value==='boolean'?value:null;
const version=value=>typeof value==='string'&&/^v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$/.test(value)?value:null;
const pair=(values,lo,hi)=>Array.isArray(values)&&values.length===2?values.map(value=>number(value,lo,hi)):null;
const modes={manual:'手动探索',member:'会员算法',firmware:'STM32 固件'};
const stages={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const colors={red:'红块',yellow:'黄块',blue:'蓝块'};
const fields=['rules-2000','practice-legacy','custom'];
const faultMarks={wall_contact:'撞到挡板',block_dropped:'意外掉块',member_error:'会员程序异常',firmware_error:'固件执行异常'};
const replayMarks={...faultMarks,round_timeout:'达到时限，任务未完成'};
const verdicts=[
  '会员程序异常','固件执行异常','抓放：未稳定提起','抓放：夹持中掉块','抓放：物块未放入圈内',
  '抓放：放置侧别错误','抓放：放置颜色错误','抓放：未放对圈','通道：未完整通过','通道：碰到挡板',
  '停车：未停稳','停车：未摆正','适用检查均通过'
];

function faultSummary(run){
  if(run?.version!==1)return undefined;
  const window=value=>{
    const start=integer(value?.start_ms,0,180000),duration=integer(value?.duration_ms,64,180000);
    return start===null||duration===null||start+duration>180000?null:{start_ms:start,duration_ms:duration};
  };
  const sonar=window(run.plan?.sonar), uart=window(run.plan?.uart);
  const action=enumValue(run.plan?.uart?.action,['drop','delay']);
  const delay=integer(run.plan?.uart?.delay_ms,1,3000);
  const event=enumValue(run.first_event?.kind,Object.keys(faultMarks));
  return {
    sonar:sonar?{...sonar,first_ms:integer(run.sonar_first_ms,0,180000),
      sampled_cycles:integer(run.sonar_samples,0,180000)}:null,
    uart:uart&&action&&(action!=='delay'||delay!==null)?{...uart,action,
      ...(action==='delay'?{delay_ms:delay}:{}),first_ms:integer(run.uart_first_ms,0,180000),
      affected_bytes:integer(run.uart_affected_bytes,0,Number.MAX_SAFE_INTEGER),
      dropped_bytes:integer(run.uart_dropped_bytes,0,Number.MAX_SAFE_INTEGER),
      overflow_bytes:integer(run.uart_overflow_bytes,0,Number.MAX_SAFE_INTEGER),
      pending_bytes:integer(run.uart_pending_bytes,0,65536)}:null,
    first_event:event?{kind:event,elapsed_ms:integer(run.first_event.elapsed_ms,0,180032),
      position_mm:pair(run.first_event.position_mm,-10000,10000)}:null,
  };
}

function captureRound(config,row,state,verdict){
  const rig=config.robot, board=config.board, env=row.environment||{};
  const mode=enumValue(row.mode,Object.keys(modes));
  const runtimeFault=row.reason==='error'||Boolean(state?.round_error);
  const parity=Number.isSafeInteger(row.group)&&row.group>0?(row.group%2?'odd':'even'):null;
  const place=row.placed;
  const calibration=row.calibration||{};
  return {
    schema:'moonlight-round-support-v1',
    project_version:version(state?.project_version),
    scenario:{
      field:enumValue(row.field_revision,fields),
      stage:enumValue(row.stage,Object.keys(stages)),mode,group_parity:parity,
      color:enumValue(row.color,Object.keys(colors)),step_ms:enumValue(row.step_ms,[4,8,16,32]),
      physics_step_ms:enumValue(config.physics_step_ms,[4,8,16,32]),
      suite:enumValue(row.suite,['parity-colors']),case_index:integer(row.case_index,1,6),
      limit_s:integer(row.limit_s,1,180),
      timed_faults:faultSummary(row.fault_run),
    },
    configuration:{
      robot_kind:enumValue(rig.model?.kind??'parametric',['parametric','urdf','mesh']),
      gripper_kind:enumValue(config.gripper?.kind,['parametric','urdf','none']),
      body_length_mm:number(rig.body?.length_mm,0,1000),body_width_mm:number(rig.body?.width_mm,0,1000),
      wheel_radius_mm:number(rig.wheels?.radius_mm,0,500),wheel_track_mm:number(rig.wheels?.track_mm,0,1000),
      camera_width:integer(rig.camera?.width,1,4096),camera_height:integer(rig.camera?.height,1,4096),
      ir_channels:integer(rig.ir?.positions_mm?.length,0,32),sonar_channels:integer(rig.sonar?.sensors?.length,0,16),
      bound_ports:integer(Object.keys(board?.bindings||{}).length,0,128),
      calibration_configured:Object.keys(calibration).length>0,
      disturbance:{
        seed:integer(env.seed??0,-2147483648,2147483647),
        motor_gain:pair(env.motor_gain??[1,1],0,20),motor_deadzone:pair(env.motor_deadzone??[0,0],0,1),
        ir_noise:number(env.ir_noise??0,0,1),sonar_noise_mm:number(env.sonar_noise_mm??0,0,10000),
        light_gain:number(env.light_gain??1,0,3),
        uart_delay_ms:number(env.uart_delay_ms??0,0,60000),uart_loss:number(env.uart_loss??0,0,1),
      },
    },
    result:{
      termination:enumValue(runtimeFault?'error':row.reason,['manual','time','error']),
      first_unmet:enumValue(verdict,verdicts),elapsed_s:number(row.elapsed,0,86400),
      grasped:flag(row.grasped),max_clearance_mm:number(row.max_clearance_mm,0,10000),
      falls:integer(row.falls,0,100000),
      placement:place?{side:enumValue(place.side,['left','right']),color:enumValue(place.color,Object.keys(colors)),
        correct:flag(place.correct),offset_mm:number(place.offset_mm,0,10000)}:null,
      crossed:flag(row.crossed),wall_hits:integer(row.wall_hits,0,100000),
      sonar_triggers:integer(row.sonar_triggers,0,10000000),
      parked:flag(row.parked),aligned:flag(row.aligned),parking_angle_deg:number(row.parking_angle_deg,0,180),
    },
    round_diagnostics:{
      controller_report_received:true,
      fault:runtimeFault?({member:'member_error',firmware:'firmware_error'}[mode]||'controller_error'):null,
      wall_contact:Number.isInteger(row.wall_hits)&&row.wall_hits>0,
      block_dropped:Number.isInteger(row.falls)&&row.falls>0,
    },
  };
}

const supportSchema='moonlight-round-support-v1';
// Rebuild persisted snapshots with exactly the same structure as a live
// support export. Keep identifier-like strings outside this allowlist.
const sampleEnvironment={start_ms:0,duration_ms:64};
const supportShape=captureRound({robot:{body:{},wheels:{},camera:{},ir:{},sonar:{}}},
  {placed:{},fault_run:{version:1,plan:{sonar:sampleEnvironment,uart:{...sampleEnvironment,action:'delay',delay_ms:1}},
    first_event:{kind:'wall_contact',position_mm:[0,0]}}},null,null);
const supportStrings=new Set([supportSchema,...fields,...Object.keys(modes),...Object.keys(stages),...Object.keys(colors),
  'odd','even','parity-colors','parametric','urdf','mesh','none','manual','time','error',
  ...verdicts,'controller_error',...Object.keys(faultMarks),'drop','delay','left','right']);
function restoreFields(raw,shape){
  if(Array.isArray(shape)){
    if(!Array.isArray(raw)||raw.length!==shape.length||
        !raw.every(value=>typeof value==='number'&&Number.isFinite(value)||value===null))return null;
    return raw.slice();
  }
  if(shape&&typeof shape==='object'){
    if(!raw||typeof raw!=='object'||Array.isArray(raw))return null;
    const output={};
    for(const [key,child] of Object.entries(shape)){
      if(!Object.hasOwn(raw,key))continue;
      output[key]=key==='project_version'?version(raw[key]):restoreFields(raw[key],child);
    }
    return output;
  }
  return typeof raw==='boolean'||(typeof raw==='number'&&Number.isFinite(raw))||supportStrings.has(raw)?raw:null;
}

export function restoreRoundSnapshot(raw){
  if(raw?.schema!==supportSchema||
     ['scenario','configuration','result','round_diagnostics'].some(key=>!raw[key]||typeof raw[key]!=='object'||Array.isArray(raw[key]))||
     !Object.hasOwn(stages,raw.scenario.stage)||!Object.hasOwn(colors,raw.scenario.color))return null;
  return restoreFields(raw,supportShape);
}

// A diagnostic ZIP is supplied by the member. Keep only known values useful
// in triage; never preserve whole rows, filenames, identifiers, or timestamps.
const knownEvents={
  setup:['setup_started','python_found','environment_ready','setup_finished','setup_failed'],
  launch:['launch_started','dependencies_ready','scene_generated','webots_started','panel_ready','launch_ready','launch_failed','browser_open_failed'],
  runtime:['startup_begin','config_ready','controller_ready','algorithm_loaded','vision_link_ready',
    'firmware_start_requested','firmware_started','firmware_closed','error','shutdown','log_limit'],
  serial:['serial_started','serial_ports','serial_opened','serial_result'],
};
const knownCodes=[
  'missing_python','missing_webots','dependency_check_failed','port_conflict','scene_generation_failed','webots_start_failed',
  'webots_exit','panel_timeout','controller_timeout','unexpected_failure','unsupported_python','venv_failed','dependency_install_failed','browser_open_failed',
  'serial_dependency_missing','serial_list_failed','serial_no_ports','serial_list_only','serial_cancelled','serial_port_unavailable',
  'serial_port_denied_or_busy','serial_open_failed','serial_io_failed','serial_write_failed','serial_listening_silent',
  'serial_bytes_received','serial_reply_timeout','serial_reply_mismatch','serial_reply_match','serial_rx_limit',
];
const knownReasons=['firmware_missing','renode_missing','motor_pins_missing','renode_launch_failed','renode_connection_closed'];
const knownPhases=['dependency_load','scene_config','webots_setup','controller_ready','vision_link','algorithm_load','panel_message',
  'panel_start','panel_pause','panel_mode','panel_reload','panel_drive','round_reset','round_end','gripper','contact',
  'simulation_step','member_vision','member_step','firmware_start','firmware_step','firmware_stop','actuator','shutdown'];
const knownErrors=['FileNotFoundError','PermissionError','OSError','ImportError','ModuleNotFoundError','ValueError','TypeError',
  'KeyError','IndexError','AttributeError','NameError','SyntaxError','IndentationError','UnicodeError','JSONDecodeError',
  'RuntimeError','ConnectionError','TimeoutError','BrokenPipeError','ConnectionResetError','ConnectionRefusedError',
  'ZeroDivisionError','AssertionError','OverflowError','ArithmeticError','MemoryError','SystemExit','KeyboardInterrupt','OtherError','OpenCVError'];
const knownHints=['python_module_missing','python_binary_incompatible','webots_controller_error','opengl_initialization_error',
  'qt_platform_error','dependency_download_error','dependency_no_compatible_package','port_in_use','renode_handshake_failed'];
const serialNumbers={
  port_count:[0,4096],baudrate:[300,3000000],data_bits:[5,8],stop_bits:[1,2],duration_ms:[100,30000],
  tx_bytes:[0,2000000],rx_bytes:[0,2000000],first_rx_ms:[0,30000],
};

export function summarizeDiagnostic(raw){
  if(raw?.schema!==1||!Array.isArray(raw.sessions)||!Array.isArray(raw.process_hints))
    throw new Error('diagnostic report schema');
  const events=[];
  for(const source of Object.keys(knownEvents)){
    const selected=raw.sessions.slice(0,16).filter(item=>item?.source===source&&Array.isArray(item.events));
    const sourceRows=selected.flatMap(s=>s.events.slice(-100).reverse().filter(e=>knownEvents[source].includes(e?.event))).slice(0,8);
    for(const row of sourceRows){
      const event={source,event:row.event};
      const add=(key,value)=>{if(value!==null)event[key]=value;};
      add('code',enumValue(row.code,knownCodes));
      add('reason',enumValue(row.reason,knownReasons));
      add('phase',enumValue(row.phase,knownPhases));
      add('error_class',enumValue(row.error_class,knownErrors));
      add('mode',enumValue(row.mode,Object.keys(modes)));
      add('exit_code',integer(row.exit_code,-2147483648,2147483647));
      if(source==='serial'){
        for(const [key,[min,max]] of Object.entries(serialNumbers))add(key,integer(row[key],min,max));
        add('parity',enumValue(row.parity,['N','E','O']));
      }
      events.push(event);
    }
  }
  const hints=[];
  for(const item of raw.process_hints.slice(0,16)){
    if(!Array.isArray(item?.hints))continue;
    for(const hint of knownHints)if(item.hints.includes(hint)&&!hints.includes(hint))hints.push(hint);
  }
  const win=raw.windows_version;
  return {
    schema:'moonlight-support-diagnostic-summary-v1',
    relation:'user_selected_context_not_matched_to_round',
    project_version:version(raw.project_version),
    windows_version:typeof win==='string'&&/^[0-9]{1,6}(\.[0-9]{1,6}){2,3}$/.test(win)?win:null,
    os_64_bit:flag(raw.os_64_bit),venv_python_present:flag(raw.venv_python_present),events,hints,
  };
}

const json=value=>JSON.stringify(value,null,2)+'\n';

export function initHelpBundle(config){
  const $=key=>document.getElementById('help-'+key);
  const modal=$('dialog'), include=$('with-clip'), file=$('diagnostic-file'), save=$('download'), status=$('status');
  let snapshot,getClip,supplement,reading=false,selection=0;
  const setStatus=value=>{status.textContent=value;};
  const roundClip=()=>{
    const clip=getClip?.(), scenario=snapshot.scenario;
    return clip&&clip.field===scenario.field&&clip.stage===scenario.stage&&clip.color===scenario.color&&
      clip.step_ms===scenario.step_ms?clip:null;
  };
  function preview(clip){
    const {scenario,result}=snapshot;
    $('scenario').textContent=[stages[scenario.stage],modes[scenario.mode],scenario.group_parity==='odd'?'奇数组':scenario.group_parity==='even'?'偶数组':'组别未知',
      colors[scenario.color],scenario.case_index===null?null:'工况 '+scenario.case_index+'/6'].filter(Boolean).join(' · ');
    $('finding').textContent=(snapshot.project_version||'未知版本')+' · '+(result.first_unmet||'未判定');
    $('round-data').textContent=json(snapshot);
    $('diagnostic-data').textContent=supplement?json(supplement):'未添加安装或运行日志摘要。';
    $('diagnostic-clear').disabled=!supplement&&!reading;
    include.disabled=!clip;
    if(!clip)include.checked=false;
    $('clip-state').textContent=clip?
      clip.marks.map(mark=>replayMarks[mark.kind]).filter((name,i,all)=>all.indexOf(name)===i).join('、')+' · '+clip.samples.length+' 个采样点'+(clip.partial?' · 提前结束':''):
      '本轮没有可打包的失败片段（未生成、仍在截取或已被淘汰）。';
    $('files').textContent='README.txt、round.json'+(supplement?'、diagnostics-summary.json':'')+
      (include.checked&&clip?'、failure-replay.json':'');
    save.disabled=reading;
  }
  function open(data,readClip){
    snapshot=data;getClip=readClip;supplement=null;reading=false;selection++;
    include.checked=false;file.value='';setStatus('');
    for(const details of modal.querySelectorAll('details'))details.open=false;
    preview(roundClip());
    modal.showModal();
  }
  include.onchange=()=>preview(roundClip());
  $('diagnostic-clear').onclick=()=>{
    selection++;supplement=null;reading=false;file.value='';
    setStatus('补充诊断摘要已移除。');preview(roundClip());
  };
  file.onchange=async()=>{
    const chosen=file.files[0], request=++selection;
    if(!chosen)return;
    supplement=null;reading=true;
    setStatus('正在本地读取诊断摘要…');preview(roundClip());
    try{
      const safe=summarizeDiagnostic(await readDiagnostic(chosen));
      if(request!==selection||!modal.open)return;
      supplement=safe;
      setStatus(snapshot.project_version&&safe.project_version&&safe.project_version!==snapshot.project_version?
        '已添加。补充诊断版本与本轮版本不同，请核对是否是相关日志。':'已添加补充诊断摘要。它不会自动匹配到本轮。');
    }catch{
      if(request!==selection||!modal.open)return;
      setStatus('无法读取此文件。请选择现有 Windows 诊断 ZIP 或解压后的 diagnostics.json；仍可下载基础求助包。');
      file.value='';
    }finally{
      if(request===selection&&modal.open){reading=false;preview(roundClip());}
    }
  };
  save.onclick=()=>{
    const clip=roundClip();
    if(include.checked&&!clip){
      preview(clip);
      setStatus('本轮片段已不可用。请确认文件列表后再下载。');
      return;
    }
    const info=[
      '月光杯仿真 · 本轮求助包','',
      $('scenario').textContent,$('finding').textContent,
      'round.json：所选轮次的工况、练习结果与诊断状态。练习判定不等于正式计分。',
    ];
    if(supplement)info.push('diagnostics-summary.json：自选本机安装或运行诊断摘要，可能来自其他运行；不能用它判定发生在本轮的事件。');
    if(include.checked&&clip)info.push('failure-replay.json：所选轮次的失败片段。解压后可在月光杯面板的「失败回放」打开。');
    info.push('','这个 ZIP 仅在本地生成，由队员自行决定是否发送。');
    const entries=[['README.txt','\ufeff'+info.join('\r\n')+'\r\n'],['round.json',json(snapshot)]];
    if(supplement)entries.push(['diagnostics-summary.json',json(supplement)]);
    if(include.checked&&clip)entries.push(['failure-replay.json',json(clip)]);
    const url=URL.createObjectURL(storedZip(entries));
    const link=document.createElement('a');
    link.href=url;link.download='moonlight-round-support.zip';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    setStatus('求助包已生成。核对后可自行发送。');
  };
  $('close').onclick=()=>modal.close();
  modal.onclose=()=>{selection++;snapshot=null;getClip=null;supplement=null;file.value='';};
  return {capture:(row,state,verdict)=>captureRound(config,row,state,verdict),open};
}
