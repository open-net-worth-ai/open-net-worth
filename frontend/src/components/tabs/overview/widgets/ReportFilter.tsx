// The filter on a Reports widget's own header: a pill naming what the widget covers ("3 months
// · Dining · #travel") that opens a popover with Period, Categories and Tags. A choice applies
// as it is made and is saved with the widget; the settings panel shows the same controls
// (ReportFilterControls). The popover portals to <body> (like ChartToggleMenu): the trigger sits inside a
// clipped, scrolling card. On a phone the same controls open in a bottom sheet instead.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from '../../../../ui/icons'
import { BottomSheet } from '../../../ui/BottomSheet'
import { Checkbox } from '../../../ui'
import { fMonth } from '../../../../utils/format'
import { useIsMobile } from '../../../../hooks/useIsMobile'
import { useCategories } from '../../../../hooks/serverState'
import { useTransactionTags } from '../../accounts/tagOptions'
import { FilterCombo } from '../../accounts/pickers'
import type { WidgetDefinition, WidgetInstance } from '../types'
import { CUSTOM_PERIOD, reportHasFilters, reportScope } from './reportScope'

const POPOVER_W = 320
/** A period-only widget's menu: a menu's width — wider only while Custom shows From / To. */
const MENU_W = 176
const MENU_CUSTOM_W = 248

interface Props {
  instance: WidgetInstance
  def: WidgetDefinition
  onPatch: (patch: Partial<WidgetInstance>) => void
  /** What the period resolves to right now ("Oct", "Sep – Nov") — shown on the pill. */
  label: string
}

