// @vitest-environment jsdom
// The shell-wide "Saving…" pill: a section wraps its write in useSaving()'s track, the shell
// counts what is in flight, and the pill shows only when a write is slow.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { SavingIndicator, SectionSavingProvider, useSaving } from './ds'

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useSaving', () => {
  function Probe({
    work,
    out,
  }: {
    work: () => Promise<unknown>
    out: { run?: () => Promise<unknown> }
  }) {
    const track = useSaving()
    out.run = () => track(work)
    return null
  }

  it('counts a write while it is pending and releases it when it settles', async () => {
    const deltas: number[] = []
    const out: { run?: () => Promise<unknown> } = {}
    let finish: (v: string) => void = () => {}
    const work = () => new Promise<string>((r) => (finish = r))
    render(
      <SectionSavingProvider onChange={(d) => deltas.push(d)}>
        <Probe work={work} out={out} />
      </SectionSavingProvider>,
    )
    const result = out.run!()
    expect(deltas).toEqual([1])
    finish('ok')
    await expect(result).resolves.toBe('ok')
    expect(deltas).toEqual([1, -1])
  })

  it('releases it when the write throws, and rethrows', async () => {
    const deltas: number[] = []
    const out: { run?: () => Promise<unknown> } = {}
    render(
      <SectionSavingProvider onChange={(d) => deltas.push(d)}>
        <Probe work={() => Promise.reject(new Error('boom'))} out={out} />
      </SectionSavingProvider>,
    )
    await expect(out.run!()).rejects.toThrow('boom')
    expect(deltas).toEqual([1, -1])
  })

  it('is a plain pass-through outside the Settings shell', async () => {
    const out: { run?: () => Promise<unknown> } = {}
    render(<Probe work={async () => 7} out={out} />)
    await expect(out.run!()).resolves.toBe(7)
  })
})

describe('SavingIndicator', () => {
  it('stays hidden for a fast save and shows for a slow one', () => {
    const { rerender } = render(<SavingIndicator active={false} />)
    expect(screen.queryByRole('status')).toBeNull()

    rerender(<SavingIndicator active />)
    act(() => void vi.advanceTimersByTime(150))
    expect(screen.queryByRole('status')).toBeNull()

    act(() => void vi.advanceTimersByTime(100))
    expect(screen.getByRole('status').textContent).toContain('Saving')

    rerender(<SavingIndicator active={false} />)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('never shows for a save that finishes inside the delay', () => {
    const { rerender } = render(<SavingIndicator active />)
    act(() => void vi.advanceTimersByTime(100))
    rerender(<SavingIndicator active={false} />)
    act(() => void vi.advanceTimersByTime(500))
    expect(screen.queryByRole('status')).toBeNull()
  })
})
