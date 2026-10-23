// Autocomplete.tsx — inline grouped @ / / picker.
//
// Ports prototype app.jsx Autocomplete (lines 343-457) using real data hooks.
// Groups: On this screen → Deeper sources → Talk to → entity types → Everywhere.
// CSS classes (.ac, .ac-scroll, .ac-zone, .ac-item, .ac-foot, etc.) from lucius.css.

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useMentionables } from '../../../hooks/useMentionables'
import type { MentionRow } from '../../../hooks/useMentionables'
import { allHandles } from '../../../lucius/screenContract'
import { Identity, Spark } from '../../../lucius/identity'
import { norm } from '../../../lucius/matchEntity'
import { C } from '../../../lucius/mentionColors'
import {
  parseTransactionSublabel,
  isSafeCssColor,
  renderMaskableSublabel,
  isMaskableAmountOrQuantity,
} from '../../../utils/mentionable'
import { labelFor } from '../../../lucius/entityRegistry'
import { Plus } from '../../../ui/icons'

export interface AutocompleteProps {
  mode: '@' | '/'
  query: string
  visibleIds: Set<string>
  chosenIds?: Set<string>
  onPick: (row: MentionRow) => void
  /** Agent/skill "as context" pick — attaches as ctx chip rather than routing. */
  onContext?: (row: MentionRow) => void
  onClose: () => void
  /** Opens the popup DOWNWARD (below the composer) instead of the default upward anchor —
   *  the Lucius home tab's composer sits near the top of the page, so opening up would
   *  collide with the greeting above it. See Composer.tsx's `homeMode` prop, its caller. */
  openDown?: boolean
  /** The composer's own root element — measured to position the portaled popup (see below).
   *  Optional so existing standalone renders (tests) keep working without one. */
  anchorRef?: RefObject<HTMLElement | null>
}

export interface AutocompleteHandle {
  /** Keyboard navigation: d = +1 (down) or -1 (up). */
  move: (d: 1 | -1) => void
  /** Pick the currently highlighted item. */
  pickHighlighted: () => void
  /** Count of items currently visible. */
  count: number
  /** Jump the section tab strip: d = +1 (next) or -1 (previous). No-op with 0/1 sections. */
  moveSection: (d: 1 | -1) => void
}

type HeadRow = {
  kind: 'head'
  label: string
  spark?: boolean
  eye?: boolean
  src?: boolean
  remote?: boolean
}
type ItemRow = {
  kind: 'item'
  row: MentionRow
  idx: number
  showCtx: boolean
}
type GroupRow = HeadRow | ItemRow

function rowKey(r: MentionRow) {
  return `${r.type}:${r.id}`
}

