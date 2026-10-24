// Settings overlay design primitives — ported from
// design_handoffs/settings_handoff/prototype/redesign/shell.jsx.
//
// These are the handoff's own "rd-" primitives, typed. They are deliberately NOT the
// components/ui/ design-system ones: the flattening rules in settings.css key off this exact
// DOM (a panel's body becomes the card, each direct child becomes a hairline row), so the
// markup IS the layout contract. The shared DS primitives emit different chrome and would
// break every row.
//
// Not ported (the prototype's standalone-shell scaffolding): AppNav, Shell, Kebab, Tabs, Dot.
import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, CSSProperties, ReactNode, SelectHTMLAttributes } from 'react'
import { ChevronDown, X } from '../../../ui/icons'

// ── primitives ───────────────────────────────────────────────────────────────
export type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-solid'

export interface BtnProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: BtnVariant
  size?: 'xs'
}

export function Btn({ variant = 'secondary', size, children, type = 'button', ...rest }: BtnProps) {
  return (
    <button type={type} className={`rd-btn ${variant}${size ? ' ' + size : ''}`} {...rest}>
      {children}
    </button>
  )
}

/** SegTabs — the Settings pill tab group (`rd-seg`). Arrow keys move between tabs; only the
 *  active one is in the tab order (roving tabindex), like ui/SegmentedControl. */
export function SegTabs<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  scroll,
}: {
  options: readonly { value: T; label: ReactNode }[]
  value: T
  onChange: (value: T) => void
  ariaLabel: string
  scroll?: boolean
}) {
  const move = (dir: 1 | -1) => {
    const idx = options.findIndex((o) => o.value === value)
    const next = options[(idx + dir + options.length) % options.length]
    if (next) onChange(next.value)
  }
  return (
    <div
      className={'rd-seg' + (scroll ? ' rd-seg--scroll' : '')}
      role="tablist"
      aria-label={ariaLabel}
    >
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            type="button"
            key={o.value}
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            className={'rd-seg-b' + (active ? ' on' : '')}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                e.preventDefault()
                move(1)
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                e.preventDefault()
                move(-1)
              }
            }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export interface SelectProps extends Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  'className' | 'style'
> {
  children: ReactNode
  style?: CSSProperties
}

export function Select({ children, style, ...rest }: SelectProps) {
  return (
    <span className="rd-select-wrap" style={style}>
      <select className="rd-select" {...rest}>
        {children}
      </select>
      <ChevronDown className="rd-select-ic" size={14} strokeWidth={1.8} aria-hidden="true" />
    </span>
  )
}

export type BadgeTone = '' | 'good' | 'warn' | 'bad' | 'accent'

export function Badge({ tone = '', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`rd-badge ${tone}`}>{children}</span>
}

export function Toggle({
  on,
  onChange,
  disabled,
  label,
}: {
  on: boolean
  onChange?: (next: boolean) => void
  disabled?: boolean
  label?: string
}) {
  return (
    <button
      type="button"
      className={'rd-toggle' + (on ? ' on' : '')}
      role="switch"
      aria-checked={on}
      aria-label={label || 'Toggle'}
      disabled={disabled}
      onClick={() => onChange?.(!on)}
    />
  )
}

export function Panel({
  title,
  sub,
  actions,
  children,
  flush,
  className,
}: {
  title?: ReactNode
  sub?: ReactNode
  actions?: ReactNode
  children: ReactNode
  flush?: boolean
  className?: string
}) {
  return (
    <section className={'rd-panel' + (className ? ' ' + className : '')}>
      {title || actions ? (
        <div className="rd-panel-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="rd-panel-title" role="heading" aria-level={3}>
              {title}
            </div>
            {sub ? <div className="rd-panel-sub">{sub}</div> : null}
          </div>
          {actions}
        </div>
      ) : null}
      <div className={'rd-panel-body' + (flush ? ' flush' : '')}>{children}</div>
    </section>
  )
}

export function Field({
  label,
  help,
  children,
  span,
}: {
  label: ReactNode
  help?: ReactNode
  children: ReactNode
  span?: 'full'
}) {
  return (
    <div className={'rd-field' + (span === 'full' ? ' full' : '')}>
      <span className="rd-label">{label}</span>
      {children}
      {help ? <span className="rd-help">{help}</span> : null}
    </div>
  )
}

