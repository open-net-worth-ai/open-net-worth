// The searchable chooser used by every long list in first run — currencies, models.
//
// A MODAL rather than an inline dropdown. Inline, the control had to open a popup over running
// text, and swapping a value line for a select changed the row's height the moment you tapped
// Change — the page moved under the pointer. A modal has neither problem, and a list that can
// run to hundreds of entries (OpenRouter's models) gets the room to be SEARCHED rather than
// scrolled past.
//
// It needs no surface override: this screen is where the app's modal ground was found wrong (a
// blue-tinted card over plain --bg) and the fix went into the `modal-bg` token, so every dialog
// in the app now sits on the page's own ground.

import {
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Modal, Text, TextField } from '../ui'
import { Check } from '../../ui/icons'

export interface PickOption {
  value: string
  /** The short, monospaced half — a currency code, a model id. */
  code: string
  /** The human half, if there is one. */
  name?: string
}

export interface PickDialogProps {
  title: string
  subtitle?: string
  searchPlaceholder: string
  options: PickOption[]
  value: string
  onPick: (value: string) => void
  onClose: () => void
  /** Shown when a search matches nothing; `{q}` is substituted. */
  emptyLabel?: string
}

export function PickDialog({
  title,
  subtitle,
  searchPlaceholder,
  options,
  value,
  onPick,
  onClose,
  emptyLabel = 'Nothing matches “{q}”.',
}: PickDialogProps) {
  const [q, setQ] = useState('')
  const currentRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  // Which row the keyboard is on. A list of 419 is unusable if the only way to reach a row is
  // the pointer — you type to narrow, then arrow to the one you want without leaving the
  // field. Kept separate from `value`: highlighting is not choosing.
  const [active, setActive] = useState(0)

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return options
    return options.filter(
      (o) => o.code.toLowerCase().includes(needle) || (o.name || '').toLowerCase().includes(needle),
    )
  }, [q, options])

  // Typing re-orders what is in front of you, so the highlight returns to the top rather
  // than pointing at whatever happens to still be at its old index.
  useEffect(() => {
    setActive(0)
  }, [q])

  // Keep the highlighted row visible as it moves past the fold.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-active="true"]')
    // Guarded: not every environment implements it (jsdom does not), and a missing scroll is
    // never worth throwing over.
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [active])

  function onKeyDown(e: ReactKeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(i + 1, matches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const pick = matches[active]
      if (pick) onPick(pick.value)
    }
  }

  // Open ON the current value. These lists are long and ordered by something other than
  // relevance, so without this you land at the top and hunt for the answer the row already
  // showed you.
  useEffect(() => {
    currentRef.current?.scrollIntoView?.({ block: 'center' })
  }, [])

  return (
    <Modal open onClose={onClose} title={title} subtitle={subtitle} size="md">
      <div className="firstrun-pick">
        <TextField
          id="fr-pick-search"
          value={q}
          autoFocus
          spellCheck={false}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />

        <div className="firstrun-pick__list" role="listbox" aria-label={title} ref={listRef}>
          {matches.map((o, i) => (
            <button
              key={o.value}
              ref={o.value === value ? currentRef : undefined}
              type="button"
              role="option"
              aria-selected={o.value === value}
              data-active={i === active}
              className={`firstrun-pick__row${o.value === value ? ' is-current' : ''}${
                i === active ? ' is-active' : ''
              }`}
              onMouseEnter={() => setActive(i)}
              onClick={() => onPick(o.value)}
            >
              <span className="firstrun-pick__code">{o.code}</span>
              {o.name && <span className="firstrun-pick__name">{o.name}</span>}
              {o.value === value && (
                <span className="firstrun-pick__tick" aria-hidden="true">
                  <Check size={16} />
                </span>
              )}
            </button>
          ))}
          {matches.length === 0 && (
            <Text as="p" size="xs" tone="faint" className="firstrun-pick__empty">
              {emptyLabel.replace('{q}', q)}
            </Text>
          )}
        </div>
      </div>
    </Modal>
  )
}
