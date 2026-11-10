// @vitest-environment jsdom
//
// useLuciusTab is the DESKTOP dock's open flag and nothing more. On a phone Lucius is the
// `/lucius` route (docs/adr/2026-09-05-phone-lucius-is-the-lucius-route.md), so the hook no
// longer takes a viewport or a pathname and never closes anything on navigation.
//
// History: it used to close the flag on every route change on a phone — the one rule that
// stopped a route from changing underneath a still-open Lucius (owner, 2026-07-30). That rule
// is now structural: leaving Lucius on a phone IS a route change.

import { act, renderHook } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startFreshConversation } from '../utils/freshConversation'
import { isLuciusDockVisible, routeNewConversation, useLuciusTab } from './useLuciusTab'

beforeEach(() => localStorage.clear())
afterEach(() => localStorage.clear())

describe('useLuciusTab', () => {
  it('does not reserve the desktop dock while the Lucius main tab is visible', () => {
    expect(isLuciusDockVisible(true, false, 'lucius')).toBe(false)
    expect(isLuciusDockVisible(true, false, 'accounts')).toBe(true)
    expect(isLuciusDockVisible(true, true, 'accounts')).toBe(false)
  })

  it('restores the persisted open state on mount', () => {
    localStorage.setItem('chat-open', 'true')
    const { result } = renderHook(() => useLuciusTab())
    expect(result.current[0]).toBe(true)
  })

  it('persists every change so a reload lands on the same surface', () => {
    const { result } = renderHook(() => useLuciusTab())
    act(() => result.current[1](true))
    expect(result.current[0]).toBe(true)
    expect(localStorage.getItem('chat-open')).toBe('true')
    act(() => result.current[1](false))
    expect(localStorage.getItem('chat-open')).toBe('false')
  })

  it('takes a functional update (the ⌘/ toggle)', () => {
    const { result } = renderHook(() => useLuciusTab())
    act(() => result.current[1]((o) => !o))
    expect(result.current[0]).toBe(true)
  })

  it('survives StrictMode double-invoking effects on mount', () => {
    localStorage.setItem('chat-open', 'true')
    const { result } = renderHook(() => useLuciusTab(), { wrapper: StrictMode })
    expect(result.current[0]).toBe(true)
  })

  it('the setter is stable across renders', () => {
    const { result, rerender } = renderHook(() => useLuciusTab())
    const first = result.current[1]
    rerender()
    expect(result.current[1]).toBe(first)
  })
})

describe('Cmd/Ctrl+N new conversation', () => {
  const surfaces = () => ({ startFresh: vi.fn(), focusPanel: vi.fn(), openLuciusTab: vi.fn() })

  it('opens it in the side panel while the panel is showing', () => {
    const s = surfaces()
    routeNewConversation(true, s)
    expect(s.startFresh).toHaveBeenCalledTimes(1)
    expect(s.focusPanel).toHaveBeenCalledTimes(1)
    expect(s.openLuciusTab).not.toHaveBeenCalled()
  })

  it('opens it on the Lucius screen when the side panel is closed', () => {
    const s = surfaces()
    routeNewConversation(false, s)
    expect(s.startFresh).toHaveBeenCalledTimes(1)
    expect(s.openLuciusTab).toHaveBeenCalledTimes(1)
    expect(s.focusPanel).not.toHaveBeenCalled()
  })

  it('reuses an unsaved empty conversation instead of minting another', () => {
    const createConversation = vi.fn(() => 'new')
    const conversations = [{ id: 'saved' }] as Parameters<
      typeof startFreshConversation
    >[0]['conversations']
    startFreshConversation({ conversations, activeId: 'unsaved', createConversation })
    expect(createConversation).not.toHaveBeenCalled()
    startFreshConversation({ conversations, activeId: 'saved', createConversation })
    expect(createConversation).toHaveBeenCalledTimes(1)
  })
})
