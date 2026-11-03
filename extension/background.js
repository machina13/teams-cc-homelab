// Teams CC Homelab 2.0: captions + explicit audio capture via offscreen document.
const DEFAULT_SERVER='';
const DEFAULTS={
  enabled:false,token:'',server:DEFAULT_SERVER,queue:[],lastSync:'',lastError:'',
  lastAttempt:0,lastCaptionSeen:'',connection:null,audioStatus:{status:'stopped',error:''}
};
let work=Promise.resolve();
const serial=fn=>(work=work.catch(()=>{}).then(fn));
function schedule(){chrome.alarms.create('sendCaptions',{periodInMinutes:1})}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='sendCaptions')serial(flush)});

async function probe(){
  const c=await chrome.storage.local.get(DEFAULTS);
  const result={ok:false,server:c.server,at:new Date().toISOString(),error:'',meetings:0};
  if(!c.token){
    result.error='Falta configurar el token';
  }else if(!c.server){
    result.error='Configura la URL del servidor en el popup';
  }else{
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),9000);
    try{
      const health=await fetch(c.server+'/health',{cache:'no-store',signal:controller.signal});
      if(!health.ok)throw Error('Servidor respondió HTTP '+health.status);
      const info=await health.json();
      if(info.service!=='teams-captions')throw Error('Servidor inesperado');
      const auth=await fetch(c.server+'/api/meetings',{
        headers:{Authorization:'Bearer '+c.token},cache:'no-store',signal:controller.signal
      });
      if(auth.status===401)throw Error('Token incorrecto o caducado (HTTP 401)');
      if(!auth.ok)throw Error('Error de API HTTP '+auth.status);
      const data=await auth.json();
      if(!Array.isArray(data.meetings))throw Error('Respuesta de API inválida');
      result.ok=true;
      result.meetings=data.meetings.length;
    }catch(err){
      result.error=err.name==='AbortError'?'Conexión agotó tiempo de espera':String(err.message||err);
    }finally{clearTimeout(timeout)}
  }
  await chrome.storage.local.set({connection:result});
  return result;
}

chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if (m?.type === 'OPEN_RECORDING_POPUP') {
    const page = sender.tab?.url || sender.url || '';
    if (sender.id !== chrome.runtime.id || !sender.tab?.id ||
        !/^https:\/\/teams\.(microsoft\.com|cloud\.microsoft|live\.com)\//.test(page)) {
      reply({ok:false,error:'La solicitud debe venir de una reunión de Teams Web'});
      return false;
    }
    // Invoke immediately in response to the user's click in the Teams page.
    // Some Chrome policies do not propagate the gesture; the content script
    // then explains how to open the toolbar popup manually.
    try {
      if (typeof chrome.action.openPopup !== 'function')
        throw Error('No está disponible la apertura del popup');
      Promise.resolve(chrome.action.openPopup({windowId:sender.tab.windowId}))
        .then(()=>reply({ok:true}))
        .catch(()=>reply({ok:false,error:'Abre Teams CC desde la barra de Chrome'}));
    } catch (_err) {
      reply({ok:false,error:'Abre Teams CC desde la barra de Chrome'});
    }
    return true;
  }
  if(m?.type==='CHECK_CONNECTION'){
    serial(probe).then(reply).catch(err=>reply({ok:false,error:String(err)}));
    return true;
  }
  if(m?.type==='FLUSH'){
    serial(flush).then(reply).catch(err=>reply({ok:false,error:String(err)}));
    return true;
  }
  if(m?.type==='AUDIO_STATE'){
    if(sender.id!==chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL('offscreen.html')))
      return false;
    chrome.storage.local.set({audioStatus:{
      status:m.status,error:m.error||'',pending:m.pending||0,sequence:m.sequence||0,tabId:m.tabId??null,
      updatedAt:new Date().toISOString()
    }}).then(()=>reply({ok:true}));
    return true;
  }
  if(m?.type==='GET_AUDIO_STATUS'){
    getAudioStatus(m.tabId).then(reply).catch(e=>reply({ok:false,error:String(e.message||e)}));
    return true;
  }
  if(m?.type==='PREPARE_AUDIO'){
    prepareAudioDocument().then(()=>reply({ok:true}))
      .catch(e=>reply({ok:false,error:'Preparación de audio: '+String(e.message||e)}));
    return true;
  }
  if(m?.type==='START_AUDIO'){
    // IMPORTANT: Chrome requires a fresh user gesture for tabCapture.
    // Call the gated API synchronously in this message listener, BEFORE any await.
    // The popup must dispatch START_AUDIO directly from the Record button's click.
    let streamIdPromise;
    try{
      if(!Number.isInteger(m.tabId) || !m.context?.meeting_id)
        throw Error('Pestaña o reunión no preparada. Cierra y abre el popup.');
      streamIdPromise=chrome.tabCapture.getMediaStreamId({targetTabId:m.tabId});
    }catch(e){
      reply({ok:false,error:'Permiso de pestaña: '+String(e.message||e)});
      return false;
    }
    startAudio(m,streamIdPromise).then(reply).catch(e=>reply({
      ok:false,error:String(e.message||e)
    }));
    return true;
  }
  if(m?.type==='STOP_AUDIO' || m?.type==='RETRY_AUDIO'){
    sendOffscreenControl(m.type==='STOP_AUDIO'?'CAPTURE_STOP':'CAPTURE_RETRY')
      .then(reply).catch(e=>reply({ok:false,error:String(e.message||e)}));
    return true;
  }
  if(m?.type!=='CAPTIONS_BATCH')return false;
  if(!sender.url || !/^https:\/\/teams\.(microsoft\.com|cloud\.microsoft|live\.com)\//.test(sender.url)){
    reply({ok:false,error:'Origen no autorizado'});return false;
  }
  serial(async()=>{
    const c=await chrome.storage.local.get(DEFAULTS);
    if(!c.enabled)return {ok:false,error:'Captura desactivada'};
    const joined=new Map((c.queue||[]).map(x=>[x.meeting_id+'|'+x.caption.id,x]));
    for(const caption of m.captions||[]){
      if(typeof caption.id!=='string' || !caption.text)continue;
      joined.set(m.meeting_id+'|'+caption.id,{
        meeting_id:m.meeting_id,title:m.title,started_at:m.started_at,caption
      });
    }
    await chrome.storage.local.set({
      queue:[...joined.values()].slice(-10000),
      lastCaptionSeen:new Date().toISOString()
    });
    if(Date.now()-c.lastAttempt>5000)await flush();
    return {ok:true};
  }).then(reply).catch(err=>reply({ok:false,error:String(err)}));
  return true;
});

