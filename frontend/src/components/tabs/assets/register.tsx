// Assets tab register — summary strip, filter bar, card grid, rail. A faithful port of the
// design handoff's register.jsx (design_handoffs/assets_handoff/prototype/assets_tab/
// register.jsx) — same DOM, same `as-*` class vocabulary, same inline styles. Only the data
// seam changed: `window.AS` helpers → assetsData.ts / icons.tsx, `.mask` → the app's `.money`
// privacy convention, and the summary strip / class cards are server money math (`summary`
// prop) instead of the prototype's client-derived `worthTotals()` / `CLASSES` mock (ADR
// Decision 7). `AssetTable` is NOT ported — the prototype's main.jsx never mounts it.
import { useEffect, useRef } from 'react'
import type { ChangeEvent, CSSProperties } from 'react'
import type { AssetCategory, AssetLoan, AssetsSummary, OwnedAsset } from '../../../types/assets'
import type { ClassCardModel } from './assetsData'
import { catOf, compositionView, fmt, fmtOrDash, gainPctOf, pct, sharePct } from './assetsData'
import { Icons } from './icons'
import { photoUrl } from './api'
import { EntityRow } from '../../../lucius/EntityRow'

// Field mapping shared by every EntityRow wrapping an OwnedAsset — mirrors the backend's
// "asset" mention descriptor (backend/services/mentions/descriptors/new_types.py) verbatim so
// filterByDescriptor never drops a field the popover expects.
export function assetFields(a: OwnedAsset): Record<string, string | number | null | undefined> {
  return {
    name: a.name,
    category: a.category,
    value: a.value,
    valued_on: a.valued_on,
    share_pct: a.share_pct,
    notes: a.notes,
  }
}

// Mirrors the backend's `_asset_sublabel` — "category • valued_on", either side dropped if
// empty (Python's `.strip(" •")` equivalent).
export function assetSublabel(a: OwnedAsset): string {
  return [a.category, a.valued_on].filter(Boolean).join(' • ')
}

export interface SummaryStripProps {
  summary: AssetsSummary
}

export function SummaryStrip({ summary }: SummaryStripProps) {
  return (
    <div className="as-sum">
      <div>
        <div className="as-sum-k">Everything you own</div>
        <div className="as-sum-v money">{fmt(summary.own)}</div>
      </div>
      <div>
        <div className="as-sum-k">What you owe</div>
        <div className="as-sum-v money">{'−' + fmt(summary.owe)}</div>
      </div>
      <div className="hi">
        <div className="as-sum-k">Net worth</div>
        <div className="as-sum-v money">{fmt(summary.net_worth)}</div>
      </div>
      <div>
        <div className="as-sum-k">Unrealized</div>
        <div className={'as-sum-v money ' + (summary.unrealized >= 0 ? 'pos' : 'neg')}>
          {(summary.unrealized >= 0 ? '+' : '') + fmt(summary.unrealized)}
        </div>
      </div>
    </div>
  )
}

export interface FilterBarProps {
  list: OwnedAsset[]
  cats: AssetCategory[]
  cat: string
  setCat: (cat: string) => void
}

// Categories only. Cash, Investments and Retirement are one card each, always first on All —
// a chip for a single card pushed the category chips off a phone's row (owner, 2026-10-03).
export function FilterBar({ list, cats, cat, setCat }: FilterBarProps) {
  const counts: Record<string, number> = {}
  list.forEach((a) => {
    counts[a.category] = (counts[a.category] || 0) + 1
  })
  // The selected chip is always in view — a category opened from its stack (or a link) lands
  // far along a row a phone scrolls sideways (owner, 2026-09-28). Scrolls the ROW only:
  // scrollIntoView also moves the page, and on desktop, where the bar scrolls away with it,
  // every return from an asset pulled the window up to the bar.
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const row = wrap.current
    const chip = row?.querySelector<HTMLElement>('.as-chip.on')
    if (!row || !chip) return
    const r = row.getBoundingClientRect()
    const c = chip.getBoundingClientRect()
    const left = row.scrollLeft + (c.left - r.left) - (r.width - c.width) / 2
    row.scrollTo?.({ left, behavior: 'smooth' })
  }, [cat])
  return (
    <div className="as-bar">
      <div className="as-bar-wrap" ref={wrap}>
        <button className={'as-chip' + (cat === 'all' ? ' on' : '')} onClick={() => setCat('all')}>
          All <em>{list.length}</em>
        </button>
        {cats
          .filter((c) => counts[c.key])
          .map((c) => (
            <button
              key={c.key}
              className={'as-chip' + (cat === c.key ? ' on' : '')}
              onClick={() => setCat(c.key)}
            >
              <i style={{ background: c.color }} />
              {c.plural} <em>{counts[c.key]}</em>
            </button>
          ))}
        {/* Trailing-edge padding on a scrollable container isn't reliably honored as real
            scroll space (a well-known Chrome/WebKit quirk) — .as-bar-wrap's own
            padding-right gets ignored once content overflows, so the last chip sits flush
            against the screen edge with no gutter. A real (if invisible) flex child forces
            genuine scrollable space after it instead. */}
        <span aria-hidden="true" style={{ flex: 'none', width: 16 }} />
      </div>
    </div>
  )
}

