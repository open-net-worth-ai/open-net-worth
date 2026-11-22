import { escapeHtml, nativeDocument } from './native-page';

// "Continue in your browser": never a silent wait. Every way out is on the screen.
export function waitingPage(theme: 'dark' | 'light', moving: boolean): string {
  const body = `<h1>Continue in your browser.</h1><p>We opened your Private Cloud sign-in in your browser. Finish there and this app opens your Private Cloud on its own.${moving ? ' Your book moves as soon as you’re signed in.' : ''}</p><p>If your browser asks to open Open Net Worth, allow it. If it can’t, use the code the sign-in page shows.</p><p>Private Cloud is invitation-only. If the page says you’re on the waitlist, there is nothing more to do here: cancel, and your book stays on this computer.</p><div id="main" class="actions"><button class="primary" id="again">Open browser again</button><button id="code">Enter code</button><button class="link" id="cancel">Cancel</button></div><form id="form" hidden><label for="short">Code from the sign-in page</label><input id="short" autocomplete="off" spellcheck="false" maxlength="12" placeholder="ABCD-2345" aria-describedby="error"><div class="actions"><button class="primary" id="redeem" type="submit">Continue</button><button class="link" id="back" type="button">Back</button></div></form>`;
  const script = `const flow=window.onwFlow,main=document.getElementById('main'),form=document.getElementById('form'),short=document.getElementById('short');
document.getElementById('again').onclick=()=>run(()=>flow.action('again'));
document.getElementById('cancel').onclick=()=>run(()=>flow.action('cancel'));
document.getElementById('code').onclick=()=>{main.hidden=true;form.hidden=false;short.focus()};
document.getElementById('back').onclick=()=>{form.hidden=true;main.hidden=false;document.getElementById('error').textContent=''};
form.onsubmit=event=>{event.preventDefault();run(()=>flow.action('redeem',short.value))};
document.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();run(()=>flow.action('cancel'))}});`;
  return nativeDocument({ theme, title: 'Sign in to Private Cloud', body, script, nonce: 'onw-flow' });
}

// Driven by the main process: `setProgress(percent|null, text)` and `setFinished(title, text)` run in this page.
export function progressPage(theme: 'dark' | 'light', title: string): string {
  const body = `<h1>${escapeHtml(title)}</h1><p id="status" role="status">Getting ready…</p><div class="bar busy" id="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><span id="fill"></span></div><div class="actions"><button id="cancel">Cancel</button></div>`;
  const script = `const bar=document.getElementById('bar'),fill=document.getElementById('fill'),status=document.getElementById('status'),cancel=document.getElementById('cancel');
window.setProgress=(percent,text)=>{status.textContent=text;if(percent===null){bar.classList.add('busy');bar.removeAttribute('aria-valuenow')}else{bar.classList.remove('busy');fill.style.width=percent+'%';bar.setAttribute('aria-valuenow',String(Math.round(percent)))}};
cancel.onclick=()=>run(()=>window.onwFlow.action('cancel'));
document.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();cancel.click()}});`;
  return nativeDocument({ theme, title, body, script, nonce: 'onw-flow' });
}
