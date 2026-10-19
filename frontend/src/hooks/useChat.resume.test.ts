// @vitest-environment jsdom
//
// Regression tests for the pause→approve→resume lifecycle of ONE logical turn
// (RFC 2026-07-22 permission cards).
//
// A confirm/permission card ENDS the backend stream, so `turn_done` arrives while the model
// is still mid-task. Live report 2026-07-22: after approving, the continuation streamed into
// a bubble still rendered as finished — no typing cursor, and a cost chip already showing —
// "makes users feel like the chat is already finished".
//
// The contract pinned here:
//   1. a turn that ends on a proposed change is flagged `awaitingApproval` (→ no cost chip)
//   2. resolving re-enters `streaming: true` on the SAME assistant message, so the typing
//      indicator comes back for the resumed stream
//   3. usage ACCUMULATES across the paused + resumed segments — the chip after approval is
//      the whole turn's cost, not just the continuation's
//
// Server-owned turn lifetime (concurrent-conversations RFC Phase 3): sendMessage /
// resolvePendingToolUse now POST for a 202 and the attach effect (a separate, un-awaited
// useEffect) does the actual GET /api/sessions/{id}/live + consumeEventStream. `waitFor` is
// used wherever the ORIGINAL test relied on `sendMessage`/`resolvePendingToolUse` itself
// having consumed the whole stream by the time it resolved.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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

function json202(): Response {
  return new Response(
    JSON.stringify({ session_id: 'x', turn_id: 'turn_test', accepted: 'started' }),
    { status: 202, headers: { 'Content-Type': 'application/json' } },
  )
}

function sseResponse(events: string[]): Response {
  const body = events.map((e) => `data: ${e}\n\n`).join('') + 'data: [DONE]\n\n'
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

// Routes on URL: POST /api/chat(/resolve) → 202 JSON; GET .../live → the scripted SSE body.
function mockRoutes(liveEvents: string[]) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (typeof url === 'string' && url.includes('/live')) return sseResponse(liveEvents)
    return json202()
  })
}

const PAUSED_TURN = [
  '{"type":"tool_use_start","name":"delete_transactions","id":"toolu_1"}',
  '{"type":"proposed_change","tool_use_id":"toolu_1","change":{"type":"tool_permission","grant_tool":"delete_transactions"}}',
  '{"type":"turn_done","usage":{"input_tokens":4,"output_tokens":188,"cost_usd":0.474}}',
]

const TWO_PAUSED_CALLS = [
  '{"type":"tool_use_start","name":"delete_transactions","id":"toolu_1"}',
  '{"type":"proposed_change","tool_use_id":"toolu_1","change":{"type":"tool_permission","grant_tool":"delete_transactions"}}',
  '{"type":"tool_use_start","name":"delete_transactions","id":"toolu_2"}',
  '{"type":"proposed_change","tool_use_id":"toolu_2","change":{"type":"tool_permission","grant_tool":"delete_transactions"}}',
  '{"type":"turn_done","usage":{}}',
]

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.clearAllMocks()
})

function assistantMessage(result: { current: { messages: { role: string }[] } }) {
  return result.current.messages.find((m) => m.role === 'assistant') as
    | ((typeof result.current.messages)[number] & {
        id: string
        awaitingApproval?: boolean
        streaming?: boolean
        usage?: { cost_usd?: number; output_tokens?: number }
        error?: string
        blocks?: { type: string; change?: { status?: string; refused?: boolean } }[]
        proposedChanges?: { status?: string; refused?: boolean }[]
        text?: string
      })
    | undefined
}

