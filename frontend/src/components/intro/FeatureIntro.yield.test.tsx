// @vitest-environment jsdom
// An intro already on screen steps aside when another dialog opens after it — Lucius's setup
// card landed on Connectors, and the intro claimed the screen a beat before the setup form
// mounted, ending up on top of it (owner test, 2026-10-31). It stays away for the visit and is
// left unseen, so a later visit can still show it.
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FeatureIntro } from './FeatureIntro'
import { resetGuidedForTests } from './tourTarget'

beforeEach(() => {
  vi.useFakeTimers()
  resetGuidedForTests()
})
afterEach(() => {
  cleanup()
  document.querySelectorAll('[data-test-dialog]').forEach((d) => d.remove())
  vi.useRealTimers()
})

function openDialog(): HTMLElement {
  const d = document.createElement('div')
  d.setAttribute('role', 'dialog')
  d.setAttribute('aria-modal', 'true')
  d.setAttribute('data-test-dialog', '')
  document.body.appendChild(d)
  return d
}

it('steps aside for a dialog that opens after it, and stays away for the visit', () => {
  const onClose = vi.fn()
  render(
    <FeatureIntro
      open
      steps={[{ title: 'Extend what Lucius can do', points: [] }]}
      lucius={{ label: 'Set up with Lucius', onClick: () => {} }}
      secondary={{ label: 'Set up myself', onClick: () => {} }}
      onClose={onClose}
    />,
  )
  expect(screen.getAllByText('Extend what Lucius can do').length).toBeGreaterThan(0)

  const setupForm = openDialog()
  act(() => {
    vi.advanceTimersByTime(800)
  })
  expect(screen.queryAllByText('Extend what Lucius can do')).toEqual([])
  // Not dismissed by the user, so not marked seen.
  expect(onClose).not.toHaveBeenCalled()

  setupForm.remove()
  act(() => {
    vi.advanceTimersByTime(2000)
  })
  expect(screen.queryAllByText('Extend what Lucius can do')).toEqual([])
})