async function flush(){
  const c=await chrome.storage.local.get(DEFAULTS);
  if(!c.enabled)return {ok:false,error:'Captura desactivada'};
  if(!c.token)return {ok:false,error:'Falta el token'};
  if(!c.queue?.length)return {ok:true,sent:0};
  const first=c.queue[0];
  const batch=c.queue.filter(x=>x.meeting_id===first.meeting_id).slice(0,100);
  await chrome.storage.local.set({lastAttempt:Date.now()});
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),10000);
  try{
    const response=await fetch(c.server+'/api/events',{
      method:'POST',signal:controller.signal,cache:'no-store',
      headers:{
        'Content-Type':'application/json',
        Authorization:'Bearer '+c.token
      },
      body:JSON.stringify({
        meeting_id:first.meeting_id,title:first.title,
        started_at:first.started_at,captions:batch.map(x=>x.caption)
      })
    });
    if(!response.ok)throw Error('Error de envío HTTP '+response.status);
    const confirmation=await response.json();
    if(!confirmation.ok)throw Error('Servidor no confirmó el guardado');
    const sentKeys=new Set(batch.map(x=>x.meeting_id+'|'+x.caption.id));
    const latest=(await chrome.storage.local.get({queue:[]})).queue;
    await chrome.storage.local.set({
      queue:latest.filter(x=>!sentKeys.has(x.meeting_id+'|'+x.caption.id)),
      lastSync:new Date().toISOString(),lastError:''
    });
    return {ok:true,sent:batch.length};
  }catch(e){
    const error=String(e.message||e).slice(0,160);
    await chrome.storage.local.set({lastError:error});
    return {ok:false,error};
  }finally{clearTimeout(timeout)}
}

