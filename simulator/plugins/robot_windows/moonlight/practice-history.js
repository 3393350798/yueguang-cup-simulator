const maxRounds=60, maxClipBytes=24*1024*1024;
const kinds={single:'单轮记录',repeat:'同条件多轮',matrix:'固定工况',tolerance:'容差练习'};
const stages={full:'全程',pickup:'识别与抓放',channel:'超声波通道',parking:'终点停车'};
const colors={red:'红块',yellow:'黄块',blue:'蓝块'};
const projectPattern=/^[0-9a-f]{32}$/;
const roundPattern=/^[0-9a-f]{32}:[0-9]+$/;

function openStorage(){
  return new Promise((resolve,reject)=>{
    const request=indexedDB.open('moonlight-practice-history',1);
    request.onupgradeneeded=()=>{
      const db=request.result;
      for(const name of ['rounds','clips'])
        db.createObjectStore(name,{keyPath:['project','key']}).createIndex('project','project');
    };
    request.onerror=()=>reject(request.error);
    request.onblocked=()=>reject(new Error('history storage blocked'));
    request.onsuccess=()=>resolve(request.result);
  });
}

const order=(a,b)=>b.created-a.created||a.index-b.index;

// Round metadata and clip payloads are separate so listing/reloading history
// does not read or deserialize all the waveforms.
function read(db,store,selector){
  return new Promise((resolve,reject)=>{
    const request=selector(db.transaction(store).objectStore(store));
    request.onerror=()=>reject(request.error);
    request.onsuccess=()=>resolve(request.result);
  });
}

function byBatch(rows){
  const batches=new Map();
  for(const row of [...rows].sort(order)){
    if(!batches.has(row.batch))batches.set(row.batch,[]);
    batches.get(row.batch).push(row);
  }
  return [...batches.values()];
}

function bound(rows){
  const keep=new Set(), keepClips=new Set();
  for(const batch of byBatch(rows)){
    if(keep.size+batch.length>maxRounds)break;
    for(const item of batch)keep.add(item.key);
  }
  let bytes=0;
  for(const item of [...rows].sort(order)){
    if(!keep.has(item.key)||!item.clipBytes)continue;
    if(bytes+item.clipBytes>maxClipBytes)break;
    keepClips.add(item.key);
    bytes+=item.clipBytes;
  }
  return {keep,keepClips};
}

const matches=(row,clip)=>{
  const s=row.snapshot.scenario;
  return clip&&clip.field===s.field&&clip.stage===s.stage&&clip.color===s.color&&clip.step_ms===s.step_ms;
};

function applyUpdate(rows,key,meta,clip,clipBytes,setClip,dropClip,setRound,dropRound){
  const prior=rows.find(row=>row.key===key);
  if(!prior&&!meta)return rows;
  // A second tab that did not start the suite can hear the same result.
  // Keep the single authoritative record and prefer explicit batch membership.
  const replace=!prior||(meta&&prior.kind==='single'&&meta.kind!=='single');
  const item=replace?{...meta,clipBytes:prior?.clipBytes||0}:prior;
  if(clipBytes&&matches(item,clip)&&(!item.clipBytes||item.clipPartial)){
    item.clipBytes=clipBytes;
    item.clipPartial=clip.partial;
    setClip(item,clip);
  }else if(replace&&prior?.clipPartial)item.clipPartial=true;
  if(replace&&prior)rows=rows.filter(row=>row!==prior);
  if(replace)rows.push(item);
  const {keep,keepClips}=bound(rows);
  const kept=[];
  for(const row of rows){
    if(!keep.has(row.key)){
      dropRound(row);dropClip(row);continue;
    }
    if(row.clipBytes&&!keepClips.has(row.key)){
      dropClip(row);row.clipBytes=0;setRound(row);
    }else if(row===item)setRound(row);
    kept.push(row);
  }
  return kept;
}

