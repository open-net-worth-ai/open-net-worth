/**
 * The document a drawing Lucius wrote (`show_visual` with `code`) runs in. The markup goes in
 * as written — scripts, styles, CDN libraries, images and links all work (owner, 2026-11-18:
 * keep only the limits Claude's own drawing tool has). It runs in a sandboxed frame with no
 * `allow-same-origin`, so its scripts never run as the signed-in app.
 *
 * The parent hands the frame the app's theme (CSS variables + the `iv-` classes the prompt
 * names) and the frame reports its height so it grows to fit.
 */

export const THEME_VARS = [
  '--ink',
  '--muted',
  '--line',
  '--line-strong',
  '--good',
  '--bad',
  '--accent',
  '--bg',
  '--bg-2',
  '--surface',
  '--surface2',
  '--mono',
] as const

/** Message types between the frame and the chat. */
export const MSG_HEIGHT = 'onw-visual-height'
export const MSG_THEME = 'onw-visual-theme'

const CLASSES = `
.iv-t{fill:var(--ink);color:var(--ink);font-size:13px}
.iv-ts{fill:var(--muted);color:var(--muted);font-size:11px}
.iv-th{fill:var(--ink);color:var(--ink);font-size:14px;font-weight:600}
.iv-line{fill:none;stroke:var(--line);stroke-width:1}
.iv-c1{fill:#3b86dd;stroke:#3b86dd;color:#3b86dd}
.iv-c2{fill:#34b08a;stroke:#34b08a;color:#34b08a}
.iv-c3{fill:#e0a032;stroke:#e0a032;color:#e0a032}
.iv-c4{fill:#9a7fd0;stroke:#9a7fd0;color:#9a7fd0}
.iv-c5{fill:#22b8cf;stroke:#22b8cf;color:#22b8cf}
.iv-c6{fill:#d4537e;stroke:#d4537e;color:#d4537e}
.iv-good{fill:var(--good);stroke:var(--good);color:var(--good)}
.iv-bad{fill:var(--bad);stroke:var(--bad);color:var(--bad)}
.iv-muted{fill:var(--muted);stroke:var(--muted);color:var(--muted)}
.iv-nofill{fill:none}
`

/** The frame's own script: report height whenever the drawing's size changes, take theme
 *  updates from the chat. Runs before the drawing's markup so a drawing that throws still
 *  reports its height. */
const BRIDGE = `
(function(){
  var send=function(){parent.postMessage({type:'${MSG_HEIGHT}',height:Math.ceil(document.documentElement.scrollHeight)},'*')};
  window.addEventListener('message',function(e){
    if(e.source!==parent||!e.data||e.data.type!=='${MSG_THEME}')return;
    var s=document.documentElement.style,v=e.data.vars||{};
    for(var k in v)s.setProperty(k,v[k]);
  });
  window.addEventListener('load',send);
  document.addEventListener('DOMContentLoaded',function(){
    send();
    if(window.ResizeObserver)new ResizeObserver(send).observe(document.documentElement);
  });
})();
`

/** The current value of each theme variable on the chat's own page. */
export function readTheme(root: Element = document.documentElement): Record<string, string> {
  const css = getComputedStyle(root)
  const out: Record<string, string> = {}
  for (const name of THEME_VARS) {
    const value = css.getPropertyValue(name).trim()
    if (value) out[name] = value
  }
  return out
}

/** The full HTML document for the frame. `font` is the chat's font stack. */
export function buildDrawingDocument(
  code: string,
  vars: Record<string, string>,
  font: string,
): string {
  const root = Object.entries(vars)
    .map(([k, v]) => `${k}:${v};`)
    .join('')
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<base target="_blank">
<style>:root{${root}}html,body{margin:0;padding:0;background:transparent}
body{color:var(--ink);font-family:${font || 'system-ui,sans-serif'};font-size:13px;line-height:1.45}
svg{max-width:100%;height:auto;display:block}${CLASSES}</style>
<script>${BRIDGE}</script>
</head><body>${code}</body></html>`
}
