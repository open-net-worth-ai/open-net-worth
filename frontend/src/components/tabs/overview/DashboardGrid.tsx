// Overview v2 — the grid renderer: lays out every item (widget instance or
// section divider) from a `DashboardView`, hosts the drag/8-direction-resize
// gestures (via pointer capture, so a gesture keeps tracking even over an
// iframe-backed widget — the same reason the handoff's prototype uses
// pointer events rather than mouse events) with LIVE reflow (other widgets
// visibly shift as you drag/resize — item 3 of the parity audit), edge
// auto-scroll, resize snap-to-neighbor-edge, the customize-mode grid overlay,
// section collapse/rename, widget collapse-to-slim-bar, the view-mode
// fullscreen detail view, and resolves each widget's content component from
// the SAME registry regardless of whether its def is hand-built or Lucius-
// generated. On narrow viewports the CSS switches every `.ov-griditem` to
// static/stacked (see overview.css's `@media (max-width: 640px)`); drag
// reduces to the mobile move-up/move-down buttons on GridCard.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ComputedRow } from '../../../types/portfolio'
import { findDefinition } from './definitions'
import {
  EDGE_SCROLL_MARGIN_PX,
  EDGE_SCROLL_MAX_PX,
  GAP_PX,
  RESIZE_SNAP_PX,
} from './layoutConstants'
import { ErrorBoundary } from '../../ErrorBoundary'
import { GridCard } from './GridCard'
import type { ResizeDir } from './GridCard'
import type { KebabItem } from '../planning/KebabMenu'
import { WIDGET_COMPONENTS } from './registry'
import { WidgetShell } from './WidgetShell'
import { WidgetPlaceholder } from './WidgetPlaceholder'
import { WidgetDisplayPanel } from './WidgetDisplayPanel'
import { WidgetFullscreen } from './WidgetFullscreen'
import { GridItemContext } from './gridItemContext'
import type { GridItem, RegisterDeepLink, SectionDivider, WidgetInstance } from './types'
import { isSection } from './types'
import {
  AUTOH_IDS,
  COLS,
  MAX_H,
  MIN_H,
  MIN_W,
  SECTION_H,
  compact,
  computeDisplayLayout,
  sectionMembership,
  fitMode,
  phoneOrdered,
  topmostFit,
} from './grid'
import type { UseDashboardLayout } from './useDashboardLayout'

export interface DashboardGridProps {
  layout: UseDashboardLayout
  editing: boolean
  onNavigate?: (tab: string) => void
  onOpenAsset?: (label: string) => void
  onAskLucius?: (prompt: string) => void
  onShareWithLucius?: (label: string) => void
  onOpenRegister?: (filter: RegisterDeepLink) => void
}

type DragState =
  | {
      kind: 'move'
      instanceId: string
      startX: number
      startClientX: number
      startClientY: number
      colWidth: number
      scroller: Element
      startScrollTop: number
    }
  | {
      kind: 'resize'
      instanceId: string
      startX: number
      startY: number
      startW: number
      startH: number
      startClientX: number
      startClientY: number
      colWidth: number
      dirX: ResizeDir
      dirY: ResizeDir
      scroller: Element
      startScrollTop: number
    }

const NARROW_BREAKPOINT_PX = 640

/** Topmost scrollable ancestor (falls back to the document) — mirrors the
 * handoff's `findScroller`, used to keep the edge-auto-scroll + resize
 * scroll-compensation targeting the actual scrolling element regardless of
 * where the grid is mounted (a plain page scroll here; a modal body if this
 * component is ever hosted inside one). */
function findScroller(node: HTMLElement | null): Element {
  for (let el: HTMLElement | null = node; el; el = el.parentElement) {
    const overflowY = getComputedStyle(el).overflowY
    if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 2)
      return el
  }
  return document.scrollingElement || document.documentElement
}

interface AutoScrollState {
  id: number
  scroller: Element | null
  clientY: number
  onFrame: (() => void) | null
}

