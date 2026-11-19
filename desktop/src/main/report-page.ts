import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { LIMITS, type ReportPayload } from './problem-report';
import { DISCORD_CHANNEL, SUPPORT_EMAIL } from './report-config';

const assets = JSON.parse(readFileSync(join(__dirname, '..', '..', 'resources', 'onboarding.generated.json'), 'utf8')) as { css: string; logo: string };

// The report dialog is a native page the main process owns, so it opens on every screen — the
// welcome page and the recovery pages included — and with no book running. It holds no network
// access: Send, Copy and Email go through the preload bridge to the main process.
// Over the main window it is the app's own modal: a scrim that closes it when clicked, the modal
// card, a close button in its head. Opened on its own it fills its window instead. Either way the
// form scrolls inside the card above a footer that stays put, under the app's thin themed scrollbar.
const STYLE = `*{box-sizing:border-box}[hidden]{display:none!important}html,body{height:100%}
body{background:var(--bg);color:var(--ink);font:400 var(--text-md)/1.5 'Plus Jakarta Sans',-apple-system,system-ui,sans-serif;-webkit-font-smoothing:antialiased;margin:0;overflow:hidden}
html.overlay,html.overlay body{background:transparent}
.scrim{position:fixed;inset:0;display:flex;align-items:center;justify-content:center}html.overlay .scrim{background:rgba(6,9,16,.66);padding:24px}
.card{display:flex;flex-direction:column;overflow:hidden;width:100%;height:100%;background:var(--bg)}
html.overlay .card{width:min(640px,100%);height:auto;max-height:100%;background:var(--modal-bg);border:1px solid var(--modal-border);border-radius:var(--radius-xl);box-shadow:var(--shadow-modal)}
.scroll{flex:1;min-height:0;overflow:auto;scrollbar-width:thin;scrollbar-color:color-mix(in srgb,var(--line) 90%,transparent) transparent}
.scroll::-webkit-scrollbar,pre::-webkit-scrollbar,textarea::-webkit-scrollbar{width:10px;height:10px}.scroll::-webkit-scrollbar-track,pre::-webkit-scrollbar-track,textarea::-webkit-scrollbar-track{background:transparent}
.scroll::-webkit-scrollbar-thumb,pre::-webkit-scrollbar-thumb,textarea::-webkit-scrollbar-thumb{background:color-mix(in srgb,var(--line) 85%,transparent);border-radius:999px;border:2px solid transparent;background-clip:padding-box}
.scroll::-webkit-scrollbar-thumb:hover,pre::-webkit-scrollbar-thumb:hover,textarea::-webkit-scrollbar-thumb:hover{background:var(--muted)}
main{max-width:640px;margin:0 auto;padding:var(--space-8) var(--space-8) var(--space-6)}
.head{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-5)}
h1{font-size:var(--text-lg);font-weight:600;line-height:1.3;margin:0}
.x{all:unset;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:var(--faint);padding:var(--space-1);border-radius:4px;-webkit-app-region:no-drag}.x:hover{color:var(--ink)}.x svg{width:18px;height:18px}
label,.label{display:block;font-weight:600;margin:var(--space-6) 0 var(--space-3)}.req{color:var(--faint);font-weight:400}
textarea,input[type=email]{width:100%;background:transparent;color:var(--ink);border:1px solid var(--line);border-radius:var(--radius-sm);padding:10px 12px;font:400 var(--text-md)/1.5 'Plus Jakarta Sans',sans-serif}
textarea{resize:vertical;min-height:84px}textarea.mono,pre{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}textarea:disabled{opacity:.5}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius-sm)}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
.check{display:flex;gap:var(--space-3);align-items:flex-start;font-weight:400;cursor:pointer;margin:var(--space-6) 0 var(--space-3)}.check.off{cursor:default}
.check input{appearance:none;-webkit-appearance:none;flex:none;width:16px;height:16px;margin:2px 0 0;border-radius:4px;border:1.5px solid var(--line-strong);background:transparent no-repeat center;cursor:inherit}
.check input:checked{background-color:var(--fr-gold);border-color:var(--fr-gold);background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='3.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='20 6 9 17 4 12'/%3E%3C/svg%3E")}
.check input:disabled{opacity:.5}.check-text{display:flex;flex-direction:column;gap:2px}.check-label{line-height:1.3}.check.off .check-label{opacity:.6}.check-desc{font-size:var(--text-sm);color:var(--faint);line-height:1.35}
.hint{margin:6px 0 0;font-size:13px;color:var(--muted)}.discord-hint{margin-top:var(--space-6)}.mark{width:15px;height:15px;margin-right:7px;flex:none}.link{padding:0;border:0;background:none;font:inherit;font-size:13px;color:var(--ink);text-decoration:underline;cursor:pointer;height:auto}
details{margin-top:var(--space-6)}details.included>dl{margin-top:var(--space-4)}summary{cursor:pointer;color:var(--muted)}pre{margin:var(--space-3) 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius-sm);white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto}
footer{flex:none;border-top:1px solid var(--line);padding:var(--space-5) var(--space-8)}.bar{max-width:640px;margin:0 auto;display:flex;gap:var(--space-4);justify-content:flex-end;flex-wrap:wrap}
.status{max-width:640px;margin:0 auto var(--space-4);font-size:13px}.status:empty{display:none}.status.bad{color:var(--bad)}.status .addr{user-select:text;-webkit-user-select:text;font-weight:600;color:var(--ink)}
button{box-sizing:border-box;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--ink);height:36px;padding:0 20px;font:600 13px/1 'Plus Jakarta Sans',sans-serif;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
button.primary{background:var(--fr-gold);border-color:var(--fr-gold);color:var(--bg)}button:disabled{opacity:.5;cursor:default}
input:focus-visible,textarea:focus-visible,button:focus-visible,summary:focus-visible,.x:focus-visible{outline:2px solid var(--fr-gold);outline-offset:2px}
.done p{color:var(--muted)}.done .actions{display:flex;justify-content:flex-end;margin-top:var(--space-6)}`;

