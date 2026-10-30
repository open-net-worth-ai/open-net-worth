// @vitest-environment jsdom
//
// The connecting step of Test connect — what a user sees while a probe runs.
//
// THE INCIDENT. Adding a real vendor's MCP server with the vendor's own documented
// `npx -y interactive-brokers-mcp` failed with a bare "timed out after 60s". Nothing was
// broken: the package unpacks to ~264 MB and its cold npm download alone took 61.2s, so the
// probe killed npx before the MCP server had started (warm-cache, the identical probe took
// 3.6s and found all 14 tools). For that whole minute the UI showed a spinner and one
// sentence — no way to tell "still downloading" from "this server is broken".
//
// So the connecting step has to carry three things a spinner cannot: an elapsed clock (which
// moves even when the command is silent), the command's own output (which stops when it
// stalls), and — only once the wait is long enough to be worth explaining — WHY a first
// connect is slow. These tests pin each one, plus the wire contract that makes the live tail
// reachable at all: a token this client mints and the backend accepts.

import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { McpAddFlowBody, PROBE_SLOW_HINT_MS, type McpAddFlow } from './McpAddFlow'
import { EMPTY_MCP_FORM } from './mcpConfig'
import { fetchProbeProgress, newProbeToken, probeMcpServer, type McpWireConfig } from './mcpApi'

afterEach(cleanup)

const WIRE: McpWireConfig = {
  transport: 'stdio',
  url: '',
  command: 'npx',
  args: ['-y', 'interactive-brokers-mcp'],
  env: {},
  auth_type: 'none',
  auth_header: 'Authorization',
  token: '',
}

/** A flow parked on `connecting` with the activity a running probe would have reported. */
function connectingFlow(
  activity: {
    elapsed: number
    lines: string[]
    lastOutputAt?: number
  },
  transport: 'local' | 'remote' = 'local',
): McpAddFlow {
  return {
    step: 'connecting',
    mode: 'add',
    editing: null,
    form: {
      ...EMPTY_MCP_FORM,
      transport,
      command: 'npx',
      url: 'https://mcp.vendor.test/sse',
      args: '-y interactive-brokers-mcp',
      auth: 'none',
    },
    setForm: () => {},
    tools: [],
    toolGroups: [],
    meta: { publisher: null, verification: 'unknown', curated: false, endpoint: '' },
    error: '',
    needsAuth: null,
    switchAuth: () => {},
    pasteOpen: false,
    setPasteOpen: () => {},
    perms: {},
    setTool: () => {},
    setGroup: () => {},
    saving: false,
    saveError: '',
    activity: { lastOutputAt: 0, ...activity },
    canConnect: true,
    idFixed: false,
    oauth: {
      status: 'idle',
      reason: null,
      error: null,
      redirectUris: [],
      needsClientId: false,
      clientId: '',
      setClientId: () => {},
      summary: null,
      authorizeUrl: null,
      signIn: () => {},
      cancel: () => {},
    },
    start: () => {},
    runConnect: () => {},
    back: () => {},
    finish: () => {},
    discardAbandonedOAuthServer: () => {},
  }
}

describe('the elapsed clock — the half that works when the command says nothing', () => {
  it('shows the wait as a duration, not a spinner alone', () => {
    render(<McpAddFlowBody flow={connectingFlow({ elapsed: 78, lines: [] })} />)
    expect(screen.getByRole('timer').textContent).toBe('1:18')
  })

  it('reads m:ss from the first second, so the shape never jumps', () => {
    render(<McpAddFlowBody flow={connectingFlow({ elapsed: 3, lines: [] })} />)
    expect(screen.getByRole('timer').textContent).toBe('0:03')
  })

  it('names the LOCAL command, never a URL the form still happens to hold', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 1, lines: [] })} />,
    )
    expect(container.textContent).toContain('npx')
    expect(container.textContent).not.toContain('mcp.vendor.test')
  })

  it('names the URL on a remote connect', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 1, lines: [] }, 'remote')} />,
    )
    expect(container.textContent).toContain('mcp.vendor.test')
  })
})

