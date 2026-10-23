import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, FastForward, Hand, TriangleAlert, type LucideIcon } from 'lucide-react'

// Tooltip max-width (260px) + its 8px gap + an 8px margin from the window edge.
const TOOLTIP_REACH = 276

export type PermissionMode = 'manual' | 'auto' | 'skip'

interface PermissionModeControlProps {
  value: PermissionMode
  onChange: (value: PermissionMode) => void
  /** What the three choices govern, for the trigger's accessible name. */
  subject?: string
  /** `filled` (default) suits a card or form row; `bare` matches a composer's icon tools. */
  tone?: 'filled' | 'bare'
  /** Read-only: shows what is in force without offering to change it. Used where something
   *  else owns the setting (an Automation run's mode belongs to the Automation). */
  disabled?: boolean
  /** Why it is read-only — becomes the trigger's tooltip. */
  disabledReason?: string
}

interface ModeOption {
  value: PermissionMode
  label: string
  shortLabel: string
  Icon: LucideIcon
  description: string
}

const SKIP_WARNING = 'Lucius never pauses, even for unsafe actions.'

const APPROVAL_OPTIONS: readonly ModeOption[] = [
  {
    value: 'manual',
    label: 'Manually approve',
    shortLabel: 'Manual',
    Icon: Hand,
    description: 'Lucius pauses so you can approve each action.',
  },
  {
    value: 'auto',
    label: 'Automatically approve',
    shortLabel: 'Auto',
    Icon: FastForward,
    description: 'Lucius runs on its own and pauses to ask if anything looks unsafe.',
  },
  {
    value: 'skip',
    label: 'Skip all approvals',
    shortLabel: 'Skip',
    Icon: TriangleAlert,
    description: SKIP_WARNING,
  },
]

