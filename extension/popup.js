const $=s=>document.querySelector(s);
const DEFAULTS={enabled:false,token:'',server:'',audioStatus:{status:'stopped',error:''},queue:[],includeMic:false,lastSync:'',lastError:'',lastCaptionSeen:'',connection:null};
function time(s){if(!s)return '';const t=new Date(s);return Number.isNaN(t.valueOf())?s:t.toLocaleString('es-CL')}
function status(message,klass){const label=$('#connectStatus');label.textContent=message;label.className=klass||''}
async function render(){
  const c=await chrome.storage.local.get(DEFAULTS);
  $('#enabled').checked=c.enabled;
  $('#token').value=c.token;
  $('#server').value=c.server;
  $('#mic').checked=!!c.includeMic;
  showAudio(c.audioStatus);
  $('#captureStatus').textContent=c.enabled
    ? (c.lastCaptionSeen?'Captura habilitada · Último CC: '+time(c.lastCaptionSeen):
      'Captura habilitada · Esperando CC de Teams')
    : 'Captura deshabilitada';
  $('#queueStatus').textContent='Pendientes: '+c.queue.length;
  $('#syncStatus').textContent=c.lastSync
    ? 'Último envío confirmado: '+time(c.lastSync)
    : 'Sin transcripciones enviadas todavía';
  if(c.lastError)$('#syncStatus').textContent+=' · Error: '+c.lastError;
  const conn=c.connection;
  if(conn && conn.ok){
    status('● Conectado · Token válido','good');
    $('#meetingCount').textContent='Reuniones en servidor: '+conn.meetings+
      ' · Verificado: '+time(conn.at);
  }else if(conn){
    status('● Sin conexión autenticada','bad');
    $('#meetingCount').textContent=conn.error||'No se pudo comprobar el servidor';
  }else{
    status('Conexión sin comprobar','warn');
    $('#meetingCount').textContent='';
  }
}
async function check(){
  status('Comprobando servidor y token...','warn');
  try{
    const result=await chrome.runtime.sendMessage({type:'CHECK_CONNECTION'});
    await render();
    if(!result?.ok){
      status('● No conectado','bad');
      $('#meetingCount').textContent=result?.error||'Error desconocido';
    }
  }catch(err){
    status('● Error al consultar extensión','bad');
    $('#meetingCount').textContent=String(err?.message||err);
  }
}
function normalizeServer(raw){
  let url;
  try{url=new URL(String(raw||'').trim())}
  catch(_e){throw Error('Introduce una URL completa, incluyendo http:// o https:// y, si corresponde, el puerto.')}
  if(!['http:','https:'].includes(url.protocol) || url.username || url.password ||
     (url.pathname!=='/' && url.pathname!=='') || url.search || url.hash)
    throw Error('Utiliza únicamente el origen del servidor: esquema, dominio/IP y puerto opcional, sin credenciales ni rutas.');
  return url.origin;
}
$('#save').onclick=()=>{
  const button=$('#save');
  let server;
  try{server=normalizeServer($('#server').value)}
  catch(err){status(String(err.message||err),'bad');return}
  button.disabled=true;
  // Permissions.request MUST be called synchronously in this click gesture.
  // Only the entered origin is granted, never all hostnames in the wildcard declaration.
  // Chrome host match patterns do not include a port: grant only the entered
  // scheme+hostname. Network requests still use the exact configured port.
  const parsed=new URL(server);
  const permission=chrome.permissions.request({
    origins:[parsed.protocol+'//'+parsed.hostname+'/*']
  });
  Promise.resolve(permission).then(async granted=>{
    if(!granted)throw Error('Chrome no autorizó el acceso al servidor. Pulsa Guardar y concede el permiso.');
    await chrome.storage.local.set({
      enabled:$('#enabled').checked,
      token:$('#token').value.trim(),
      server,
      connection:null,lastError:''
    });
    await render();
    await check();
  }).catch(err=>status('Configuración no guardada: '+String(err.message||err),'bad'))
    .finally(()=>{button.disabled=false});
};
$('#check').onclick=check;
$('#retry').onclick=async()=>{
  status('Sincronizando cola...','warn');
  try{
    const result=await chrome.runtime.sendMessage({type:'FLUSH'});
    await render();
    if(result?.error)$('#syncStatus').textContent='Sincronización: '+result.error;
  }catch(err){
    $('#syncStatus').textContent='Error de sincronización: '+String(err?.message||err);
  }
};
(async()=>{await render();const cfg=await chrome.storage.local.get({token:''});
  if(cfg.token)await check()})();

