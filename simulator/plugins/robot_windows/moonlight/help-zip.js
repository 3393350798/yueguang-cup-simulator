// Small ZIP writer (stored entries) and bounded reader for the existing
// Windows diagnostic export. Other archive entries are never extracted.
const LOCAL=0x04034b50, CENTRAL=0x02014b50, END=0x06054b50;
const MAX_ARCHIVE=4*1024*1024, MAX_JSON=1024*1024;
const encode=new TextEncoder();
const decode=new TextDecoder('utf-8',{fatal:true});
const table=Uint32Array.from({length:256},(_,i)=>{
  let c=i;
  for(let bit=0;bit<8;bit++)c=(c>>>1)^((c&1)?0xedb88320:0);
  return c;
});
function crc32(bytes){
  let c=0xffffffff;
  for(const byte of bytes)c=(c>>>8)^table[(c^byte)&255];
  return (c^0xffffffff)>>>0;
}

export function storedZip(entries){
  let offset=0, directoryLength=0;
  const body=[], directory=[];
  for(const [name,text] of entries){
    const label=encode.encode(name), data=encode.encode(text), checksum=crc32(data);
    const local=new DataView(new ArrayBuffer(30)), central=new DataView(new ArrayBuffer(46));
    local.setUint32(0,LOCAL,true);
    local.setUint16(4,20,true);
    local.setUint16(12,33,true); // 1980-01-01; no producer timezone or timestamp.
    local.setUint32(14,checksum,true);
    local.setUint32(18,data.length,true);
    local.setUint32(22,data.length,true);
    local.setUint16(26,label.length,true);
    central.setUint32(0,CENTRAL,true);
    central.setUint16(4,20,true);
    central.setUint16(6,20,true);
    central.setUint16(14,33,true);
    central.setUint32(16,checksum,true);
    central.setUint32(20,data.length,true);
    central.setUint32(24,data.length,true);
    central.setUint16(28,label.length,true);
    central.setUint32(42,offset,true);
    body.push(local.buffer,label,data);
    directory.push(central.buffer,label);
    offset+=local.byteLength+label.length+data.length;
    directoryLength+=central.byteLength+label.length;
  }
  const end=new DataView(new ArrayBuffer(22));
  end.setUint32(0,END,true);
  end.setUint16(8,entries.length,true);
  end.setUint16(10,entries.length,true);
  end.setUint32(12,directoryLength,true);
  end.setUint32(16,offset,true);
  return new Blob([...body,...directory,end.buffer],{type:'application/zip'});
}

const wanted=encode.encode('diagnostics.json');
function isDiagnosticName(bytes,start,length){
  return length===wanted.length&&wanted.every((byte,i)=>bytes[start+i]===byte);
}

function extractEntry(bytes){
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const u16=p=>view.getUint16(p,true), u32=p=>view.getUint32(p,true);
  const invalid=()=>{throw new Error('invalid diagnostics archive');};
  let end=bytes.length-22;
  const lower=Math.max(0,end-65535);
  while(end>=lower&&(u32(end)!==END||end+22+u16(end+20)!==bytes.length))end--;
  if(end<lower||u16(end+4)!==0||u16(end+6)!==0)invalid();
  const count=u16(end+10), first=u32(end+16);
  if(!count||count>32||u16(end+8)!==count||first+u32(end+12)!==end)invalid();
  let found=null, at=first;
  for(let i=0;i<count;i++){
    if(at+46>end||u32(at)!==CENTRAL)invalid();
    const nameLength=u16(at+28), next=at+46+nameLength+u16(at+30)+u16(at+32);
    if(next>end)invalid();
    if(isDiagnosticName(bytes,at+46,nameLength)){
      if(found||u16(at+34)!==0)invalid();
      found={flags:u16(at+8),method:u16(at+10),sum:u32(at+16),packed:u32(at+20),size:u32(at+24),local:u32(at+42)};
    }
    at=next;
  }
  if(at!==end||!found)invalid();
  const {flags,method,sum,packed,size,local}=found;
  if(flags&~0x80e||![0,8].includes(method)||!size||size>MAX_JSON||
      local+30>first||u32(local)!==LOCAL||u16(local+6)!==flags||u16(local+8)!==method)invalid();
  const nameLength=u16(local+26), start=local+30+nameLength+u16(local+28);
  if(start+packed>first||!isDiagnosticName(bytes,local+30,nameLength)||(!method&&packed!==size))invalid();
  return {method,sum,size,payload:bytes.subarray(start,start+packed)};
}

async function checkedInflate(compressed,size){
  if(typeof DecompressionStream!=='function')throw new Error('no deflate support');
  const reader=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
  const data=new Uint8Array(size);
  let offset=0;
  try{
    for(;;){
      const {value,done}=await reader.read();
      if(done)break;
      if(offset+value.length>size){
        await reader.cancel();
        throw new Error('diagnostics size limit');
      }
      data.set(value,offset);offset+=value.length;
    }
  }finally{reader.releaseLock();}
  if(offset!==size)throw new Error('short diagnostics entry');
  return data;
}

export async function readDiagnostic(file){
  if(!file.size||file.size>MAX_ARCHIVE)throw new Error('diagnostics file size limit');
  let bytes=new Uint8Array(await file.arrayBuffer());
  if(bytes[0]===80&&bytes[1]===75){
    const {method,sum,size,payload}=extractEntry(bytes);
    bytes=method?await checkedInflate(payload,size):payload;
    if(crc32(bytes)!==sum)throw new Error('diagnostics checksum');
  }else if(bytes.length>MAX_JSON)throw new Error('diagnostics JSON size limit');
  return JSON.parse(decode.decode(bytes));
}
