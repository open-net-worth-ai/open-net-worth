// The segmented "how it's funded" bar — shared by the Funding tab's full-size render
// (detailFunding.tsx) and the Overview tab's condensed digest (detailOverview.tsx), so both
// surfaces get the identical hover-popover behavior (owner: "let's reuse the same chart/
// implementation for these two") on top of the coverage math they already share via
// computeCoverage. Owns the hover-intent state machine (open/close delay, cancel-on-enter)
// so neither caller has to re-implement it. The segment/track itself renders via the shared
// <Meter> primitive (frontend/src/components/ui/Meter.tsx) — this component only supplies the
// per-type color (a domain decision that stays local, in commitments.css) and the segment event
// handlers that drive the hover-intent state machine + popover below.
import { useEffect, useRef, useState } from 'react'
import type { FocusEvent, MouseEvent } from 'react'
import { fBase } from '../../../utils/format'
import { fCompactCurrency } from '../../../utils/commitments'
import { FundingSourcesPopover } from './FundingSourcesPopover'
import { Meter } from '../../ui'
import type { MeterSegment } from '../../ui'
import type { CoverageBar } from './detailFunding'
import type { FundingSource } from '../../../types/commitments'
import type { PositionMeta } from './fundingValue'

const HOVER_INTENT_MS = 150

interface Props {
  segs: CoverageBar['segs']
  gap: number
  scaleMax: number
  remaining: number
  fundingPlan: FundingSource[]
  positions: Record<string, PositionMeta>
}

// The "gap" (unfunded) segment, appended after the real funding-type segments when there's a
// shortfall — a plain, non-interactive entry (Meter renders it as a <div> since it carries no
// event handlers), matching the original hand-rolled bar exactly.
function gapSegment(gap: number, scaleMax: number): MeterSegment | null {
  if (gap <= 0) return null
  return {
    id: 'gap',
    pct: (gap / scaleMax) * 100,
    className: 'gap',
    title: `Unfunded: ${fBase(gap)}`,
    ariaLabel: `Unfunded: ${fBase(gap)}`,
    content: <span className="money">{fCompactCurrency(gap)}</span>,
  }
}

export function FundingCoverageBar({
  segs,
  gap,
  scaleMax,
  remaining,
  fundingPlan,
  positions,
}: Props) {
  const [hovered, setHovered] = useState<{ type: string; rect: DOMRect } | null>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (closeTimer.current) clearTimeout(closeTimer.current)
      if (openTimer.current) clearTimeout(openTimer.current)
    }
  }, [])

  const scheduleOpen = (type: string, rect: DOMRect) => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
    if (openTimer.current) clearTimeout(openTimer.current)
    openTimer.current = setTimeout(() => setHovered({ type, rect }), HOVER_INTENT_MS)
  }
  const scheduleClose = () => {
    if (openTimer.current) {
      clearTimeout(openTimer.current)
      openTimer.current = null
    }
    if (closeTimer.current) clearTimeout(closeTimer.current)
    closeTimer.current = setTimeout(() => setHovered(null), HOVER_INTENT_MS)
  }
  // Hover intent is a mouse concept. A tap must open (or close) the segment IMMEDIATELY —
  // waiting HOVER_INTENT_MS after a finger lands reads as a dropped tap. Toggling is also the
  // only dismiss a touch user has, since no mouseleave ever arrives.
  const toggleNow = (type: string, rect: DOMRect) => {
    if (openTimer.current) {
      clearTimeout(openTimer.current)
      openTimer.current = null
    }
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
    setHovered((cur) => (cur?.type === type ? null : { type, rect }))
  }
  const cancelClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }

  const hoveredSeg = hovered ? segs.find((s) => s.type === hovered.type) : undefined

  useEffect(() => {
    if (!hovered) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setHovered(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [hovered])

  // A <button> segment, not a <div>: the sources breakdown was hover-only, which excluded both
  // touch and keyboard users from it entirely.
  const typeSegments: MeterSegment[] = segs
    .filter((s) => s.pct > 0.5)
    .map((s) => ({
      id: s.type,
      pct: s.pct,
      className: s.type,
      ariaLabel: `${s.label}: ${fBase(s.val)}`,
      ariaExpanded: hovered?.type === s.type,
      content: <span className="money">{fCompactCurrency(s.val)}</span>,
      onClick: (e: MouseEvent<HTMLButtonElement>) =>
        toggleNow(s.type, e.currentTarget.getBoundingClientRect()),
      onMouseEnter: (e: MouseEvent<HTMLButtonElement>) =>
        scheduleOpen(s.type, e.currentTarget.getBoundingClientRect()),
      onMouseLeave: () => scheduleClose(),
      onFocus: (e: FocusEvent<HTMLButtonElement>) =>
        scheduleOpen(s.type, e.currentTarget.getBoundingClientRect()),
      onBlur: () => scheduleClose(),
    }))
  const gapSeg = gapSegment(gap, scaleMax)
  const meterSegments = gapSeg ? [...typeSegments, gapSeg] : typeSegments

  return (
    <>
      <Meter
        variant="linear"
        size="lg"
        segments={meterSegments}
        ariaLabel={`Funding coverage: ${fBase(Math.max(0, remaining - gap))} of ${fBase(remaining)} covered`}
      />
      {hovered && hoveredSeg && (
        <FundingSourcesPopover
          anchorRect={hovered.rect}
          type={hoveredSeg.type}
          typeLabel={hoveredSeg.label}
          typeTotal={hoveredSeg.val}
          sources={hoveredSeg.sources}
          fundingPlan={fundingPlan}
          positions={positions}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        />
      )}
      <div className="c2-fund-scale">
        <span className="money">{fBase(0)}</span>
        <span>
          <span className="money">{fBase(remaining)}</span> to cover
        </span>
      </div>
    </>
  )
}
