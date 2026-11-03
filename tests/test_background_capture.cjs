// User gesture must be preserved into tabCapture within the message listener.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('START_AUDIO invokes tabCapture synchronously, before storage or tab lookups',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../extension/background.js'),'utf8');
  let receiver;
  const events=[];
  const chrome={
    runtime:{
      onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}},
      onMessage:{addListener:fn=>{receiver=fn}}
    },
    alarms:{create:()=>{},onAlarm:{addListener:()=>{}}},
    tabCapture:{
      getMediaStreamId:options=>{
        events.push('capture');
        assert.equal(options.targetTabId,42);
        return Promise.resolve('synthetic-stream-id');
      }
    },
    storage:{local:{get:async()=>{events.push('storage');return {token:'x',server:'http://127.0.0.1:9000'}}}}
  };
  vm.runInNewContext(source,{chrome,console,AbortController,setTimeout,clearTimeout,Date,URL,Promise,Map,Set});
  const finished=new Promise(resolve=>{
    const answer=receiver(
      {type:'START_AUDIO',tabId:42,context:{meeting_id:'m_valid',started_at:'2026-11-02T12:00:00Z'}},
      {},resolve);
    assert.equal(answer,true);
    assert.equal(events[0],'capture');
  });
  const response=await finished;
  assert.equal(response.ok,false);
});

test('duplicate capture is diagnosed as an active Teams CC recording',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../extension/background.js'),'utf8');
  let receiver;
  let captures=0;
  const chrome={
    runtime:{
      id:'test-extension',
      getURL:path=>'chrome-extension://test-extension/'+path,
      getContexts:async()=>[{documentUrl:'chrome-extension://test-extension/offscreen.html'}],
      sendMessage:async m=>m.type==='CAPTURE_STATUS'?
        {ok:true,active:true,tabId:42,sequence:2,pending:0,state:'recording'} : {ok:false},
      onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}},
      onMessage:{addListener:fn=>receiver=fn},
    },
    alarms:{create:()=>{},onAlarm:{addListener:()=>{}}},
    tabCapture:{
      getMediaStreamId:()=>{captures++;return Promise.reject(new Error('Cannot capture a tab with an active stream.'))},
      getCapturedTabs:async()=>[{tabId:42,status:'active'}]
    },
    tabs:{get:async()=>({id:42,url:'https://teams.microsoft.com/v2/'})},
    storage:{local:{get:async()=>({token:'configured',server:'http://127.0.0.1:9000'})}}
  };
  vm.runInNewContext(source,{chrome,console,AbortController,setTimeout,clearTimeout,Date,URL,Promise,Map,Set});
  const response=await new Promise(resolve=>{
    const ongoing=receiver({
      type:'START_AUDIO',tabId:42,
      context:{meeting_id:'m_valid',started_at:'2026-11-02T12:00:00Z'}}, {}, resolve);
    assert.equal(ongoing,true);
  });
  assert.equal(captures,1);
  assert.equal(response.ok,false);
  assert.match(response.error,/ya está grabándose/);
  assert.match(response.error,/Detener/);
});

test('GET_AUDIO_STATUS reports offscreen state without starting another capture',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../extension/background.js'),'utf8');
  let receiver,captures=0;
  const chrome={
    runtime:{
      getURL:p=>'chrome-extension://test/'+p,
      getContexts:async()=>[{documentUrl:'chrome-extension://test/offscreen.html'}],
      sendMessage:async()=>({ok:true,active:true,tabId:11,sequence:3,state:'recording'}),
      onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}},
      onMessage:{addListener:f=>receiver=f},
    },
    alarms:{create:()=>{},onAlarm:{addListener:()=>{}}},
    tabCapture:{
      getMediaStreamId:()=>{captures++;return Promise.resolve('stream')},
      getCapturedTabs:async()=>[{tabId:11,status:'active'}]
    }
  };
  vm.runInNewContext(source,{chrome,console,AbortController,setTimeout,clearTimeout,Date,URL,Promise,Map,Set});
  const result=await new Promise(resolve=>receiver({type:'GET_AUDIO_STATUS',tabId:11},{},resolve));
  assert.equal(result.active,true);
  assert.equal(result.occupied,true);
  assert.equal(result.tabId,11);
  assert.equal(captures,0);
});
