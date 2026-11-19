// The permission ladder's two controls, on their own: the three-way switch for ONE tool and
// the "set them all" menu for a GROUP. The ladder (PermissionLadder) is built from them, and so
// is the REST connector's endpoint list, where each endpoint is a tool and its permission sits
// on its own row. One markup for both, so the two can never drift apart.

import { createElement, useEffect, useRef, useState, type JSX } from 'react'
import { Btn } from './primitives'
import { icon, Icons } from './icons'
import { LEVELS, TOOL_LEVELS, type GroupPermissionLevel, type PermissionLevel } from './model'

export interface LevelSegProps {
  /** What this switch is for, read out with each level: `${label}: Always allow`. */
  label: string
  value: PermissionLevel
  onChange: (level: PermissionLevel) => void
  /** How each level is named and drawn. */
  levels?: typeof LEVELS
}

/** One tool's level: Always allow / Needs approval / Blocked, as three icon buttons. */
export function LevelSeg({ label, value, onChange, levels = LEVELS }: LevelSegProps): JSX.Element {
  return (
    <span className="cn-permseg">
      {TOOL_LEVELS.map((lv) => {
        const LevelIcon = icon(levels[lv].icon)
        const on = value === lv
        return (
          <button
            key={lv}
            type="button"
            className={'cn-permbtn' + (on ? ` on ${levels[lv].tone}` : '')}
            title={levels[lv].label}
            aria-label={`${label}: ${levels[lv].label}`}
            aria-pressed={on}
            onClick={() => onChange(lv)}
          >
            <LevelIcon />
          </button>
        )
      })}
    </span>
  )
}

export interface GroupLevelMenuProps {
  /** What the group's tools are at now: one level, or `custom` when they differ. */
  value: GroupPermissionLevel
  /** Stamp every tool in the group. Never called for `custom`, which is in the menu for
   *  legibility only. */
  onPick: (level: PermissionLevel) => void
  levels?: typeof LEVELS
}

/** A group's level, and the menu that stamps one level onto every tool in it. */
export function GroupLevelMenu({
  value,
  onPick,
  levels = LEVELS,
}: GroupLevelMenuProps): JSX.Element {
  const [open, setOpen] = useState(false)
  // A popover, so a click anywhere else dismisses it — matching `Kebab`.
  const root = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (root.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <span className="rd-kebab" ref={root}>
      <Btn size="xs" aria-expanded={open} onClick={() => setOpen(!open)} style={{ fontSize: 12.5 }}>
        {/* Looked up, not declared: `icon` returns one of a fixed set of components. */}
        {createElement(icon(levels[value].icon))} {levels[value].label}{' '}
        <span style={{ display: 'inline-flex', width: 12, height: 12 }}>
          <Icons.chev />
        </span>
      </Btn>
      {open && (
        <span className="rd-kebab-pop">
          {(Object.keys(levels) as (keyof typeof levels)[]).map((lv) => {
            const ItemIcon = icon(levels[lv].icon)
            return (
              <button
                key={lv}
                type="button"
                className="rd-kebab-item"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 'var(--text-base)',
                }}
                onClick={() => {
                  if (lv !== 'custom') onPick(lv)
                  setOpen(false)
                }}
              >
                <ItemIcon /> {levels[lv].label}
              </button>
            )
          })}
        </span>
      )}
    </span>
  )
}
