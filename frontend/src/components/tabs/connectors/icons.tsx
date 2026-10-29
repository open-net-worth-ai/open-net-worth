// Connector category glyphs — ported VERBATIM from the prototype's `window.RD.Icons`
// (design_handoffs/connectors_handoff/prototype/redesign/shell.jsx), same path data,
// same per-icon sizes, same stroke geometry.
//
// These are deliberately NOT routed through `src/ui/icons.tsx`: that seam's own comment
// excludes connector glyphs ("Lucide dropped brand icons; these are a cohesive custom
// set"), and the prototype's sizes/stroke-width are a pixel contract of the design.
// They are generic CATEGORY icons (chat, mail, bank, chart, search, cpu) tinted per
// connector — never a brand mark (spec §10).

import type { JSX, SVGProps } from 'react'

const STROKE = {
  stroke: 'currentColor',
  fill: 'none',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

function Svg({ children, ...props }: SVGProps<SVGSVGElement>): JSX.Element {
  return (
    <svg width={19} height={19} viewBox="0 0 24 24" {...STROKE} {...props}>
      {children}
    </svg>
  )
}

export type IconKey =
  | 'accounts'
  | 'blocked'
  | 'chat'
  | 'copy'
  | 'check'
  | 'checkcircle'
  | 'chev'
  | 'cpu'
  | 'dots'
  | 'hand'
  | 'investments'
  | 'link'
  | 'mail'
  | 'plus'
  | 'search'
  | 'settings'
  | 'warn'

export type IconComponent = () => JSX.Element

export const Icons: Record<IconKey, IconComponent> = {
  investments: () => (
    <Svg>
      <path d="M4 16l5-5 3 3 7-7" />
      <path d="M16 7h4v4" />
    </Svg>
  ),
  accounts: () => (
    <Svg>
      <rect x={3} y={6} width={18} height={13} rx={2.4} />
      <path d="M3 9.5h18" />
      <circle cx={16.5} cy={13.5} r={1.1} />
    </Svg>
  ),
  settings: () => (
    <Svg>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx={12} cy={12} r={3} />
    </Svg>
  ),
  search: () => (
    <Svg width={14} height={14}>
      <circle cx={11} cy={11} r={6.5} />
      <line x1={15.8} y1={15.8} x2={20} y2={20} />
    </Svg>
  ),
  chev: () => (
    <Svg width={12} height={12}>
      <path d="M9 6l6 6-6 6" />
    </Svg>
  ),
  cpu: () => (
    <Svg width={13} height={13}>
      <rect x={6} y={6} width={12} height={12} rx={2} />
      <rect x={10} y={10} width={4} height={4} rx={0.5} />
      <line x1={12} y1={2} x2={12} y2={6} />
      <line x1={12} y1={18} x2={12} y2={22} />
      <line x1={2} y1={12} x2={6} y2={12} />
      <line x1={18} y1={12} x2={22} y2={12} />
    </Svg>
  ),
  check: () => (
    <Svg width={13} height={13}>
      <path d="M20 6 9 17l-5-5" />
    </Svg>
  ),
  warn: () => (
    <Svg width={14} height={14}>
      <path d="M12 3L2.5 20h19z" />
      <line x1={12} y1={9.5} x2={12} y2={14.5} />
      <circle cx={12} cy={17.3} r={0.4} />
    </Svg>
  ),
  // Two axis-aligned strokes at 13px on a 24-unit grid land ~0.9px wide on a half pixel, which
  // antialiases into a blur — `crispEdges` snaps them to the pixel grid, same geometry.
  plus: () => (
    <Svg width={13} height={13} shapeRendering="crispEdges">
      <line x1={12} y1={5} x2={12} y2={19} />
      <line x1={5} y1={12} x2={19} y2={12} />
    </Svg>
  ),
  hand: () => (
    <Svg width={15} height={15}>
      <path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" />
      <path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" />
      <path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" />
      <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
    </Svg>
  ),
  checkcircle: () => (
    <Svg width={15} height={15}>
      <circle cx={12} cy={12} r={10} />
      <path d="m9 12 2 2 4-4" />
    </Svg>
  ),
  blocked: () => (
    <Svg width={15} height={15}>
      <circle cx={12} cy={12} r={10} />
      <line x1={5.9} y1={5.9} x2={18.1} y2={18.1} />
    </Svg>
  ),
  dots: () => (
    <Svg width={15} height={15}>
      <circle cx={5} cy={12} r={1.4} />
      <circle cx={12} cy={12} r={1.4} />
      <circle cx={19} cy={12} r={1.4} />
    </Svg>
  ),
  mail: () => (
    <Svg>
      <rect x={3} y={6} width={18} height={12} rx={2} />
      <path d="M3.5 7l8.5 6.2L20.5 7" />
    </Svg>
  ),
  copy: () => (
    <Svg>
      <rect x={9} y={9} width={11} height={11} rx={2} />
      <path d="M5 15V6a2 2 0 0 1 2-2h8" />
    </Svg>
  ),
  link: () => (
    <Svg>
      <path d="M9 15l6-6" />
      <path d="M10 6.7l1-1a3.6 3.6 0 0 1 5.1 5.1l-1.4 1.4" />
      <path d="M14 17.3l-1 1a3.6 3.6 0 0 1-5.1-5.1l1.4-1.4" />
    </Svg>
  ),
  chat: () => (
    <Svg>
      <path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4.2 3.2A.6.6 0 0 1 4 18.7V6a1 1 0 0 1 1-1z" />
    </Svg>
  ),
}

/** Icons key → component, with the prototype's `Icons[icon] || Icons.plus` fallback. */
export function icon(key: string): IconComponent {
  return Icons[key as IconKey] ?? Icons.plus
}
