const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const source=fs.readFileSync(path.join(__dirname,'../extension/options.js'),'utf8');

async function setup(micInitiallyAllowed=false,fail=false){
  const elements=new Map();
  const el=id=>{
    if(!elements.has(id))elements.set(id,{
      textContent:'',className:'',disabled:false,onclick:null,
      addEventListener(name,fn){this[name]=fn}
    });
    return elements.get(id);
  };
  let state=micInitiallyAllowed?'granted':'prompt',starts=0,stops=0;
  const chrome={runtime:{id:'abcdefghijklmnopabcdefghijklmnop',getURL:()=>'chrome-extension://abcdefghijklmnopabcdefghijklmnop/'},
    tabs:{create:async()=>({})}};
  const navigator={
    permissions:{query:async ({name})=>{
      assert.equal(name,'microphone');
      return {state,onchange:null};
    }},
    mediaDevices:{getUserMedia:async({audio})=>{
      starts++;
      assert.ok(audio);
      if(fail)throw Object.assign(new Error('Permission dismissed'),{name:'NotAllowedError'});
      state='granted';
      return {getTracks:()=>[{stop(){stops++}}]};
    }}
  };
  vm.runInNewContext(source,{document:{getElementById:el},navigator,chrome,console});
  for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));
  return {el,navigator,stats:()=>({state,starts,stops})};
}

test('full-page permission setup requests mic once and stops test stream',async()=>{
  const app=await setup();
  assert.match(app.el('state').textContent,/Pendiente/);
  await app.el('authorize').click();
  assert.deepEqual(app.stats(),{state:'granted',starts:1,stops:1});
  assert.match(app.el('state').textContent,/autorizado/);
});

test('already granted mic only queries permission without requesting media',async()=>{
  const app=await setup(true);
  assert.deepEqual(app.stats(),{state:'granted',starts:0,stops:0});
  assert.match(app.el('state').textContent,/autorizado/);
});

test('dismissed mic prompt gives clear guidance, no recording',async()=>{
  const app=await setup(false,true);
  await app.el('authorize').click();
  assert.deepEqual(app.stats(),{state:'prompt',starts:1,stops:0});
  assert.match(app.el('state').textContent,/No se concedió/);
  assert.match(app.el('detail').textContent,/Permission dismissed/);
});