const CLOSE = `<button id="dismiss" class="x" type="button" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg></button>`;

// A draft is data, never markup: it is embedded as JSON with every `<` escaped.
const json = (value: unknown): string => JSON.stringify(value).replace(/</g, '\\u003c');

const SCRIPT = `
const $=id=>document.getElementById(id);const draft=JSON.parse($('draft').textContent);const bridge=window.onwReport;
const fields={description:$('description'),email:$('email'),error:$('error'),logs:$('logs'),include:$('include')};
let busy=false;
fields.description.value=draft.description;if(fields.error)fields.error.value=draft.error||'';if(fields.logs)fields.logs.value=draft.logs||'';
fields.include.checked=draft.logs!==null;fields.include.disabled=draft.logs===null;
const payload=()=>({description:fields.description.value.trim(),contact_email:fields.email.value.trim()||null,app_version:draft.app_version,platform:draft.platform,mode:draft.mode,screen:draft.screen,error:fields.error?fields.error.value.trim()||null:null,logs:fields.logs&&fields.include.checked&&fields.logs.value.trim()?fields.logs.value.trim():null});
const status=(text,bad)=>{const node=$('status');node.className='status'+(bad?' bad':'');node.replaceChildren(text);return node};
const refresh=()=>{$('included-summary').textContent='Also sent: app details'+(fields.logs&&fields.include.checked&&fields.logs.value.trim()?' and recent logs (masked)':'');if(fields.logs)fields.logs.disabled=!fields.include.checked||busy;$('json').textContent=JSON.stringify(payload(),null,2);const ready=Boolean(fields.description.value.trim())&&!busy;for(const id of ['send','copy','email','discord'])$(id).disabled=!ready;$('cancel').disabled=busy;$('dismiss').disabled=busy};
const clean=failure=>String(failure&&failure.message||failure).replace(/^Error invoking remote method '[^']+': (Error: )?/,'');
const guarded=run=>async()=>{busy=true;refresh();status('');try{await run()}catch(failure){fallback(clean(failure))}finally{busy=false;refresh()}};
const fallback=message=>{const node=status(message+' You can copy the report or email it to ',true);const address=document.createElement('span');address.className='addr';address.textContent=${JSON.stringify(SUPPORT_EMAIL)};node.append(address,'.');$('copy').hidden=false;$('email').hidden=false};
for(const field of Object.values(fields))if(field)field.addEventListener('input',refresh);fields.include.addEventListener('change',refresh);
$('send').addEventListener('click',guarded(async()=>{const sent=await bridge.send(payload());$('form').hidden=true;$('foot').hidden=true;$('done').hidden=false;if(sent.id)$('reference').textContent='Reference '+sent.id;$('close').focus()}));
$('discord').addEventListener('click',guarded(async()=>{await bridge.community(payload());$('discord-hint').textContent='Copied — press '+(draft.platform.startsWith('darwin')?'⌘V':'Ctrl+V')+' in '+${JSON.stringify(DISCORD_CHANNEL)}+' to paste it.'}));$('copy').addEventListener('click',guarded(async()=>{await bridge.copy(payload());status('Copied. Paste it wherever you like.')}));
$('email').addEventListener('click',guarded(async()=>{await bridge.email(payload());status('Your mail app opened with the start of the report. The whole report, logs included, is on your clipboard to paste.')}));
const close=()=>{if(!busy)bridge.close()};
for(const id of ['cancel','close','dismiss'])$(id).addEventListener('click',close);
$('scrim').addEventListener('click',event=>{if(event.target===event.currentTarget&&document.documentElement.classList.contains('overlay'))close()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close()}});
refresh();fields.description.focus();`;

