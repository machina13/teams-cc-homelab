/* Regression: Teams already open before extension installation/reload. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const popupSource = fs.readFileSync(path.join(__dirname, '../extension/popup.js'), 'utf8');
const tabUrl='https://teams.microsoft.com/v2/';
const meeting={meeting_id:'m_test_meeting',started_at:'2026-11-02T12:00:00Z',title:'Meeting'};

async function runScenario(receiverInitiallyAvailable) {
  const elements=new Map();
  const element=(id)=>{
    if (!elements.has(id))elements.set(id,{value:'',textContent:'',className:'',checked:false,disabled:false,onclick:null});
    return elements.get(id);
  };
  const sent=[];
  let requests=0,injects=0;
  const chrome={
    tabs:{
      query:async()=>[{id:17,url:tabUrl}],
      sendMessage:async(id,msg)=>{
        assert.equal(id,17);
        assert.equal(msg.type,'GET_MEETING_CONTEXT');
        requests++;
        if(!receiverInitiallyAvailable && injects===0){
          throw new Error('Could not establish connection. Receiving end does not exist.');
        }
        return meeting;
      }
    },
    scripting:{
      executeScript:async(options)=>{
        assert.equal(options.target.tabId,17);
        assert.deepEqual(Array.from(options.files),['content.js']);
        injects++;
      }
    },
    runtime:{
      sendMessage:async(msg)=>{sent.push(msg);return {ok:true}},
    },
    storage:{
      local:{
        get:async(defaults)=>Object.assign({},defaults,{
          token:'test-token',server:'http://127.0.0.1:9000',
          audioStatus:{status:'stopped',error:''}
        }),
        set:async()=>{}
      },
      onChanged:{addListener:()=>{}}
    }
  };
  const context=vm.createContext({
    chrome,
    document:{querySelector:(selector)=>element(selector)},
    setTimeout,
    console,
    Date,
    URL,
  });
  vm.runInContext(popupSource,context,{filename:'popup.js'});
  // Let popup preflight finish before invoking Record.
  for(let tick=0;tick<10;tick++)await new Promise(resolve=>setImmediate(resolve));
  element('#consent').checked=true;
  element('#mic').checked=false;
  // Capture dispatch must happen synchronously during the real user click.
  element('#record').onclick();
  assert.ok(sent.some(m=>m.type==='START_AUDIO'), 'START_AUDIO must be dispatched before any await');
  for(let tick=0;tick<5;tick++)await new Promise(resolve=>setImmediate(resolve));
  return {requests,injects,sent,error:element('#audioState').textContent};
}

test('recovers missing Teams content-script receiver without reloading meeting',async()=>{
  const r=await runScenario(false);
  assert.equal(r.injects,1);
  assert.equal(r.requests,2);
  assert.ok(r.sent.some(m=>m.type==='START_AUDIO' && m.context.meeting_id===meeting.meeting_id));
  assert.match(r.error,/Grabando/);
});
test('does not reinject when Teams content script is already active',async()=>{
  const r=await runScenario(true);
  assert.equal(r.injects,0);
  assert.equal(r.requests,1);
  assert.ok(r.sent.some(m=>m.type==='START_AUDIO'));
});

test('microphone permission is configured once in a stable options tab',async()=>{
  let granted=false, opened=0;
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{value:'',textContent:'',checked:false,disabled:false,onclick:null});
    return elements.get(id);
  };
  const sent=[];
  const chrome={
    tabs:{query:async()=>[{id:3,url:tabUrl}],sendMessage:async()=>meeting},
    runtime:{
      sendMessage:async msg=>{sent.push(msg);return {ok:true}},
      openOptionsPage:async()=>{opened++}
    },
    storage:{local:{get:async d=>Object.assign({},d,{token:'x',server:'http://127.0.0.1:9000',includeMic:true}),set:async()=>{}},
             onChanged:{addListener:()=>{}}},
  };
  const navigator={permissions:{query:async()=>({state:granted?'granted':'prompt'})}};
  const initialize=()=>{
    const env=vm.createContext({chrome,document:{querySelector:element},navigator,setTimeout,URL,Date,console});
    vm.runInContext(popupSource,env);
  };
  initialize();
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  element('#consent').checked=true;
  assert.equal(element('#mic').checked,true);
  element('#record').onclick();
  assert.equal(sent.filter(m=>m.type==='START_AUDIO').length,0);
  assert.match(element('#audioState').textContent,/Configurar permiso permanente/);
  element('#prepareMic').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(opened,1);

  // Returning to the popup after a one-time grant must work without asking again.
  granted=true;
  initialize();
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  element('#consent').checked=true;
  element('#record').onclick();
  assert.equal(sent.filter(m=>m.type==='START_AUDIO').length,1);
  assert.equal(opened,1);
});
