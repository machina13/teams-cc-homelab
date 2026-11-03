// Teams CC Homelab: todos los horarios visuales se muestran en Chile continental.
// Los timestamps de SQLite y de la API permanecen en UTC para integridad y orden cronologico.
const $ = s => document.querySelector(s);
const CHILE_ZONE = 'America/Santiago';
const CHILE_FORMAT = new Intl.DateTimeFormat('es-CL', {
  timeZone: CHILE_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
});
function formatChile(raw) {
  if (!raw) return 'Sin fecha';
  const value = String(raw).trim();
  // Los registros historicos sin zona explicita se interpretan como UTC.
  const iso = /(?:Z|[+-][0-9]{2}:[0-9]{2})$/i.test(value) ? value : value + 'Z';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? value : CHILE_FORMAT.format(date) + ' (Chile)';
}

let token=sessionStorage.getItem('teamsToken')||'',current=null,activeAudioURL=null;
let playFetchId=0;
$('#token').value=token;
async function api(path){
  const res=await fetch(path,{headers:{Authorization:'Bearer '+token}});
  if(!res.ok) throw Error('HTTP '+res.status);
  return res.json();
}
async function reload(){
  $('#status').textContent='Cargando...';
  $('#meetings').replaceChildren();
  try{
    const result=await api('/api/meetings');
    $('#status').textContent=result.meetings.length+' reuniones';
    for(const m of result.meetings){
      const a=document.createElement('article');
      const b=document.createElement('button');
      const meta=document.createElement('small');
      b.textContent=m.title;
      b.addEventListener('click',()=>show(m));
      meta.textContent=' '+formatChile(m.started_at)+' · '+m.caption_count+' CC · '+(m.audio_count||0)+' audios · '+(m.asr_count||0)+' ASR';
      a.append(b,meta);
      $('#meetings').append(a);
    }
  }catch(e){$('#status').textContent='No autorizado o servidor no disponible: '+e.message}
}
async function show(m){
  // Invalidate any pending audio download and detach player from the old row.
  playFetchId++;
  resetAudioPlayback();
  current=m;
  try{
    const d=await api('/api/meetings/'+encodeURIComponent(m.id));
    $('#detail').hidden=false;
    $('#meetingTitle').textContent=d.meeting.title+' · '+formatChile(d.meeting.started_at);
    const rows=$('#captionRows');
    rows.replaceChildren();
    for(const c of d.captions){
      const p=document.createElement('p'),b=document.createElement('strong');
      b.textContent='['+formatChile(c.captured_at)+'] '+c.speaker+': ';
      p.append(b,document.createTextNode(c.content));
      rows.append(p);
    }
    const audioList=$('#audioList');
    audioList.replaceChildren();
    for(const f of (d.audio_files||[])){
      const item=document.createElement('div');
      item.style.cssText='display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #445;flex-wrap:wrap';
      const cb=document.createElement('input');
      cb.type='checkbox';cb.className='audioSelect';cb.value=String(f.sequence);
      cb.disabled=f.status==='transcribed' || f.status==='processing' || f.status==='queued';
      cb.setAttribute('aria-label','Seleccionar fragmento '+(f.sequence+1));
      const label=document.createElement('span');
      label.textContent='#'+(f.sequence+1)+' · '+formatChile(f.captured_at)+' · '+
        Math.ceil(f.bytes/1024)+' KiB · '+({
          stored:'Guardado',queued:'En cola',processing:'Transcribiendo',
          transcribed:'Transcrito',failed:'Falló ('+(f.error||'error')+')'
        }[f.status]||f.status);
      const play=document.createElement('button');play.textContent='Escuchar';
      const audioSlot=document.createElement('div');
      audioSlot.style.cssText='flex-basis:100%;min-width:0';
      play.onclick=()=>playAudio(d.meeting.id,f.sequence,audioSlot,play);
      const save=document.createElement('button');save.textContent='Descargar';
      save.onclick=()=>downloadAudio(d.meeting.id,f.sequence);
      item.append(cb,label,play,save,audioSlot);audioList.append(item);
    }
    if(!d.audio_files?.length)audioList.textContent='No se ha recibido audio de esta reunión.';
    $('#authorizeTranscribe').checked=false;
    $('#asrStatus').textContent=(d.audio_files||[]).some(f=>['processing','queued'].includes(f.status))
      ? 'Transcripción en curso. Pulsa Actualizar estados para consultar el progreso.'
      : 'Selecciona los fragmentos de esta reunión que quieras transcribir.';
    const asr=$('#asrRows');
    asr.replaceChildren();
    for(const c of (d.asr_segments||[])){
      const p=document.createElement('p'),b=document.createElement('strong');
      b.textContent='['+formatChile(c.captured_at)+'] Groq '+(c.sequence+1)+': ';
      p.append(b,document.createTextNode(c.content));
      asr.append(p);
    }
    if(!d.asr_segments?.length)asr.textContent='Sin audio transcrito todavía.';
  }catch(e){alert(e.message)}
}
async function dl(fmt){
  if(!current)return;
  const res=await fetch('/api/meetings/'+encodeURIComponent(current.id)+'/'+fmt,{
    headers:{Authorization:'Bearer '+token}
  });
  if(!res.ok)return alert('Error '+res.status);
  const blob=await res.blob();
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='teams-'+current.id+'.'+fmt;
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),1500);
}
$('#connect').onclick=()=>{
  token=$('#token').value.trim();
  sessionStorage.setItem('teamsToken',token);
  reload();
  loadGroqStatus();
};
$('#downloadTxt').onclick=()=>dl('txt');
$('#downloadJson').onclick=()=>dl('json');
if(token){reload();loadGroqStatus()}