// `overlay`: drawn over the main window (a click on the scrim closes it); otherwise it fills a window of its own.
export function reportPage(draft: ReportPayload, theme: 'dark' | 'light', overlay = false): string {
  const logs = draft.logs !== null;
  return `<!doctype html><html lang="en" data-theme="${theme}"${overlay ? ' class="overlay"' : ''}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; script-src 'nonce-onw-report';"><title>Report a problem — Open Net Worth</title><style>${assets.css}
${STYLE}</style></head><body><div id="scrim" class="scrim"><div class="card" role="dialog" aria-modal="true" aria-labelledby="title">
<div class="scroll"><main><div id="form"><div class="head"><h1 id="title">Report a problem</h1>${CLOSE}</div>
<label for="description">What happened?</label><textarea id="description" rows="5" maxlength="${LIMITS.description}"></textarea>
<label for="email">Email</label><input id="email" type="email" maxlength="${LIMITS.email}" autocomplete="off" aria-describedby="email-hint"><p id="email-hint" class="hint">We need it only to tell you when the fix is out — never for marketing, and it’s never shared.</p>
${draft.error ? `<label for="error">Error</label><textarea id="error" rows="3" maxlength="${LIMITS.error}" class="mono"></textarea>` : ''}
<details class="included"><summary id="included-summary"></summary><dl><dt>Version</dt><dd>${escapeHtml(draft.app_version)}</dd><dt>Platform</dt><dd>${escapeHtml(draft.platform)}</dd><dt>Mode</dt><dd>${draft.mode}</dd>${draft.screen ? `<dt>Screen</dt><dd>${escapeHtml(draft.screen)}</dd>` : ''}</dl>
<label class="check${logs ? '' : ' off'}"><input id="include" type="checkbox"><span class="check-text"><span class="check-label">Include recent logs</span><span class="check-desc">${logs ? 'Amounts, names and keys are masked.' : 'No logs yet.'}</span></span></label>
${logs ? `<textarea id="logs" class="mono" rows="9" aria-label="Recent logs" maxlength="${LIMITS.logs}" spellcheck="false"></textarea>` : ''}
<details><summary>Show exactly what is sent</summary><pre id="json"></pre></details></details><p id="discord-hint" class="hint discord-hint" role="status" aria-live="polite">Post on Discord copies your message for you to paste in ${DISCORD_CHANNEL}.</p></div>
<div id="done" class="done" hidden><h1>Thank you. Your report was sent.</h1><p>We will investigate and fix this within 48 hours!</p><p id="reference"></p><div class="actions"><button id="close" class="primary" type="button">Close</button></div></div></main></div>
<footer id="foot"><div id="status" class="status" role="status" aria-live="polite"></div><div class="bar"><button id="cancel" type="button">Cancel</button><button id="copy" type="button" hidden>Copy report</button><button id="email" type="button" hidden>Email instead</button><button id="send" type="button">Send report</button><button id="discord" class="primary" type="button"><svg class="mark" viewBox="0 0 20 19" fill="currentColor" aria-hidden="true"><path d="M16.224 3.768a14.5 14.5 0 0 0-3.67-1.153c-.158.286-.343.67-.47.976a13.5 13.5 0 0 0-4.067 0c-.128-.306-.317-.69-.476-.976A14.4 14.4 0 0 0 3.868 3.77C1.546 7.28.916 10.703 1.231 14.077a14.7 14.7 0 0 0 4.5 2.306q.545-.748.965-1.587a9.5 9.5 0 0 1-1.518-.74q.191-.14.372-.293c2.927 1.369 6.107 1.369 8.999 0q.183.152.372.294-.723.437-1.52.74.418.838.963 1.588a14.6 14.6 0 0 0 4.504-2.308c.37-3.911-.63-7.302-2.644-10.309m-9.13 8.234c-.878 0-1.599-.82-1.599-1.82 0-.998.705-1.82 1.6-1.82.894 0 1.614.82 1.599 1.82.001 1-.705 1.82-1.6 1.82m5.91 0c-.878 0-1.599-.82-1.599-1.82 0-.998.705-1.82 1.6-1.82.893 0 1.614.82 1.599 1.82 0 1-.706 1.82-1.6 1.82"/></svg>Post on Discord</button></div></footer>
</div></div><script type="application/json" id="draft">${json(draft)}</script><script nonce="onw-report">${SCRIPT}</script></body></html>`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!);
}