function stopAutoScroll(state: AutoScrollState) {
  if (state.id) cancelAnimationFrame(state.id)
  state.id = 0
}

/** While dragging/resizing near the viewport top/bottom, scroll the page and
 * re-run the gesture's own move handler each frame so the widget keeps
 * following past the fold — mirrors the handoff's `autoScroll`. */
function autoScroll(
  state: AutoScrollState,
  clientY: number,
  onFrame: () => void,
  scroller: Element,
) {
  state.clientY = clientY
  state.onFrame = onFrame
  state.scroller = scroller
  const step = () => {
    const vh = window.innerHeight
    const y = state.clientY
    let dy = 0
    if (y < EDGE_SCROLL_MARGIN_PX)
      dy = -EDGE_SCROLL_MAX_PX * Math.min(1, (EDGE_SCROLL_MARGIN_PX - y) / EDGE_SCROLL_MARGIN_PX)
    else if (y > vh - EDGE_SCROLL_MARGIN_PX)
      dy =
        EDGE_SCROLL_MAX_PX * Math.min(1, (y - (vh - EDGE_SCROLL_MARGIN_PX)) / EDGE_SCROLL_MARGIN_PX)
    if (dy && state.scroller) {
      state.scroller.scrollTop += dy
      state.onFrame?.()
      state.id = requestAnimationFrame(step)
    } else {
      state.id = 0
    }
  }
  if (
    (clientY < EDGE_SCROLL_MARGIN_PX || clientY > window.innerHeight - EDGE_SCROLL_MARGIN_PX) &&
    !state.id
  ) {
    state.id = requestAnimationFrame(step)
  }
}

const chevronIcon = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M6 9l6 6 6-6" />
  </svg>
)

/** A set of instance ids plus a stable `(id, on) => void` toggle — what the
 * fit-content signal reports into. */
function useIdSet(): [ReadonlySet<string>, (instanceId: string, on: boolean) => void] {
  const [ids, setIds] = useState<ReadonlySet<string>>(() => new Set())
  const toggle = useCallback((instanceId: string, on: boolean) => {
    setIds((prev) => {
      if (prev.has(instanceId) === on) return prev
      const next = new Set(prev)
      if (on) next.add(instanceId)
      else next.delete(instanceId)
      return next
    })
  }, [])
  return [ids, toggle]
}