export function Empty({
  title,
  body,
  action,
}: {
  title: ReactNode
  body?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="rd-empty">
      <div className="rd-empty-title">{title}</div>
      {body ? <div className="rd-empty-body">{body}</div> : null}
      {action}
    </div>
  )
}

/** The handoff's in-pane editor dialog. Escape is owned by the host overlay, so this
 *  marks itself as an escape layer and closes itself first. */
export function Modal({
  title,
  onClose,
  footer,
  children,
  size,
  className,
}: {
  title: ReactNode
  onClose: () => void
  footer?: ReactNode
  children: ReactNode
  size?: 'lg'
  /** A dialog-specific hook on `.rd-modal` (e.g. a fixed height). */
  className?: string
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <div
      className="rd-modal-scrim"
      data-modal-escape-layer=""
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={'rd-modal' + (size === 'lg' ? ' lg' : '') + (className ? ' ' + className : '')}
        role="dialog"
        aria-label={typeof title === 'string' ? title : undefined}
      >
        <div className="rd-modal-head">
          <div className="rd-modal-title">{title}</div>
          <button type="button" className="rd-modal-x" onClick={onClose} aria-label="Close">
            <X size={18} strokeWidth={1.6} aria-hidden="true" />
          </button>
        </div>
        <div className="rd-modal-body">{children}</div>
        {footer ? <div className="rd-modal-foot">{footer}</div> : null}
      </div>
    </div>
  )
}

export type Toast = (message: string) => void

export function useToast(): [Toast, ReactNode] {
  const [msg, setMsg] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const show: Toast = (m) => {
    setMsg(m)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMsg(null), 2200)
  }
  const node = msg ? <div className="rd-toast">{msg}</div> : null
  return [show, node]
}

/** The default settings row: label + consequence left, its single control right. */
export function row(
  title: ReactNode,
  sub: ReactNode,
  right: ReactNode,
  options?: { className?: string },
) {
  return (
    <div className={options?.className} style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600 }}>{title}</div>
        {sub ? (
          <div style={{ fontSize: 11.5, color: 'var(--faint)', marginTop: 2, lineHeight: 1.5 }}>
            {sub}
          </div>
        ) : null}
      </div>
      {right}
    </div>
  )
}

/** Every section body gets the same props. */
export interface SectionProps {
  toast: Toast
  // A DOM node OUTSIDE the scrolling `.rd-set-body`, provided by the shell (Settings.tsx) —
  // a section with its own sticky save bar (currently only ProfileSection) portals into it so
  // the bar sits below the scroller instead of floating, sticky, over whatever content happens
  // to be at the bottom of the viewport (a `position: sticky` DESCENDANT of an overflow:auto
  // container cannot escape its clipping — only leaving that DOM subtree can). Undefined/null
  // while the shell hasn't mounted the slot yet; callers should fall back to rendering inline.
  actionBarSlot?: HTMLDivElement | null
  /** Behind an authenticating proxy: sign-in belongs to the proxy, not this app. */
  gatewayMode?: boolean
  /** Jump to another Settings section by key (e.g. 'terminal'). */
  openSection?: (key: string) => void
}

// ── first-load skeleton, declared by whoever owns the fetch ───────────────────
// A section's data can be fetched at its top level (Security, Appearance) or several levels
// down inside a subcomponent (Developer has five such hooks). Wrapping each render root would
// mean a large, fragile diff and would still miss the nested ones. Instead any component that
// owns a first-load says so in one line, and the PANE renders the skeleton — so a section is
// covered no matter how deep its fetch lives.
//
// A counter, not a boolean: several hooks in one section load concurrently, and the skeleton
// must persist until the last one lands, not clear when the first does.
const SectionBusyContext = createContext<((delta: number) => void) | null>(null)

export function SectionBusyProvider({
  onChange,
  children,
}: {
  onChange: (delta: number) => void
  children: ReactNode
}) {
  const ref = useRef(onChange)
  ref.current = onChange
  // Stable identity: a fresh callback each render would retrigger every consumer's effect,
  // decrementing and re-incrementing the counter and flickering the skeleton.
  const bump = useRef((delta: number) => ref.current(delta)).current
  return <SectionBusyContext.Provider value={bump}>{children}</SectionBusyContext.Provider>
}

/** Declare that this component is doing a FIRST load — pass `initialLoading && !data`. */
export function useSectionBusy(busy: boolean): void {
  const bump = useContext(SectionBusyContext)
  useEffect(() => {
    if (!bump || !busy) return
    bump(1)
    return () => bump(-1)
  }, [busy, bump])
}
