// Native updater popup tests: explicit click only, never update active recordings.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../extension/updater.js'),'utf8');

async function scenario(audio, nativeResult, {manifestVersion='2.1.1',declared=true,apiAvailable=true}={}){
  const handlers={};
  const controls={
    updateFromGitHub:{
      disabled:false,
      addEventListener:(type,fn)=>{handlers[type]=fn}
    },
    updateGitHubStatus:{textContent:''}
  };
  let nativeCalls=0,reloads=0;
  const chrome={
    storage:{local:{
      get:async(defaults)=>Object.assign({},defaults,{
        audioStatus:audio,lastGitHubUpdate:null
      }),
      set:async()=>{}
    }},
    runtime:{
      getManifest:()=>({version:manifestVersion, permissions:declared?['nativeMessaging']:[]}),
      sendMessage:async()=>({
        ok:true,active:audio?.status==='recording',pending:audio?.pending||0
      }),
      sendNativeMessage:async(name,request)=>{
        assert.equal(name,'com.machina13.teamscc_updater');
        assert.deepEqual({...request},{action:'update'});
        nativeCalls++;
        return nativeResult;
      },
      reload:()=>{reloads++}
    }
  };
  if(!apiAvailable)delete chrome.runtime.sendNativeMessage;
  vm.runInNewContext(source,{chrome,document:{getElementById:id=>controls[id]},Date});
  await new Promise(resolve=>setImmediate(resolve));
  await handlers.click();
  return {nativeCalls,reloads,status:controls.updateGitHubStatus.textContent,
    disabled:controls.updateFromGitHub.disabled};
}
test('updates only on explicit click and reloads after successful private update',async()=>{
  const r=await scenario({status:'stopped',pending:0},
    {ok:true,updated:true,version:'2.1.1',commit:'aaaabbbbcccc'});
  assert.equal(r.nativeCalls,1);
  assert.equal(r.reloads,1);
  assert.equal(r.disabled,false);
});
test('does not update or reload if a Teams recording is active',async()=>{
  const r=await scenario({status:'recording',pending:0},{ok:true,updated:true});
  assert.equal(r.nativeCalls,0);
  assert.equal(r.reloads,0);
  assert.match(r.status,/audio activo/);
});
test('does not update if fragments are still pending',async()=>{
  const r=await scenario({status:'error',pending:2},{ok:true,updated:true});
  assert.equal(r.nativeCalls,0);
  assert.equal(r.reloads,0);
});
test('up-to-date response does not reload or start other work',async()=>{
  const r=await scenario({status:'stopped',pending:0},
    {ok:true,updated:false,version:'2.1.0',commit:'aaaabbbbcccc'});
  assert.equal(r.nativeCalls,1);
  assert.equal(r.reloads,0);
  assert.match(r.status,/última versión/);
});
test('a stale Chrome manifest gives explicit reload instructions without native call',async()=>{
  const r=await scenario({status:'stopped',pending:0},{ok:true,updated:false},
    {manifestVersion:'2.0.4',declared:false,apiAvailable:false});
  assert.equal(r.nativeCalls,0);
  assert.equal(r.reloads,0);
  assert.match(r.status,/manifiesto anterior/);
  assert.match(r.status,/chrome:\/\/extensions/);
  assert.match(r.status,/v2\.0\.4/);
});
test('a missing API with declared permission yields policy diagnostics',async()=>{
  const r=await scenario({status:'stopped',pending:0},{ok:true,updated:false},
    {manifestVersion:'2.1.1',declared:true,apiAvailable:false});
  assert.equal(r.nativeCalls,0);
  assert.equal(r.reloads,0);
  assert.match(r.status,/chrome:\/\/policy/);
  assert.match(r.status,/NativeMessaging/);
});
test('native host errors are shown without reloading',async()=>{
  const r=await scenario({status:'stopped',pending:0},
    {ok:false,error:'Acceso privado no configurado'});
  assert.equal(r.nativeCalls,1);
  assert.equal(r.reloads,0);
  assert.match(r.status,/Acceso privado/);
});
