// Shell primitives for the Connectors redesign — ported VERBATIM from the prototype's
// `window.RD` (design_handoffs/connectors_handoff/prototype/redesign/shell.jsx): same
// DOM, same class names, so the result is pixel-identical.
//
// Why these are ported rather than mapped onto `components/ui/*`:
//   Btn     — `ui/Button` has no `xs` size and is padding-sized; spec §9 makes the fixed
//             30px/24px pill heights a contract ("controls never resize").
//   Badge   — `ui/Badge` tones are pos|neg|neutral|muted|warn; the ladder needs
//             good|warn|bad|accent + a bare count badge.
//   Tabs    — `ui/PageTabs` renders a different underline/badge treatment.
//   Field   — `ui/FormField` emits `ui-field`; the MCP form's two-column `.rd-formgrid`
//             layout is driven by `.rd-field.full`.
//   Empty   — `ui/EmptyState` emits `ui-state`, a different vertical rhythm.
//   Kebab   — `ui/ActionMenu` portals to <body>; the group-permission control reuses
//             `.rd-kebab-pop` INLINE, so one popover implementation keeps them identical.
//   Modal   — `ui/Modal` (Radix) has a different DOM and size vocabulary; spec §9 pins
//             this one's geometry (flex:none header/footer, only `.rd-modal-body`
//             scrolls, the lone-footer-button rule).
//
// NOT ported, by design: AppNav and the Lucius side panel — the real RouterShell and
// LuciusDock host this tab. Dot / Toggle / Panel are unused by the Connectors prototype.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type JSX,
  type ReactNode,
} from 'react'
import { Icons, icon } from './icons'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import { X } from '../../../ui/icons'

// ── Btn ─────────────────────────────────────────────────────────────────────

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  /** `xs` (24px) for dense in-row actions · default 30px · `lg` (38px) for the ONE action a
   *  header exists to drive. Each is a fixed `height` with `box-sizing: border-box`, and no
   *  breakpoint moves any of them (spec §9). */
  size?: 'xs' | 'lg'
}

export function Btn({ variant = 'secondary', size, children, ...rest }: BtnProps): JSX.Element {
  return (
    <button className={`rd-btn ${variant}${size ? ' ' + size : ''}`} {...rest}>
      {children}
    </button>
  )
}

// ── Badge ───────────────────────────────────────────────────────────────────

export interface BadgeProps {
  tone?: 'good' | 'warn' | 'bad' | 'accent' | ''
  children: ReactNode
}

export function Badge({ tone = '', children }: BadgeProps): JSX.Element {
  return <span className={`rd-badge ${tone}`}>{children}</span>
}

// ── Mono: the connector tile ────────────────────────────────────────────────

export interface MonoProps {
  /** An `Icons` key — the tinted category glyph, and the FALLBACK whenever `logo` is absent
   *  or fails to load. Several connectors have no mark, and an MCP server a user adds never
   *  will, so this path is ordinary rather than exceptional (see `logos.ts`). */
  icon: string
  /** A token reference, e.g. `var(--ibkr)` — tints glyph, fill and border. */
  hue: string
  /** A real brand mark, from `connectorLogo(id)`. */
  logo?: string
  /** `lg` (52px) is the header tile; the default 34px is the one a list row and a directory
   *  card use. */
  size?: 'lg'
}

export function Mono({ icon: iconKey, hue, logo, size }: MonoProps): JSX.Element {
  const Ic = icon(iconKey)
  // A file can 404 or decode-fail at runtime — a broken-image glyph in place of a connector's
  // identity is worse than the tinted glyph we already have, so a failure falls back rather
  // than showing the browser's placeholder. Keyed by src so a re-render for a DIFFERENT
  // connector is not still suppressed by the previous one's failure.
  const [broken, setBroken] = useState<string | null>(null)
  const showLogo = !!logo && broken !== logo

  return (
    <span
      className={'cn-tile' + (size === 'lg' ? ' lg' : '') + (showLogo ? ' plate' : '')}
      style={
        showLogo
          ? undefined
          : {
              color: hue,
              background: `color-mix(in srgb, ${hue} 14%, transparent)`,
              borderColor: `color-mix(in srgb, ${hue} 30%, transparent)`,
            }
      }
    >
      {showLogo ? (
        // `alt=""` on purpose: the connector's name is always beside the tile, so a screen
        // reader announcing the mark again would only repeat it.
        <img className="cn-tile-logo" src={logo} alt="" onError={() => setBroken(logo)} />
      ) : (
        <Ic />
      )}
    </span>
  )
}

