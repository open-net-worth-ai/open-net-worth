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
/** A drawing still being written: its markup so far, put in the page with no script run. */
export const MSG_DRAFT = 'onw-visual-draft'
/** Copy / download (visualExport.ts): the chat asks, the frame answers with a data URL. */
export const MSG_EXPORT = 'onw-visual-export'
export const MSG_EXPORTED = 'onw-visual-exported'

/** The frame's export: a drawing that is one SVG (no canvas) is serialized as that SVG, with
 *  the frame's styles, theme and font inlined so it opens the same anywhere. Anything else
 *  goes through html-to-image, whose browser build the chat posts in with the request. */
const EXPORT = `
  var exportIt=function(m){
    var reply=function(o){o.type='${MSG_EXPORTED}';o.id=m.id;parent.postMessage(o,'*')};
    var fail=function(err){reply({error:String(err&&err.message||err)})};
    try{
      var kids=[].filter.call(d.body.children,function(n){return n.tagName!=='SCRIPT'&&n.tagName!=='STYLE'});
      var one=kids.length===1&&kids[0].tagName.toLowerCase()==='svg'&&!d.querySelector('canvas');
      if(m.format==='svg'&&one){
        var src=kids[0],svg=src.cloneNode(true),bb=src.getBoundingClientRect();
        var st=d.createElementNS('http://www.w3.org/2000/svg','style');
        st.textContent=[].map.call(d.querySelectorAll('style'),function(s){return s.textContent}).join(' ');
        svg.insertBefore(st,svg.firstChild);
        svg.setAttribute('xmlns','http://www.w3.org/2000/svg');
        svg.setAttribute('data-mode',d.documentElement.getAttribute('data-mode')||'light');
        svg.setAttribute('width',Math.round(bb.width));svg.setAttribute('height',Math.round(bb.height));
        svg.setAttribute('style',(d.documentElement.getAttribute('style')||'')+';font-family:'+getComputedStyle(d.body).fontFamily);
        reply({dataUrl:'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(svg))});
        return}
      if(!window.htmlToImage)(0,eval)(m.lib);
      var fn=m.format==='svg'?window.htmlToImage.toSvg:window.htmlToImage.toPng;
      // html-to-image copies computed styles but not the style sheet, and it misses what SVG
      // shapes get from classes (class="s1" -> var(--series-1)): their fills came out black.
      // Write each SVG node's resolved paint and type onto it for the copy, then put it back.
      var props=['fill','stroke','stroke-width','stroke-dasharray','opacity','fill-opacity','stroke-opacity','font-family','font-size','font-weight','text-anchor','dominant-baseline'];
      var saved=[].map.call(d.body.querySelectorAll('svg, svg *'),function(n){
        var cs=getComputedStyle(n),was=n.getAttribute('style');
        n.setAttribute('style',props.map(function(p){return p+':'+cs.getPropertyValue(p)}).join(';')+';'+(was||''));
        return [n,was]});
      var restore=function(){saved.forEach(function(s){if(s[1]===null)s[0].removeAttribute('style');else s[0].setAttribute('style',s[1])})};
      fn(d.body,{backgroundColor:m.bg,pixelRatio:2}).then(function(u){restore();reply({dataUrl:u})},function(err){restore();fail(err)});
    }catch(err){fail(err)}
  };`

/** The frame's own script: report height whenever the drawing's size changes or the chat asks
 *  (a report sent before the chat listens is lost — live, a long transcript left drawings at
 *  their starting height for ~10s), take theme updates, and export itself on request. Runs
 *  before the drawing's markup so a drawing that throws still reports its height. */
const BRIDGE = `
(function(){
  var d=document;
  var height=function(){var b=d.body,h=d.documentElement;
    return Math.ceil(Math.max(h.scrollHeight,b?b.scrollHeight:0,b?b.getBoundingClientRect().bottom:0))};
  var send=function(){parent.postMessage({type:'${MSG_HEIGHT}',height:height()},'*')};${EXPORT}
  window.addEventListener('message',function(e){
    if(e.source!==parent||!e.data)return;
    if(e.data.type==='${MSG_MEASURE}'){send();return}
    if(e.data.type==='${MSG_DRAFT}'){d.body.innerHTML=String(e.data.html||'');return}
    if(e.data.type==='${MSG_RENDER}'){d.open();d.write(String(e.data.html||''));d.close();return}
    if(e.data.type==='${MSG_EXPORT}'){exportIt(e.data);return}
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