export function PermissionModeControl({
  value,
  onChange,
  subject = 'Permission mode',
  tone = 'filled',
  disabled = false,
  disabledReason,
}: PermissionModeControlProps) {
  // Unique per instance: this control used to be the Automation editor's alone and could
  // hardcode its ids. As a shared primitive two can share a screen, and duplicate ids would
  // point every trigger's aria-controls at the first menu.
  const idBase = useId()
  // Where to pin a `bare` menu. The composer nests this control inside rounded, clipping
  // containers, so an absolutely-positioned (and later, plain `position: fixed`) menu was
  // cut off by an ancestor's overflow — the top item vanished entirely (owner, 2026-08-27),
  // then the WHOLE menu (`.lx-side-foot` gained its own overflow-y:hidden later, 3d0688e8).
  // Portaled to document.body instead (below) — the menu no longer follows the trigger via
  // CSS, so its coordinates have to be measured. Only `bare` needs this; the Automation
  // editor's card does not clip.
  const [anchor, setAnchor] = useState<{ left: number; bottom: number } | null>(null)

  function measureAnchor() {
    const el = triggerRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    // Viewport coordinates, because the menu is `position: fixed`. `bottom` is measured up
    // from the viewport floor so the menu sits just above the trigger, matching the
    // absolutely-positioned variant's `bottom: calc(100% + 7px)`.
    return { left: r.left - 6, bottom: window.innerHeight - r.top + 7 }
  }
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const focusOnOpen = useRef<'selected' | 'last' | null>(null)
  // The option descriptions open to the menu's right. A composer pinned to the window's right
  // edge (the Lucius side panel) has no room there, so they flip to the left instead of
  // running off-screen — measured, because only the rendered menu knows where it landed.
  const [tipsLeft, setTipsLeft] = useState(false)
  const active = APPROVAL_OPTIONS.find((option) => option.value === value) ?? APPROVAL_OPTIONS[0]!
  const ActiveIcon = active.Icon

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      // `menuRef` is checked separately from `rootRef`: a portaled `bare` menu (below) is no
      // longer a DOM descendant of `rootRef`, so a click inside it would otherwise read as
      // "outside" and close itself on its own opening click's bubble.
      if (rootRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  useLayoutEffect(() => {
    if (!open || !menuRef.current) return
    const right = menuRef.current.getBoundingClientRect().right
    setTipsLeft(right + TOOLTIP_REACH > window.innerWidth)
  }, [open, anchor])

  useEffect(() => {
    if (!open || !focusOnOpen.current) return
    const items = menuItems(menuRef.current)
    const target =
      focusOnOpen.current === 'last'
        ? items.at(-1)
        : (items.find((item) => item.getAttribute('aria-checked') === 'true') ?? items[0])
    focusOnOpen.current = null
    target?.focus()
  }, [open])

  const close = (returnFocus = false) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }

  const choose = (policy: PermissionMode) => {
    onChange(policy)
    close(true)
  }

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = menuItems(menuRef.current)
    const index = items.indexOf(document.activeElement as HTMLButtonElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      close(true)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      items[(index + 1 + items.length) % items.length]?.focus()
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      items[(index - 1 + items.length) % items.length]?.focus()
    } else if (event.key === 'Home') {
      event.preventDefault()
      items[0]?.focus()
    } else if (event.key === 'End') {
      event.preventDefault()
      items.at(-1)?.focus()
    }
  }

  // Only `bare` (the composer) needs the portal — it's the only tone nested inside a
  // clipping ancestor (.lx-side-foot). The Automation editor's card doesn't clip, so
  // `filled` stays inline, position:absolute, exactly as before.
  const menu = open && (
    <div
      ref={menuRef}
      id={`${idBase}-menu`}
      className={`pm-mode-menu${tone === 'bare' ? ' pm-mode-menu--fixed' : ''}${tipsLeft ? ' pm-mode-menu--tips-left' : ''}`}
      role="menu"
      aria-label={subject}
      data-portal-layer={tone === 'bare' ? true : undefined}
      onKeyDown={onMenuKeyDown}
      style={
        tone === 'bare' && anchor
          ? { left: `${anchor.left}px`, bottom: `${anchor.bottom}px` }
          : undefined
      }
    >
      {APPROVAL_OPTIONS.map((option) => {
        const OptionIcon = option.Icon
        const selected = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="menuitemradio"
            aria-checked={selected}
            aria-describedby={option.value === 'skip' ? `${idBase}-skip-tooltip` : undefined}
            className="pm-mode-option pm-mode-tooltip-target"
            data-tooltip={option.description}
            onClick={() => choose(option.value)}
          >
            <OptionIcon className="pm-mode-icon" size={17} strokeWidth={1.7} aria-hidden="true" />
            <span>{option.label}</span>
            {selected && (
              <Check className="pm-mode-check" size={17} strokeWidth={2} aria-hidden="true" />
            )}
          </button>
        )
      })}
    </div>
  )

  return (
    <div
      ref={rootRef}
      className={`pm-mode${tone === 'bare' ? ' pm-mode--bare' : ''}${open ? ' is-open' : ''}`}
    >
      <button
        ref={triggerRef}
        id={`${idBase}-trigger`}
        type="button"
        className="pm-mode-trigger pm-mode-tooltip-target"
        aria-label={`${subject}: ${active.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={`${idBase}-menu`}
        aria-describedby={value === 'skip' ? `${idBase}-skip-tooltip` : undefined}
        data-tooltip={disabled ? disabledReason : value === 'skip' ? SKIP_WARNING : undefined}
        disabled={disabled}
        onClick={() => {
          if (disabled) return
          setOpen((current) => {
            if (!current) setAnchor(measureAnchor())
            return !current
          })
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
          event.preventDefault()
          focusOnOpen.current = event.key === 'ArrowUp' ? 'last' : 'selected'
          setAnchor(measureAnchor())
          setOpen(true)
        }}
      >
        <ActiveIcon className="pm-mode-icon" size={16} strokeWidth={1.8} aria-hidden="true" />
        <span>{active.shortLabel}</span>
        <ChevronDown className="pm-mode-caret" size={13} strokeWidth={1.8} aria-hidden="true" />
      </button>

      {menu && (tone === 'bare' ? createPortal(menu, document.body) : menu)}

      {(value === 'skip' || open) && (
        <span id={`${idBase}-skip-tooltip`} role="tooltip" className="ui-visually-hidden">
          {SKIP_WARNING}
        </span>
      )}
    </div>
  )
}

function menuItems(menu: HTMLDivElement | null): HTMLButtonElement[] {
  return Array.from(menu?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [])
}