function showAudio(state){
  const labels={stopped:'Detenido',recording:'Grabando',uploading:'Enviando audio',error:'Error de captura/envío'};
  $('#audioState').textContent=(labels[state?.status]||'No iniciado')+
    (state?.sequence?' · '+state.sequence+' fragmentos':'')+
    (state?.pending?' · '+state.pending+' pendientes':'')+
    (state?.error?' · '+state.error:'');
  $('#audioState').className='small '+(state?.status==='error'?'bad':state?.status==='recording'?'good':'');
}
chrome.storage.onChanged.addListener((changes,area)=>{
  if(area==='local' && changes.audioStatus)showAudio(changes.audioStatus.newValue);
});
// An already-open Teams tab can predate the extension installation/reload.
// Recover the content script without forcing the user out of the meeting.
async function getMeetingContext(tabId){
  const request=()=>chrome.tabs.sendMessage(tabId,{type:'GET_MEETING_CONTEXT'});
  try {
    const answer=await request();
    if(answer?.meeting_id && answer?.started_at)return answer;
  } catch(err) {
    // Missing receiver is expected when Teams was opened before installing v2.
    if(!/Receiving end does not exist|Could not establish connection/i.test(String(err?.message||err)))
      throw Error('Comunicación con Teams: '+String(err?.message||err));
  }
  try {
    await chrome.scripting.executeScript({
      target:{tabId},
      files:['content.js'],
      injectImmediately:true
    });
    const answer=await request();
    if(answer?.meeting_id && answer?.started_at)return answer;
  } catch(err) {
    throw Error('No se pudo activar Teams CC en esta pestaña: '+String(err?.message||err)+'. Recarga Teams y prueba de nuevo.');
  }
  throw Error('Teams CC no devolvió una reunión válida. Recarga la pestaña de Teams.');
}

let prepared=null;
let micGranted=false;

