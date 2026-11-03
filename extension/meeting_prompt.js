// Optional, consent-first prompt when Teams Web enters an active call.
// It never starts recording and never sends audio or meeting details.
(() => {
  if (globalThis.__teamsCcMeetingPromptReady) return;
  globalThis.__teamsCcMeetingPromptReady = true;

  const PROMPT_ID = 'homelab-teamscc-recording-prompt';
  const TEAM_CALL_END = /(?:hang[\s_-]?up|end[\s_-]?call|leave[\s_-]?(?:meeting|call|button)|call[\s_-]?leave)/i;
  const END_LABEL = /(?:^|\b)(?:hang\s?up|leave(?:\s+(?:the\s+)?(?:meeting|call))?|end\s+(?:the\s+)?call|salir(?:\s+de\s+la\s+reuni[oó]n)?|abandonar(?:\s+reuni[oó]n)?|colgar|finalizar\s+(?:la\s+)?llamada|terminar\s+(?:la\s+)?llamada)(?:\b|$)/i;
  const NOT_CALL = /(?:log\s?out|sign\s?out|cerrar\s+sesi[oó]n|leave\s+(?:chat|group|team|channel))/i;
  const STABLE_MS = 1100;
  const RESET_MS = 15000;

  let noticeEnabled = true;
  let audioBusy = false;
  let activeSince = null;
  let absentSince = null;
  let prompted = false;
  let promptNode = null;
  let scanTimer = null;

  function visible(node) {
    if (node.hasAttribute?.('hidden') || node.getAttribute?.('aria-hidden') === 'true') return false;
    if (node.closest?.('[aria-hidden="true"],[hidden]')) return false;
    return typeof node.getClientRects !== 'function' || node.getClientRects().length > 0;
  }

  function inCall() {
    // A visible Leave/Hang up button exists only after joining the call;
    // calendar entries, chat pages and prejoin screens must not trigger it.
    for (const button of document.querySelectorAll('button,[role="button"]')) {
      if (!visible(button)) continue;
      const tid = String(button.getAttribute?.('data-tid') || '');
      const label = String(button.getAttribute?.('aria-label') || button.getAttribute?.('title') || '');
      if (NOT_CALL.test(label)) continue;
      if (TEAM_CALL_END.test(tid) || END_LABEL.test(label)) return true;
    }
    return false;
  }

  function removePrompt() {
    if (promptNode) promptNode.remove();
    promptNode = null;
  }

  function renderPrompt() {
    if (promptNode || !document.body) return;
    const wrapper = document.createElement('div');
    wrapper.id = PROMPT_ID;
    wrapper.style.cssText = 'position:fixed;right:18px;bottom:54px;z-index:2147483647;max-width:calc(100vw - 36px)';
    const root = wrapper.attachShadow({mode:'open'});
    root.innerHTML = `
      <style>
        *,*::before,*::after{box-sizing:border-box}
        .card{width:310px;max-width:100%;border:1px solid #6176a2;border-radius:13px;
          color:#f3f6ff;background:#17243c;box-shadow:0 10px 30px #0007;
          padding:15px;font:13px/1.45 system-ui,-apple-system,sans-serif}
        .title{font-weight:700;font-size:14px;margin:0 0 6px}
        p{margin:0 0 11px;color:#d7e1f5}
        .controls{display:flex;gap:8px;flex-wrap:wrap}
        button{border:0;border-radius:7px;padding:9px 12px;cursor:pointer;
          font:600 12px system-ui,-apple-system,sans-serif;background:#5977e7;color:#fff}
        button.secondary{background:#364968}
        button:focus-visible{outline:2px solid #ffdf80;outline-offset:2px}
        button:disabled{opacity:.6;cursor:default}
        .fine{font-size:11px;color:#b9cae5;margin-top:9px}
      </style>
      <section class="card" role="dialog" aria-label="Teams CC: grabación opcional">
        <div class="title">Teams CC · Reunión detectada</div>
        <p id="notice">¿Quieres preparar la grabación de audio en tu Homelab?</p>
        <div class="controls">
          <button type="button" id="prepare">Preparar grabación</button>
          <button type="button" class="secondary" id="dismiss">Ahora no</button>
        </div>
        <div class="fine">No se graba nada sin tu autorización.</div>
      </section>`;
    const prepare = root.getElementById('prepare');
    const dismiss = root.getElementById('dismiss');
    const notice = root.getElementById('notice');

    dismiss.addEventListener('click', () => {
      prompted = true;
      removePrompt();
    });
    prepare.addEventListener('click', () => {
      prompted = true;
      prepare.disabled = true;
      notice.textContent = 'Abriendo Teams CC para confirmar la grabación...';
      // This click is a user gesture; Chrome 127+ may open the action popup.
      // Browser policy or gesture propagation can still block it, so offer
      // the toolbar icon fallback instead of recording automatically.
      chrome.runtime.sendMessage({type:'OPEN_RECORDING_POPUP'}).then(result => {
        if (result?.ok) {
          removePrompt();
          return;
        }
        notice.textContent = 'Abre Teams CC desde el icono de extensiones de Chrome y confirma la grabación.';
        prepare.remove();
      }).catch(() => {
        notice.textContent = 'Abre Teams CC desde el icono de extensiones de Chrome y confirma la grabación.';
        prepare.remove();
      });
    });
    document.body.appendChild(wrapper);
    promptNode = wrapper;
  }

  function scan() {
    if (!document.body) return;
    const now = Date.now();
    if (inCall()) {
      absentSince = null;
      if (activeSince === null) activeSince = now;
      if (now - activeSince >= STABLE_MS && !prompted && noticeEnabled && !audioBusy) {
        prompted = true; // A single prompt per stable call, even with DOM rerenders.
        renderPrompt();
      }
    } else {
      activeSince = null;
      if (absentSince === null) absentSince = now;
      if (now - absentSince >= RESET_MS) {
        prompted = false;
        removePrompt();
      }
    }
    if (!noticeEnabled || audioBusy) removePrompt();
  }

  function busy(state) {
    return ['recording','uploading'].includes(state?.status) || (state?.pending || 0) > 0;
  }

  chrome.storage.local.get({
    meetingPrompts:true,
    audioStatus:{status:'stopped',pending:0}
  }, prefs => {
    noticeEnabled = prefs.meetingPrompts !== false;
    audioBusy = busy(prefs.audioStatus);
    scan();
  });
  chrome.storage.onChanged.addListener((changes,area) => {
    if (area !== 'local') return;
    if (changes.meetingPrompts) noticeEnabled = changes.meetingPrompts.newValue !== false;
    if (changes.audioStatus) audioBusy = busy(changes.audioStatus.newValue);
    scan();
  });

  const observer = new MutationObserver(() => {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(scan,450);
  });
  observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,
    attributeFilter:['aria-label','title','data-tid','hidden','aria-hidden']});
  setInterval(scan,1500);
  window.addEventListener('pagehide',removePrompt);
  scan();
})();
