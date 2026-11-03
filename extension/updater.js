// Teams CC GitHub updater: manual-only Native Messaging from the extension popup.
// Never sends the Teams token, Groq key, paths or GitHub credentials to the host.
(() => {
  const button=document.getElementById('updateFromGitHub');
  const status=document.getElementById('updateGitHubStatus');
  if(!button || !status)return;
  const HOST='com.machina13.teamscc_updater';
  const text=(message)=>{status.textContent=message};
  function ensureNativeMessaging(){
    const manifest=chrome.runtime.getManifest?.() || {};
    const version=manifest.version||'desconocida';
    if(!manifest.permissions?.includes('nativeMessaging')){
      throw Error('Chrome conserva el manifiesto anterior (v'+version+
        ') sin el permiso nativeMessaging. Abre chrome://extensions, '+
        'busca Teams CC Homelab y pulsa Recargar. No la elimines.');
    }
    if(typeof chrome.runtime.sendNativeMessage!=='function'){
      throw Error('El manifiesto v'+version+' declara nativeMessaging, '+
        'pero Chrome no expone la API. Cierra y reinicia Chrome; '+
        'si persiste, revisa chrome://policy (NativeMessagingUserLevelHosts, '+
        'NativeMessagingBlocklist, NativeMessagingAllowlist).');
    }
  }
  chrome.storage.local.get({lastGitHubUpdate:null}).then(({lastGitHubUpdate})=>{
    if(lastGitHubUpdate?.updated && lastGitHubUpdate?.version){
      text('Última actualización instalada: v'+lastGitHubUpdate.version+
        ' · commit '+(lastGitHubUpdate.commit||'')+'.');
    }
  }).catch(()=>{});
  button.addEventListener('click',async()=>{
    button.disabled=true;
    try{
      ensureNativeMessaging();
      // Never reload an extension while recorded audio is still in RAM.
      const {audioStatus}=await chrome.storage.local.get({
        audioStatus:{status:'stopped',pending:0}
      });
      if(['recording','uploading'].includes(audioStatus?.status) || audioStatus?.pending>0)
        throw Error('Hay audio activo o pendiente. Detén la grabación y confirma que todos los fragmentos estén guardados antes de actualizar.');
      const live=await chrome.runtime.sendMessage({type:'GET_AUDIO_STATUS',tabId:null});
      if(live?.active || live?.pending>0)
        throw Error('Hay una grabación o envíos pendientes. Finaliza primero el audio.');
      text('Buscando actualizaciones en GitHub...');
      // Only a fixed action string, no arbitrary commands, URLs or paths.
      const reply=await chrome.runtime.sendNativeMessage(HOST,{action:'update'});
      if(!reply?.ok)throw Error(reply?.error||'No se pudo actualizar desde GitHub.');
      if(!reply.updated){
        text('Ya tienes la última versión de la rama configurada (v'+reply.version+').');
        return;
      }
      await chrome.storage.local.set({lastGitHubUpdate:{
        updated:true,version:reply.version,commit:reply.commit,
        at:new Date().toISOString()
      }});
      text('Actualizada a v'+reply.version+'. Reiniciando la extensión...');
      // Same unpacked path => same extension ID and Chrome storage/token.
      chrome.runtime.reload();
    }catch(err){
      const reason=String(err?.message||err);
      text(/native messaging host|specified native messaging/i.test(reason)
        ? 'Host local no disponible en este perfil. Comprueba la instalación con scripts/install_native_updater.py --browser chrome --diagnose.'
        : 'Actualización no aplicada: '+reason.slice(0,220));
    }finally{
      button.disabled=false;
    }
  });
})();
