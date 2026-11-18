// @vitest-environment jsdom
//
// Send-path tests for useChat — verify that `responding_agent` is threaded into
// the POST /api/chat body correctly.
//
// Rule: null respondingAgent → field is null in body (Lucius default preserved).
//       non-null respondingAgent → field is the agent value string.
//
// Server-owned turn lifetime (concurrent-conversations RFC Phase 3): POST /api/chat
// returns 202 {session_id, turn_id, accepted} — it no longer streams. The turn's actual
// events now arrive only via the attach effect's GET /api/sessions/{id}/live fetch, so the
// mock below routes on URL: a POST /api/chat gets the 202 JSON, the GET /live gets the
// scripted SSE body. `waitFor` (not a fixed number of extra `act` ticks) is used to await
// the attach effect's own async work, which `sendMessage` itself does not await.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// ── Module mocks (hoisted — no variable references allowed in factory) ─────────

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('../utils/chatStore', () => ({
  loadMessages: vi.fn().mockResolvedValue([]),
  reconcileWithRemote: vi.fn().mockResolvedValue(null),
  saveMessages: vi.fn(),
}))
vi.mock('../utils/chatServerSync', () => import('./__testing__/transcriptReadDouble'))

// ── Import after mocks ─────────────────────────────────────────────────────────

import { useChat } from './useChat'
import { authFetch } from '../api/client'
import { _resetBusForTests, onDataChanged, onScreenCommand } from '../lucius/bus'

// ── Tests ──────────────────────────────────────────────────────────────────────

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
  const readable = new ReadableStream({
    start(ctrl) {
      ctrl.enqueue(encoder.encode(body))
      ctrl.close()
    },
  })
  return new Response(readable, { status: 200 })
}

// Routes on URL: POST /api/chat (or /resolve) → 202 JSON; GET .../live → the scripted SSE
// body, defaulting to a bare turn_done for tests that only care about the request shape.
function mockRoutes(liveEvents: string[] = ['{"type":"turn_done","usage":{}}']) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    if (typeof url === 'string' && url.includes('/live')) return sseResponse(liveEvents)
    return json202()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockRoutes()
})

afterEach(() => {
  vi.clearAllMocks()
})

function getChatBody(): Record<string, unknown> | null {
  // Find the POST /api/chat call (not /api/chat/suggestions or other endpoints).
  const chatCall = mockAuthFetch.mock.calls.find((call: unknown[]) => call[0] === '/api/chat')
  if (!chatCall) return null
  const init = chatCall[1] as RequestInit | undefined
  if (!init?.body) return null
  return JSON.parse(init.body as string)
}

describe('useChat send-path: responding_agent', () => {
  it('sends responding_agent: null when no agent is selected (Lucius default)', async () => {
    const { result } = renderHook(() => useChat('conv-1', vi.fn()))
    await act(async () => {
      await result.current.sendMessage('hello', [], 'medium', false, '', [], null)
    })
    const body = getChatBody()
    expect(body).not.toBeNull()
    // null must be explicitly present — not omitted — so the server always gets the field.
    expect(body!['responding_agent']).toBeNull()
  })

  it('sends responding_agent with the agent value when an agent is selected', async () => {
    const { result } = renderHook(() => useChat('conv-2', vi.fn()))
    await act(async () => {
      await result.current.sendMessage('hello', [], 'medium', false, '', [], 'agent-1')
    })
    const body = getChatBody()
    expect(body).not.toBeNull()
    expect(body!['responding_agent']).toBe('agent-1')
  })

  it('sends responding_agent: null when called with default params (Lucius path unchanged)', async () => {
    const { result } = renderHook(() => useChat('conv-3', vi.fn()))
    await act(async () => {
      // Default param — respondingAgent defaults to null.
      await result.current.sendMessage('hello')
    })
    const body = getChatBody()
    expect(body).not.toBeNull()
    expect(body!['responding_agent']).toBeNull()
  })
})

describe('useChat send-path: explicit skill invocation', () => {
  it('sends the immutable structured skill id instead of encoding it as a mention', async () => {
    const { result } = renderHook(() => useChat('conv-skill', vi.fn()))
    await act(async () => {
      await result.current.sendMessage(
        '[skill: account-balance-tracer|account-balance-tracer]',
        [],
        'medium',
        false,
        '',
        [],
        null,
        ['account-balance-tracer'],
      )
    })

    expect(getChatBody()).toMatchObject({
      messages: [
        {
          role: 'user',
          content: '[skill: account-balance-tracer|account-balance-tracer]',
        },
      ],
      skills: ['account-balance-tracer'],
    })
    expect(getChatBody()).not.toHaveProperty('mentions')
  })
})

describe('useChat stream: bus events', () => {
  it('forwards data_changed domains onto the bus at turn end', async () => {
    _resetBusForTests()
    const seen = vi.fn()
    onDataChanged(seen)
    mockRoutes([
      '{"type":"data_changed","domains":["plan","profile"]}',
      '{"type":"turn_done","usage":{}}',
    ])
    const { result } = renderHook(() => useChat('conv-dc', vi.fn()))
    await act(async () => {
      await result.current.sendMessage('hello')
    })
    await waitFor(() => expect(seen).toHaveBeenCalledWith(['plan', 'profile']))
  })

  it('dispatches a screen_command onto the bus and appends a chip block when parked', async () => {
    _resetBusForTests()
    const seen = vi.fn()
    onScreenCommand('plan.', seen)
    mockRoutes([
      '{"type":"screen_command","status":"parked","command":{"name":"plan.stage_whatif","args":{"retireAge":45}},"open":{"route":"plan-outlook"}}',
      '{"type":"turn_done","usage":{}}',
    ])
    const { result } = renderHook(() => useChat('conv-sc', vi.fn()))
    await act(async () => {}) // let the IndexedDB hydration settle before sending
    await act(async () => {
      await result.current.sendMessage('what if I retire at 45?')
    })
    await waitFor(() =>
      expect(seen).toHaveBeenCalledWith({ name: 'plan.stage_whatif', args: { retireAge: 45 } }),
    )
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant')
      const chip = assistant?.blocks?.find((b) => b.type === 'screen_command')
      expect(chip).toEqual({
        type: 'screen_command',
        command: { name: 'plan.stage_whatif', args: { retireAge: 45 } },
        open: { route: 'plan-outlook' },
      })
    })
  })

  it('a dispatched (live) screen_command leaves no chip block', async () => {
    _resetBusForTests()
    mockRoutes([
      '{"type":"screen_command","status":"dispatched","command":{"name":"plan.reset","args":{}}}',
      '{"type":"turn_done","usage":{}}',
    ])
    const { result } = renderHook(() => useChat('conv-sc2', vi.fn()))
    await act(async () => {}) // let the IndexedDB hydration settle before sending
    await act(async () => {
      await result.current.sendMessage('reset it')
    })
    await waitFor(() => expect(result.current.streaming).toBe(false))
    // A DISPATCHED command produces no chip block. With no block and no text, no assistant
    // bubble is created at all now (a bubble exists only because the assistant produced
    // something) — so "no chip" is the stronger statement that there is nothing to chip.
    const assistant = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistant?.blocks?.some((b) => b.type === 'screen_command') ?? false).toBe(false)
  })
})
