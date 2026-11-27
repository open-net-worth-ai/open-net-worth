// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PerfChart } from './PerfChart'
import { fmtGap, nearestIndex } from './perfChartFormat'
import type { ReturnSeriesPoint } from '../../../types/portfolio'

afterEach(cleanup)

const series: ReturnSeriesPoint[] = [
  { date: '2025-06-02', portfolio: 0, benchmark: 0 },
  { date: '2025-06-03', portfolio: 0.123, benchmark: 0.053 },
  { date: '2025-11-14', portfolio: -0.2, benchmark: null },
  { date: '2026-01-05', portfolio: 0.05, benchmark: 0.08 },
]

function chart() {
  render(<PerfChart series={series} hasBenchmark benchLabel="ACWI" />)
  return screen.getByRole('group')
}

function tip(): string {
  return document.querySelector('.mk-chart-tip')?.textContent ?? ''
}

function mockWidth(el: HTMLElement) {
  el.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 300, height: 200, right: 300, bottom: 200 }) as DOMRect
}

describe('PerfChart tooltip', () => {
  it('snaps a mouse to the nearest point and reads that point’s own date', () => {
    const el = chart()
    mockWidth(el)
    fireEvent.pointerMove(el, { clientX: 110, pointerType: 'mouse' })
    expect(tip()).toContain('Jun 3, 2025')
    expect(tip()).toContain('Portfolio+12.3%')
    expect(tip()).toContain('ACWI+5.3%')
    expect(tip()).toContain('Difference+7.0 pts')
    fireEvent.pointerLeave(el, { pointerType: 'mouse' })
    expect(document.querySelector('.mk-chart-tip')).toBeNull()
  })

  it('says a missing benchmark reading is missing, not 0%', () => {
    const el = chart()
    mockWidth(el)
    fireEvent.pointerMove(el, { clientX: 200, pointerType: 'mouse' })
    expect(tip()).toContain('Nov 14, 2025')
    expect(tip()).toContain('ACWI—')
    expect(tip()).not.toContain('Difference')
  })

  it('opens on a tap and stays after the finger lifts', () => {
    const el = chart()
    mockWidth(el)
    fireEvent.pointerDown(el, { clientX: 300, pointerType: 'touch' })
    fireEvent.pointerLeave(el, { pointerType: 'touch' })
    expect(tip()).toContain('Jan 5, 2026')
    fireEvent.pointerDown(document.body, { pointerType: 'touch' })
    expect(document.querySelector('.mk-chart-tip')).toBeNull()
  })

  it('steps point by point with the arrow keys and announces each', () => {
    const el = chart()
    el.focus()
    fireEvent.keyDown(el, { key: 'ArrowLeft' })
    expect(tip()).toContain('Jan 5, 2026')
    fireEvent.keyDown(el, { key: 'ArrowLeft' })
    expect(tip()).toContain('Nov 14, 2025')
    fireEvent.keyDown(el, { key: 'Home' })
    expect(tip()).toContain('Jun 2, 2025')
    fireEvent.keyDown(el, { key: 'ArrowRight' })
    expect(tip()).toContain('Jun 3, 2025')
    expect(document.querySelector('[aria-live]')?.textContent).toContain('difference +7.0 pts')
    fireEvent.keyDown(el, { key: 'Escape' })
    expect(document.querySelector('.mk-chart-tip')).toBeNull()
  })
})

describe('helpers', () => {
  it('nearestIndex clamps and survives a zero-width box', () => {
    expect(nearestIndex(-1, 5)).toBe(0)
    expect(nearestIndex(2, 5)).toBe(4)
    expect(nearestIndex(0.49, 5)).toBe(2)
    expect(nearestIndex(NaN, 5)).toBe(0)
  })
  it('fmtGap reads in points', () => {
    expect(fmtGap(-0.23)).toBe('−23.0 pts')
  })
})
