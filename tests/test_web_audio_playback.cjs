// Regression coverage for browser-native manual playback after authenticated fetch.
// The browser must NOT call audio.play() after async fetch (Chrome autoplay gesture).
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const script=fs.readFileSync(path.join(__dirname,'../app/app.js'),'utf8');
function harness(supported=true){
 const els=new Map(), fetched=[], urls=[], revoked=[];
 function element(id){
   if(!els.has(id)){
     els.set(id,{
       textContent:'',value:'',hidden:false,disabled:false,style:{},
       children:[],volume:0,muted:true,
       append(child){this.children.push(child);child.parent=this;},
       addEventListener(){},
       removeAttribute(key){if(key==='src')this.src='';},
       pause(){this.paused=true;},
       load(){this.loaded=true;},
       scrollIntoView(){this.scrolled=true;},
       canPlayType(){return supported?'probably':'';},
       play(){throw Error('Unexpected autoplay attempt after fetch')}
     });
   }
   return els.get(id);
 }
 const document={querySelector:element};
 const url={createObjectURL:blob=>{assert.equal(blob.size,143840);urls.push('blob:test:'+urls.length);return urls.at(-1)},revokeObjectURL:value=>revoked.push(value)};
 const fetch=async (link,args)=>{
   fetched.push({link,args});
   return {ok:true,blob:async()=>({size:143840,type:'audio/webm'})};
 };
 const state=vm.createContext({
   document,URL:url,fetch,sessionStorage:{getItem:()=>'',setItem(){}},
   Intl,Date,console,setTimeout,clearTimeout
 });
 vm.runInContext(script,state,{filename:'app.js'});
 vm.runInContext("current={id:'m_test_audio'}",state);
 return {element,state,fetched,urls,revoked};
}
test('attaches native media controls to selected row without autoplay',async()=>{
 const h=harness();
 const row=h.element('row'),button=h.element('button');
 await vm.runInContext('playAudio("m_test_audio",0,globalRow,globalButton)',Object.assign(h.state,{globalRow:row,globalButton:button}));
 const p=h.element('#audioPlayer');
 assert.equal(h.fetched[0].link,'/api/meetings/m_test_audio/audio/0');
 assert.equal(p.src,'blob:test:0');
 assert.equal(p.controls,true);
 assert.equal(p.autoplay,false);
 assert.equal(p.muted,false);
 assert.equal(p.volume,1);
 assert.equal(p.hidden,false);
 assert.equal(p.loaded,true);
 assert.ok(row.children.includes(p));
 assert.match(h.element('#audioPlaybackStatus').textContent,/pulsa/);
 assert.equal(button.disabled,false);
});
test('returns helpful message instead of attempting autoplay on unsupported codecs',async()=>{
 const h=harness(false);
 const row=h.element('row'),button=h.element('button');
 await vm.runInContext('playAudio("m_test_audio",0,globalRow,globalButton)',Object.assign(h.state,{globalRow:row,globalButton:button}));
 assert.equal(h.fetched.length,0);
 assert.match(h.element('#audioPlaybackStatus').textContent,/no admite WebM\/Opus/);
});
test('releases previous blob before loading a second file',async()=>{
 const h=harness();
 const row=h.element('row'),button=h.element('button');
 Object.assign(h.state,{globalRow:row,globalButton:button});
 await vm.runInContext('playAudio("m_test_audio",0,globalRow,globalButton)',h.state);
 await vm.runInContext('playAudio("m_test_audio",1,globalRow,globalButton)',h.state);
 assert.deepEqual(h.revoked,['blob:test:0']);
 assert.equal(h.element('#audioPlayer').src,'blob:test:1');
});
