// The filter on a Reports widget's own header: a pill naming what the widget covers ("3 months
// · Dining · #travel") that opens a popover with Period, Categories and Tags. A choice applies
// as it is made and is saved with the widget; the settings panel shows the same controls
// (ReportFilterControls). The popover portals to <body> (like ChartToggleMenu): the trigger sits inside a
// clipped, scrolling card. On a phone the same controls open in a bottom sheet instead.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from '../../../../ui/icons'
import { BottomSheet } from '../../../ui/BottomSheet'
import { useIsMobile } from '../../../../hooks/useIsMobile'
import { useCategories } from '../../../../hooks/serverState'
import { useTransactionTags } from '../../accounts/tagOptions'
import type { WidgetDefinition, WidgetInstance } from '../types'
import { reportScope } from './reportScope'

/** How many tags show before "N more" — the most used come first. */
const TAG_CHIPS_SHOWN = 12
const POPOVER_W = 320

interface Props {
  instance: WidgetInstance
  def: WidgetDefinition
  onPatch: (patch: Partial<WidgetInstance>) => void
  /** What the period resolves to right now ("Oct", "Sep – Nov") — shown on the pill. */
  label: string
}

export function ReportFilter({ instance, def, onPatch, label }: Props) {
  const sc = reportScope(instance, def)
  // A phone gets a bottom sheet with thumb-sized rows; a pointer gets the anchored popover.
  const isMobile = useIsMobile()
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number; maxH: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const { data: cats } = useCategories()
  const catLabel = useMemo(
    () => new Map((Array.isArray(cats) ? cats : []).map((c) => [c.id, c.label || c.id])),
    [cats],
  )

  useLayoutEffect(() => {
    if (!open || isMobile || !wrapRef.current) return
    const r = wrapRef.current.getBoundingClientRect()
    const left = Math.min(Math.max(8, r.right - POPOVER_W), window.innerWidth - POPOVER_W - 8)
    setPos({ top: r.bottom + 6, left: Math.max(8, left), maxH: window.innerHeight - r.bottom - 20 })
  }, [open, isMobile])

  useEffect(() => {
    if (!open || isMobile) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || popRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, isMobile])

  const catText =
    sc.categories.length === 0
      ? ''
      : sc.categories.length === 1
        ? (catLabel.get(sc.categories[0]!) ?? '1 category')
        : `${sc.categories.length} categories`
  const tagText =
    sc.tags.length === 0 ? '' : sc.tags.length === 1 ? `#${sc.tags[0]}` : `${sc.tags.length} tags`
  const filtered = sc.categories.length > 0 || sc.tags.length > 0
  const summary = [label, catText, tagText].filter(Boolean).join(' · ')
  const body = <ReportFilterControls instance={instance} def={def} onPatch={onPatch} />

  return (
    <div className="kebab-wrap ov-rf-wrap" ref={wrapRef}>
      <button
        type="button"
        className={'ov-charttoggle__trigger ov-rf-trigger' + (filtered ? ' is-filtered' : '')}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Filter: ${summary}`}
        title={summary}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((o) => !o)
        }}
      >
        <span className="ov-rf-label">{summary}</span>
        <ChevronDown size={11} strokeWidth={2.2} aria-hidden="true" />
      </button>
      {open && isMobile && (
        <BottomSheet
          title={`Filter ${instance.title || def.title || 'widget'}`}
          onClose={() => setOpen(false)}
          footer={
            <button type="button" className="ov-rf-done" onClick={() => setOpen(false)}>
              Done
            </button>
          }
        >
          <div className="ov-rf-sheet">{body}</div>
        </BottomSheet>
      )}
      {open &&
        !isMobile &&
        createPortal(
          <div
            className="kebab-menu kebab-menu--portal ov-rf-pop"
            role="dialog"
            aria-label="Filter this widget"
            ref={popRef}
            style={
              pos
                ? { top: pos.top, left: pos.left, width: POPOVER_W, maxHeight: pos.maxH }
                : { visibility: 'hidden' }
            }
            onClick={(e) => e.stopPropagation()}
          >
            {body}
          </div>,
          document.body,
        )}
    </div>
  )
}

/** Period, Categories and Tags — the same controls in the header popover and in the widget's
 *  settings panel, so a filter can be set from either place. */
export function ReportFilterControls({
  instance,
  def,
  onPatch,
}: {
  instance: WidgetInstance
  def: WidgetDefinition
  onPatch: (patch: Partial<WidgetInstance>) => void
}) {
  const sc = reportScope(instance, def)
  const { data: cats } = useCategories()
  // A widget offers only the filters its definition declares (Savings rate: a period alone).
  const has = (key: string) => !!def.settings?.some((s) => s.key === key)
  const patch = (next: Record<string, unknown>) =>
    onPatch({ config: { ...instance.config, ...next } })
  const filtered = sc.categories.length > 0 || sc.tags.length > 0
  return (
    <>
      <PeriodSection def={def} value={sc.periodLabel} onPick={(p) => patch({ period: p })} />
      {has('categories') && (
        <CategorySection
          value={sc.categories}
          options={Array.isArray(cats) ? cats : []}
          onChange={(next) => patch({ categories: next })}
        />
      )}
      {has('tags') && <TagSection value={sc.tags} onChange={(next) => patch({ tags: next })} />}
      {filtered && (
        <button
          type="button"
          className="ov-rf-reset"
          onClick={() => patch({ categories: [], tags: [] })}
        >
          Clear categories and tags
        </button>
      )}
    </>
  )
}

function PeriodSection({
  def,
  value,
  onPick,
}: {
  def: WidgetDefinition
  value: string
  onPick: (label: string) => void
}) {
  const options = def.settings?.find((s) => s.key === 'period')?.options ?? []
  return (
    <section className="ov-rf-sec">
      <h4 className="ov-rf-h">Period</h4>
      <div className="ov-fs-seg" role="group" aria-label="Period">
        {options.map((o) => (
          <button
            key={o}
            type="button"
            className={'ov-fs-chip' + (value === o ? ' on' : '')}
            aria-pressed={value === o}
            onClick={() => onPick(o)}
          >
            {o}
          </button>
        ))}
      </div>
    </section>
  )
}

function CategorySection({
  value,
  options,
  onChange,
}: {
  value: string[]
  options: { id: string; label?: string }[]
  onChange: (next: string[]) => void
}) {
  const [q, setQ] = useState('')
  const chosen = new Set(value)
  const needle = q.trim().toLowerCase()
  const list = options
    .filter((c) => c.id !== 'split')
    .map((c) => ({ id: c.id, label: c.label || c.id }))
    .sort((a, b) => a.label.localeCompare(b.label))
  // The categories chosen when the filter opened come first, so they never hide below the
  // fold — fixed for as long as it stays open, so a row never jumps under a finger mid-tap.
  const [pinned] = useState(() => new Set(value))
  const matches = (c: { label: string }) => !needle || c.label.toLowerCase().includes(needle)
  const shown = [
    ...list.filter((c) => pinned.has(c.id)),
    ...list.filter((c) => !pinned.has(c.id) && matches(c)),
  ]
  const toggle = (id: string) =>
    onChange(chosen.has(id) ? value.filter((v) => v !== id) : [...value, id])
  return (
    <section className="ov-rf-sec">
      <h4 className="ov-rf-h">
        Categories
        <span className="ov-rf-h-note">{value.length ? `${value.length} chosen` : 'All'}</span>
      </h4>
      <input
        className="ov-fs-input"
        placeholder="Search categories"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Search categories"
      />
      <div className="ov-rf-list" role="group" aria-label="Categories">
        {shown.length === 0 ? (
          <span className="ov-fs-ctl-hint">No category matches.</span>
        ) : (
          shown.map((c) => (
            <label key={c.id} className="ov-rf-check">
              <input type="checkbox" checked={chosen.has(c.id)} onChange={() => toggle(c.id)} />
              <span>{c.label}</span>
            </label>
          ))
        )}
      </div>
    </section>
  )
}

function TagSection({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const tags = useTransactionTags()
  const [showAll, setShowAll] = useState(false)
  const known = new Set(tags)
  // A chosen tag no transaction carries any more stays listed, so it can still be removed.
  const all = [...value.filter((t) => !known.has(t)), ...tags]
  const chosen = new Set(value)
  const shown = showAll
    ? all
    : [...all.slice(0, TAG_CHIPS_SHOWN), ...all.slice(TAG_CHIPS_SHOWN).filter((t) => chosen.has(t))]
  const hidden = all.length - shown.length
  const toggle = (tag: string) =>
    onChange(chosen.has(tag) ? value.filter((t) => t !== tag) : [...value, tag])
  return (
    <section className="ov-rf-sec">
      <h4 className="ov-rf-h">
        Tags
        <span className="ov-rf-h-note">{value.length ? `${value.length} chosen` : 'All'}</span>
      </h4>
      {all.length === 0 ? (
        <span className="ov-fs-ctl-hint">
          No tags yet. Add one to a transaction in Accounts to filter by it here.
        </span>
      ) : (
        <div className="ov-fs-seg" role="group" aria-label="Tags">
          {shown.map((tag) => (
            <button
              key={tag}
              type="button"
              className={'ov-fs-chip' + (chosen.has(tag) ? ' on' : '')}
              aria-pressed={chosen.has(tag)}
              onClick={() => toggle(tag)}
            >
              #{tag}
            </button>
          ))}
          {(hidden > 0 || showAll) && all.length > TAG_CHIPS_SHOWN && (
            <button
              type="button"
              className="ov-fs-chip ov-fs-chip--more"
              onClick={() => setShowAll((v) => !v)}
            >
              {showAll ? 'Fewer' : `${hidden} more`}
            </button>
          )}
        </div>
      )}
      <span className="ov-fs-ctl-hint">
        {value.length === 0
          ? 'Pick tags to count only transactions carrying any of them.'
          : `Counts only transactions tagged ${value.map((t) => '#' + t).join(' or ')}.`}
      </span>
    </section>
  )
}