async function primeMeetingContext(){
  const tabs=await chrome.tabs.query({active:true,currentWindow:true});
  const tab=tabs[0];
  if(!tab?.id || !/^https:\/\/teams\.(microsoft\.com|cloud\.microsoft|live\.com)\//.test(tab.url||''))
    throw Error('Abre el popup desde una pestaña de Teams Web');
  const context=await getMeetingContext(tab.id);
  const result=await chrome.runtime.sendMessage({type:'PREPARE_AUDIO'});
  if(!result?.ok)throw Error(result?.error||'Módulo de audio no preparado');
  const captureStatus=await chrome.runtime.sendMessage({type:'GET_AUDIO_STATUS',tabId:tab.id});
  return {tabId:tab.id,context,captureStatus};
}
$('#record').disabled=true;
showAudio({status:'stopped',error:'Preparando permisos de captura...'});
primeMeetingContext().then(ready=>{
  prepared=ready;
  applyCaptureStatus(ready.captureStatus);
}).catch(err=>{
  $('#record').disabled=true;
  showAudio({status:'error',error:String(err.message||err).slice(0,180)});
});

function showMicStatus(message,klass='warn'){
  $('#micState').textContent=message;
  $('#micState').className='small '+klass;
}
async function checkMicPermission(){
  try{
    const perm=await navigator.permissions.query({name:'microphone'});
    micGranted=perm.state==='granted';
    if(perm.state==='granted')
      showMicStatus('Micrófono autorizado para esta extensión. Se recordará entre reuniones.','good');
    else if(perm.state==='denied')
      showMicStatus('Micrófono bloqueado. Abre Configurar permiso permanente y revisa la configuración de Chrome.','bad');
    else
      showMicStatus('Micrófono pendiente. Abre Configurar permiso permanente una sola vez.','warn');
    perm.onchange=()=>{void checkMicPermission()};
    return micGranted;
  }catch(_error){
    micGranted=false;
    showMicStatus('No fue posible comprobar el permiso. Abre Configurar permiso permanente.','warn');
    return false;
  }
}
function applyCaptureStatus(state){
  if(!state?.ok){
    $('#record').disabled=false;
    showAudio({status:'stopped',error:'Preparado. Autoriza la reunión para grabar.'});
    return;
  }
  const occupied=!!state.occupied || !!state.active;
  $('#record').disabled=occupied;
  if(state.active && prepared && state.tabId===prepared.tabId){
    showAudio({status:'recording',sequence:state.sequence,pending:state.pending,
      error:'Esta pestaña ya se está grabando. Usa Detener al finalizar.'});
  }else if(state.active){
    showAudio({status:'error',error:'Teams CC ya está grabando otra pestaña. Usa Detener primero.'});
  }else if(state.occupied){
    showAudio({status:'error',error:'Chrome ya tiene una captura de esta pestaña. Detén la captura activa antes de iniciar otra.'});
  }else{
    showAudio({status:'stopped',error:'Preparado para grabar. Se requiere autorización de la reunión.'});
  }
}
$('#inspectAudio').onclick=async()=>{
  if(!prepared)return showAudio({status:'error',error:'Abre el popup desde Teams Web'});
  try{
    const state=await chrome.runtime.sendMessage({type:'GET_AUDIO_STATUS',tabId:prepared.tabId});
    applyCaptureStatus(state);
  }catch(err){showAudio({status:'error',error:String(err.message||err)})}
};

$('#prepareMic').onclick=()=>{
  // A full-size options tab has the browser UI needed for getUserMedia prompts.
  chrome.runtime.openOptionsPage().catch(err=>
    showMicStatus('No pude abrir la configuración: '+String(err.message||err),'bad')
  );
};
$('#mic').onchange=()=>{
  chrome.storage.local.set({includeMic:$('#mic').checked}).catch(()=>{});
};
void checkMicPermission();

$('#record').onclick=()=>{
  if(!$('#consent').checked){
    showAudio({status:'error',error:'Confirma primero la autorización de la reunión'});return;
  }
  if(!prepared){
    showAudio({status:'error',error:'La captura aún no está preparada. Cierra y abre el popup.'});return;
  }
  if($('#mic').checked && !micGranted){
    showAudio({status:'error',error:'Micrófono sin permiso. Pulsa Configurar permiso permanente, autoriza en la pestaña y vuelve a abrir Teams CC.'});
    return;
  }
  const includeMic=$('#mic').checked;
  $('#record').disabled=true;
  // No awaits before this call: the Chrome transient user gesture must survive
  // from this button into the service worker's START_AUDIO listener.
  const capture=chrome.runtime.sendMessage({
    type:'START_AUDIO',tabId:prepared.tabId,context:prepared.context,includeMic
  });
  capture.then(result=>{
    if(!result?.ok)throw Error(result?.error||'No se inició el audio');
    $('#consent').checked=false;
    showAudio({status:'recording',error:'Grabación en curso. Usa Detener al finalizar.'});
  }).catch(err=>{
    $('#record').disabled=false;
    showAudio({status:'error',error:String(err?.message||err).slice(0,220)});
  });
};
$('#stop').onclick=async()=>{
  try{
    const result=await chrome.runtime.sendMessage({type:'STOP_AUDIO'});
    if(!result?.ok)throw Error(result?.error||'No se pudo detener');
    showAudio({status:'uploading',error:'Finalizando y guardando fragmentos. Pulsa Comprobar captura para revisar.'});
    $('#record').disabled=true;
  }catch(e){showAudio({status:'error',error:String(e.message||e)})}
};
$('#retryAudio').onclick=async()=>{
  try{
    const result=await chrome.runtime.sendMessage({type:'RETRY_AUDIO'});
    if(!result?.ok)throw Error(result?.error||'Sin fragmentos pendientes');
    showAudio({status:'uploading'});
  }catch(e){showAudio({status:'error',error:String(e.message||e)})}
};