function update(db,project,key,meta,clip,clipBytes,validate){
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(['rounds','clips'],'readwrite'), rounds=tx.objectStore('rounds'), clips=tx.objectStore('clips');
    let result;
    tx.onabort=()=>reject(tx.error);
    tx.oncomplete=()=>resolve(result);
    const request=rounds.index('project').getAll(project);
    request.onsuccess=()=>{
      result=applyUpdate(validate(request.result),key,meta,clip,clipBytes,
        (row,replay)=>clips.put({project,key:row.key,replay}),
        row=>clips.delete([project,row.key]),row=>rounds.put(row),row=>rounds.delete([project,row.key]));
    };
  });
}

function clear(db,project){
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(['rounds','clips'],'readwrite');
    tx.onabort=()=>reject(tx.error);
    tx.oncomplete=()=>resolve();
    for(const name of ['rounds','clips']){
      const store=tx.objectStore(name), request=store.index('project').getAllKeys(project);
      request.onsuccess=()=>{for(const key of request.result)store.delete(key);};
    }
  });
}

export function initPracticeHistory({openClip,openHelp,clearIncidents,validateClip,validateSnapshot}){
  const node=id=>document.getElementById('history-'+id);
  const list=node('items'), status=node('status'), clearButton=node('clear');
  let project, db, state='waiting', rows=[], queue=Promise.resolve(), memoryClips=new Map(), channel, selection=0, clearFailure='';
  try{channel=new BroadcastChannel('moonlight-practice-history');}catch{}
  const report=text=>{status.textContent=text;};
  const usable=data=>data.flatMap(row=>{
    if(!row||row.project!==project||!roundPattern.test(row.key||'')||
       typeof row.batch!=='string'||row.batch.length>60||!Object.hasOwn(kinds,row.kind)||
       !Number.isSafeInteger(row.index)||!Number.isSafeInteger(row.total)||row.index<1||
       row.index>row.total||row.total>20||!Number.isSafeInteger(row.created)||row.created<0||row.created>8.64e15||
       !Number.isSafeInteger(row.clipBytes)||row.clipBytes<0||row.clipBytes>maxClipBytes)return [];
    const snapshot=validateSnapshot(row.snapshot);
    return snapshot?[{project:row.project,key:row.key,batch:row.batch,kind:row.kind,index:row.index,total:row.total,
      group:Number.isSafeInteger(row.group)?row.group:null,label:typeof row.label==='string'?row.label.slice(0,50):'',
      created:row.created,clipBytes:row.clipBytes,clipPartial:row.clipPartial===true,snapshot}]:[];
  });
  const say=()=>{
    if(state==='waiting')return '等待场景确认本工程。';
    if(state==='unidentified')return '未收到工程身份，当前练习仍可正常记录。';
    if(state==='loading')return '正在读取本工程历史…';
    const count=rows.length?'已保留 '+rows.length+' 轮，按批次查看。':'本工程尚无保存的练习。';
    return state==='memory'?'浏览器存储不可用，仅保留本页记录。'+count:count;
  };
  const button=(title,action)=>{
    const item=document.createElement('button');
    item.type='button';item.className='small';item.textContent=title;item.onclick=action;
    return item;
  };
  const loadClip=async row=>{
    const data=memoryClips.get(row.key)||
      (db&&row.clipBytes?(await read(db,'clips',store=>store.get([row.project,row.key])))?.replay:null);
    const clip=validateClip(data);
    return matches(row,clip)?clip:null;
  };
  async function show(row,help){
    const marker=++selection;
    let clip;
    try{clip=await loadClip(row);}catch{}
    if(marker!==selection||project!==row.project)return;
    if(help)openHelp(row.snapshot,()=>clip);
    else if(clip)openClip(clip);
    else report('本轮没有保留的回放片段。');
  }
  function redraw(){
    const opened=new Set([...list.querySelectorAll('details[open]')].map(el=>el.dataset.batch));
    const groups=byBatch(rows).map(group=>{
      const first=group[0], detail=document.createElement('details'), summary=document.createElement('summary');
      detail.dataset.batch=first.batch;detail.open=opened.has(first.batch);
      const time=new Date(Math.min(...group.map(row=>row.created))).toLocaleString('zh-CN',{hour12:false});
      summary.textContent=kinds[first.kind]+' · '+time+' · '+group.length+' / '+first.total+' 轮';
      const rounds=document.createElement('ol');
      for(const row of group.sort((a,b)=>a.index-b.index)){
        const item=document.createElement('li'), label=document.createElement('span'), buttons=document.createElement('div');
        const {scenario,result}=row.snapshot;
        label.textContent=[row.label,stages[scenario.stage],Number.isSafeInteger(row.group)?row.group+' 组':'',
          colors[scenario.color],result.first_unmet,
          typeof result.elapsed_s==='number'?result.elapsed_s.toFixed(2)+' s':''].filter(Boolean).join(' · ');
        item.value=row.index;
        if(row.clipBytes)buttons.append(button('查看本轮失败片段',()=>show(row,false)));
        buttons.append(button('导出本轮求助包',()=>show(row,true)));
        item.append(label,buttons);rounds.append(item);
      }
      detail.append(summary,rounds);
      return detail;
    });
    list.replaceChildren(...groups);
    clearButton.disabled=!rows.length;
    report(clearFailure||say());
  }
  function schedule(identity,task){
    queue=queue.then(async()=>{
      if(project!==identity)return;
      await task();
      if(project===identity)redraw();
    }).catch(()=>{
      if(project===identity){state='memory';redraw();}
    });
  }
  function reload(identity){
    selection++;
    schedule(identity,async()=>{
      if(!db||state==='memory')return;
      const data=await read(db,'rounds',store=>store.index('project').getAll(identity));
      if(project===identity)rows=usable(data);
    });
  }
  if(channel)channel.onmessage=event=>{if(event.data===project)reload(project);};
  function bind(identity){
    if(!projectPattern.test(identity||'')){
      if(state!=='unidentified'){
        project=undefined;state='unidentified';selection++;rows=[];memoryClips=new Map();clearFailure='';redraw();
      }
      return;
    }
    if(identity===project)return;
    project=identity;state='loading';selection++;rows=[];memoryClips=new Map();clearFailure='';redraw();
    schedule(identity,async()=>{
      if(!db)db=await openStorage();
      const data=await read(db,'rounds',store=>store.index('project').getAll(identity));
      if(project===identity){rows=usable(data);state='saved';}
    });
  }
  function write(key,meta,replay){
    const identity=project;
    if(!identity||!roundPattern.test(key))return;
    schedule(identity,async()=>{
      clearFailure='';
      const clipBytes=replay?JSON.stringify(replay).length*2:0;
      if(db&&state!=='memory'){
        try{
          const data=await update(db,identity,key,meta,replay,clipBytes,usable);
          if(project===identity)rows=data;
          channel?.postMessage(identity);
          return;
        }catch{if(project===identity)state='memory';}
      }
      if(project!==identity)return;
      rows=applyUpdate(rows,key,meta,replay,clipBytes,
        (row,clip)=>memoryClips.set(row.key,clip),row=>memoryClips.delete(row.key),()=>{},()=>{});
    });
  }
  clearButton.onclick=()=>{
    if(!project||!rows.length||!window.confirm('清除本工程在此浏览器中保存的练习历史与失败片段？'))return;
    clearButton.disabled=true;
    const identity=project;
    selection++;
    schedule(identity,async()=>{
      let persisted=false;
      try{
        if(!db)db=await openStorage();
        await clear(db,identity);
        persisted=true;
      }catch{}
      if(project!==identity)return;
      rows=[];memoryClips.clear();
      const recentCleared=clearIncidents();
      state=persisted?'saved':'memory';
      if(persisted)channel?.postMessage(identity);
      clearFailure=persisted&&recentCleared?'':'已清本页记录，未能确认浏览器里的历史与片段全部清除。';
    });
  };
  return {
    bind,
    record(key,meta,snapshot,clip){
      write(key,{...meta,project,key,created:Date.now(),snapshot},clip);
    },
    attach:(key,clip)=>write(key,null,clip)
  };
}