async function fetchAudio(meetingId,sequence){
  const res=await fetch('/api/meetings/'+encodeURIComponent(meetingId)+'/audio/'+sequence,{
    headers:{Authorization:'Bearer '+token},cache:'no-store'
  });
  if(!res.ok)throw Error('No se pudo recuperar el audio (HTTP '+res.status+')');
  return res.blob();
}
function resetAudioPlayback(){
  const player=$('#audioPlayer');
  player.pause();
  player.removeAttribute('src');
  player.load();
  $('#audioPlayerHome').append(player); // Keep the player alive when a row is cleared.
  player.hidden=true;
  if(activeAudioURL){
    URL.revokeObjectURL(activeAudioURL);
    activeAudioURL=null;
  }
  $('#audioPlaybackStatus').textContent='';
}
async function playAudio(meetingId,sequence,slot,button){
  // Fetching a Bearer-protected file is asynchronous; the original code
  // called play() after fetch and Chrome could reject the lost user gesture.
  // Instead load native controls into the selected fragment's row and let
  // the user press Play directly, where Chrome accepts the gesture.
  const thisRequest=++playFetchId;
  button.disabled=true;
  button.textContent='Preparando...';
  try{
    const player=$('#audioPlayer');
    if(!player.canPlayType('audio/webm; codecs="opus"'))
      throw Error('Este navegador no admite WebM/Opus. Usa Descargar.');
    const blob=await fetchAudio(meetingId,sequence);
    if(thisRequest!==playFetchId || current?.id!==meetingId)return;
    if(!blob.size)throw Error('El archivo de audio está vacío.');
    resetAudioPlayback();
    activeAudioURL=URL.createObjectURL(blob);
    player.src=activeAudioURL;
    player.controls=true;
    player.autoplay=false;
    player.preload='metadata';
    player.muted=false;
    player.volume=1;
    player.hidden=false;
    player.style.cssText='width:100%;max-width:460px;display:block;margin:9px 0';
    slot.append(player);
    player.load();
    $('#audioPlaybackStatus').textContent='Audio listo: pulsa ▶ en el reproductor del fragmento #'+(sequence+1)+'.';
    player.scrollIntoView?.({block:'nearest',behavior:'smooth'});
  }catch(err){
    if(thisRequest===playFetchId)
      $('#audioPlaybackStatus').textContent='No se pudo preparar el audio: '+String(err.message||err);
  }finally{
    if(thisRequest===playFetchId){
      button.disabled=false;
      button.textContent='Escuchar';
    }
  }
}
$('#audioPlayer').addEventListener('error',()=>{
  $('#audioPlaybackStatus').textContent='El navegador no pudo decodificar el archivo. Prueba Descargar y abrirlo en VLC.';
});
$('#audioPlayer').addEventListener('play',()=>{
  $('#audioPlaybackStatus').textContent='Reproduciendo audio en este navegador. Comprueba el volumen y la salida de sonido.';
});
async function downloadAudio(meetingId,sequence){
  try{
    const blob=await fetchAudio(meetingId,sequence);
    const uri=URL.createObjectURL(blob),anchor=document.createElement('a');
    anchor.href=uri;anchor.download='teams-'+meetingId+'-'+sequence+'.webm';
    anchor.click();setTimeout(()=>URL.revokeObjectURL(uri),3000);
  }catch(err){$('#asrStatus').textContent=String(err.message||err)}
}
$('#transcribeSelected').onclick=async()=>{
  if(!current)return;
  const sequences=[...document.querySelectorAll('.audioSelect:checked')].map(x=>Number(x.value));
  if(!sequences.length){$('#asrStatus').textContent='Selecciona al menos un fragmento';return}
  if(!$('#authorizeTranscribe').checked){
    $('#asrStatus').textContent='Debes autorizar expresamente el envío de estos audios a Groq';return
  }
  $('#transcribeSelected').disabled=true;
  try{
    const res=await fetch('/api/meetings/'+encodeURIComponent(current.id)+'/transcribe',{
      method:'POST',
      headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify({sequences,confirm_external_processing:true})
    });
    const data=await res.json();
    if(!res.ok)throw Error(data.error||'HTTP '+res.status);
    $('#asrStatus').textContent='Se han encolado '+data.queued+' audios para Groq. Actualiza los estados para ver resultados.';
  }catch(err){$('#asrStatus').textContent='No se pudo iniciar: '+String(err.message||err)}
  finally{$('#transcribeSelected').disabled=false}
};
$('#refreshDetail').onclick=()=>{if(current)show(current)};