export interface ThumbProps {
  a: OwnedAsset
  cats: AssetCategory[]
  size?: number
  editable?: boolean
  onPhoto?: (file: File) => void
}

export function Thumb({ a, cats, size, editable, onPhoto }: ThumbProps) {
  const c = catOf(cats, a.category)
  const Ic = Icons[a.category] ?? Icons.other!
  const s = size || 46
  const fileRef = useRef<HTMLInputElement>(null)
  const photoSrc = a.photo_ref ? photoUrl(a.photo_ref) : null
  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    e.stopPropagation()
    const file = e.target.files?.[0]
    if (!file) return
    onPhoto?.(file)
  }
  const iconSize = Math.round(s * 0.46)
  const body = photoSrc ? (
    <>
      {Ic({ width: iconSize, height: iconSize })}
      {/* `key={photoSrc}` forces a fresh DOM node whenever the src changes (a new upload,
          a replaced photo) — `onError` below hides a BROKEN image by mutating the node's
          style directly, and nothing ever un-hides it; without a key, React reuses the
          same node across a src change and a photo that previously failed to load (the
          HEIC bug) stays invisible forever after, even once replaced with a working one,
          until a full page reload mounts a clean node. */}
      <img
        key={photoSrc}
        src={photoSrc}
        alt=""
        onError={(e) => {
          e.currentTarget.style.display = 'none'
        }}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          borderRadius: 'inherit',
        }}
      />
    </>
  ) : (
    Ic({ width: iconSize, height: iconSize })
  )
  const wrapStyle: CSSProperties = {
    width: s,
    height: s,
    position: 'relative',
    background: photoSrc ? 'var(--bg-2)' : `color-mix(in srgb, ${c.color} 15%, var(--bg-2))`,
    borderColor: `color-mix(in srgb, ${c.color} 32%, var(--line))`,
    color: c.color,
    overflow: 'hidden',
  }
  if (!editable) {
    return (
      <div className="as-thumb" style={wrapStyle}>
        {body}
      </div>
    )
  }
  return (
    <label
      className="as-thumb editable"
      style={{ ...wrapStyle, cursor: 'pointer' }}
      title={photoSrc ? 'Replace photo' : 'Add a photo'}
    >
      {body}
      <span className="as-thumb-edit">{Icons.pencil!({ width: 13, height: 13 })}</span>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={pick}
      />
    </label>
  )
}

export interface AssetCardProps {
  a: OwnedAsset
  cats: AssetCategory[]
  loans: AssetLoan[]
  onOpen: (id: string) => void
}

export function AssetCard({ a, cats, onOpen }: AssetCardProps) {
  const c = catOf(cats, a.category)
  const gp = gainPctOf(a)
  return (
    <EntityRow
      as="button"
      className="as-card"
      type="asset"
      id={a.id}
      label={a.name || '(unnamed)'}
      item={a}
      toFields={assetFields}
      search={`${a.name} ${a.category} ${a.description ?? ''}`}
      sublabel={assetSublabel(a)}
      onClick={() => onOpen(a.id)}
    >
      <div className="as-card-top">
        <Thumb a={a} cats={cats} />
        <div className="as-card-id">
          <div className="as-card-nm">{a.name}</div>
          {/* A jointly-owned card says so beside its category, so it agrees with the
              composition rail, which counts only your share. */}
          <div className="as-card-sub">
            {a.share_pct < 100 ? `${c.label} · Your ${sharePct(a.share_pct)}%` : c.label}
          </div>
        </div>
      </div>
      <div className="as-card-row">
        <span className="as-card-val money">{fmtOrDash(a.value)}</span>
        <span className="as-card-side">
          <span className={'as-delta money ' + (gp == null ? 'flat' : gp >= 0 ? 'pos' : 'neg')}>
            {gp == null ? '' : pct(gp)}
          </span>
        </span>
      </div>
    </EntityRow>
  )
}

export interface ClassCardProps {
  c: ClassCardModel
  onNav: (tab: string) => void
}