// The baseline — before the elapsed clock, the log or the slow hint have anything to add —
// used to be just the header line over a blank box: honest, but for the first several seconds
// of a cold `npx` install it read as broken rather than "nothing to report yet". The
// always-visible sub-line under the header is a calmer preview of the urgent `slow`
// explanation. `.cn-connecting` is ALWAYS vertically centered now (2026-08-18) — it used to
// center only this bare baseline and drop to top-aligned once real detail landed, but that
// binary CSS-class flip made the whole hero visibly jump the instant it happened; there is no
// longer a `.bare` class or a centering-toggle behavior left to test here.
describe('the connecting step baseline — before there is anything else to say', () => {
  it(
    'shows a calm, always-visible reassurance from the very first render — well before the ' +
      'slow hint would normally say anything',
    () => {
      const { container } = render(
        <McpAddFlowBody flow={connectingFlow({ elapsed: 1, lines: [] })} />,
      )
      expect(container.textContent).toContain('every connect after that is fast')
      // The literal phrase the slow-hint block uses is reserved for once it actually fires —
      // this baseline reassurance must read as calmer, not as an early copy of that excuse.
      expect(container.textContent).not.toContain('downloads the package')
    },
  )

  it('shows a shorter baseline reassurance for a remote target — no install to excuse', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 1, lines: [] }, 'remote')} />,
    )
    expect(container.textContent).toContain('This usually takes just a few seconds')
  })

  it('swaps the baseline reassurance out, rather than stacking it, once slow fires', () => {
    const { container } = render(
      <McpAddFlowBody
        flow={connectingFlow({ elapsed: PROBE_SLOW_HINT_MS / 1000 + 1, lines: [] })}
      />,
    )
    expect(container.textContent).not.toContain('every connect after that is fast')
  })
})

describe("the command's own output", () => {
  it('renders the tail the backend captured, in order', () => {
    const lines = ['npm warn deprecated left-pad@1.0.0', 'npm http fetch GET 200 264MB']
    const { container } = render(<McpAddFlowBody flow={connectingFlow({ elapsed: 20, lines })} />)
    const rendered = Array.from(container.querySelectorAll('.cn-probe-log-line')).map(
      (n) => n.textContent,
    )
    expect(rendered).toEqual(lines)
  })

  it('shows no log frame at all until there is something in it', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 4, lines: [] })} />,
    )
    expect(container.querySelector('.cn-probe-log')).toBeNull()
  })

  it('announces new output politely rather than interrupting a screen reader', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 9, lines: ['fetching…'] })} />,
    )
    expect(container.querySelector('.cn-probe-log')?.getAttribute('aria-live')).toBe('polite')
  })
})

describe('the slow-connect explanation', () => {
  const before = PROBE_SLOW_HINT_MS / 1000 - 1
  const after = PROBE_SLOW_HINT_MS / 1000 + 1

  it('stays quiet on a normal connect — an unprompted excuse reads as a problem', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: before, lines: [] })} />,
    )
    expect(container.textContent).not.toContain('downloads the package')
  })

  it('names the actual root cause once the wait is long enough to need one', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: after, lines: ['npm http fetch'] })} />,
    )
    // The one fact a user needs: the download happens BEFORE the server starts, and it is a
    // first-connect cost, not what this server will cost every time.
    expect(container.textContent).toContain('downloads the package before the server starts')
    expect(container.textContent).toContain('later connects take seconds')
  })

  it('says plainly when the command has printed nothing — silence is its own fact', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: after, lines: [] })} />,
    )
    expect(container.textContent).toContain('has not printed anything yet')
  })

  it('does not claim silence while output is arriving', () => {
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: after, lines: ['still fetching'] })} />,
    )
    expect(container.textContent).not.toContain('has not printed anything yet')
  })
})

/** A poll response shaped like the real thing — `authFetch` de-dups GETs and hands every
 *  waiter its own `clone()`, so a mock without one is not the code path that ships. */
function pollResponse(body: unknown) {
  const res = { ok: true, status: 200, json: async () => body, clone: () => res }
  return res
}

/** The JSON body of the first request the mock saw. */
function sentBody(mock: ReturnType<typeof vi.fn>): string {
  return String((mock.mock.calls[0]?.[1] as RequestInit | undefined)?.body ?? '{}')
}

