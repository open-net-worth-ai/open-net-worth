/**
 * The style sheet a drawing Lucius writes runs under — a full design system for drawings
 * (owner, 2026-11-18: a real style guide, not two lines). The
 * prompt's VISUALS paragraph (backend/ai/prompts.py) names these classes and variables; keep
 * the two in step.
 *
 * - Ink: --text-primary / --text-secondary / --text-muted, mapped from the app's own tokens.
 * - Series: --series-1…8 in a fixed order (blue, orange, aqua, yellow, magenta, green, violet,
 *   red), classes s1…s8. Status: good / warn / serious / bad.
 * - Chart chrome: --grid (hairline), --axis (baseline); classes grid / axis.
 * - Diagram ramps: c-{purple,teal,coral,pink,gray,blue,green,amber,red} — light fill, darker
 *   stroke and text, flipped in dark mode.
 * - Text: t (13px), ts (12px secondary), th (14px, weight 500). Two weights only.
 *
 * `iv-*` are the earlier names; kept so drawings already in a transcript still render.
 */

const SERIES_LIGHT = [
  '#2a78d6',
  '#eb6834',
  '#1baf7a',
  '#eda100',
  '#e87ba4',
  '#008300',
  '#6250d6',
  '#e34948',
]
const SERIES_DARK = [
  '#3987e5',
  '#d95926',
  '#199e70',
  '#c98500',
  '#d55181',
  '#008300',
  '#9085e9',
  '#e66767',
]

// [ramp, 50, 200, 600, 800, 100] — light fill / dark stroke / light stroke / dark fill / dark text
const RAMPS: [string, string, string, string, string, string][] = [
  ['purple', '#EEEDFE', '#AFA9EC', '#534AB7', '#3C3489', '#CECBF6'],
  ['teal', '#E1F5EE', '#5DCAA5', '#0F6E56', '#085041', '#9FE1CB'],
  ['coral', '#FAECE7', '#F0997B', '#993C1D', '#712B13', '#F5C4B3'],
  ['pink', '#FBEAF0', '#ED93B1', '#993556', '#72243E', '#F4C0D1'],
  ['gray', '#F1EFE8', '#B4B2A9', '#5F5E5A', '#444441', '#D3D1C7'],
  ['blue', '#E6F1FB', '#85B7EB', '#185FA5', '#0C447C', '#B5D4F4'],
  ['green', '#EAF3DE', '#97C459', '#3B6D11', '#27500A', '#C0DD97'],
  ['amber', '#FAEEDA', '#EF9F27', '#854F0B', '#633806', '#FAC775'],
  ['red', '#FCEBEB', '#F09595', '#A32D2D', '#791F1F', '#F7C1C1'],
]

const seriesVars = (hex: string[]) => hex.map((h, i) => `--series-${i + 1}:${h};`).join('')

const ramps = RAMPS.map(
  ([n, l50, d200, l600, l800, d100]) =>
    `[data-mode=light] .c-${n}>rect,[data-mode=light] .c-${n}>circle,[data-mode=light] .c-${n}>ellipse,` +
    `[data-mode=light] rect.c-${n},[data-mode=light] circle.c-${n},[data-mode=light] ellipse.c-${n}` +
    `{fill:${l50};stroke:${l600}}` +
    `[data-mode=light] .c-${n} .t,[data-mode=light] .c-${n} .th{fill:${l800}}` +
    `[data-mode=light] .c-${n} .ts{fill:${l600}}` +
    `[data-mode=dark] .c-${n}>rect,[data-mode=dark] .c-${n}>circle,[data-mode=dark] .c-${n}>ellipse,` +
    `[data-mode=dark] rect.c-${n},[data-mode=dark] circle.c-${n},[data-mode=dark] ellipse.c-${n}` +
    `{fill:${l800};stroke:${d200}}` +
    `[data-mode=dark] .c-${n} .t,[data-mode=dark] .c-${n} .th{fill:${d100}}` +
    `[data-mode=dark] .c-${n} .ts{fill:${d200}}`,
).join('')

const series = Array.from(
  { length: 8 },
  (_, i) =>
    `.s${i + 1}{fill:var(--series-${i + 1});stroke:var(--series-${i + 1});color:var(--series-${i + 1})}`,
).join('')

export const DRAWING_CSS = `
:root,[data-mode=dark]{${seriesVars(SERIES_DARK)}--grid:#2c2c2a;--axis:#383835;--status-good:#0ca30c;--status-warning:#c98500;--status-serious:#d95926;--status-critical:#e66767}
[data-mode=light]{${seriesVars(SERIES_LIGHT)}--grid:#e1e0d9;--axis:#c3c2b7;--status-good:#0ca30c;--status-warning:#fab219;--status-serious:#ec835a;--status-critical:#d03b3b}
[data-mode=dark]{color-scheme:dark}[data-mode=light]{color-scheme:light}
html,body{margin:0;padding:0;background:transparent}html{overflow-y:hidden}
body{color:var(--text-primary);font-size:14px;line-height:1.6;font-weight:400}
b,strong,h1,h2,h3,h4,h5,h6,th{font-weight:500}
h1{font-size:18px}h2{font-size:16px}h3,h4,h5,h6{font-size:14px}
svg{max-width:100%;height:auto;display:block;font-family:inherit}
text{fill:var(--text-primary)}
.t{fill:var(--text-primary);color:var(--text-primary);font-size:13px}
.ts{fill:var(--text-secondary);color:var(--text-secondary);font-size:12px}
.th{fill:var(--text-primary);color:var(--text-primary);font-size:14px;font-weight:500}
.tm{fill:var(--text-muted);color:var(--text-muted);font-size:11px}
${series}
.good{fill:var(--status-good);stroke:var(--status-good);color:var(--status-good)}
.warn{fill:var(--status-warning);stroke:var(--status-warning);color:var(--status-warning)}
.serious{fill:var(--status-serious);stroke:var(--status-serious);color:var(--status-serious)}
.bad{fill:var(--status-critical);stroke:var(--status-critical);color:var(--status-critical)}
.muted{fill:var(--text-muted);stroke:var(--text-muted);color:var(--text-muted)}
.grid{fill:none;stroke:var(--grid);stroke-width:1}
.axis{fill:none;stroke:var(--axis);stroke-width:1}
.nofill{fill:none}
${ramps}
table{border-collapse:collapse;font-size:13px}th,td{padding:4px 8px;border-bottom:0.5px solid var(--border);text-align:left}
button{font:inherit;font-size:13px;color:inherit;background:transparent;border:0.5px solid var(--border-strong);border-radius:8px;padding:6px 12px;cursor:pointer}
.iv-t{fill:var(--text-primary);font-size:13px}.iv-ts{fill:var(--text-secondary);font-size:11px}
.iv-th{fill:var(--text-primary);font-size:14px;font-weight:500}.iv-line{fill:none;stroke:var(--grid);stroke-width:1}
.iv-c1{fill:var(--series-1);stroke:var(--series-1)}.iv-c2{fill:var(--series-3);stroke:var(--series-3)}
.iv-c3{fill:var(--series-4);stroke:var(--series-4)}.iv-c4{fill:var(--series-7);stroke:var(--series-7)}
.iv-c5{fill:var(--series-5);stroke:var(--series-5)}.iv-c6{fill:var(--series-2);stroke:var(--series-2)}
.iv-good{fill:var(--status-good);stroke:var(--status-good)}.iv-bad{fill:var(--status-critical);stroke:var(--status-critical)}
.iv-muted{fill:var(--text-muted);stroke:var(--text-muted)}.iv-nofill{fill:none}
`
