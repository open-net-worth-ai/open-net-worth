// @vitest-environment jsdom
//
// permissions_v2: a card has three answers, and the one given is stored on the card — `status`
// alone cannot tell an approval from an Always allow, and a reload must still read "Approved",
// "Always allowed" or "Denied". `always` goes to the server only when it is true, so a card
// that never sets it (the old one) sends the body it always did.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../utils/chatStore', () => ({
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: vi.fn().mockResolvedValue(null),
  saveMessages: vi.fn(),
}))

import { useChat } from './useChat'
import { authFetch } from '../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
const encoder = new TextEncoder()

const PAUSED_TURN = [
  '{"type":"tool_use_start","name":"delete_transactions","id":"toolu_1"}',
  '{"type":"proposed_change","tool_use_id":"toolu_1","change":{"type":"tool_permission","grant_tool":"delete_transactions"}}',
  '{"type":"turn_done","usage":{}}',
]

function mockRoutes(liveEvents: string[]) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (typeof url === 'string' && url.includes('/live')) {
      const body = liveEvents.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
      return new Response(
        new ReadableStream({
          start(ctrl) {
            ctrl.enqueue(encoder.encode(body))
            ctrl.close()
          },
        }),
        { status: 200 },
      )
    }
    return new Response(
      JSON.stringify({ session_id: 'x', turn_id: 'turn_test', accepted: 'started' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    )
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useChat: which answer a permission card was given', () => {
  it.each([
    ['approve', undefined, 'approved'],
    ['approve', { always: true }, 'always_allowed'],
    ['deny', undefined, 'denied'],
  ] as const)(
    'stores WHICH answer %s %j was on the card (%s), so a reload can say so',
    async (decision, options, resolution) => {
      mockRoutes(PAUSED_TURN)
      const { result } = renderHook(() => useChat(`conv-outcome-${resolution}`, vi.fn()))
      await act(async () => {})
      await act(async () => {
        await result.current.sendMessage('delete the duplicate')
      })
      const assistant = () =>
        result.current.messages.find((m) => m.role === 'assistant') as
          | {
              awaitingApproval?: boolean
              proposedChanges?: { status?: string; resolution?: string }[]
            }
          | undefined
      await waitFor(() => expect(assistant()?.awaitingApproval).toBe(true))

      await act(async () => {
        await result.current.resolvePendingToolUse('toolu_1', decision, options)
      })

      const change = assistant()?.proposedChanges?.[0]
      expect(change?.status).toBe(decision === 'approve' ? 'confirmed' : 'dismissed')
      expect(change?.resolution).toBe(resolution)
      const call = mockAuthFetch.mock.calls.find((c) => String(c[0]).endsWith('/resolve'))
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
        tool_use_id: 'toolu_1',
        decision,
        ...(options?.always ? { always: true } : {}),
      })
    },
  )
})
