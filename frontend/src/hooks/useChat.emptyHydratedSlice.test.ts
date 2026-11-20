// @vitest-environment jsdom
//
// An Automation's conversation rendered the empty-state greeting forever, while the run card
// beside it showed a completed run with a full task list. The server had the transcript the
// whole time — 38 entries, a 10k-char opening message and an assistant turn carrying 20 tool
// blocks (owner, 2026-08-16).
//
// The slice was sealed empty. `useChat`'s hydration effect skipped on `slice.hydrated` alone,
// and the apply inside it deferred to `s.hydrated` alone. So opening the conversation ONCE
// while it was still empty — mid-run, or before the server had written anything — stamped
// `{messages: [], hydrated: true}` permanently. The effect never re-ran for that conversation,
// and `reconcileWithRemote` could not rescue it either: `shouldAdoptRemote` returns false on
// an empty remote, so the one check that might have refilled it declined too.
//
// A LIVE run looked fine throughout, because the live-attach path populates the slice without
// going through hydration at all. That is why the bug read as random rather than as a rule.
//
// Hydrated-AND-EMPTY is the absence of a result, not a result. The guard exists to stop a
// stale cache read clobbering a POPULATED slice; an empty one has nothing to protect.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

const loadMessages = vi.fn()
const reconcileWithRemote = vi.fn()

vi.mock('../utils/chatStore', () => ({
  fetchServerSnapshot: vi.fn().mockResolvedValue(null),
  loadMessages: (...a: unknown[]) => loadMessages(...a),
  reconcileWithRemote: (...a: unknown[]) => reconcileWithRemote(...a),
  saveMessages: vi.fn(),
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

import { useChat } from './useChat'

const MSG = { id: 'automation-run-x-2', role: 'assistant', text: 'Logged 8 rows.', blocks: [] }

beforeEach(() => {
  vi.clearAllMocks()
  reconcileWithRemote.mockResolvedValue(null)
})

describe('useChat: a slice hydrated while empty must not seal the conversation', () => {
  it('refetches when the conversation is reopened, and shows what the server has', async () => {
    // First visit: the run has written nothing yet, so both the cache and the server are
    // empty. This is the state that used to become permanent.
    loadMessages.mockResolvedValueOnce([])
    const first = renderHook(() => useChat('automation-run-x', vi.fn()))
    await waitFor(() => expect(first.result.current.hydrated).toBe(true))
    expect(first.result.current.messages).toEqual([])
    first.unmount()

    // Reopened later, after the run finished writing its transcript.
    loadMessages.mockResolvedValueOnce([MSG])
    const second = renderHook(() => useChat('automation-run-x', vi.fn()))
    await waitFor(() => expect(second.result.current.messages).toHaveLength(1))
    expect(second.result.current.messages[0]?.text).toBe('Logged 8 rows.')
  })

  it('still refuses to reload over a slice that already holds messages', async () => {
    // The guard's real job, unchanged: a populated slice is authoritative, and a second
    // mount must not spend a fetch — nor risk clobbering it with a staler read.
    loadMessages.mockResolvedValueOnce([MSG])
    const first = renderHook(() => useChat('conv-populated', vi.fn()))
    await waitFor(() => expect(first.result.current.messages).toHaveLength(1))
    first.unmount()

    loadMessages.mockClear()
    const second = renderHook(() => useChat('conv-populated', vi.fn()))
    await act(async () => {})
    expect(loadMessages).not.toHaveBeenCalled()
    expect(second.result.current.messages).toHaveLength(1)
  })

  it('adopts the server transcript even when the first load came back null', async () => {
    // `loadMessages` returns null when the cache is empty AND the fetch failed. The slice
    // still had to be marked hydrated (so the UI stops waiting), which is precisely how it
    // got sealed; the follow-up reconcile must be free to fill it.
    loadMessages.mockResolvedValueOnce(null)
    reconcileWithRemote.mockResolvedValueOnce([MSG])
    const { result } = renderHook(() => useChat('automation-run-y', vi.fn()))
    await waitFor(() => expect(result.current.messages).toHaveLength(1))
  })
})
