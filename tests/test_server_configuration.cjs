// Security regression: an installed Teams CC can use an arbitrary private
// server address without publishing a hostname, IP or port in source control.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const ROOT=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(ROOT,'extension/popup.js'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(ROOT,'extension/manifest.json'),'utf8'));
function setup({grant=true}={}){
  const els=new Map(),requests=[],writes=[];
  const el=id=>{
    if(!els.has(id))els.set(id,{
      checked:false,value:'',disabled:false,textContent:'',className:'',onclick:null,
      addEventListener(){}
    });
    return els.get(id);
  };
  const chrome={
    storage:{local:{
      get:async defaults=>Object.assign({},defaults,{queue:[],audioStatus:{status:'stopped'},token:''}),
      set:async data=>writes.push(data)
    },onChanged:{addListener(){}}},
    permissions:{
      request: options=>{requests.push(options);return Promise.resolve(grant)}
    },
    runtime:{sendMessage:async()=>({ok:true}),openOptionsPage:async()=>{}},
    tabs:{query:async()=>[]}
  };
  const navigator={permissions:{query:async()=>({state:'prompt'})}};
  vm.runInNewContext(source,{chrome,document:{querySelector:el},navigator,console,Date,URL,setTimeout,clearTimeout});
  return {el,requests,writes};
}
test('manifest has no hardcoded server and grants site access only on demand',()=>{
  assert.ok(!manifest.host_permissions.some(v=>v.includes('duckdns')));
  assert.deepEqual(manifest.optional_host_permissions,['http://*/*','https://*/*']);
  assert.ok(manifest.permissions.includes('permissions'));
});
test('save requests only entered scheme and host, then stores exact port locally',async()=>{
  const h=setup();
  h.el('#server').value='https://voice.example.test:9443/';
  h.el('#token').value='test-secret-token';
  h.el('#save').onclick();
  assert.equal(h.requests.length,1,'permission must be requested synchronously on click');
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0])),{origins:['https://voice.example.test/*']});
  for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));
  const stored=h.writes.find(x=>x.server);
  assert.equal(stored.server,'https://voice.example.test:9443');
});
test('rejects server URLs containing passwords and extra paths',async()=>{
  const h=setup();
  for(const address of ['http://u:p@voice.example.test:9443',
                        'http://voice.example.test:9443/extra',
                        'javascript:alert(1)']){
    h.el('#server').value=address;
    h.el('#save').onclick();
  }
  assert.equal(h.requests.length,0);
});
test('permission denial does not save a token or endpoint',async()=>{
  const h=setup({grant:false});
  h.el('#server').value='http://127.0.0.1:9000';
  h.el('#token').value='test-token';
  h.el('#save').onclick();
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.writes.some(x=>x.server),false);
});