export function DashboardGrid({
  layout,
  editing,
  onNavigate,
  onOpenAsset,
  onAskLucius,
  onShareWithLucius,
  onOpenRegister,
}: DashboardGridProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [preview, setPreview] = useState<GridItem[] | null>(null)
  const [displayFor, setDisplayFor] = useState<string | null>(null)
  const [fullscreenFor, setFullscreenFor] = useState<string | null>(null)
  const [fullscreenMode, setFullscreenMode] = useState<'view' | 'edit'>('view')
  const [renamingSectionId, setRenamingSectionId] = useState<string | null>(null)
  const [narrow, setNarrow] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= NARROW_BREAKPOINT_PX,
  )
  const [measuredHeights, setMeasuredHeights] = useState<ReadonlyMap<string, number>>(
    () => new Map(),
  )
  const [fitIds, setFit] = useIdSet()
  const fsSnapshotRef = useRef<WidgetInstance | null>(null)

  const items = useMemo(
    () => preview ?? layout.activeView?.widgets ?? [],
    [preview, layout.activeView?.widgets],
  )
  // Mid-gesture the live preview IS the layout: it started from what was on screen
  // (positions and heights as displayed), so re-laying it out would only fight the drag.
  const { displayItems, countBySection } = useMemo(
    () =>
      preview
        ? { displayItems: preview, countBySection: sectionMembership(preview).countBySection }
        : computeDisplayLayout(items, editing, measuredHeights, { fitIds }),
    [preview, items, editing, measuredHeights, fitIds],
  )
  const phoneOrder = layout.activeView?.phoneOrder
  const sorted = useMemo(() => {
    const desktop = [...displayItems].sort((a, b) => a.y - b.y || a.x - b.x)
    // A phone stacks one column in its own saved order; the desktop grid ignores it.
    return narrow ? phoneOrdered(desktop, phoneOrder) : desktop
  }, [displayItems, narrow, phoneOrder])
  const positionMap = useMemo(
    () => new Map(displayItems.map((it) => [it.instanceId, it])),
    [displayItems],
  )

  // The GRID CONTAINER's own width, not window.innerWidth — the Lucius panel eats real
  // width the raw viewport check never saw, so a plenty-wide window with Lucius open still
  // rendered multi-column at a squeeze the "narrow" fallback should have caught.
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const measure = () => setNarrow(el.clientWidth > 0 && el.clientWidth <= NARROW_BREAKPOINT_PX)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [sorted.length])

  const colWidth = useCallback(() => (containerRef.current?.clientWidth ?? 1200) / COLS, [])

  const beginDrag = (item: GridItem, e: React.PointerEvent) => {
    if (!editing) return
    ;(e.target as Element).setPointerCapture(e.pointerId)
    const scroller = findScroller(containerRef.current)
    setDrag({
      kind: 'move',
      instanceId: item.instanceId,
      startX: item.x,
      startClientX: e.clientX,
      startClientY: e.clientY,
      colWidth: colWidth(),
      scroller,
      startScrollTop: scroller.scrollTop,
    })
    setPreview(displayItems)
    e.preventDefault()
  }

  const beginResize = (item: GridItem, e: React.PointerEvent, dirX: ResizeDir, dirY: ResizeDir) => {
    if (!editing) return
    ;(e.target as Element).setPointerCapture(e.pointerId)
    const scroller = findScroller(containerRef.current)
    setDrag({
      kind: 'resize',
      instanceId: item.instanceId,
      startX: item.x,
      startY: item.y,
      startW: item.w,
      startH: item.h,
      startClientX: e.clientX,
      startClientY: e.clientY,
      colWidth: colWidth(),
      dirX,
      dirY,
      scroller,
      startScrollTop: scroller.scrollTop,
    })
    setPreview(displayItems)
    e.preventDefault()
    e.stopPropagation()
  }

  // Recompacts the WHOLE grid on every pointermove (WYSIWYG — mirrors the
  // handoff's `startDrag`/`startResize` `move` handlers) so other widgets
  // visibly shift out of the way as you drag/resize, instead of only moving
  // once on release.
  const applyGesture = useCallback(
    (clientX: number, clientY: number) => {
      const d = drag
      if (!d) return
      const dx = clientX - d.startClientX
      const dy = clientY - d.startClientY
      if (d.kind === 'move') {
        const rect = containerRef.current?.getBoundingClientRect()
        if (!rect) return
        const dxCols = Math.round(dx / d.colWidth)
        setPreview((prev) => {
          // `arr` is in display space already (heights as drawn), so it is compacted as-is.
          const arr = prev ?? displayItems
          const target = arr.find((it) => it.instanceId === d.instanceId)
          if (!target) return prev
          const newX = Math.max(0, Math.min(COLS - target.w, d.startX + dxCols))
          const pointerRow = Math.max(0, clientY - rect.top)
          const draggingIsSection = isSection(target)
          const moved = arr.map((it) => (it.instanceId === d.instanceId ? { ...it, x: newX } : it))
          return compact(moved, d.instanceId, pointerRow, draggingIsSection)
        })
      } else {
        const dxCols = Math.round(dx / d.colWidth)
        const dRow = Math.round(dy + (d.scroller.scrollTop - d.startScrollTop))
        let x = d.startX
        let y = d.startY
        let w = d.startW
        let h = d.startH
        if (d.dirX === 1) w = d.startW + dxCols
        else if (d.dirX === -1) {
          const nx = Math.max(0, Math.min(d.startX + d.startW - MIN_W, d.startX + dxCols))
          w = d.startX + d.startW - nx
          x = nx
        }
        const others0 = (preview ?? displayItems).filter((it) => it.instanceId !== d.instanceId)
        const snapYs = Array.from(new Set(others0.flatMap((it) => [it.y, it.y + it.h])))
        const snap = (v: number) => {
          for (const s of snapYs) if (Math.abs(v - s) <= RESIZE_SNAP_PX) return s
          return v
        }
        if (d.dirY === 1) {
          h = snap(d.startY + d.startH + dRow) - d.startY
        } else if (d.dirY === -1) {
          let top = snap(d.startY + dRow)
          top = Math.max(0, Math.min(d.startY + d.startH - MIN_H, top))
          h = d.startY + d.startH - top
          y = top
        }
        w = Math.max(MIN_W, Math.min(COLS - x, w))
        h = Math.max(MIN_H, Math.min(MAX_H, h))
        // A Fit-mode (content-fit) widget has no height handle (see GridCard's
        // RESIZE_HANDLES filter) — only its WIDTH is being dragged here, and
        // changing the width can change how much vertical space its content
        // needs (e.g. text reflow). `h` above stays pinned to `d.startH` for
        // this whole gesture, which is stale the instant the width changes —
        // the real height only gets measured (and other widgets only
        // actually shift) via the `.ov-griditem--autoh` ResizeObserver
        // effect below, which writes to the COMMITTED layout store, not this
        // gesture's `preview` — so it stayed invisible mid-drag and only
        // "caught up" on release. Re-measuring the dragged panel's real DOM
        // height here, every pointermove, keeps the live reflow honest
        // instead of frozen at the gesture's starting height.
        if (d.dirY === 0) {
          const draggedItem = items.find((it) => it.instanceId === d.instanceId)
          if (
            draggedItem &&
            !isSection(draggedItem) &&
            fitMode(draggedItem.defId, draggedItem.heightMode)
          ) {
            const cell = containerRef.current?.querySelector<HTMLElement>(
              `[data-instance-id="${d.instanceId}"]`,
            )
            const panel = cell?.querySelector<HTMLElement>(
              '.ov-griditem__body > .panel, .ov-griditem__body > section',
            )
            if (panel) h = Math.max(MIN_H, panel.offsetHeight + GAP_PX)
          }
        }
        setPreview((prev) => {
          const arr = prev ?? displayItems
          const others = arr.filter((it) => it.instanceId !== d.instanceId && !isSection(it))
          const secAbove = arr
            .filter((it): it is SectionDivider => isSection(it) && it.y <= d.startY)
            .sort((a, b) => b.y - a.y)[0]
          const floorY = secAbove ? secAbove.y + SECTION_H : 0
          const liftY =
            d.dirY !== 0 ? y : Math.max(floorY, Math.min(y, topmostFit(x, w, h, others)))
          return compact(
            arr.map((it) => (it.instanceId === d.instanceId ? { ...it, x, y: liftY, w, h } : it)),
            d.instanceId,
          )
        })
      }
    },
    [drag, items, displayItems, preview],
  )
  // Always-fresh handle for the auto-scroll rAF loop, which runs outside
  // React's event cycle and must never call a stale closure. Updated in an
  // effect (not during render) so it's always in sync by the time the loop
  // reads it, without violating the rule against mutating a ref mid-render.
  const moveFnRef = useRef(applyGesture)
  useEffect(() => {
    moveFnRef.current = applyGesture
  })
  const lastPointerRef = useRef({ x: 0, y: 0 })
  const autoScrollStateRef = useRef<AutoScrollState>({
    id: 0,
    scroller: null,
    clientY: 0,
    onFrame: null,
  })

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return
    lastPointerRef.current = { x: e.clientX, y: e.clientY }
    applyGesture(e.clientX, e.clientY)
    autoScroll(
      autoScrollStateRef.current,
      e.clientY,
      () => moveFnRef.current(lastPointerRef.current.x, lastPointerRef.current.y),
      drag.scroller,
    )
  }

  const endGesture = () => {
    stopAutoScroll(autoScrollStateRef.current)
    if (!drag || !layout.activeView) {
      setDrag(null)
      setPreview(null)
      return
    }
    const finalItems = preview ?? items
    if (drag.kind === 'move') {
      const positions: Record<string, { x: number; y: number }> = {}
      for (const it of finalItems) positions[it.instanceId] = { x: it.x, y: it.y }
      layout.dragItem(positions, drag.instanceId)
    } else {
      const target = finalItems.find((it) => it.instanceId === drag.instanceId)
      if (target) layout.resizeItem(drag.instanceId, target.x, target.y, target.w, target.h)
    }
    setDrag(null)
    setPreview(null)
  }

  // Phone ↑↓: swap with the neighbour in the PHONE order only. It used to nudge the card's
  // desktop row (and keep its column), so reordering on a phone reshuffled the desktop grid —
  // and two cards side by side on desktop barely moved at all.
  const moveInOrder = (instanceId: string, dir: -1 | 1) => {
    const view = layout.activeView
    if (!view) return
    const ids = sorted.map((it) => it.instanceId)
    const idx = ids.indexOf(instanceId)
    const swap = idx + dir
    if (idx < 0 || swap < 0 || swap >= ids.length) return
    ;[ids[idx], ids[swap]] = [ids[swap]!, ids[idx]!]
    layout.setPhoneOrder(view.viewId, ids)
  }

  // ── Generic responsive height contract: every widget body is a named
  // inline-size container (GridCard/overview.css). Measure every card's
  // natural panel height after its container-driven reflow, keep that value
  // ephemeral (saved `h` remains the user's explicit floor), and feed all
  // measurements through computeDisplayLayout's single compaction path.
  //
  // ResizeObserver detects card/container changes; MutationObserver catches
  // async content changes that alter scroll height without changing the
  // panel's fixed border box. Both schedule the SAME rAF-batched measurement,
  // so observer callbacks never synchronously resize their own targets.
  useLayoutEffect(() => {
    if (narrow) return
    const root = containerRef.current
    if (!root) return

    const itemById = new Map(
      (layout.activeView?.widgets ?? []).map((item) => [item.instanceId, item]),
    )
    const measurePanel = (panel: HTMLElement, preserveContentFitCap: boolean): number => {
      const previous = {
        height: panel.style.height,
        maxHeight: panel.style.maxHeight,
        overflowY: panel.style.overflowY,
      }
      panel.style.height = 'auto'
      if (!preserveContentFitCap) {
        panel.style.maxHeight = 'none'
        panel.style.overflowY = 'visible'
      }
      const naturalHeight = preserveContentFitCap
        ? panel.offsetHeight
        : Math.max(panel.offsetHeight, panel.scrollHeight)
      panel.style.height = previous.height
      panel.style.maxHeight = previous.maxHeight
      panel.style.overflowY = previous.overflowY
      return Math.max(MIN_H, Math.min(MAX_H, Math.ceil(naturalHeight + GAP_PX)))
    }

    const measureAll = () => {
      const next = new Map<string, number>()
      root
        .querySelectorAll<HTMLElement>('.ov-griditem:not(.ov-griditem--section)')
        .forEach((cell) => {
          const instanceId = cell.dataset.instanceId
          const panel = cell.querySelector<HTMLElement>(
            '.ov-griditem__body > .panel, .ov-griditem__body > section',
          )
          if (instanceId && panel && !cell.querySelector('.ov-griditem-collapsed')) {
            const item = itemById.get(instanceId)
            const preserveContentFitCap =
              !!item && !isSection(item) && fitMode(item.defId, item.heightMode)
            next.set(instanceId, measurePanel(panel, preserveContentFitCap))
          }
        })
      setMeasuredHeights((previous) => {
        if (
          previous.size === next.size &&
          [...next].every(([instanceId, height]) => previous.get(instanceId) === height)
        ) {
          return previous
        }
        return next
      })
    }

    let frame = 0
    const scheduleMeasurement = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        measureAll()
      })
    }
    const resizeObserver = new ResizeObserver(scheduleMeasurement)
    root
      .querySelectorAll<HTMLElement>('.ov-griditem:not(.ov-griditem--section) .ov-griditem__body')
      .forEach((body) => {
        resizeObserver.observe(body)
        const panel = body.querySelector<HTMLElement>(':scope > .panel, :scope > section')
        if (panel) resizeObserver.observe(panel)
      })
    const mutationObserver = new MutationObserver(scheduleMeasurement)
    mutationObserver.observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    })
    measureAll()
    return () => {
      if (frame) cancelAnimationFrame(frame)
      resizeObserver.disconnect()
      mutationObserver.disconnect()
    }
  }, [narrow, editing, layout.activeView?.widgets])

  // New-widget landing: scroll it into view once it mounts — the pulse class
  // (applied via `isNew` below) clears itself on `useDashboardLayout`'s own timer.
  useEffect(() => {
    if (!layout.newlyAddedId || !containerRef.current) return
    const el = containerRef.current.querySelector(`[data-instance-id="${layout.newlyAddedId}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [layout.newlyAddedId])

  const openFullscreen = (instanceId: string, mode: 'view' | 'edit' = 'view') => {
    const item = items.find((it) => it.instanceId === instanceId)
    if (item && !isSection(item)) fsSnapshotRef.current = item
    setFullscreenMode(mode)
    setFullscreenFor(instanceId)
  }
  const saveFullscreen = () => {
    fsSnapshotRef.current = null
    setFullscreenFor(null)
  }
  const discardFullscreen = () => {
    const snap = fsSnapshotRef.current
    if (snap) layout.patchItem(snap.instanceId, snap)
    fsSnapshotRef.current = null
    setFullscreenFor(null)
  }

  const displayItem = displayFor ? items.find((it) => it.instanceId === displayFor) : null
  const fullscreenItem = fullscreenFor ? items.find((it) => it.instanceId === fullscreenFor) : null
  const dialogs = (
    <>
      {displayItem && !isSection(displayItem) && (
        <WidgetDisplayPanel
          open
          onClose={() => setDisplayFor(null)}
          defId={displayItem.defId}
          hideTitle={displayItem.hideTitle}
          heightMode={displayItem.heightMode}
          limit={displayItem.limit}
          accent={displayItem.accent}
          onSave={(patch) => layout.patchItem(displayItem.instanceId, patch)}
        />
      )}
      {fullscreenItem &&
        !isSection(fullscreenItem) &&
        (() => {
          const def = findDefinition(fullscreenItem.defId)
          const Content = WIDGET_COMPONENTS[fullscreenItem.defId]
          if (!def || !Content) return null
          return (
            <WidgetFullscreen
              mode={fullscreenMode}
              instance={fullscreenItem}
              def={def}
              Content={Content}
              onPatch={(patch) => layout.patchItem(fullscreenItem.instanceId, patch)}
              onDiscard={discardFullscreen}
              onSave={saveFullscreen}
              onNavigate={onNavigate}
              onOpenAsset={onOpenAsset}
              onAskLucius={onAskLucius}
              onShareWithLucius={onShareWithLucius}
              onOpenRegister={onOpenRegister}
            />
          )
        })()}
    </>
  )

  if (!sorted.length) return dialogs

  const totalHeight = sorted.reduce((m, it) => Math.max(m, it.y + it.h), 0)

  return (
    <>
      <div
        className={
          'ov-grid' + (editing ? ' ov-grid--editing' : '') + (narrow ? ' ov-grid--narrow' : '')
        }
        ref={containerRef}
        style={{ position: 'relative', minHeight: totalHeight }}
        onPointerMove={onPointerMove}
        onPointerUp={endGesture}
        onPointerCancel={endGesture}
      >
        {editing && drag && !narrow && (
          <div className="ov-grid-overlay" aria-hidden="true">
            {Array.from({ length: 12 }).map((_, i) => (
              <i key={i} />
            ))}
          </div>
        )}
        {sorted.map((displayItem) => {
          const pos = positionMap.get(displayItem.instanceId) ?? displayItem
          if (isSection(displayItem)) {
            const count = countBySection.get(displayItem.instanceId) ?? 0
            const renaming = renamingSectionId === displayItem.instanceId
            return (
              <GridCard
                key={displayItem.instanceId}
                instanceId={displayItem.instanceId}
                x={pos.x}
                y={pos.y}
                w={displayItem.w}
                h={displayItem.h}
                editing={editing}
                isSection
                dragging={drag?.kind === 'move' && drag.instanceId === displayItem.instanceId}
                isNew={layout.newlyAddedId === displayItem.instanceId}
                onDragPointerDown={(e) => beginDrag(displayItem, e)}
                onRemove={editing ? () => layout.removeItem(displayItem.instanceId) : undefined}
              >
                <div
                  className={
                    'ov-section-divider' +
                    (displayItem.collapsed ? ' ov-section-divider--collapsed' : '')
                  }
                  onClick={
                    !editing && !renaming
                      ? () =>
                          layout.patchItem(displayItem.instanceId, {
                            collapsed: !displayItem.collapsed,
                          })
                      : undefined
                  }
                >
                  <button
                    type="button"
                    className={
                      'ov-section-chevron' +
                      (displayItem.collapsed ? ' ov-section-chevron--collapsed' : '')
                    }
                    onClick={(e) => {
                      e.stopPropagation()
                      layout.patchItem(displayItem.instanceId, {
                        collapsed: !displayItem.collapsed,
                      })
                    }}
                    aria-label={displayItem.collapsed ? 'Expand section' : 'Collapse section'}
                  >
                    {chevronIcon}
                  </button>
                  {renaming ? (
                    <input
                      className="ov-section-rename"
                      autoFocus
                      defaultValue={displayItem.title}
                      onClick={(e) => e.stopPropagation()}
                      onPointerDown={(e) => e.stopPropagation()}
                      onBlur={(e) => {
                        layout.patchItem(displayItem.instanceId, {
                          title: e.target.value.trim() || 'Section',
                        })
                        setRenamingSectionId(null)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') e.currentTarget.blur()
                        if (e.key === 'Escape') setRenamingSectionId(null)
                      }}
                    />
                  ) : (
                    <span
                      className="ov-section-title"
                      title="Double-click to rename"
                      onClick={(e) => e.stopPropagation()}
                      onPointerDown={(e) => e.stopPropagation()}
                      onDoubleClick={(e) => {
                        e.stopPropagation()
                        setRenamingSectionId(displayItem.instanceId)
                      }}
                    >
                      {displayItem.title}
                    </span>
                  )}
                  <span className="ov-section-rule" />
                  <span className="ov-section-meta">
                    {count} widget{count === 1 ? '' : 's'}
                    {displayItem.collapsed ? ' hidden' : ''}
                  </span>
                </div>
              </GridCard>
            )
          }
          const def = findDefinition(displayItem.defId)
          const Content = WIDGET_COMPONENTS[displayItem.defId]
          const label = displayItem.title || def?.title || def?.label || 'Widget'
          // The widget header's ⋯ (view mode only — Customize has its own in-card toolbar).
          // WidgetShell lists the editor first, then Full screen / Collapse, then these.
          const primaryMenuItems: KebabItem[] = [
            { label: 'Edit…', onClick: () => openFullscreen(displayItem.instanceId, 'edit') },
          ]
          const sourceTab = def?.source && def.source.tab !== 'dashboard' ? def.source : null
          const menuItems: KebabItem[] = [
            { label: 'Display…', onClick: () => setDisplayFor(displayItem.instanceId) },
            ...(onShareWithLucius
              ? [{ label: 'Share with Lucius', onClick: () => onShareWithLucius(label) }]
              : []),
            { label: 'Duplicate', onClick: () => layout.duplicateItem(displayItem.instanceId) },
            ...(sourceTab
              ? [
                  {
                    label: `Open in ${sourceTab.label}`,
                    onClick: () => onNavigate?.(sourceTab.tab),
                  },
                ]
              : []),
            {
              label: 'Remove',
              danger: true,
              onClick: () => layout.removeItem(displayItem.instanceId),
            },
          ]
          const idx = sorted.indexOf(displayItem)
          return (
            <GridCard
              key={displayItem.instanceId}
              instanceId={displayItem.instanceId}
              x={pos.x}
              y={pos.y}
              w={displayItem.w}
              h={displayItem.h}
              editing={editing}
              autoHeight={
                !narrow &&
                fitMode(displayItem.defId, displayItem.heightMode) &&
                !displayItem.collapsed
              }
              accent={displayItem.accent}
              hideTitle={displayItem.hideTitle}
              dragging={drag?.kind === 'move' && drag.instanceId === displayItem.instanceId}
              resizing={drag?.kind === 'resize' && drag.instanceId === displayItem.instanceId}
              isNew={layout.newlyAddedId === displayItem.instanceId}
              collapsed={!!displayItem.collapsed}
              collapsedLabel={label}
              onToggleCollapse={() =>
                layout.patchItem(displayItem.instanceId, { collapsed: !displayItem.collapsed })
              }
              onOpenFullscreen={() => openFullscreen(displayItem.instanceId)}
              onEdit={() => openFullscreen(displayItem.instanceId, 'edit')}
              isAutoh={AUTOH_IDS.has(displayItem.defId)}
              fitOn={fitMode(displayItem.defId, displayItem.heightMode)}
              onToggleFit={() =>
                layout.patchItem(displayItem.instanceId, {
                  heightMode: fitMode(displayItem.defId, displayItem.heightMode)
                    ? 'fixed'
                    : undefined,
                })
              }
              onRemove={editing ? () => layout.removeItem(displayItem.instanceId) : undefined}
              onDragPointerDown={(e) => beginDrag(displayItem, e)}
              onResizePointerDown={(e, dirX, dirY) => beginResize(displayItem, e, dirX, dirY)}
              resizeReadout={`${pos.w} / 12 cols`}
              onMoveUp={
                editing && idx > 0 ? () => moveInOrder(displayItem.instanceId, -1) : undefined
              }
              onMoveDown={
                editing && idx < sorted.length - 1
                  ? () => moveInOrder(displayItem.instanceId, 1)
                  : undefined
              }
            >
              {def && Content ? (
                <GridItemContext.Provider
                  value={{
                    instanceId: displayItem.instanceId,
                    editing,
                    icon: def.icon,
                    onRenameTitle: (title) => layout.patchItem(displayItem.instanceId, { title }),
                    collapsed: !!displayItem.collapsed,
                    onToggleCollapse: () =>
                      layout.patchItem(displayItem.instanceId, {
                        collapsed: !displayItem.collapsed,
                      }),
                    onOpenFullscreen: () => openFullscreen(displayItem.instanceId),
                    primaryMenuItems,
                    menuItems,
                    setFit,
                  }}
                >
                  <ErrorBoundary
                    key={displayItem.instanceId}
                    label={label}
                    fallback={
                      <WidgetShell base={label}>
                        <WidgetPlaceholder
                          title="This widget hit an error."
                          detail="Remove it, or try again from Customize mode."
                        />
                      </WidgetShell>
                    }
                  >
                    <Content
                      instance={displayItem}
                      def={def}
                      onPatch={(patch) => layout.patchItem(displayItem.instanceId, patch)}
                      onNavigate={onNavigate}
                      onOpenAsset={onOpenAsset}
                      onAskLucius={onAskLucius}
                      onShareWithLucius={onShareWithLucius}
                      onOpenRegister={onOpenRegister}
                    />
                  </ErrorBoundary>
                </GridItemContext.Provider>
              ) : (
                <WidgetShell base={displayItem.title || 'Unknown widget'}>
                  <WidgetPlaceholder
                    title="This widget type is no longer available"
                    detail="Remove it from Customize mode."
                  />
                </WidgetShell>
              )}
            </GridCard>
          )
        })}
      </div>
      {dialogs}
    </>
  )
}

// Re-exported so RootOverview can type onOpenAsset without importing ComputedRow itself.
export type { ComputedRow }