export const Autocomplete = forwardRef<AutocompleteHandle, AutocompleteProps>(function Autocomplete(
  {
    mode,
    query,
    visibleIds,
    chosenIds = new Set(),
    onPick,
    onContext,
    onClose,
    openDown = false,
    anchorRef,
  },
  ref,
) {
  const [hi, setHi] = useState(0)
  const kbdNav = useRef(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Portaled to document.body (below) so the popup escapes .lx-side-foot's overflow:hidden
  // clip — it needs to render well outside that ancestor's own small box, both opening up
  // (over the transcript) and down (owner report 2026-08-28, "the popup is not showing up
  // on mobile": .lx-side-foot gained overflow-y:hidden for scrollbar-gutter alignment,
  // 3d0688e8, which silently clips this overlay on every surface that pins the composer via
  // .lx-side-foot — i.e. every mobile screen, since LuciusMobile always renders through it).
  // Measured off the composer's own root (`anchorRef`) rather than relying on CSS
  // position:absolute against an ancestor, exactly like ModelPicker's non-mobile popover.
  const [pos, setPos] = useState<{ left: number; width: number; top: number } | null>(null)
  const popRef = useRef<HTMLDivElement>(null)
  // Phone: NOT portaled and NOT measured. Rendered in place inside .lx-compose and anchored to
  // its top edge with CSS alone (.ac--inline) — three rounds of fixed-position placement
  // failed on iOS with the keyboard up (behind the keyboard, then one row too low, covering
  // the text being typed): position:fixed and getBoundingClientRect stop sharing a coordinate
  // space there once the visual viewport has scrolled for the caret, so no amount of
  // re-measuring can land it. Inside the composer's own containing block there is one space.
  // Always opens upward: the phone's composer is pinned at the bottom on every screen, the
  // landing included (openDown was true there and sent the popup BELOW the composer).
  const inline = useIsMobile()
  useLayoutEffect(() => {
    if (inline) return
    const el = anchorRef?.current
    if (!el) return
    // Tracked EVERY FRAME while open, not on events. The composer keeps moving after every
    // event this used to listen to (window resize, visualViewport resize/scroll): on the phone
    // the keyboard and the composer's own pinned layout settle over several frames, and a
    // rect read inside any of those handlers is the composer's position mid-travel — the
    // popup then sat exactly one row too low, covering the text being typed (owner, three
    // reports). A collapsed rect read per frame is cheap, and the popup is open only while a
    // query is being typed; setPos fires only when a number actually changed.
    let raf = 0
    let last = ''
    const track = () => {
      const r = el.getBoundingClientRect()
      const left = r.left - 1
      const width = r.width + 2
      // `top` from the anchor's own rect and this popup's measured height — never
      // `bottom: innerHeight - r.top`, so every number is in one coordinate space.
      const h = popRef.current?.offsetHeight || 320
      const top = openDown ? r.bottom + 6 : r.top - 6 - h
      const key = `${left},${width},${top}`
      if (key !== last) {
        last = key
        setPos({ left, width, top })
      }
      raf = requestAnimationFrame(track)
    }
    track()
    return () => cancelAnimationFrame(raf)
  }, [anchorRef, openDown, inline])
  // Normalized query (lowercased, non-alphanumerics stripped) — so "dbsmul" matches
  // "DBS Multiplier (SGD)" across spaces / parens, matching how matchEntity filters upstream.
  const nq = norm(query)
  // onClose is called by the parent's Escape handler; kept here for callers that want
  // to close from outside (e.g. outside-click). Silence unused-var check.
  void onClose

  // Fetch from backend (includes entity + agent surfaces on @; verb on /).
  const surfaces: string[] = mode === '@' ? ['entity', 'agent'] : ['verb']
  // Default remoteItems/error/retry so the component is resilient to a hook/mock that
  // returns the older { items, loading } shape (otherwise remoteItems.map throws).
  const {
    items: backendItems,
    loading,
    error = false,
    retry,
    remoteItems = [],
  } = useMentionables(query, { surfaces })

  // Screen handles become pseudo-MentionRows of type 'source'.
  const handles = allHandles()
  const handleRows: MentionRow[] = useMemo(
    () =>
      handles
        .filter((h) => !chosenIds.has(`source:${h.id}`))
        .filter((h) => !nq || norm(h.label).includes(nq) || norm(h.descriptor ?? '').includes(nq))
        .map((h) => ({
          type: 'source',
          id: h.id,
          label: h.label,
          sublabel: h.descriptor ?? '',
          value: '',
          icon: '◷',
          onScreen: false,
        })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [handles.length, nq, chosenIds],
  )

  const { groupRows, items } = useMemo(() => {
    const rows: GroupRow[] = []
    const items: MentionRow[] = []
    let idx = 0

    const push = (row: MentionRow, showCtx = false) => {
      rows.push({ kind: 'item', row, idx, showCtx })
      items.push(row)
      idx++
    }

    const alreadyChosen = (r: MentionRow) => chosenIds.has(rowKey(r))
    // Rows from useMentionables are already filtered by matchEntity; rows that matched
    // via type-synonym carry a hint. Pass those through — checking the label alone would
    // drop them (no account is literally named "account").
    const matchQ = (r: MentionRow) =>
      !nq ||
      r.hint != null ||
      norm(r.label).includes(nq) ||
      norm(r.search ?? '').includes(nq) ||
      norm(r.sublabel ?? '').includes(nq)

    if (mode === '@') {
      // On this screen (rows with onScreen=true from the backend/DOM harvest).
      // Sorted (stably — transactions keep their relative order among themselves,
      // same for everything else) so a transaction-heavy screen (e.g. a register
      // filtered/scrolled to one payee) doesn't bury the account/holding/payee
      // itself under a wall of individual line items that all happen to match the
      // same text (owner report 2026-08-11 — "@ibkr" on the Accounts register
      // showed 6 IBKR transactions before the IBKR account or payee at all). A
      // transaction is the least likely thing meant by typing an entity's name —
      // every other type sorts first.
      const onScreen = backendItems
        .filter((r) => r.onScreen && !alreadyChosen(r) && matchQ(r))
        .sort((a, b) => Number(a.type === 'transaction') - Number(b.type === 'transaction'))
      if (onScreen.length) {
        rows.push({ kind: 'head', label: 'On this screen', eye: true })
        onScreen.forEach((r) => push(r))
      }

      // Deeper sources (screen handles)
      const srcRows = handleRows.filter((r) => !alreadyChosen(r))
      if (srcRows.length) {
        rows.push({ kind: 'head', label: 'Deeper sources', src: true })
        srcRows.forEach((r) => push(r))
      }

      // Talk to (agent surface rows)
      const agents = backendItems.filter(
        (r) => r.surface === 'agent' && !alreadyChosen(r) && matchQ(r),
      )
      if (agents.length) {
        rows.push({ kind: 'head', label: 'Talk to', spark: true })
        agents.forEach((r) => push(r, true))
      }

      // Entity groups by type (non-agent, non-onScreen, non-remote backend results)
      const localEntityItems = backendItems.filter(
        (r) => r.surface !== 'agent' && !r.onScreen && !alreadyChosen(r) && matchQ(r),
      )
      // Exclude rows that will appear in the Everywhere group (remote-only backend rows).
      const remoteKeys = new Set(remoteItems.map((r) => `${r.type}:${r.value}`))
      const localOnlyEntityItems = localEntityItems.filter(
        (r) => !remoteKeys.has(`${r.type}:${r.value}`),
      )
      const byType: Record<string, MentionRow[]> = {}
      localOnlyEntityItems.forEach((r) => {
        if (!byType[r.type]) byType[r.type] = []
        byType[r.type]!.push(r)
      })
      Object.entries(byType).forEach(([type, typeRows]) => {
        rows.push({ kind: 'head', label: labelFor(type) })
        typeRows.forEach((r) => push(r))
      })

      // Everywhere — backend-only rows not already shown above, grouped by type (owner
      // request 2026-08-11: "i want to group by type as well! payee, transaction...")
      // instead of one large mixed bucket where whichever type the backend's search
      // ranking happened to return first buried everything else, same problem the
      // "On this screen" type-tier sort above exists to fix, just for the remote pool.
      // Each type gets its own header — and, via the section tabs below, its own tab.
      const everywhereRows = remoteItems.filter((r) => r.surface !== 'agent' && !alreadyChosen(r))
      const everywhereByType: Record<string, MentionRow[]> = {}
      everywhereRows.forEach((r) => {
        if (!everywhereByType[r.type]) everywhereByType[r.type] = []
        everywhereByType[r.type]!.push(r)
      })
      Object.entries(everywhereByType).forEach(([type, typeRows]) => {
        rows.push({ kind: 'head', label: labelFor(type), remote: true })
        typeRows.forEach((r) => push(r))
      })
    } else {
      // Skill / verb mode — only skill/verb rows. backendItems always includes the
      // local DOM harvest (accounts, holdings, etc.) regardless of the `surfaces` param,
      // so we must filter to skill/verb surface explicitly to prevent on-screen entities
      // from leaking into the `/` picker.
      const skills = backendItems.filter(
        (r) => (r.surface === 'verb' || r.type === 'skill') && !alreadyChosen(r) && matchQ(r),
      )
      skills.forEach((r) => push(r, true))
    }

    return { groupRows: rows, items }
  }, [mode, nq, backendItems, handleRows, chosenIds, remoteItems])

  // Section tabs — one per group header actually present for this query (owner request
  // 2026-08-11). groupRowIndex is the header's own position in groupRows, used to find its
  // rendered DOM node for the scroll-jump; itemIdx is the FIRST item's `idx` in that section
  // (if any — a section can be header-only while its own fetch is still loading), so jumping
  // to a section also moves the keyboard highlight into it rather than leaving `hi` stranded
  // in whatever section it was last in.
  const sections = useMemo(
    () =>
      groupRows.reduce<{ label: string; groupRowIndex: number; itemIdx: number | null }[]>(
        (acc, r, i) => {
          if (r.kind === 'head') {
            acc.push({ label: r.label, groupRowIndex: i, itemIdx: null })
          } else if (acc.length && acc[acc.length - 1]!.itemIdx === null) {
            acc[acc.length - 1]!.itemIdx = r.idx
          }
          return acc
        },
        [],
      ),
    [groupRows],
  )
  const [activeSection, setActiveSection] = useState(0)
  const headRefs = useRef(new Map<number, HTMLDivElement>())
  const tabRefs = useRef(new Map<number, HTMLButtonElement>())

  const jumpToSection = (sectionIdx: number) => {
    const s = sections[sectionIdx]
    if (!s) return
    // MUST be false, not just "not set true here": a stale `true` left over from an
    // earlier ArrowUp/ArrowDown press (move() below) made the SEPARATE "scroll
    // highlighted item into view" effect (keyed on [hi]) ALSO fire right after this
    // function's own scroll assignment below — using its own different math (align the
    // ITEM, not the section header, and only nudge the minimum distance needed) — which
    // silently overrode landing on the header at all (owner report 2026-08-11, "when
    // switching section, it needs to show from the start of the section... including
    // the header").
    kbdNav.current = false
    setActiveSection(sectionIdx)
    if (s.itemIdx !== null) setHi(s.itemIdx)
    const el = headRefs.current.get(s.groupRowIndex)
    const sc = scrollRef.current
    if (el && sc) sc.scrollTop = el.offsetTop
    // The tab strip itself scrolls horizontally (more sections than fit at once —
    // owner report 2026-08-11, "it doesn't scroll to see everything here"). Bring the
    // newly-active tab into view within .ac-tabs specifically: `inline` for the
    // horizontal axis .ac-tabs actually overflows on, `block: 'nearest'` so this never
    // ALSO scrolls .ac-scroll or the page — scrollIntoView walks every scrollable
    // ancestor by default, and .ac-scroll (just adjusted above) is one of them.
    tabRefs.current.get(sectionIdx)?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  }

  // Keyboard nav via imperative handle.
  useImperativeHandle(
    ref,
    () => ({
      move(d: 1 | -1) {
        kbdNav.current = true
        setHi((h) => Math.max(0, Math.min(items.length - 1, h + d)))
      },
      pickHighlighted() {
        const r = items[hi]
        if (r) onPick(r)
      },
      get count() {
        return items.length
      },
      moveSection(d: 1 | -1) {
        if (sections.length < 2) return
        jumpToSection(Math.max(0, Math.min(sections.length - 1, activeSection + d)))
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items, hi, onPick, sections, activeSection],
  )

  // Reset highlight + active section when query or mode changes.
  useEffect(() => {
    setHi(0)
    setActiveSection(0)
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }, [nq, mode])

  // Clamp when items list shrinks (e.g. after a pick).
  useEffect(() => {
    if (hi >= items.length) setHi(Math.max(0, items.length - 1))
  }, [items.length, hi])

  // Scroll highlighted item into view on keyboard nav.
  useEffect(() => {
    const sc = scrollRef.current
    if (!sc || !kbdNav.current) return
    kbdNav.current = false
    const el = sc.querySelector<HTMLElement>('.ac-item.hi')
    if (!el) return
    let top = el.offsetTop
    const prev = el.previousElementSibling
    if (prev?.classList.contains('ac-zone')) top = (prev as HTMLElement).offsetTop
    const b = el.offsetTop + el.offsetHeight
    if (top < sc.scrollTop + 4) sc.scrollTop = Math.max(0, top - 4)
    else if (b > sc.scrollTop + sc.clientHeight) sc.scrollTop = b - sc.clientHeight + 4
  }, [hi])

  const modeHint = mode === '@' ? 'Mention accounts, transactions, holdings, …' : 'Run a skill'

  const popup = (
    <div
      className={'ac' + (inline ? ' ac--inline' : openDown ? ' ac--down' : '')}
      data-portal-layer
      onMouseDown={(e) => e.preventDefault()}
      style={
        inline
          ? undefined
          : pos
            ? {
                position: 'fixed',
                left: pos.left,
                width: pos.width,
                top: pos.top,
              }
            : { position: 'fixed', visibility: 'hidden' }
      }
      ref={popRef}
    >
      {mode === '@' && sections.length > 1 && (
        <div className="ac-tabs" role="tablist" aria-label="Result sections">
          {sections.map((s, i) => (
            <button
              key={s.groupRowIndex}
              type="button"
              role="tab"
              ref={(el) => {
                if (el) tabRefs.current.set(i, el)
                else tabRefs.current.delete(i)
              }}
              aria-selected={i === activeSection}
              className={'ac-tab' + (i === activeSection ? ' active' : '')}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => jumpToSection(i)}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="ac-scroll" ref={scrollRef}>
        {groupRows.map((r, i) => {
          if (r.kind === 'head') {
            return (
              <div
                key={`h${i}`}
                ref={(el) => {
                  if (el) headRefs.current.set(i, el)
                  else headRefs.current.delete(i)
                }}
                className={'ac-zone' + (r.remote ? ' remote' : '')}
              >
                {r.spark && <Spark size={11} />}
                {r.eye && <span className="ac-eye">◉</span>}
                {r.src && <span className="ac-eye">◷</span>}
                {r.label}
              </div>
            )
          }

          const { row, idx, showCtx } = r
          const isSkill = row.type === 'skill'
          // Build the per-item dot for colorFor() in the picker icon:
          //   transaction → direction tint derived from sublabel (green=inflow, red=outflow)
          //   any entity with its own display color (account tint, envelope/category dot)
          //     → that color (isSafeCssColor-guarded), matches the chip glyph
          //   others      → no dot, IdentityInner uses the TYPE_COLOR default
          let itemDot: string | undefined
          if (row.type === 'transaction' && row.sublabel) {
            const dir = parseTransactionSublabel(row.sublabel)
            if (dir.chipAmtNeg !== undefined) {
              itemDot = dir.chipAmtNeg ? C.neg : C.pos
            }
          } else if (row.color && isSafeCssColor(row.color)) {
            itemDot = row.color
          }
          const identityItem = {
            type: row.type,
            label: row.label,
            id: row.id,
            dot: itemDot,
          }

          return (
            <div
              key={rowKey(row)}
              className={'ac-item' + (idx === hi ? ' hi' : '') + (isSkill ? ' skill' : '')}
              onMouseEnter={() => {
                kbdNav.current = false
                setHi(idx)
              }}
              onClick={() => onPick(row)}
            >
              {isSkill ? (
                <span className="ac-item-ic cmd">/</span>
              ) : (
                <Identity item={identityItem} />
              )}
              <div className="ac-item-main">
                <div className="ac-item-name">
                  {isSkill ? <span className="ac-cmd-name">{row.label}</span> : row.label}
                </div>
                <div className={'ac-item-sub' + (row.hint || row.search ? ' why' : '')}>
                  {renderMaskableSublabel(row.hint ?? row.sublabel)}
                </div>
              </div>
              {showCtx && onContext && (
                <button
                  className="ac-item-add"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => {
                    e.stopPropagation()
                    onContext(row)
                  }}
                  title="Attach as context instead of routing"
                >
                  <Plus size={11} aria-hidden="true" /> as context
                </button>
              )}
              {mode === '@' && row.type !== 'agent' && row.type !== 'source' && (
                <span className="ac-item-tt">
                  {visibleIds.has(row.id) && (
                    <span className="ac-onscreen" title="Visible on screen">
                      ◉
                    </span>
                  )}
                  {labelFor(row.type)}
                </span>
              )}
              {/* Show `value` only when it's a meaningful selector (e.g. "1Y", "asset_class:equity"),
                    never the raw stored id/ref (value === id) — that's noise. The id lives in the
                    chip-tap detail popup, not the picker row. */}
              {mode === '@' && row.value && row.value !== row.id && (
                <span
                  className={'ac-item-v' + (isMaskableAmountOrQuantity(row.value) ? ' money' : '')}
                >
                  {row.value}
                </span>
              )}
            </div>
          )
        })}

        {/* Searching spinner — shown while debounced backend fetch is in flight, INCLUDING
              the very first render right after typing a bare "@" (query === ''): without the
              query check, that window hit none of these three branches (this one required a
              query, the empty-state below required !loading) — .ac-scroll rendered nothing at
              all until the fetch resolved, so the popover briefly collapsed to just its
              footer strip (owner report 2026-08-11, "the mention view can be almost 0 height"). */}
        {mode === '@' && loading && (
          <div className={'ac-searching' + (items.length === 0 ? ' ac-searching--empty' : '')}>
            <span className="ac-spin" />
            Searching everywhere…
          </div>
        )}
        {/* Error + retry — shown when backend fetch failed. */}
        {mode === '@' && query && error && !loading && (
          <div className={'ac-error' + (items.length === 0 ? ' ac-error--empty' : '')}>
            <span className="x">⚠</span>
            Couldn't reach search.
            <button
              className="ac-retry"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => retry()}
            >
              Retry
            </button>
          </div>
        )}
        {/* Empty state — no items at all, not loading, no error. Always the sole content
              of .ac-scroll when it renders (gated on items.length === 0), so unlike
              .ac-searching/.ac-error above it needs no conditional variant — always centered. */}
        {items.length === 0 && !loading && !error && (
          <div className="ac-empty">
            {query ? <>No matches for "{query}".</> : 'Start typing to search.'}
          </div>
        )}
      </div>
      <div className="ac-foot">
        <span className="ac-foot-hint">{modeHint}</span>
        <span className="ac-foot-keys">
          <span>
            <span className="mx-kbd">↑↓</span>navigate
          </span>
          {mode === '@' && sections.length > 1 && (
            // The section-switch shortcut has NO other affordance — it's a modifier
            // combo, not a click target — so without this it's simply undiscoverable
            // (owner report 2026-08-11, "how the fuck can user know that?"). Same
            // hardcoded ⌘ convention already used for ⌘K / ⌘/ elsewhere in this app
            // (RouterShell.tsx) — not platform-branched to "Ctrl" on Windows/Linux.
            <span>
              <span className="mx-kbd">⌘←→</span>sections
            </span>
          )}
          <span>
            <span className="mx-kbd">↵</span>select
          </span>
          <span>
            <span className="mx-kbd">esc</span>dismiss
          </span>
        </span>
      </div>
    </div>
  )

  return inline ? popup : createPortal(popup, document.body)
})