describe('useChat: a turn paused on a permission card', () => {
  it('flags the message awaiting-approval so the cost chip is withheld', async () => {
    mockRoutes(PAUSED_TURN)
    const { result } = renderHook(() => useChat('conv-pause', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete the duplicate')
    })

    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))
    // The usage is recorded (it is real spend) — it is the RENDER that withholds it.
    expect(assistantMessage(result)?.usage?.cost_usd).toBe(0.474)
  })

  it('an ordinary turn is NOT flagged awaiting-approval', async () => {
    mockRoutes(['{"type":"text_delta","text":"done"}', '{"type":"turn_done","usage":{}}'])
    const { result } = renderHook(() => useChat('conv-plain', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('hi')
    })

    await waitFor(() => expect(result.current.streaming).toBe(false))
    expect(assistantMessage(result)?.awaitingApproval).toBe(false)
  })

  it('resolving re-enters streaming on the same message and accumulates the turn cost', async () => {
    mockRoutes(PAUSED_TURN)
    const { result } = renderHook(() => useChat('conv-resume', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete the duplicate')
    })
    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

    // Hold the CONTINUATION's stream open (GET /live, triggered by resolvePendingToolUse
    // below) until we have inspected the genuinely-mid-flight state.
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => {
      release = r
    })
    mockAuthFetch.mockImplementation(async (url: string) => {
      if (typeof url === 'string' && url.includes('/live')) {
        return new Response(
          new ReadableStream({
            async start(ctrl) {
              ctrl.enqueue(encoder.encode('data: {"type":"text_delta","text":"deleted."}\n\n'))
              await gate
              ctrl.enqueue(
                encoder.encode(
                  'data: {"type":"turn_done","usage":{"input_tokens":6,"output_tokens":40,"cost_usd":0.1}}\n\ndata: [DONE]\n\n',
                ),
              )
              ctrl.close()
            },
          }),
          { status: 200 },
        )
      }
      return json202()
    })

    await act(async () => {
      await result.current.resolvePendingToolUse('toolu_1', 'approve')
    })

    // The POST has resolved; the attach effect is now consuming the gated stream, held
    // open right after its first delta.
    await waitFor(() => expect(assistantMessage(result)?.text).toContain('deleted.'))
    const midStream = assistantMessage(result)
    expect(midStream?.streaming).toBe(true)
    expect(midStream?.awaitingApproval).toBe(false)

    await act(async () => {
      release()
    })
    await waitFor(() => expect(assistantMessage(result)?.streaming).toBe(false))

    const done = assistantMessage(result)
    expect(done?.awaitingApproval).toBe(false)
    // 0.474 (before the card) + 0.1 (after approval) — one turn, one number.
    expect(done?.usage?.cost_usd).toBeCloseTo(0.574, 6)
    expect(done?.usage?.output_tokens).toBe(228)
    // The continuation landed on the SAME bubble, not a second one.
    expect(result.current.messages.filter((m) => m.role === 'assistant')).toHaveLength(1)
  })

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
      await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

      await act(async () => {
        await result.current.resolvePendingToolUse('toolu_1', decision, options)
      })

      const change = assistantMessage(result)?.proposedChanges?.[0] as
        | { status?: string; resolution?: string }
        | undefined
      expect(change?.status).toBe(decision === 'approve' ? 'confirmed' : 'dismissed')
      expect(change?.resolution).toBe(resolution)
      // The server is told too, so a reload rebuilds the card with the same label.
      const call = mockAuthFetch.mock.calls.find((c) => String(c[0]).endsWith('/resolve'))
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
        tool_use_id: 'toolu_1',
        decision,
        ...(options?.always ? { always: true } : {}),
      })
    },
  )

  it('answering the card by other means (legacy propose flow) releases the cost chip', async () => {
    mockRoutes(PAUSED_TURN)
    const { result } = renderHook(() => useChat('conv-respond', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete the duplicate')
    })
    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

    const msg = assistantMessage(result)!
    act(() => {
      result.current.respondToChange(msg.id, 'toolu_1', 'confirmed')
    })

    expect(assistantMessage(result)?.awaitingApproval).toBe(false)
  })

  it('un-paints the optimistic tick when the server refuses the approval', async () => {
    // REGRESSION (2026-07-30): the card rendered "✓ Approved" (painted optimistically on tap)
    // directly above the red "this approval is no longer pending" error — the UI asserting a
    // delete that never ran. An error naming a tool_use_id must revert THAT card to unanswered.
    mockRoutes(PAUSED_TURN)
    const { result } = renderHook(() => useChat('conv-refused', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete the duplicate')
    })
    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

    mockRoutes([
      '{"type":"error","tool_use_id":"toolu_1","message":"This approval was already answered."}',
    ])
    await act(async () => {
      await result.current.resolvePendingToolUse('toolu_1', 'approve')
    })

    await waitFor(() => expect(assistantMessage(result)?.error).toContain('already answered'))
    const assistant = assistantMessage(result)!
    const card = assistant.blocks?.find((b) => b.type === 'change')
    expect(card?.change?.status).toBeUndefined()
    expect(card?.change?.refused).toBe(true)
    expect(assistant.proposedChanges?.[0]?.status).toBeUndefined()
    expect(assistant.proposedChanges?.[0]?.refused).toBe(true)
  })

  it('leaves card state alone for an error that names no tool_use_id', async () => {
    mockRoutes(PAUSED_TURN)
    const { result } = renderHook(() => useChat('conv-generic-err', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete the duplicate')
    })
    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

    mockRoutes(['{"type":"error","message":"the model provider timed out"}'])
    await act(async () => {
      await result.current.resolvePendingToolUse('toolu_1', 'approve')
    })

    // The approval DID land server-side; only the continuation failed. Keep the tick.
    await waitFor(() =>
      expect(assistantMessage(result)?.proposedChanges?.[0]?.status).toBe('confirmed'),
    )
  })

  it('keeps the turn awaiting approval while a sibling card is unresolved', async () => {
    mockRoutes(TWO_PAUSED_CALLS)
    const { result } = renderHook(() => useChat('conv-siblings', vi.fn()))
    await act(async () => {})
    await act(async () => {
      await result.current.sendMessage('delete both')
    })
    await waitFor(() => expect(assistantMessage(result)?.awaitingApproval).toBe(true))

    mockRoutes(['{"type":"tool_done","id":"toolu_1","result":"ok"}'])
    await act(async () => {
      await result.current.resolvePendingToolUse('toolu_1', 'approve')
    })

    await waitFor(() => expect(result.current.streaming).toBe(false))
    expect(assistantMessage(result)?.awaitingApproval).toBe(true)
  })
})
