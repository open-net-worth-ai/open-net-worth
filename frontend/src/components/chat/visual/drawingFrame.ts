/**
 * The document a drawing Lucius wrote (`show_visual` with `code`) runs in. The markup goes in
 * as written — scripts, styles, CDN libraries, images and links all work (owner, 2026-11-18:
 * keep only a minimal set of limits). It runs in a sandboxed frame with no
 * `allow-same-origin`, so its scripts never run as the signed-in app.
 *
 * The parent hands the frame the app's theme (its ink/border/surface variables and light or
 * dark mode, styled by `drawingStyle.ts`) and the frame reports its height so it grows to fit.
 */

import { DRAWING_CSS } from './drawingStyle'

/** The app's own tokens -> the names the drawing guide uses (the drawing style guide's names). */
const TOKEN_MAP: Record<string, string> = {
  '--text-primary': '--ink',
  '--text-secondary': '--muted',
  '--text-muted': '--muted',
  '--border': '--line',
  '--border-strong': '--line-strong',
  '--surface-1': '--surface',
  '--surface-2': '--surface2',
  '--font-mono': '--mono',
  // the app's own names stay too, for drawings written before the guide
  '--ink': '--ink',
  '--muted': '--muted',
  '--line': '--line',
  '--good': '--good',
  '--bad': '--bad',
  '--accent': '--accent',
  '--bg': '--bg',
}

/** Message types between the frame and the chat. */
export const MSG_HEIGHT = 'onw-visual-height'
export const MSG_THEME = 'onw-visual-theme'
/** Must match `MSG_RENDER` in backend/api/visuals.py, whose loader writes the posted document. */
export const MSG_RENDER = 'onw-visual-render'
/** The loader a drawing runs in, served with its own CSP (backend/api/visuals.py). */
export const FRAME_URL = '/api/visuals/frame'

export const MSG_MEASURE = 'onw-visual-measure'

/** The frame's own script: report height whenever the drawing's size changes or the chat asks
 *  (a report sent before the chat listens is lost — live, a long transcript left drawings at
 *  their starting height for ~10s), and take theme updates. Runs before the drawing's markup
 *  so a drawing that throws still reports its height. */
const BRIDGE = `
(function(){
  var d=document;
  var height=function(){var b=d.body,h=d.documentElement;
    return Math.ceil(Math.max(h.scrollHeight,b?b.scrollHeight:0,b?b.getBoundingClientRect().bottom:0))};
  var send=function(){parent.postMessage({type:'${MSG_HEIGHT}',height:height()},'*')};
  window.addEventListener('message',function(e){
    if(e.source!==parent||!e.data)return;
    if(e.data.type==='${MSG_MEASURE}'){send();return}
    if(e.data.type!=='${MSG_THEME}')return;
    var s=d.documentElement.style,v=e.data.vars||{};
    for(var k in v)s.setProperty(k,v[k]);
    if(e.data.mode)d.documentElement.setAttribute('data-mode',e.data.mode);
  });
  window.addEventListener('load',send);
  d.addEventListener('DOMContentLoaded',function(){
    send();
    if(window.ResizeObserver){var ro=new ResizeObserver(send);ro.observe(d.documentElement);ro.observe(d.body)}
  });
})();
`

/** The chat's theme as the drawing guide names it: its ink, border and surface variables. */
export function readTheme(root: Element = document.documentElement): Record<string, string> {
  const css = getComputedStyle(root)
  const out: Record<string, string> = {}
  for (const [name, source] of Object.entries(TOKEN_MAP)) {
    const value = css.getPropertyValue(source).trim()
    if (value) out[name] = value
  }
  return out
}

/** Light or dark, from the app's own `color-scheme` (its default theme is dark). */
export function readMode(root: Element = document.documentElement): 'light' | 'dark' {
  return getComputedStyle(root).colorScheme.includes('dark') ? 'dark' : 'light'
}

/** The full HTML document for the frame. `font` is the chat's font stack; `fontFaces` the
 *  `@font-face` rules that make it load inside the frame (`drawingFonts.ts`). */
export function buildDrawingDocument(
  code: string,
  vars: Record<string, string>,
  font: string,
  mode: 'light' | 'dark' = 'dark',
  fontFaces = '',
): string {
  const root = Object.entries(vars)
    .map(([k, v]) => `${k}:${v};`)
    .join('')
  return `<!doctype html><html data-mode="${mode}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<base target="_blank">
<style>${fontFaces}:root{${root}}${DRAWING_CSS}
body{font-family:${font || 'system-ui,sans-serif'}}</style>
<script>${BRIDGE}</script>
</head><body>${code}</body></html>`
}
