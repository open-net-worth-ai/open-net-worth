// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { FeatureIntro } from './FeatureIntro'
import { markGuided, resetGuidedForTests, tourShowing } from './tourTarget'

afterEach(() => {
  cleanup()
  resetGuidedForTests()
  // A stand-in dialog left by a failed test would hold every later intro back.
  document.querySelectorAll('.modal-overlay').forEach((el) => el.remove())
})

it('an intro waits while a tour or tip is on screen, then opens', () => {
  const release = tourShowing()
  render(
    <FeatureIntro
      open
      steps={[{ title: 'Hello', points: [] }]}
      lucius={{ label: 'Set up with Lucius', onClick: vi.fn() }}
      secondary={{ label: 'Got it', onClick: vi.fn() }}
      onClose={vi.fn()}
    />,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
  act(() => release())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
})

const intro = (onClose = vi.fn()) => (
  <FeatureIntro
    open
    steps={[{ title: 'Hello', points: [] }]}
    lucius={{ label: 'Set up with Lucius', onClick: vi.fn() }}
    secondary={{ label: 'Got it', onClick: vi.fn() }}
    onClose={onClose}
  />
)

it('an intro waits for a later visit once another guide has shown in this one', () => {
  markGuided() // e.g. the paperclip guide Lucius asked for during onboarding
  const onClose = vi.fn()
  render(intro(onClose))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(onClose).not.toHaveBeenCalled() // still unseen, so it comes back next visit
})

it('an intro waits while another dialog is open, then opens once it closes', () => {
  vi.useFakeTimers()
  try {
    // The statement import, open when the Accounts intro became due (desktop e2e, 2026-10-27).
    const other = document.createElement('div')
    other.className = 'modal-overlay'
    document.body.appendChild(other)
    render(intro())
    act(() => vi.advanceTimersByTime(1500))
    expect(screen.queryByRole('dialog')).toBeNull()
    other.remove()
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
    // Its own overlay doesn't make it wait on itself.
    act(() => vi.advanceTimersByTime(1500))
    expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
  } finally {
    vi.useRealTimers()
  }
})

it('an intro mounted before a dialog opened still waits for that dialog', () => {
  vi.useFakeTimers()
  try {
    // The Accounts intro mounts with the screen, held back while Lucius replies; the import
    // opens; his reply fails and the intro becomes due over it (desktop e2e, 2026-10-28).
    const held = (open: boolean) => (
      <FeatureIntro
        open={open}
        steps={[{ title: 'Hello', points: [] }]}
        lucius={{ label: 'Set up with Lucius', onClick: vi.fn() }}
        secondary={{ label: 'Got it', onClick: vi.fn() }}
        onClose={vi.fn()}
      />
    )
    const { rerender } = render(held(false))
    const other = document.createElement('div')
    other.className = 'modal-overlay'
    document.body.appendChild(other)
    rerender(held(true))
    act(() => vi.advanceTimersByTime(1500))
    expect(screen.queryByRole('dialog')).toBeNull()
    other.remove()
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
  } finally {
    vi.useRealTimers()
  }
})

it('the intro that took the visit stays open', () => {
  const { rerender } = render(intro())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
  rerender(intro())
  expect(screen.getByRole('dialog', { name: 'Hello' })).toBeTruthy()
})