export function ClassCard({ c, onNav }: ClassCardProps) {
  const Ic = Icons[c.key]!
  const showRows = c.rows != null && c.rows.length >= 2
  // Investments nulls its own card-level gainPct once it has rows (Positions vs.
  // Settlement cash) — the return is only meaningful against the positions' own cost, not
  // the net figure once a margin debit is mixed in (owned_assets.py's _investments_card).
  // Surfacing it on the headline anyway (owner's call) borrows the one row that actually
  // carries a `pct` rather than inventing a blended number the backend deliberately avoids.
  // Nothing held, nothing earned: an empty card shows no return — never a green "0.0%".
  const headlinePct =
    c.value === 0 ? null : (c.gainPct ?? c.rows?.find((r) => r.pct != null)?.pct ?? null)
  return (
    <button className="as-card" onClick={() => onNav(c.tab)}>
      <div className="as-card-top">
        <div
          className="as-thumb"
          style={{
            width: 46,
            height: 46,
            background: `color-mix(in srgb, ${c.color} 15%, var(--bg-2))`,
            borderColor: `color-mix(in srgb, ${c.color} 32%, var(--line))`,
            color: c.color,
          }}
        >
          {Ic({ width: 21, height: 21 })}
        </div>
        <div className="as-card-id">
          <div className="as-card-nm">{c.name}</div>
          <div className="as-card-sub">{c.sub}</div>
        </div>
      </div>
      <div className="as-card-row">
        <span className={'as-card-val money' + (c.value < 0 ? ' neg' : '')}>{fmt(c.value)}</span>
        <span
          className={
            'as-delta money ' + (headlinePct == null ? 'flat' : headlinePct >= 0 ? 'pos' : 'neg')
          }
        >
          {headlinePct == null ? c.yield : pct(headlinePct)}
        </span>
      </div>
      {showRows && (
        <div className="as-card-rows">
          {c.rows!.map((r) => (
            <div key={r.key} className="as-card-brow">
              <span className="as-card-brow-lbl">{r.label}</span>
              <span className={'as-card-brow-val money' + (r.value < 0 ? ' neg' : '')}>
                {fmt(r.value)}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  )
}

export interface RailProps {
  cats: AssetCategory[]
  classes: ClassCardModel[]
  summary: AssetsSummary
  loans: AssetLoan[]
}

// Same two colors the Accounts tab's own type palette uses for these bands
// (categories.ts's TYPE_COLOR — `credit: '#e0a032'` = --contingent, `loan: '#e2554f'` =
// --bad) — a debt row should read as the same "kind" of money everywhere in the app.
const DEBT_COLOR: Record<string, string> = {
  credit: 'var(--contingent)',
  loan: 'var(--bad)',
}

export function Rail({ cats, classes, summary }: RailProps) {
  // The server builds these from the same net-worth breakdown as "Everything you own" — each
  // owned asset at your share, in the base currency — and hands back whole percentages that
  // sum to 100. Recomputing them here from full asset values was the "Property 151%" bug.
  const rows = compositionView(summary.composition, cats, classes)
  return (
    <aside className="as-rail">
      <div className="as-rcard">
        <div className="as-rk">Asset Composition</div>
        <div className="as-comp">
          {rows.map((r) => (
            <div className="as-comp-row" key={r.key}>
              {/* The label and its % share one line; the bar runs full width under them. */}
              <span>{r.label}</span>
              <span className="as-comp-bar">
                <i style={{ width: r.pct + '%', background: r.color }} />
              </span>
              {/* A sliver still owned reads "<1%", never a misleading "0%". */}
              <span className="as-comp-pc money">{r.pct > 0 ? `${r.pct}%` : '<1%'}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Mirrors Asset Composition above, against `summary.owe` — server-gated to two or
          more liability kinds (credit + loans), same "two or more kinds" rule a class
          card's own breakdown rows use, so a single-mortgage book doesn't get a redundant
          one-row card repeating the "What you owe" KPI. */}
      {summary.debt && (
        <div className="as-rcard">
          <div className="as-rk">Debt Composition</div>
          <div className="as-comp">
            {summary.debt.map((r) => (
              <div className="as-comp-row" key={r.key}>
                <span>{r.label}</span>
                <span className="as-comp-bar">
                  <i
                    style={{
                      width: (r.value / summary.owe) * 100 + '%',
                      background: DEBT_COLOR[r.key] ?? 'var(--faint)',
                    }}
                  />
                </span>
                <span className="as-comp-pc money">
                  {Math.round((r.value / summary.owe) * 100)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}