describe('the wire contract that makes the live tail reachable', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('mints a token the backend will actually accept', () => {
    // The backend treats it as an opaque `[A-Za-z0-9_-]{8,64}` key and answers "no channel"
    // for anything else — a mismatch here would silently cost the whole progress display
    // while every test on either side still passed.
    for (let i = 0; i < 50; i++) expect(newProbeToken()).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
  })

  it('mints a DIFFERENT token per probe, so two flows never read each other', () => {
    const tokens = new Set(Array.from({ length: 50 }, () => newProbeToken()))
    expect(tokens.size).toBe(50)
  })

  it('falls back to crypto.getRandomValues, not Math.random, when randomUUID is unavailable', () => {
    // randomUUID() requires a secure context (HTTPS or localhost); this app explicitly
    // supports plain-HTTP LAN access (AUTH_TRUST_LOOPBACK, QR-scan-from-phone setup) — exactly
    // where this fallback fires, and exactly where AUTH_TRUST_LOOPBACK lets any local process
    // poll the progress endpoint unauthenticated. getRandomValues() is NOT secure-context-gated,
    // so it must be what covers that case rather than the weak Math.random() + Date.now() shape.
    const realCrypto = globalThis.crypto
    const getRandomValues = vi.fn((arr: Uint8Array) => {
      for (let i = 0; i < arr.length; i++) arr[i] = i + 1
      return arr
    })
    vi.stubGlobal('crypto', { getRandomValues })
    const randomSpy = vi.spyOn(Math, 'random')
    try {
      const token = newProbeToken()
      expect(token).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
      expect(getRandomValues).toHaveBeenCalledTimes(1)
      expect(randomSpy).not.toHaveBeenCalled()
    } finally {
      vi.stubGlobal('crypto', realCrypto)
      randomSpy.mockRestore()
    }
  })

  it('falls back to Math.random only when crypto is entirely unavailable', () => {
    const realCrypto = globalThis.crypto
    vi.stubGlobal('crypto', undefined)
    try {
      expect(newProbeToken()).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
    } finally {
      vi.stubGlobal('crypto', realCrypto)
    }
  })

  it('sends the token with the probe so the backend knows where to publish', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    await probeMcpServer(WIRE, 'probe-token-0001')
    expect(JSON.parse(sentBody(fetchMock)).probe_id).toBe('probe-token-0001')
  })

  it('omits it entirely when there is none — progress is an accessory to the probe', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    await probeMcpServer(WIRE)
    expect('probe_id' in JSON.parse(sentBody(fetchMock))).toBe(false)
  })

  it('reads a running probe', async () => {
    fetchMock.mockResolvedValue(pollResponse({ running: true, elapsed: 12.5, lines: ['npm warn'] }))
    expect(await fetchProbeProgress('probe-token-0001')).toEqual({
      running: true,
      elapsed: 12.5,
      lines: ['npm warn'],
    })
  })

  it('treats a failed poll as "no news", never as an error to show', async () => {
    // The probe POST carries the real answer; a blip on the accessory channel must not
    // surface as a failure or wipe the tail already on screen.
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    expect(await fetchProbeProgress('probe-token-0001')).toBeNull()
    fetchMock.mockRejectedValue(new Error('offline'))
    expect(await fetchProbeProgress('probe-token-0001')).toBeNull()
  })

  it('never trusts the shape it gets back', async () => {
    fetchMock.mockResolvedValue(
      pollResponse({ running: 'yes', elapsed: 'ages', lines: ['ok', 7, null] }),
    )
    expect(await fetchProbeProgress('probe-token-0001')).toEqual({
      running: true,
      elapsed: 0,
      lines: ['ok'],
    })
  })
})

describe('stalled vs still working — the distinction the whole feature is about', () => {
  it('says nothing while output is still arriving', () => {
    const { container } = render(
      <McpAddFlowBody
        flow={connectingFlow({ elapsed: 40, lines: ['fetching'], lastOutputAt: 39 })}
      />,
    )
    expect(container.textContent).not.toContain('No new output')
  })

  it('names how long the command has been quiet once it stops', () => {
    // A log block that just sits there reads the same mid-download as wedged. Only one of
    // those is worth waiting out, so the UI has to say which it is looking at.
    const { container } = render(
      <McpAddFlowBody
        flow={connectingFlow({ elapsed: 100, lines: ['fetching'], lastOutputAt: 25 })}
      />,
    )
    expect(container.textContent).toContain('No new output for 1:15')
  })

  it('does not call a command quiet when it has never spoken', () => {
    // That case has its own, more useful wording — "has not printed anything yet".
    const { container } = render(
      <McpAddFlowBody flow={connectingFlow({ elapsed: 100, lines: [], lastOutputAt: 0 })} />,
    )
    expect(container.textContent).not.toContain('No new output')
    expect(container.textContent).toContain('has not printed anything yet')
  })
})

describe('a remote endpoint is not told about npm', () => {
  it('explains an install only where an install actually happens', () => {
    const { container } = render(
      <McpAddFlowBody
        flow={connectingFlow({ elapsed: 20, lines: [], lastOutputAt: 0 }, 'remote')}
      />,
    )
    // A remote probe spawns nothing — explaining package downloads to someone waiting on an
    // HTTPS handshake is noise dressed as help.
    expect(container.textContent).not.toContain('downloads the package')
    expect(container.textContent).not.toContain('has not printed anything yet')
    expect(container.textContent).toContain('The endpoint has not answered yet')
  })
})
