// ==UserScript==
// @name         djclaude → claude.ai effort fader
// @match        https://claude.ai/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==
// Polls the rig daemon; when the fable fader detent changes, drives claude.ai's
// effort picker by simulated clicks. Labels: low/medium/high/xhigh/max -> menu text.
(function () {
  const MAP = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra', max: 'Max' };
  let current = null, busy = false;
  const $ = (sel, root=document) => [...root.querySelectorAll(sel)];
  const byText = (txt) => $('div,span,button,[role=menuitem]').find(e =>
    e.childElementCount === 0 && e.textContent.trim() === txt && e.offsetParent !== null);
  async function setEffort(label) {
    busy = true;
    try {
      const modelBtn = $('button').find(b => /Fable|Sonnet|Opus/.test(b.textContent) && b.offsetParent);
      if (!modelBtn) return;
      modelBtn.click(); await new Promise(r => setTimeout(r, 400));
      const eff = byText('Effort'); if (!eff) { document.body.click(); return; }
      eff.click(); await new Promise(r => setTimeout(r, 400));
      const item = byText(MAP[label]); if (item) item.click();
      await new Promise(r => setTimeout(r, 200)); document.body.click();
    } finally { busy = false; }
  }
  setInterval(() => {
    if (busy) return;
    GM_xmlhttpRequest({ url: 'http://127.0.0.1:7683/', method: 'GET', onload: r => {
      try {
        const eff = JSON.parse(r.responseText).effort;
        if (eff && eff !== current) { const first = current === null; current = eff; if (!first) setEffort(eff); }
      } catch {}
    }});
  }, 1000);
})();
