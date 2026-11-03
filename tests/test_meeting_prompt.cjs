// Active Teams meeting prompt: simulated DOM, privacy, consent and Chrome fallback.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const root=path.join(__dirname,'..');
const promptSource=fs.readFileSync(path.join(root,'extension/meeting_prompt.js'),'utf8');
const backgroundSource=fs.readFileSync(path.join(root,'extension/background.js'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'extension/manifest.json'),'utf8');

function harness({meetingPrompts=true,audioStatus={status:'stopped',pending:0}}={}){
  let now=1000, buttons=[],shown=null,interval=null,storageListener=null,events=[];
  let nextRoot=null;
  const makeButton=()=>{
    const handlers={};
    return {
      disabled:false,removed:false,
      addEventListener:(name,fn)=>{handlers[name]=fn},
      click:()=>{assert.ok(handlers.click);return handlers.click()},
      remove(){this.removed=true}
    };
  };
  const document={
    documentElement:{},
    body:{appendChild(node){shown=node}},
    querySelectorAll:()=>buttons,
    createElement:()=>{
      const p=makeButton(),d=makeButton(),notice={textContent:''};
      const controls={prepare:p,dismiss:d,notice};
      const shadow={innerHTML:'',getElementById:id=>controls[id]};
      const node={
        id:'',style:{},remove(){if(shown===node)shown=null},
        attachShadow:()=>shadow
      };
      node.controls=controls;
      nextRoot=node;
      return node;
    }
  };
  const chrome={
    storage:{
      local:{get:(defaults,callback)=>callback({...defaults,meetingPrompts,audioStatus})},
      onChanged:{addListener:fn=>storageListener=fn}
    },
    runtime:{sendMessage:msg=>{events.push(msg);return Promise.resolve({ok:true})}}
  };
  const Date={now:()=>now};
  const sandbox={chrome,document,globalThis:{},window:{addEventListener(){}},
    Date, MutationObserver:class{observe(){}}, setTimeout:()=>1,clearTimeout:()=>{},
    setInterval:fn=>{interval=fn}};
  vm.runInNewContext(promptSource,sandbox);
  assert.ok(interval && storageListener,'detector and preference listener registered');
  const button=(label,tid='')=>({
    getAttribute(name){
      return ({'aria-label':label,title:'','data-tid':tid})[name]||null;
    },hasAttribute(){return false},closest(){return null},
    getClientRects(){return [{}]}
  });
  const advance=(ms)=>{now+=ms;interval()};
  const change=(key,value)=>storageListener({[key]:{newValue:value}},'local');
  return {button,advance,change,setButtons:arr=>{buttons=arr}, get shown(){return shown},
    get lastRoot(){return nextRoot}, get events(){return events}};
}

test('does not prompt in calendar or prejoin, prompts after stable in-call control',()=>{
  const h=harness();
  h.setButtons([h.button('Join now','prejoin-join-button')]);
  h.advance(6000);
  assert.equal(h.shown,null);
  h.setButtons([h.button('Leave','call-hangup')]);
  h.advance(100);
  assert.equal(h.shown,null,'must not prompt instantly');
  h.advance(1400);
  assert.equal(h.shown?.id,'homelab-teamscc-recording-prompt');
});
test('dismisses once per meeting and prompts again only after leaving',()=>{
  const h=harness();
  h.setButtons([h.button('Colgar','call-hangup')]);
  h.advance(100);
  h.advance(1300);
  assert.ok(h.shown);
  h.shown.controls.dismiss.click();
  assert.equal(h.shown,null);
  h.advance(10000);
  assert.equal(h.shown,null);
  h.setButtons([]);
  h.advance(100);
  h.advance(16000);
  h.setButtons([h.button('Salir de la reunión')]);
  h.advance(100);
  h.advance(1300);
  assert.ok(h.shown,'new meeting may prompt after prior call ended');
});
test('prepare sends OPEN_RECORDING_POPUP only; never starts audio automatically',async()=>{
  const h=harness();
  h.setButtons([h.button('Leave meeting','call-hangup')]);
  h.advance(100);
  h.advance(1500);
  const prompt=h.shown;
  assert.ok(prompt);
  prompt.controls.prepare.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(JSON.parse(JSON.stringify(h.events)),[{type:'OPEN_RECORDING_POPUP'}]);
  assert.equal(h.shown,null);
});
test('does not prompt when disabled or recording/upload in progress',()=>{
  const h=harness({meetingPrompts:false});
  h.setButtons([h.button('Leave','call-hangup')]);
  h.advance(100);
  h.advance(1500);
  assert.equal(h.shown,null);
  h.change('meetingPrompts',true);
  assert.ok(h.shown);
  h.change('audioStatus',{status:'recording',pending:0});
  assert.equal(h.shown,null);
});
test('sign-out button is not a meeting',()=>{
  const h=harness();
  h.setButtons([h.button('Sign out','nav')]);
  h.advance(100);
  h.advance(1700);
  assert.equal(h.shown,null);
});
test('manifest installs prompt script and advances extension version',()=>{
  assert.equal(manifest.version,'2.3.0');
  assert.ok(manifest.content_scripts[0].js.includes('meeting_prompt.js'));
  const popup=fs.readFileSync(path.join(root,'extension/popup.html'),'utf8');
  assert.match(popup,/id="meetingPrompts"/);
  assert.match(popup,/v2\.3\.0/);
});
function backgroundHandler(action={openPopup:()=>Promise.resolve()}){
  const listeners=[];
  const register=fn=>listeners.push(fn);
  const noOp=()=>{};
  const chrome={
    alarms:{create:noOp,onAlarm:{addListener:noOp}},
    runtime:{
      id:'teams-test-id',
      onInstalled:{addListener:noOp},
      onStartup:{addListener:noOp},
      onMessage:{addListener:register}
    },
    action,
    storage:{local:{get:async()=>({})}}
  };
  vm.runInNewContext(backgroundSource,{chrome,AbortController,setTimeout,clearTimeout,Date,fetch:()=>{}});
  assert.equal(listeners.length,1);
  return listeners[0];
}
test('background only opens popup on authorized Teams tab request',async()=>{
  const opened=[];
  const handler=backgroundHandler({openPopup:options=>{opened.push(options);return Promise.resolve()}});
  let result;
  const sent={type:'OPEN_RECORDING_POPUP'};
  const sender={id:'teams-test-id',url:'https://teams.microsoft.com/v2',
    tab:{id:77,windowId:4,url:'https://teams.microsoft.com/v2'}};
  assert.equal(handler(sent,sender,r=>{result=r}),true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(result.ok,true);
  assert.equal(opened.length,1);
  assert.equal(opened[0].windowId,4);
  assert.equal(handler(sent,{...sender,tab:{id:1,url:'https://not-teams.example.org/'}},()=>{}),false);
  assert.equal(opened.length,1);
});
test('background returns safe fallback when Chrome blocks opening popup',async()=>{
  const handler=backgroundHandler({openPopup:()=>Promise.reject(Error('Gesture blocked'))});
  let result;
  const sender={id:'teams-test-id',
    tab:{id:77,windowId:4,url:'https://teams.cloud.microsoft/v2'}};
  assert.equal(handler({type:'OPEN_RECORDING_POPUP'},sender,r=>{result=r}),true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(result.ok,false);
  assert.match(result.error,/barra de Chrome/);
});