export function ReportFilter({ instance, def, onPatch, label }: Props) {
  const sc = reportScope(instance, def)
  const popWidth = reportHasFilters(def)
    ? POPOVER_W
    : sc.periodLabel === CUSTOM_PERIOD
      ? MENU_CUSTOM_W
      : MENU_W
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
    const w = popWidth
    const left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8)
    setPos({ top: r.bottom + 6, left: Math.max(8, left), maxH: window.innerHeight - r.bottom - 20 })
  }, [open, isMobile, popWidth])

  useEffect(() => {
    if (!open || isMobile) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (wrapRef.current?.contains(t) || popRef.current?.contains(t)) return
      // The Period menu portals to <body> too: choosing from it isn't a click outside.
      if (t instanceof Element && t.closest('.kebab-menu--portal')) return
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
  // A widget with a period alone opens a plain menu of periods; one that also filters by
  // category or tag opens the filter box.
  const body = reportHasFilters(def) ? (
    <ReportFilterControls instance={instance} def={def} onPatch={onPatch} />
  ) : (
    <ReportPeriodControl instance={instance} def={def} onPatch={onPatch} list />
  )

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
            className={
              'kebab-menu kebab-menu--portal ov-rf-pop' +
              (reportHasFilters(def) ? '' : ' ov-rf-pop--menu')
            }
            role="dialog"
            aria-label="Filter this widget"
            ref={popRef}
            style={
              pos
                ? {
                    top: pos.top,
                    left: pos.left,
                    width: popWidth,
                    maxHeight: pos.maxH,
                    pointerEvents: 'auto',
                  }
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

/** The Period control on its own — a Savings rate's whole filter. `list` draws it as a
 *  menu's rows (its header button); otherwise it is the field the editor shows. */
export function ReportPeriodControl({
  instance,
  def,
  onPatch,
  list = false,
}: {
  instance: WidgetInstance
  def: WidgetDefinition
  onPatch: (patch: Partial<WidgetInstance>) => void
  list?: boolean
}) {
  const sc = reportScope(instance, def)
  return (
    <PeriodSection
      def={def}
      value={sc.periodLabel}
      custom={sc.custom}
      config={instance.config ?? {}}
      onPatch={(next) => onPatch({ config: { ...instance.config, ...next } })}
      list={list}
    />
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
      <PeriodSection
        def={def}
        value={sc.periodLabel}
        custom={sc.custom}
        config={instance.config ?? {}}
        onPatch={patch}
      />
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

/** YYYY-MM for the month `back` months before this one. */
function monthsAgo(back: number): string {
  const d = new Date()
  const i = d.getFullYear() * 12 + d.getMonth() - back
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`
}

function PeriodSection({
  def,
  value,
  custom,
  config,
  onPatch,
  list = false,
}: {
  def: WidgetDefinition
  value: string
  custom: { from: string; to: string } | null
  config: Record<string, unknown>
  onPatch: (next: Record<string, unknown>) => void
  /** The options as a menu's rows (the header button of a widget with a period alone). */
  list?: boolean
}) {
  const options = def.settings?.find((s) => s.key === 'period')?.options ?? []
  const from = typeof config.periodFrom === 'string' ? config.periodFrom : ''
  const to = typeof config.periodTo === 'string' ? config.periodTo : ''
  const pick = (o: string) =>
    o === CUSTOM_PERIOD && !custom
      ? // First time on Custom: the last three full months, to change from there.
        onPatch({ period: o, periodFrom: from || monthsAgo(3), periodTo: to || monthsAgo(1) })
      : onPatch({ period: o })
  const range =
    value === CUSTOM_PERIOD ? (
      <CustomRange
        from={from}
        to={to}
        onChange={(f, t) => onPatch({ periodFrom: f, periodTo: t })}
      />
    ) : null
  if (list) {
    return (
      <>
        <div role="menu" aria-label="Period">
          {options.map((o) => (
            <button
              key={o}
              type="button"
              role="menuitemradio"
              aria-checked={value === o}
              className={
                'kebab-item ov-charttoggle__item' +
                (value === o ? ' ov-charttoggle__item--active' : '')
              }
              onClick={() => pick(o)}
            >
              <span>{o}</span>
              {value === o && <Check size={13} strokeWidth={2.4} aria-hidden="true" />}
            </button>
          ))}
        </div>
        {range}
      </>
    )
  }
  return (
    <section className="ov-rf-sec">
      <h4 className="ov-rf-h">Period</h4>
      <FilterCombo
        value={value}
        options={options.map((o) => ({ value: o, label: o }))}
        anyLabel="Period"
        clearable={false}
        onChange={pick}
      />
      {range}
    </section>
  )
}

const MONTH_OPTS = Array.from({ length: 12 }, (_, i) => ({
  value: String(i + 1).padStart(2, '0'),
  label: fMonth(`2000-${String(i + 1).padStart(2, '0')}`, 'short', 'never'),
}))

/** A custom period's From and To, drawn as the Accounts register's own date range: a Month and
 *  a Year picker per row (FilterCombo; a native select on a phone, as there). */
function CustomRange({
  from,
  to,
  onChange,
}: {
  from: string
  to: string
  onChange: (from: string, to: string) => void
}) {
  const isMobile = useIsMobile()
  const thisYear = new Date().getFullYear()
  const years = new Set(Array.from({ length: 11 }, (_, i) => thisYear - i))
  for (const m of [from, to]) if (m) years.add(Number(m.slice(0, 4)))
  const yearOpts = [...years]
    .sort((x, y) => y - x)
    .map((y) => ({ value: String(y), label: String(y) }))
  // Moving one end past the other moves the other with it, so the range never runs backwards.
  const set = (end: 'from' | 'to', part: 'm' | 'y', v: string) => {
    const cur = end === 'from' ? from : to
    const next = part === 'y' ? `${v}-${cur.slice(5, 7)}` : `${cur.slice(0, 4)}-${v}`
    if (end === 'from') onChange(next, next > to ? next : to)
    else onChange(next < from ? next : from, next)
  }
  const pickerFor = (end: 'from' | 'to', part: 'm' | 'y') => {
    const cur = end === 'from' ? from : to
    const value = part === 'm' ? cur.slice(5, 7) : cur.slice(0, 4)
    const opts = part === 'm' ? MONTH_OPTS : yearOpts
    const label = `${end === 'from' ? 'From' : 'To'} ${part === 'm' ? 'month' : 'year'}`
    return isMobile ? (
      <select
        className="acct-advselect"
        aria-label={label}
        value={value}
        onChange={(e) => set(end, part, e.target.value)}
      >
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    ) : (
      <FilterCombo
        value={value}
        options={opts}
        anyLabel={label}
        clearable={false}
        popW={part === 'm' ? 148 : 120}
        onChange={(v) => set(end, part, v)}
      />
    )
  }
  return (
    <div className="ov-rf-range">
      <div className="acct-advrow">
        <span className="acct-advlbl">From</span>
        {pickerFor('from', 'm')}
        {pickerFor('from', 'y')}
      </div>
      <div className="acct-advrow">
        <span className="acct-advlbl">To</span>
        {pickerFor('to', 'm')}
        {pickerFor('to', 'y')}
      </div>
    </div>
  )
}

/** A searchable list of checkboxes — Categories and Tags alike. */
function CheckSection({
  title,
  noun,
  value,
  options,
  onChange,
  empty,
  hint,
}: {
  title: string
  noun: string
  value: string[]
  options: { id: string; label: string }[]
  onChange: (next: string[]) => void
  empty?: string
  hint?: string
}) {
  const [q, setQ] = useState('')
  const chosen = new Set(value)
  const needle = q.trim().toLowerCase()
  // The ones chosen when the filter opened come first, so they never hide below the fold —
  // fixed for as long as it stays open, so a row never jumps under a finger mid-tap.
  const [pinned] = useState(() => new Set(value))
  const matches = (c: { label: string }) => !needle || c.label.toLowerCase().includes(needle)
  const shown = [
    ...options.filter((c) => pinned.has(c.id)),
    ...options.filter((c) => !pinned.has(c.id) && matches(c)),
  ]
  const toggle = (id: string) =>
    onChange(chosen.has(id) ? value.filter((v) => v !== id) : [...value, id])
  return (
    <section className="ov-rf-sec">
      <h4 className="ov-rf-h">
        {title}
        <span className="ov-rf-h-note">{value.length ? `${value.length} chosen` : 'All'}</span>
      </h4>
      {options.length === 0 && empty ? (
        <span className="ov-fs-ctl-hint">{empty}</span>
      ) : (
        <>
          <input
            className="ov-fs-input"
            placeholder={`Search ${noun}`}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label={`Search ${noun}`}
          />
          <div className="ov-rf-list" role="group" aria-label={title}>
            {shown.length === 0 ? (
              <span className="ov-fs-ctl-hint">
                No {noun.replace(/s$/, '').replace(/ie$/, 'y')} matches.
              </span>
            ) : (
              shown.map((c) => (
                <Checkbox
                  key={c.id}
                  className="ov-rf-check"
                  label={c.label}
                  checked={chosen.has(c.id)}
                  onChange={() => toggle(c.id)}
                />
              ))
            )}
          </div>
        </>
      )}
      {hint && <span className="ov-fs-ctl-hint">{hint}</span>}
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
  const list = options
    .filter((c) => c.id !== 'split')
    .map((c) => ({ id: c.id, label: c.label || c.id }))
    .sort((a, b) => a.label.localeCompare(b.label))
  return (
    <CheckSection
      title="Categories"
      noun="categories"
      value={value}
      options={list}
      onChange={onChange}
    />
  )
}

function TagSection({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const tags = useTransactionTags()
  const known = new Set(tags)
  // A chosen tag no transaction carries any more stays listed, so it can still be removed.
  const all = [...value.filter((t) => !known.has(t)), ...tags].map((t) => ({
    id: t,
    label: `#${t}`,
  }))
  return (
    <CheckSection
      title="Tags"
      noun="tags"
      value={value}
      options={all}
      onChange={onChange}
      empty="No tags yet. Add one to a transaction in Accounts to filter by it here."
      hint={
        value.length
          ? `Counts only transactions tagged ${value.map((t) => '#' + t).join(' or ')}.`
          : undefined
      }
    />
  )
}
