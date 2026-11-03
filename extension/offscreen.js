// Offscreen document (Chrome 116+). Audio is archived on homelab before any ASR;
// credentials for Groq remain in the homelab backend.
let session=null;
const LIMIT=8*1024*1024;
function notify(status,error=''){
  chrome.runtime.sendMessage({type:'AUDIO_STATE',status,error,
    pending:session?.pending.length||0,sequence:session?.sequence||0,
    tabId:session?.tabId??null}).catch(()=>{});
}
async function start(message){
  if(session && !session.closed)throw Error('Finaliza la captura anterior o reintenta los envíos pendientes');
  let tabStream;
  try{
    tabStream=await navigator.mediaDevices.getUserMedia({
      audio:{mandatory:{chromeMediaSource:'tab',chromeMediaSourceId:message.streamId}},video:false
    });
  }catch(err){
    throw Error('Permiso de audio de la pestaña ('+(err.name||'error')+'): '+String(err.message||err));
  }
  let micStream=null,ctx=null;
  try {
    if(message.includeMic){
      try{
        micStream=await navigator.mediaDevices.getUserMedia({
          audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}
        });
      }catch(err){
        throw Error('Permiso del micrófono ('+(err.name||'error')+'): '+String(err.message||err)+
          '. Pulsa Autorizar micrófono en el popup, o graba solo audio remoto.');
      }
    }
    ctx=new AudioContext();
    const tabSource=ctx.createMediaStreamSource(tabStream);
    // tabCapture silences normal playback unless the captured audio is explicitly restored.
    tabSource.connect(ctx.destination);
    const destination=ctx.createMediaStreamDestination();
    const tabGain=ctx.createGain();
    tabGain.gain.value=message.includeMic?0.72:1;
    tabSource.connect(tabGain).connect(destination);
    if(micStream){
      const micGain=ctx.createGain();
      micGain.gain.value=0.72;
      ctx.createMediaStreamSource(micStream).connect(micGain).connect(destination);
    }
    const mime='audio/webm;codecs=opus';
    if(!MediaRecorder.isTypeSupported(mime))throw Error('Chrome no soporta audio WebM/Opus');
    session={active:true,recording:null,tabStream,micStream,ctx,
      stream:destination.stream,mime,server:message.server,token:message.token,
      context:message.context,tabId:message.tabId,sequence:0,pending:[],uploading:false,
      timer:null,error:'',closed:false};
    tabStream.getAudioTracks().forEach(t=>t.addEventListener('ended',()=>stop().catch(()=>{}),{once:true}));
    recordNext();
    notify('recording');
  } catch(err){
    tabStream.getTracks().forEach(t=>t.stop());
    micStream?.getTracks().forEach(t=>t.stop());
    if(ctx)await ctx.close().catch(()=>{});
    throw err;
  }
}
function recordNext(){
  const state=session;
  if(!state?.active)return;
  const recorder=new MediaRecorder(state.stream,{mimeType:state.mime,audioBitsPerSecond:64000});
  const parts=[];
  recorder.addEventListener('dataavailable',event=>{if(event.data.size)parts.push(event.data)});
  recorder.addEventListener('stop',()=>{
    clearTimeout(state.timer);
    if(parts.length){
      const blob=new Blob(parts,{type:'audio/webm'});
      if(blob.size>=100 && blob.size<=LIMIT){
        if(state.pending.length>=6){
          state.error='Cola de audio llena; se detuvo la captura';
          notify('error',state.error);
          void stop();
        }else{
          state.pending.push({sequence:state.sequence++,at:new Date().toISOString(),blob});
          notify('uploading');
          void drain();
        }
      } else if(blob.size>LIMIT){
        state.error='Fragmento de audio excede 8 MiB';
        notify('error',state.error);
        void stop();
      }
    }
    if(state.active)recordNext();
    else void finalizeIfIdle();
  });
  state.recording=recorder;
  recorder.start();
  state.timer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop()},45000);
}
async function upload(state,item){
  const headers={
    'Authorization':'Bearer '+state.token,
    'Content-Type':'audio/webm',
    'X-Meeting-Id':state.context.meeting_id,
    'X-Sequence':String(item.sequence),
    'X-Started-At':state.context.started_at,
    'X-Captured-At':item.at
  };
  const ctrl=new AbortController();
  const timeout=setTimeout(()=>ctrl.abort(),75000);
  try{
    const res=await fetch(state.server+'/api/audio',{
      method:'POST',headers,body:item.blob,signal:ctrl.signal,cache:'no-store'
    });
    if(!res.ok)throw Error('API audio HTTP '+res.status);
    const result=await res.json();
    if(!result.ok || result.stored!==true)throw Error('El servidor no confirmó almacenamiento persistente');
  }finally{clearTimeout(timeout)}
}
async function drain(){
  const state=session;
  if(!state || state.uploading)return;
  state.uploading=true;
  try {
    while(state.pending.length){
      const item=state.pending[0];
      try {await upload(state,item)}
      catch(err){
        state.error=String(err.message||err).slice(0,180);
        notify('error',state.error);
        if(state.active)await stop();
        // Do not discard unconfirmed audio. The user can retry while this document is alive.
        break;
      }
      state.pending.shift();
      state.error='';
      notify(state.active?'recording':'uploading');
    }
  }finally{
    state.uploading=false;
    await finalizeIfIdle();
  }
}
async function stop(){
  const state=session;
  if(!state || state.closed)return;
  state.active=false;
  clearTimeout(state.timer);
  if(state.recording?.state==='recording'){
    state.recording.stop();
    return;
  }
  await finalizeIfIdle();
}
async function finalizeIfIdle(){
  const state=session;
  if(!state || state.closed || state.active || state.recording?.state==='recording' || state.uploading)return;
  // Stop device capture even if uploading failed; keep only unconfirmed blobs in RAM.
  if(!state.mediaReleased){
    state.mediaReleased=true;
    state.tabStream.getTracks().forEach(t=>t.stop());
    state.micStream?.getTracks().forEach(t=>t.stop());
    await state.ctx.close().catch(()=>{});
  }
  if(state.pending.length){notify('error',state.error||'Audio pendiente: pulsa Reintentar');return}
  state.closed=true;
  session=null;
  notify('stopped');
}
async function retry(){
  if(!session?.pending.length)return {ok:false,error:'No hay fragmentos pendientes'};
  session.error='';
  void drain();
  return {ok:true};
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message?.target!=='offscreen')return false;
  if(message.type==='CAPTURE_STATUS'){
    reply({ok:true,active:!!session?.active,tabId:session?.tabId??null,
      pending:session?.pending.length||0,sequence:session?.sequence||0,
      state:session?.active?'recording':session?.pending.length?'pending':'stopped'});
    return false;
  }
  if(message.type==='CAPTURE_PING'){
    reply({ok:true,ready:true});
    return false;
  }
  if(message.type==='CAPTURE_START'){
    start(message).then(()=>reply({ok:true})).catch(e=>{
      const error=String(e.message||e).slice(0,180);
      notify('error',error);
      reply({ok:false,error});
    });
    return true;
  }
  if(message.type==='CAPTURE_STOP'){
    stop().then(()=>reply({ok:true})).catch(e=>reply({ok:false,error:String(e)}));
    return true;
  }
  if(message.type==='CAPTURE_RETRY'){
    retry().then(reply).catch(e=>reply({ok:false,error:String(e)}));
    return true;
  }
  return false;
});
