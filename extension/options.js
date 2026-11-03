// Full extension tab: unlike toolbar popups and offscreen pages, Chrome can
// present the first-time microphone permission prompt here.
const $=id=>document.getElementById(id);
let state='prompt';
function show(message,kind='warn',detail=''){
  $('state').textContent=message;
  $('state').className=kind;
  $('detail').textContent=detail;
}
async function checkPermission(){
  if(!navigator.permissions?.query){
    state='unknown';
    show('Permiso no verificable en este navegador','warn',
      'Pulsa Autorizar micrófono y comprueba el resultado.');
    return state;
  }
  try{
    const perm=await navigator.permissions.query({name:'microphone'});
    state=perm.state;
    if(state==='granted')
      show('Micrófono autorizado permanentemente para esta extensión','good',
        'Ya puedes volver a Teams. No tendrás que volver a conceder permiso en cada reunión, salvo que Chrome lo revoque.');
    else if(state==='denied')
      show('Micrófono bloqueado por Chrome','bad',
        'Cambia Micrófono a Permitir en la configuración del sitio o revisa las políticas del navegador.');
    else
      show('Pendiente de autorización','warn',
        'Pulsa Autorizar micrófono; cuando Chrome pregunte, elige Permitir (no una autorización de una sola vez).');
    perm.onchange=()=>{void checkPermission()};
  }catch(err){
    state='unknown';
    show('No se pudo consultar el permiso','warn',String(err.message||err));
  }
  return state;
}
$('authorize').addEventListener('click',async()=>{
  $('authorize').disabled=true;
  try{
    // getUserMedia must run from this visible tab after an explicit click.
    const stream=await navigator.mediaDevices.getUserMedia({
      audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}
    });
    stream.getTracks().forEach(track=>track.stop());
    await checkPermission();
    if(state!=='granted')
      show('Micrófono comprobado; verifica si Chrome guardó el permiso','warn',
        'Comprueba que Chrome permita acceder al micrófono en las siguientes reuniones.');
  }catch(err){
    const label=err?.name||'Error';
    show('No se concedió el permiso del micrófono ('+label+')','bad',
      String(err?.message||err)+'. Si el permiso figura como bloqueado, revisa Configuración del sitio.');
  }finally{
    $('authorize').disabled=false;
  }
});
$('check').addEventListener('click',()=>{void checkPermission()});
$('origin').textContent=chrome.runtime.getURL('');
$('settings').addEventListener('click',()=>{
  const site='chrome-extension://'+chrome.runtime.id;
  chrome.tabs.create({
    url:'chrome://settings/content/siteDetails?site='+encodeURIComponent(site)
  }).catch(()=>{
    show('Abre chrome://extensions → Detalles → Configuración del sitio','warn');
  });
});
void checkPermission();