async function getAudioContext(){
  const existing=await chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT']});
  return existing.some(context=>context.documentUrl===chrome.runtime.getURL('offscreen.html'));
}
async function pingAudioDocument(){
  for(let attempt=0;attempt<5;attempt++){
    try{
      const result=await chrome.runtime.sendMessage({target:'offscreen',type:'CAPTURE_PING'});
      if(result?.ready)return;
    }catch(_error){}
    await new Promise(resolve=>setTimeout(resolve,100*(attempt+1)));
  }
  throw Error('No se inició el módulo de audio. Recarga la extensión e intenta nuevamente.');
}
async function getAudioStatus(tabId){
  let result={ok:true,active:false,tabId:null,pending:0,sequence:0,state:'stopped',occupied:false};
  if(await getAudioContext()){
    try{
      const live=await chrome.runtime.sendMessage({target:'offscreen',type:'CAPTURE_STATUS'});
      if(live?.ok) result={...result,...live};
    }catch(_error){}
  }
  try{
    const tabs=await chrome.tabCapture.getCapturedTabs();
    result.occupied=tabs.some(t=>t.tabId===tabId && (t.status==='active'||t.status==='pending'));
  }catch(_error){}
  return result;
}

async function sendOffscreenControl(type){
  if(!await getAudioContext())return {ok:false,error:'No hay grabación de audio activa'};
  await pingAudioDocument();
  return chrome.runtime.sendMessage({target:'offscreen',type});
}

let preparePromise=null;
async function prepareAudioDocument(){
  if(!preparePromise){
    preparePromise=(async()=>{
      if(!await getAudioContext()){
        try{
          await chrome.offscreen.createDocument({
            url:'offscreen.html',reasons:['USER_MEDIA'],
            justification:'Capturar audio de Teams únicamente al pulsar Grabar'
          });
        }catch(err){
          // Other requests may have created it while this promise was pending.
          if(!await getAudioContext())throw err;
        }
      }
      await pingAudioDocument();
    })().finally(()=>{preparePromise=null});
  }
  return preparePromise;
}
async function startAudio(message,streamIdPromise){
  const c=await chrome.storage.local.get(DEFAULTS);
  if(!c.token)throw Error('Configura el token del servidor');
  let url;
  try {url=new URL(c.server)}
  catch {throw Error('URL del servidor inválida')}
  if(!['http:','https:'].includes(url.protocol) || !url.hostname ||
     url.username || url.password ||
     (url.pathname!=='/' && url.pathname!=='') || url.search || url.hash)
    throw Error('Configura una URL de servidor válida en Teams CC');
  if(chrome.permissions?.contains){
    const authorized=await chrome.permissions.contains({
      origins:[url.protocol+'//'+url.hostname+'/*']
    });
    if(!authorized)throw Error('Permiso de acceso pendiente. Abre Teams CC y pulsa Guardar para autorizar esta URL.');
  }
  const tab=await chrome.tabs.get(message.tabId);
  if(!tab.url || !/^https:\/\/teams\.(microsoft\.com|cloud\.microsoft|live\.com)\//.test(tab.url))
    throw Error('Abre Teams Web en la pestaña activa');
  const context=message.context;
  if(!context || !/^[A-Za-z0-9_-]{4,100}$/.test(context.meeting_id) ||
     !context.started_at)throw Error('Identificador de reunión inválido');
  // Claim the ID created synchronously under Chrome's user gesture.
  let streamId;
  try{
    streamId=await streamIdPromise;
  }catch(err){
    const reason=String(err.message||err);
    if(/Cannot capture a tab with an active stream/i.test(reason)){
      const state=await getAudioStatus(message.tabId);
      if(state.active && state.tabId===message.tabId)
        throw Error('Esta pestaña ya está grabándose en Teams CC. Usa Detener, no Grabar otra vez.');
      if(state.active)
        throw Error('Teams CC ya graba otra pestaña. Detén primero la grabación anterior.');
      throw Error('Esta pestaña ya tiene una captura activa en Chrome. Revisa otras extensiones de grabación; no cierres una reunión en curso.');
    }
    throw Error('Permiso de pestaña denegado por Chrome: '+reason);
  }
  if(!await getAudioContext())
    throw Error('El módulo de audio no estaba preparado. Abre nuevamente el popup.');
  await pingAudioDocument();
  try{
    const response=await chrome.runtime.sendMessage({
      target:'offscreen',type:'CAPTURE_START',streamId,
      tabId:tab.id,context,includeMic:!!message.includeMic,token:c.token,
      server:url.origin
    });
    if(!response?.ok)throw Error(response?.error||'El módulo de audio no confirmó el inicio');
    return response;
  }catch(err){
    throw Error('Inicio de grabación: '+String(err.message||err));
  }
}
