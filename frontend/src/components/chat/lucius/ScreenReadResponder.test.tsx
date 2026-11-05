// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emitScreenRequest } from '../../../lucius/bus'
import { ScreenReadResponder } from './ScreenReadResponder'

const h = vi.hoisted(() => ({
  authFetch: vi.fn(async () => new Response('{}')),
  capture: vi.fn(() => ({ id: 'lucius-read', title: 'ACME Budget' })),
  activeId: 'conv-1',
  enabled: true,
  call: { phase: 'idle', sessionId: null as string | null, view: 'stage' },
}))

vi.mock('../../../api/client', () => ({ authFetch: h.authFetch }))
vi.mock('../../../lucius/screenContract', () => ({ captureAndSerialize: h.capture }))
vi.mock('../../../lucius/visibleIds', () => ({
  useVisibleIdsValue: () => ({ visibleIds: new Set<string>() }),
}))
vi.mock('../../../state/luciusChat', () => ({
  useLuciusChat: () => ({ activeId: h.activeId }),
}))
vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({ enabled: h.enabled, pinnedHandleIds: [] }),
}))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => h.call }))

function ask(sessionId = 'conv-1') {
  render(<ScreenReadResponder />)
  emitScreenRequest({ request_id: 'r/1', session_id: sessionId })
}

function posted() {
  const [url, init] = h.authFetch.mock.calls[0] as unknown as [string, RequestInit]
  return { url, body: JSON.parse(init.body as string) }
}

describe('ScreenReadResponder', () => {
  beforeEach(() => {
    h.authFetch.mockClear()
    h.capture.mockClear()
    h.activeId = 'conv-1'
    h.enabled = true
    h.call = { phase: 'idle', sessionId: null, view: 'stage' }
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('captures the page and posts it, like a typed message would', () => {
    ask()
    const { url, body } = posted()
    expect(url).toBe('/api/screen-reads/r%2F1')
    expect(body).toEqual({
      status: 'ok',
      rank: 2,
      screen: { id: 'lucius-read', title: 'ACME Budget' },
    })
  })

  it('ignores a request for a conversation this app is not showing', () => {
    ask('conv-2')
    expect(h.authFetch).not.toHaveBeenCalled()
  })

  it('answers for the conversation its call is on, whichever chat is open', () => {
    h.activeId = 'conv-2'
    h.call = { phase: 'live', sessionId: 'conv-1', view: 'bar' }
    ask('conv-1')
    expect(posted().body.rank).toBe(3)
  })

  it('reads the page the same with Screen awareness off', () => {
    h.enabled = false
    ask()
    expect(h.capture).toHaveBeenCalledOnce()
    expect(posted().body).toEqual({
      status: 'ok',
      rank: 2,
      screen: { id: 'lucius-read', title: 'ACME Budget' },
    })
  })

  it('never captures the Lucius page as if it were a screen', () => {
    const shell = document.createElement('div')
    shell.setAttribute('data-ctx-screen', 'Lucius')
    document.body.appendChild(shell)
    ask()
    shell.remove()
    expect(h.capture).not.toHaveBeenCalled()
    expect(posted().body.status).toBe('no_screen')
  })

  it('treats the full-screen call on the page as covering it', () => {
    h.call = { phase: 'live', sessionId: 'conv-1', view: 'stage' }
    const stage = document.createElement('div')
    stage.className = 'lx-callstage lx-callstage--screen'
    document.body.appendChild(stage)
    ask()
    stage.remove()
    expect(h.capture).not.toHaveBeenCalled()
    expect(posted().body).toEqual({ status: 'no_screen', rank: 0, screen: null })
  })

  it('reads the page when the call still says stage but nothing covers it', () => {
    h.call = { phase: 'live', sessionId: 'conv-1', view: 'stage' }
    ask()
    expect(posted().body).toMatchObject({ status: 'ok', rank: 3 })
  })
})
