import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button } from '../ui'
import { Plus } from '../../ui/icons'

interface CreateSkillMenuProps {
  onCreateWithLucius: () => void
  onWriteInstructions: () => void
  onUpload: () => void
  onBrowseStarters: () => void
}

/**
 * "+ New skill" dropdown menu in the PageHeader.
 * Uses the KebabMenu outside-click / Escape pattern but rendered as a full
 * dropdown (not a ⋯ trigger) — per spec: NOT a new ui/ primitive, just a
 * positioned panel with outside-click + Escape.
 *
 * Items:
 *   "Create with Lucius"        — runs the skill-creator skill in chat
 *   "Write skill instructions"  — opens SkillEditor (prompt authoring)
 *   "Upload a skill"            — opens UploadSkillModal
 *   "Browse starter skills"     — opens BrowseStartersModal
 *
 * MUST say "Lucius", never "Claude".
 */
export function CreateSkillMenu({
  onCreateWithLucius,
  onWriteInstructions,
  onUpload,
  onBrowseStarters,
}: CreateSkillMenuProps) {
  const [open, setOpen] = useState(false)
  const [dropUp, setDropUp] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  // Flip the menu above the trigger when it would clip the viewport bottom.
  useLayoutEffect(() => {
    if (!open || !wrapRef.current || !menuRef.current) return
    const trigger = wrapRef.current.getBoundingClientRect()
    const menuH = menuRef.current.offsetHeight
    const spaceBelow = window.innerHeight - trigger.bottom
    setDropUp(spaceBelow < menuH + 8 && trigger.top > menuH + 8)
  }, [open])

  // Outside-click + Escape to close.
  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  function pick(fn: () => void) {
    setOpen(false)
    fn()
  }

  const items: { label: string; sub: string; onClick: () => void }[] = [
    {
      label: 'Create with Lucius',
      sub: 'Chat with Lucius to design a new skill',
      onClick: () => pick(onCreateWithLucius),
    },
    {
      label: 'Write skill instructions',
      sub: 'Author the name, body, and tools yourself',
      onClick: () => pick(onWriteInstructions),
    },
    {
      label: 'Upload a skill',
      sub: 'Install a .zip bundle from disk',
      onClick: () => pick(onUpload),
    },
    {
      label: 'Browse starter skills',
      sub: 'Add a built-in starter to your library',
      onClick: () => pick(onBrowseStarters),
    },
  ]

  return (
    <div className="create-menu-wrap" ref={wrapRef}>
      <Button
        variant="primary"
        size="sm"
        iconLeft={<Plus size={14} aria-hidden="true" />}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        New skill
      </Button>

      {open && (
        <div
          ref={menuRef}
          className={`create-menu-dropdown${dropUp ? ' create-menu-dropdown--up' : ''}`}
          role="menu"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className="create-menu-item"
              onClick={item.onClick}
            >
              <span className="create-menu-item__label">{item.label}</span>
              <span className="create-menu-item__subtitle">{item.sub}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