// ── Kebab ───────────────────────────────────────────────────────────────────

export interface KebabItem {
  label: string
  onClick?: () => void
  danger?: boolean
}

export function Kebab({ items }: { items: KebabItem[] }): JSX.Element {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  return (
    <span className="rd-kebab" ref={ref}>
      <button
        className="rd-kebab-btn"
        aria-label="More actions"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation()
          setOpen(!open)
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="5" r="2.4" />
          <circle cx="12" cy="12" r="2.4" />
          <circle cx="12" cy="19" r="2.4" />
        </svg>
      </button>
      {open && (
        <span className="rd-kebab-pop">
          {items.map((it, i) => (
            <button
              key={i}
              className={'rd-kebab-item' + (it.danger ? ' danger' : '')}
              onClick={(e) => {
                e.stopPropagation()
                setOpen(false)
                it.onClick?.()
              }}
            >
              {it.label}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}

// ── Tabs ────────────────────────────────────────────────────────────────────

export interface TabItem<T extends string> {
  key: T
  label: string
  count?: number
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: TabItem<T>[]
  value: T
  onChange: (key: T) => void
}): JSX.Element {
  return (
    <div className="rd-tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={t.key === value}
          className={'rd-tab' + (t.key === value ? ' on' : '')}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {t.count != null ? <span className="rd-tab-count">{t.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

// ── Field ───────────────────────────────────────────────────────────────────

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
}): JSX.Element {
  return (
    <div className={'rd-field' + (span === 'full' ? ' full' : '')}>
      <span className="rd-label">{label}</span>
      {children}
      {help ? <span className="rd-help">{help}</span> : null}
    </div>
  )
}

// ── Empty ───────────────────────────────────────────────────────────────────

export function Empty({
  title,
  body,
  action,
  children,
}: {
  title: ReactNode
  body?: ReactNode
  action?: ReactNode
  /** Content between the copy and the action — e.g. a few connectors to start with. */
  children?: ReactNode
}): JSX.Element {
  return (
    <div className="rd-empty">
      <div className="rd-empty-title">{title}</div>
      {body ? <div className="rd-empty-body">{body}</div> : null}
      {children}
      {action}
    </div>
  )
}

// ── Modal ───────────────────────────────────────────────────────────────────

// The scrim insets itself away from the docked Lucius side panel exactly like ui/Modal's
// own `.modal-overlay` (index.css) does — `--chat-width` is `.lx-side`'s live width while
// open, `0px` while closed/collapsed (ChatPanel.tsx sets it). Without this the scrim (full
// viewport, `--z-modal` — HIGHER than `.lx-side`'s `--z-overlay`) visually and functionally
// covers the panel: `.lx-side` is a body-level DOM SIBLING of this modal (mounted once in
// RouterShell, never a descendant — see ui/Modal.tsx's own isOutsideExempt comment), so a
// click meant for it can only ever reach it by *not* being intercepted first — there is no
// DOM-ancestry check that can rescue a click the browser already delivered to the opaque
// scrim sitting on top of it. This is a real, different mechanism from ui/Modal's
// isOutsideExempt: that guard defends a DOM-ancestry "outside click" test (Radix's
// DismissableLayer, which sees the real click target regardless of z-index) against
// wrongly firing on a *reachable* Lucius surface; this modal's own dismiss check
// (onMouseDown below) is occlusion-based instead — `e.target` can only ever be this scrim
// or something inside `.rd-modal`, so the exempt selector list would never match here even
// if reused verbatim. Preventing the occlusion in the first place is what actually keeps
// `.lx-side` reachable. `.lx-dock` (the collapsed pill) needs no equivalent fix — its own
// z-index (`--z-toast + 10`, lucius.css) already sits above `--z-modal`, so it is never
// occluded by this scrim regardless of `--chat-width`.
const SCRIM_STYLE: CSSProperties = { right: 'var(--chat-width, 0px)' }

/** Conditionally RENDERED, like the prototype — there is no `open` prop.
 *  Escape-to-close and the initial focus move are additions: invisible to the pixel
 *  diff, and the UI Quality Gate requires a dialog to be dismissable from the keyboard. */
export function Modal({
  title,
  onClose,
  footer,
  children,
  size,
  scrollKey,
  escapeDisabled,
}: {
  title: ReactNode
  onClose: () => void
  footer?: ReactNode
  children: ReactNode
  size?: 'lg' | 'xl'
  /** Changing this scrolls `.rd-modal-body` back to the top — pass the active view's
   *  identity (e.g. `grid` vs `preview:<id>`) so a modal that swaps between several
   *  scrollable views (spec §6) never opens the next one mid-scroll from the last. */
  scrollKey?: string
  /** True while ANOTHER Modal is stacked on top of this one (a confirm dialog over the page
   *  it was opened from) — Escape's listener is `document`-global, so without this BOTH
   *  modals would close on one keypress instead of just the top one. Backdrop-click needs no
   *  equivalent guard: the stacked modal's own full-viewport scrim physically covers this
   *  one, so a click can never reach this modal's `onMouseDown` in the first place. */
  escapeDisabled?: boolean
}): JSX.Element {
  const cardRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const headRef = useRef<HTMLDivElement>(null)
  const footRef = useRef<HTMLDivElement>(null)
  // The measured element: an inner wrapper around `children`, NOT `.rd-modal-body` itself.
  // Once the body below gets its own explicit `height`, ITS scrollHeight would just echo that
  // height back — measuring a child that is never height-constrained is the only way to read
  // the content's true natural size.
  const contentRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState<number | null>(null)
  // Owner review, 2026-08-21: a fixed-height card either floated a short form in a mostly-empty
  // box (Perplexity — one API key field, ~720px reserved) or, reverted to shrink-to-fit, visibly
  // snapped between a "Loading…" stub and the loaded form, and again on every provider swap
  // that changes field count (Email's provider chips). Neither is animate-able through CSS
  // alone — `height: auto` cannot be a `transition` endpoint. So `.rd-modal-body` gets an
  // EXPLICIT, ResizeObserver-measured pixel height instead (`flex: none` overriding the CSS
  // `flex:1` via a higher-precedence inline style once measured), and `.rd-modal-body`'s own
  // `transition: height` (connectors.css) animates every subsequent change smoothly.
  //
  // FIRST BUG, caught live (2026-08-21) and the reason this is documented so verbosely: the
  // target height was set to `content.scrollHeight` directly — `contentRef`'s OWN natural
  // height, which excludes `.rd-modal-body`'s `padding: 18px 20px` (36px vertical) since the
  // padding belongs to the parent being sized, not to the child being measured. Content that
  // fit fine got clipped and a needless scrollbar appeared, on every form, because the body was
  // being pinned ~36px shorter than it needed. Fixed by adding the body's OWN computed padding
  // back onto the measured content height before it becomes the body's target height.
  // SECOND BUG, same day: mid-transition, `.rd-modal-body`'s ANIMATED height is briefly less
  // than its content's full (unconstrained) height — `overflow-y: auto` reacts to that instant
  // by painting a scrollbar, then removes it once the transition lands on its target (which by
  // construction always fits, short of the size cap). The visible symptom was a scrollbar
  // flashing in and out for the ~0.18s the resize was animating. Overflow is hidden for the
  // DURATION of an actual resize (skipped on the very first, un-animated measurement) and
  // restored on the CSS `transitionend` for `height` specifically — imperative DOM writes, not
  // React state, since this is a transient visual detail with no rendering consequence and
  // React's own `style` reconciliation never touches a key (`overflowY`) it has never itself
  // written into the `style` object below.
  const measure = useCallback(() => {
    const card = cardRef.current
    const body = bodyRef.current
    const content = contentRef.current
    if (!card || !body || !content) return
    const bodyStyle = getComputedStyle(body)
    const paddingV = parseFloat(bodyStyle.paddingTop) + parseFloat(bodyStyle.paddingBottom)
    const natural = content.scrollHeight + paddingV
    const maxTotal = parseFloat(getComputedStyle(card).maxHeight)
    const available = Number.isFinite(maxTotal)
      ? Math.max(
          0,
          maxTotal - (headRef.current?.offsetHeight ?? 0) - (footRef.current?.offsetHeight ?? 0),
        )
      : Infinity
    const next = Math.min(natural, available)
    setBodyHeight((prev) => {
      if (prev != null && prev !== next) body.style.overflowY = 'hidden'
      return next
    })
  }, [])
  useLayoutEffect(() => {
    if (size !== 'xl') measure()
  }, [measure, size, scrollKey])
  useEffect(() => {
    if (size === 'xl') return
    const content = contentRef.current
    if (!content) return
    const ro = new ResizeObserver(measure)
    ro.observe(content)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure, size])
  useEffect(() => {
    if (size === 'xl') return
    const body = bodyRef.current
    if (!body) return
    const onEnd = (e: TransitionEvent) => {
      if (e.target === body && e.propertyName === 'height') body.style.overflowY = ''
    }
    body.addEventListener('transitionend', onEnd)
    return () => body.removeEventListener('transitionend', onEnd)
  }, [size])
  useLayoutEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0
  }, [scrollKey])
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [])
  // Mount-only: `onClose` is a plain function from every caller, so a dependency array
  // including it re-runs this on every re-render the caller causes (e.g. every keystroke in
  // a controlled field inside the modal) — which stole focus BACK to the card from whatever
  // the operator was actively typing into. Real bug, reproduced back on unmodified `main`
  // (2026-08-19) — pre-dates this file's `escapeDisabled` addition, not caused by it.
  useEffect(() => {
    cardRef.current?.focus()
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !escapeDisabled) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, escapeDisabled])
  // On a phone this card is a bottom sheet (connectors.css), so it closes the way every other
  // sheet in the app does: drag it down, with the same handle bar as `ui/Modal`.
  const isMobile = useIsMobile()
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: cardRef,
    overlayRef: scrimRef,
    onDismiss: onClose,
    enabled: isMobile,
  })
  return (
    <div
      ref={scrimRef}
      className="rd-modal-scrim"
      style={SCRIM_STYLE}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={cardRef}
        tabIndex={-1}
        className={'rd-modal' + (size === 'lg' ? ' lg' : '') + (size === 'xl' ? ' xl' : '')}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Connectors'}
        {...dragHandlers}
      >
        <div className="ui-modal-handle-wrap">
          <span className="ui-modal-handle" />
        </div>
        <div className="rd-modal-head" ref={headRef}>
          <div className="rd-modal-title">{title}</div>
          <button className="rd-modal-x" onClick={onClose} aria-label="Close">
            <X size={18} strokeWidth={1.6} aria-hidden="true" />
          </button>
        </div>
        <div
          className="rd-modal-body"
          ref={bodyRef}
          style={bodyHeight != null ? { flex: 'none', height: bodyHeight } : undefined}
        >
          <div ref={contentRef}>{children}</div>
        </div>
        {footer ? (
          <div className="rd-modal-foot" ref={footRef}>
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  )
}

// ── BackTitle ───────────────────────────────────────────────────────────────

/** A modal title that IS the back control (`‹ Connectors`, spec §3) — so going one level
 *  deeper looks identical wherever you are in the tab. Shared by the detail page, the
 *  directory and the MCP edit flow, which is three places and counting. */
export function BackTitle({ label, onClick }: { label: string; onClick: () => void }): JSX.Element {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: 'none',
        border: 'none',
        color: 'inherit',
        font: 'inherit',
        cursor: 'pointer',
        padding: 0,
      }}
    >
      <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}>
        <Icons.chev />
      </span>
      {label}
    </button>
  )
}

// ── useToast ────────────────────────────────────────────────────────────────

export function useToast(): [(m: string) => void, JSX.Element | null] {
  const [msg, setMsg] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )
  const show = (m: string) => {
    setMsg(m)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setMsg(null), 2200)
  }
  const node = msg ? (
    <div className="rd-toast" role="status">
      {msg}
    </div>
  ) : null
  return [show, node]
}
