// Content script may also be injected on demand into Teams tabs opened before installation.
(() => {
  if (globalThis.__teamsCcContentScriptReady) return;
  globalThis.__teamsCcContentScriptReady = true;
// Microsoft Teams live-caption observer. User must manually enable capture.
const teamsCaptionTextSelector = '[data-tid="closed-caption-text"]';
let enabled = false;
chrome.storage.local.get({enabled:false}, prefs => { enabled = prefs.enabled; });
chrome.storage.onChanged.addListener(changes => {
  if (changes.enabled) enabled = changes.enabled.newValue;
});
function visibleCaptions() {
  if (!enabled) return [];
  return [...document.querySelectorAll(teamsCaptionTextSelector)]
    .map(el => el.textContent.trim()).filter(Boolean);
}

const known = new WeakMap();
const pending = new Map();
let meetingId='', startedAt='', lastSeen=0, timer=null;
const selectors = [
  "[data-tid='closed-caption-renderer-wrapper']",
  "[data-tid='closed-caption-v2-window-wrapper']",
  "[data-tid='closed-captions-renderer']"
];
function findCaptionPanel() {
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (element) return element;
  }
  const text = document.querySelector(teamsCaptionTextSelector);
  for (let node = text?.parentElement; node; node = node.parentElement) {
    if (node.matches('[data-tid*="closed-caption"]')) return node;
  }
  return null;
}
function ensureMeetingId() {
  if (meetingId) return;
  try {
    const saved = JSON.parse(sessionStorage.getItem('homelab_teams_cc') || '{}');
    if (saved.id && saved.start) {
      meetingId = saved.id;
      startedAt = saved.start;
    }
  } catch {}
  if (!meetingId) {
    meetingId = 'm_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,9);
    startedAt = new Date().toISOString();
    sessionStorage.setItem('homelab_teams_cc',JSON.stringify({id:meetingId,start:startedAt}));
  }
}
function emitPending() {
  if (!pending.size || !meetingId || !enabled) return;
  const captions = [...pending.values()];
  pending.clear();
  chrome.runtime.sendMessage({
    type:'CAPTIONS_BATCH', meeting_id:meetingId, title:document.title || 'Teams',
    started_at:startedAt, captions
  }).catch(() => captions.forEach(c => pending.set(c.id,c)));
}

function renderIndicator(active) {
  let badge = document.getElementById('homelab-cc-indicator');
  if (!active) { if (badge) badge.remove(); return; }
  if (!badge) {
    badge = document.createElement('div');
    badge.id = 'homelab-cc-indicator';
    badge.setAttribute('role','status');
    badge.textContent = '● CC → Homelab';
    badge.style.cssText = 'position:fixed;z-index:2147483646;bottom:10px;right:12px;'+
      'color:#fff;background:#273b6c;border-radius:16px;padding:5px 12px;'+
      'box-shadow:0 2px 8px #0005;font:12px system-ui;';
    document.body.appendChild(badge);
  }
}
function scanCaptions() {
  if (!enabled) { renderIndicator(false); return; }
  const container = findCaptionPanel();
  renderIndicator(Boolean(container));
  if (!container) {
    if (lastSeen && Date.now() - lastSeen > 60000) {
      meetingId='';startedAt='';lastSeen=0;
      sessionStorage.removeItem('homelab_teams_cc');
    }
    return;
  }
  let entries = container.querySelectorAll('.fui-ChatMessageCompact');
  if (!entries.length) entries=container.querySelectorAll(teamsCaptionTextSelector);
  for (const entry of entries) {
    const root = entry.matches(teamsCaptionTextSelector)
      ? entry.closest('.fui-ChatMessageCompact') || entry.parentElement?.parentElement : entry;
    const textNode = root?.querySelector(teamsCaptionTextSelector)
      || (entry.matches(teamsCaptionTextSelector) ? entry : null);
    const text = textNode?.textContent?.trim().replace(/\s+/g,' ');
    if (!text || !root) continue;
    const speaker = root.querySelector('[data-tid="author"]')?.textContent?.trim() || 'Desconocido';
    ensureMeetingId();
    let record = known.get(root);
    const recycled = record && (speaker !== record.speaker ||
      (!text.startsWith(record.text) && record.text.length>18 && text.length < record.text.length/2));
    if (!record || recycled) {
      record = {id:'c_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,10),
        speaker, text:'', at:new Date().toISOString()};
      known.set(root,record);
    }
    if (record.text !== text) {
      record.text = text;
      pending.set(record.id,{id:record.id,speaker,text,at:record.at});
      lastSeen=Date.now();
    }
  }
  if (pending.size) {
    clearTimeout(timer);
    timer=setTimeout(emitPending,1400);
  }
}

const captionObserver = new MutationObserver(() => {
  clearTimeout(timer);
  timer = setTimeout(scanCaptions,600);
});
captionObserver.observe(document.documentElement,{
  childList:true,characterData:true,subtree:true
});
setInterval(() => {scanCaptions();if(pending.size)emitPending();},4000);
window.addEventListener('pagehide',emitPending);
setTimeout(scanCaptions,1000);

 // Audio session explicitly initiated from the popup: reuse CC meeting ID.
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(m?.type==='GET_MEETING_CONTEXT'){
    // Without visible CC, a newly authorized audio capture is a fresh session.
    if(!findCaptionPanel()){
      meetingId='';startedAt='';lastSeen=0;
      sessionStorage.removeItem('homelab_teams_cc');
    }
    ensureMeetingId();
    reply({meeting_id:meetingId,started_at:startedAt,title:document.title||'Teams'});
  }
});

})();