$('#selectPending').onclick=()=>{
  for(const checkbox of document.querySelectorAll('.audioSelect:not(:disabled)'))checkbox.checked=true;
  $('#asrStatus').textContent='Fragmentos pendientes de esta reunión seleccionados. La transcripción requiere confirmar el envío a Groq.';
};
$('#deselectAudio').onclick=()=>{
  for(const checkbox of document.querySelectorAll('.audioSelect'))checkbox.checked=false;
  $('#authorizeTranscribe').checked=false;
  $('#asrStatus').textContent='Selección desmarcada. No se enviará ningún audio a Groq.';
};

const GROQ_ERRORS={
  lan_only:'La configuración de claves está disponible solamente desde la red local.',
  unauthorized:'Token del servidor no válido. Conecta primero.',
  invalid_groq_key_format:'Formato inválido. La API key debe comenzar con gsk_.',
  invalid_request:'Solicitud inválida.',
  groq_key_store_failed:'No fue posible guardar la clave en Docker Labs.',
  groq_key_invalid_or_denied:'Groq rechazó la API key (401/403). Comprueba que esté activa.',
  groq_key_unauthorized:'Groq devolvió 401: la clave no es válida. Genera una nueva y copia el valor completo (no el texto enmascarado de la lista).',
  groq_permission_denied:'Groq devolvió 403: la clave está bloqueada por permisos de proyecto u organización. Revisa restricciones en Groq Console.',
  groq_client_rejected:'Cloudflare bloqueó la identificación del cliente (error 1010). Actualiza el backend Teams CC.',
  groq_not_configured:'Primero guarda una API key de Groq.',
  groq_network_unavailable:'Docker Labs no consiguió comunicarse con Groq.',
  groq_api_unavailable:'Groq no está disponible temporalmente.',
  groq_rate_limited:'Groq alcanzó su límite de solicitudes.',
  origin_not_allowed:'Accede desde el dominio oficial de Teams CC.',
};
async function groqRequest(url,body,method='POST'){
  const res=await fetch(url,{
    method,
    headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
    body:JSON.stringify(body),
    cache:'no-store'
  });
  const data=await res.json().catch(()=>({error:'unexpected_response'}));
  if(!res.ok)throw Error(GROQ_ERRORS[data.error]||('HTTP '+res.status+' ('+(data.error||'error')+')'));
  return data;
}
async function loadGroqStatus(){
  if(!token)return;
  try{
    const res=await fetch('/api/settings/groq',{
      headers:{Authorization:'Bearer '+token},cache:'no-store'
    });
    const data=await res.json();
    if(!res.ok)throw Error(GROQ_ERRORS[data.error]||('HTTP '+res.status));
    $('#groqStatus').textContent=data.configured
      ? 'API key configurada en Docker Labs. Puedes probarla o reemplazarla.'
      : 'Sin API key. Introduce tu clave de Groq para habilitar transcripción bajo demanda.';
  }catch(err){
    $('#groqStatus').textContent='No disponible: '+String(err.message||err);
  }
}
$('#saveGroq').onclick=async()=>{
  if(!token){$('#groqFeedback').textContent='Conecta primero con tu token Teams CC.';return}
  const key=$('#groqKey').value.trim();
  $('#groqKey').value='';
  if(!/^gsk_[A-Za-z0-9_-]{16,256}$/.test(key)){
    $('#groqFeedback').textContent='Formato de clave inválido (debe comenzar por gsk_).';
    return;
  }
  $('#saveGroq').disabled=true;
  $('#groqFeedback').textContent='Guardando credencial en el volumen privado...';
  try{
    const result=await groqRequest('/api/settings/groq',{api_key:key});
    $('#groqFeedback').textContent=result.configured
      ? 'Clave guardada. Pulsa «Probar conexión con Groq» para validarla.'
      : 'No se pudo confirmar la configuración.';
    await loadGroqStatus();
  }catch(err){
    $('#groqFeedback').textContent='No se pudo guardar: '+String(err.message||err);
  }finally{
    $('#saveGroq').disabled=false;
  }
};
$('#testGroq').onclick=async()=>{
  if(!token){$('#groqFeedback').textContent='Conecta primero con tu token Teams CC.';return}
  $('#testGroq').disabled=true;
  $('#groqFeedback').textContent='Consultando a Groq (sin enviar audios)...';
  try{
    const response=await groqRequest('/api/settings/groq/test',{});
    $('#groqFeedback').textContent=response.valid
      ? 'Conexión verificada: Groq aceptó tu API key. Modelo: '+response.model+'.'
      : 'Groq no confirmó la API key.';
  }catch(err){
    $('#groqFeedback').textContent='Prueba fallida: '+String(err.message||err);
  }finally{
    $('#testGroq').disabled=false;
  }
};
