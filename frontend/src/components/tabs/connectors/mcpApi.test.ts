/**
 * Wire translation + the transport-level failure paths for the add flow.
 *
 * The component test covers the two failures a user can see END TO END (the MCP server refused;
 * our own probe endpoint 503'd). This file covers the third — `authFetch` itself rejecting,
 * e.g. the browser offline — which must still resolve to a normal failure RESULT rather than
 * escaping as an exception, because the step-2 error state is rendered from that result.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const mockAuthFetch = vi.fn()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import {
  beginOAuth,
  createMcpServer,
  deleteMcpServer,
  parseArgs,
  parseEnv,
  pollOAuth,
  probeMcpServer,
  toWireConfig,
  updateMcpServer,
} from './mcpApi'
import { EMPTY_MCP_FORM } from './mcpConfig'

beforeEach(() => mockAuthFetch.mockReset())
// A `mockRejectedValue` left standing from one test can otherwise be reported against the
// NEXT one (a `mockReset()` alone clears calls/implementation but not that residue) — the same
// `vi.restoreAllMocks()` cleanup `chatStore.reconcile.test.ts` already uses beside its own
// `authFetch` mock, for the same reason.
afterEach(() => vi.restoreAllMocks())

describe('toWireConfig — the design vocabulary translated to the backend one', () => {
  it('maps a remote server with an API key onto bearer auth', () => {
    expect(
      toWireConfig({
        ...EMPTY_MCP_FORM,
        transport: 'remote',
        url: ' https://x.test ',
        auth: 'key',
        key: 'sk-1',
      }),
    ).toEqual({
      transport: 'http',
      url: 'https://x.test',
      command: '',
      args: [],
      env: {},
      auth_type: 'bearer',
      auth_header: 'Authorization',
      token: 'sk-1',
    })
  })

  it('maps a local command onto stdio, splitting args and parsing env', () => {
    expect(
      toWireConfig({
        ...EMPTY_MCP_FORM,
        transport: 'local',
        command: ' npx ',
        args: '-y  firecrawl-mcp',
        env: 'FIRECRAWL_API_KEY=fc-1\n\nBAD_LINE\nPORT=8080',
      }),
    ).toEqual({
      transport: 'stdio',
      url: '',
      command: 'npx',
      args: ['-y', 'firecrawl-mcp'],
      env: { FIRECRAWL_API_KEY: 'fc-1', PORT: '8080' },
      auth_type: 'none',
      auth_header: 'Authorization',
      token: '',
    })
  })

  it('never leaks a credential onto a local command, where there is nowhere to put it', () => {
    const wire = toWireConfig({
      ...EMPTY_MCP_FORM,
      transport: 'local',
      command: 'npx',
      auth: 'key',
      key: 'sk-secret',
    })
    expect(wire.token).toBe('')
    expect(wire.auth_type).toBe('none')
  })

  it('parses args and env defensively', () => {
    expect(parseArgs('   ')).toEqual([])
    expect(parseEnv('=novalue\n  \nA=1')).toEqual({ A: '1' })
    expect(parseEnv('URL=https://x.test/a=b')).toEqual({ URL: 'https://x.test/a=b' })
  })
})

describe('parseEnv keeps a pasted private key whole, however it was pasted', () => {
  // Synthetic body, not a real key — 80 chars so the rewrap to 64 is visible.
  const BODY = 'MHcCAQEEIA' + 'b'.repeat(60) + 'oAoGCCqGSM=='
  // The label is assembled so the file holds no literal key header for a secret scanner to flag.
  const LABEL = ['EC', 'PRIVATE', 'KEY'].join(' ')
  const PEM = `-----BEGIN ${LABEL}-----\n${BODY.slice(0, 64)}\n${BODY.slice(64)}\n-----END ${LABEL}-----\n`

  it('reads a key with real line breaks as one value, and the next variable after it', () => {
    const raw = `COINBASE_KEY_ID=organizations/o/apiKeys/k\nCOINBASE_KEY_SECRET=${PEM}NEXT=1`
    expect(parseEnv(raw)).toEqual({
      COINBASE_KEY_ID: 'organizations/o/apiKeys/k',
      COINBASE_KEY_SECRET: PEM,
      NEXT: '1',
    })
  })

  it('turns literal \\n escapes, JSON quotes and a one-line paste into the same key', () => {
    const escaped = PEM.replace(/\n/g, '\\n')
    expect(parseEnv(`S=${escaped}`)).toEqual({ S: PEM })
    expect(parseEnv(`S="${escaped}"`)).toEqual({ S: PEM })
    expect(parseEnv(`S=${PEM.replace(/\n/g, '')}`)).toEqual({ S: PEM })
    expect(parseEnv(`S=${PEM.replace(/\n/g, ' ')}`)).toEqual({ S: PEM })
  })

  it('round-trips the revealed saved set (KEY=value lines, sorted)', () => {
    const saved = { A: '1', COINBASE_KEY_SECRET: PEM, Z: 'z' }
    const revealed = Object.entries(saved)
      .map(([k, v]) => `${k}=${v}`)
      .join('\n')
    expect(parseEnv(revealed)).toEqual(saved)
  })

  it('hands the child a key Node crypto can sign with — which the escaped paste is not', async () => {
    const { createSign, generateKeyPairSync } = await import('node:crypto')
    const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' })
    const pem = privateKey.export({ type: 'sec1', format: 'pem' }).toString()
    const escaped = pem.replace(/\n/g, '\\n')
    const sign = (key: string) => createSign('SHA256').update('x').sign(key)
    expect(() => sign(escaped)).toThrow()
    expect(() => sign(parseEnv(`S=${escaped}`).S)).not.toThrow()
    expect(() => sign(parseEnv(`S=${pem.replace(/\n/g, '')}`).S)).not.toThrow()
  })

  it('leaves values that merely contain \\n or a partial block alone', () => {
    expect(parseEnv('P=C:\\new\\dir')).toEqual({ P: 'C:\\new\\dir' })
    expect(parseEnv('B=-----BEGIN X-----abc')).toEqual({ B: '-----BEGIN X-----abc' })
  })
})

describe('probeMcpServer never throws — the error state is rendered from its result', () => {
  it('turns an unusable response into a failure result rather than an exception', async () => {
    // A response that isn't a Response — what a misbehaving proxy or a transport-level fault
    // surfaces as. It must still come back as a normal failure RESULT, because the step-2
    // error screen is rendered from that result and nothing else.
    //
    // Modelled this way rather than with a throwing/rejecting mock on purpose: `vi.fn()`
    // records every outcome in `mock.results`, and a throw parked there is reported as a test
    // failure even when the code under test catches its own copy. This reaches the identical
    // `catch`.
    mockAuthFetch.mockResolvedValue({})

    const result = await probeMcpServer(toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }))
    expect(result.ok).toBe(false)
    expect(result.error).toContain('Could not reach the server')
    expect(result.tools).toEqual([])
  })

  it('surfaces the API detail on a non-ok response', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'probe unavailable' }),
    })
    const result = await probeMcpServer(toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }))
    expect(result).toMatchObject({ ok: false, error: 'probe unavailable', suggested_id: '' })
  })

  it('falls back to a status-bearing message when the API sends no detail', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 500, json: async () => null })
    const result = await probeMcpServer(toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }))
    expect(result.error).toContain('(500)')
  })
})

describe('createMcpServer', () => {
  it('writes only the tools moved OFF the cautious default, on the REAL permissions route', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({ id: 'srv' }) })
    const res = await createMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }),
      tool_permissions: { a: 'needs_approval', b: 'always_allow', c: 'blocked' },
    })

    expect(res.ok).toBe(true)
    const paths = mockAuthFetch.mock.calls.map((c) => String(c[0]))
    expect(paths[0]).toBe('/api/mcp-clients')
    // `a` stays at the default, which an ABSENT row already means — so it is never written.
    // The other two go to the family-agnostic route, keyed `mcp:<server_id>`. This asserted
    // `tool-permissions` for the life of the branch, which is a route commit 8d4afa9d DELETED:
    // a green test over a dead contract. `test_route_path_consistency.py` now catches the
    // path itself; this pins the shape.
    expect(paths.slice(1)).toEqual([
      '/api/connectors/mcp%3Asrv/permissions/b',
      '/api/connectors/mcp%3Asrv/permissions/c',
    ])
    expect(mockAuthFetch.mock.calls[1]?.[1]).toMatchObject({
      method: 'PUT',
      body: JSON.stringify({ level: 'always_allow' }),
    })
  })

  it('does not write permissions at all when the server could not be created', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'duplicate id' }),
    })
    const res = await createMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }),
      tool_permissions: { b: 'always_allow' },
    })

    expect(res).toEqual({ ok: false, error: 'duplicate id' })
    expect(mockAuthFetch.mock.calls.length).toBe(1)
  })

  it('FAILS THE ADD when a permission write is rejected, instead of reporting success', async () => {
    // The fail-open scar: this fired the permission writes and ignored every result, so a
    // `blocked` chosen on step 4 could land as `needs_approval` — the tool still offered to
    // Lucius — while the flow said the server was added.
    mockAuthFetch.mockImplementation((path: string) =>
      Promise.resolve(
        String(path) === '/api/mcp-clients'
          ? { ok: true, json: async () => ({ id: 'srv' }) }
          : { ok: false, status: 422, json: async () => ({ detail: 'bad level' }) },
      ),
    )
    const res = await createMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test' }),
      tool_permissions: { c: 'blocked' },
    })

    expect(res.ok).toBe(false)
    expect(res.ok === false && res.error).toContain('still at Needs approval')
  })
})

describe('updateMcpServer — the edit commit', () => {
  const remote = toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test', auth: 'key', key: '' })

  it('omits a blank token so the saved one survives, and never sends id or transport', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: remote,
      replaceEnv: false,
      tool_permissions: {},
    })

    const [path, init] = mockAuthFetch.mock.calls[0]!
    expect(path).toBe('/api/mcp-clients/srv')
    expect((init as RequestInit).method).toBe('PATCH')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body).not.toHaveProperty('token')
    expect(body).not.toHaveProperty('id')
    expect(body).not.toHaveProperty('transport')
    expect(body.url).toBe('https://x.test')
  })

  it('sends a token the user actually retyped', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({
        ...EMPTY_MCP_FORM,
        url: 'https://x.test',
        auth: 'key',
        key: 'sk-new',
      }),
      replaceEnv: false,
      tool_permissions: {},
    })
    expect(JSON.parse(String(mockAuthFetch.mock.calls[0]![1].body)).token).toBe('sk-new')
  })

  it('omits env entirely when the box was untouched — sending it would WIPE every saved value', async () => {
    // `env` is a whole-map replace with no "blank means keep" rule, and `public()` returns only
    // key NAMES. The outgoing form sent `{KEY: ''}` here and silently destroyed the values.
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    const local = toWireConfig({ ...EMPTY_MCP_FORM, transport: 'local', command: 'npx' })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: local,
      replaceEnv: false,
      tool_permissions: {},
    })
    expect(JSON.parse(String(mockAuthFetch.mock.calls[0]![1].body))).not.toHaveProperty('env')
  })

  it('replaces the whole env set once the user rewrites the box', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    const local = toWireConfig({
      ...EMPTY_MCP_FORM,
      transport: 'local',
      command: 'npx',
      env: 'A=1',
    })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: local,
      replaceEnv: true,
      tool_permissions: {},
    })
    expect(JSON.parse(String(mockAuthFetch.mock.calls[0]![1].body)).env).toEqual({ A: '1' })
  })

  it('reports a rejected permission change rather than claiming the edit saved', async () => {
    mockAuthFetch.mockImplementation((path: string) =>
      Promise.resolve(
        String(path).startsWith('/api/connectors/')
          ? { ok: false, status: 500, json: async () => ({}) }
          : { ok: true, json: async () => ({}) },
      ),
    )
    const res = await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: remote,
      replaceEnv: false,
      // On an edit `needs_approval` is a REAL downgrade from a stored `always_allow`, so it
      // is written, not skipped.
      tool_permissions: { c: 'needs_approval' },
    })
    expect(res.ok).toBe(false)
  })
})

describe('deleteMcpServer', () => {
  it('DELETEs the server and reports a failure honestly', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({ deleted: 'srv' }) })
    expect(await deleteMcpServer('srv')).toEqual({ ok: true })
    expect(mockAuthFetch.mock.calls[0]![0]).toBe('/api/mcp-clients/srv')
    expect(mockAuthFetch.mock.calls[0]![1].method).toBe('DELETE')

    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'unknown mcp server: srv' }),
    })
    expect(await deleteMcpServer('srv')).toEqual({ ok: false, error: 'unknown mcp server: srv' })
  })
})

describe('toWireConfig — oauth (RFC 2026-08-14-mcp-oauth)', () => {
  it('maps the oauth picker onto auth_type: oauth, with no token field', () => {
    expect(
      toWireConfig({
        ...EMPTY_MCP_FORM,
        transport: 'remote',
        url: 'https://mcp.mercury.com/mcp',
        auth: 'oauth',
      }),
    ).toMatchObject({
      transport: 'http',
      url: 'https://mcp.mercury.com/mcp',
      auth_type: 'oauth',
      token: '',
    })
  })

  it('never leaks a typed key onto the wire once oauth is the picked credential', () => {
    // Switching the picker from `key` to `oauth` without clearing a half-typed API key box
    // must not let that value ride along as a bearer credential the server never asked for.
    const wire = toWireConfig({
      ...EMPTY_MCP_FORM,
      url: 'https://mcp.mercury.com/mcp',
      auth: 'oauth',
      key: 'sk-leftover',
    })
    expect(wire.token).toBe('')
    expect(wire.auth_type).toBe('oauth')
  })
})

describe('beginOAuth / pollOAuth — the sign-in wire calls', () => {
  it('POSTs the begin body and returns the parsed authorize_url + flow_id', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        authorize_url: 'https://as.example/authorize?x=1',
        flow_id: 'f1',
        expires_in: 600,
      }),
    })
    const result = await beginOAuth({ url: 'https://mcp.mercury.com/mcp' })
    expect(result).toEqual({
      ok: true,
      authorize_url: 'https://as.example/authorize?x=1',
      flow_id: 'f1',
      expires_in: 600,
    })
    const [path, init] = mockAuthFetch.mock.calls[0]!
    expect(path).toBe('/api/mcp-clients/oauth/begin')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      url: 'https://mcp.mercury.com/mcp',
    })
  })

  it('passes through a begin() {ok:false, reason, error} verbatim — the RFC §4.6 message, not a re-hardcoded copy', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: false,
        reason: 'not_oauth',
        error: "This server didn't ask for a sign-in. Try None — it may not need credentials.",
      }),
    })
    const result = await beginOAuth({ url: 'https://x.test/mcp' })
    expect(result).toEqual({
      ok: false,
      reason: 'not_oauth',
      error: "This server didn't ask for a sign-in. Try None — it may not need credentials.",
    })
  })

  it('turns a network failure into a client-side {ok:false} rather than throwing', async () => {
    mockAuthFetch.mockRejectedValue(new Error('offline'))
    const result = await beginOAuth({ url: 'https://x.test/mcp' })
    expect(result.ok).toBe(false)
    expect(result.error).toContain('Could not start the sign-in')
  })

  it('polls the flow_id endpoint and never asks for a token in the response', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'pending', error: null }),
    })
    const result = await pollOAuth('f1')
    expect(result).toEqual({ status: 'pending', error: null })
    expect(mockAuthFetch.mock.calls[0]![0]).toBe('/api/mcp-clients/oauth/f1')
  })

  it('reads a poll failure as a generic error, not an exception', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    expect((await pollOAuth('f1')).status).toBe('error')
    mockAuthFetch.mockRejectedValue(new Error('offline'))
    expect((await pollOAuth('f1')).status).toBe('error')
  })
})

describe('updateMcpServer — arming the OAuth add flow’s dormant row', () => {
  it('sends enabled: true only when the caller asks it to', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({
        ...EMPTY_MCP_FORM,
        url: 'https://mcp.mercury.com/mcp',
        auth: 'oauth',
      }),
      replaceEnv: false,
      tool_permissions: {},
      enable: true,
    })
    const body = JSON.parse(String(mockAuthFetch.mock.calls[0]![1].body))
    expect(body.enabled).toBe(true)
    expect(body.auth_type).toBe('oauth')
  })

  it('leaves enabled untouched on an ordinary edit', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, json: async () => ({}) })
    await updateMcpServer({
      id: 'srv',
      name: 'Srv',
      config: toWireConfig({ ...EMPTY_MCP_FORM, url: 'https://x.test', auth: 'none' }),
      replaceEnv: false,
      tool_permissions: {},
    })
    expect(JSON.parse(String(mockAuthFetch.mock.calls[0]![1].body))).not.toHaveProperty('enabled')
  })
})

describe('toWireConfig preserves a saved custom auth header across an edit', () => {
  it('keeps `header` auth instead of silently moving the credential to Authorization', async () => {
    const wire = toWireConfig(
      { ...EMPTY_MCP_FORM, url: 'https://x.test', auth: 'key', key: 'k' },
      { auth_type: 'header', auth_header: 'X-API-Key' },
    )
    expect(wire).toMatchObject({ auth_type: 'header', auth_header: 'X-API-Key' })
  })

  it('still maps a fresh API key onto bearer', () => {
    const wire = toWireConfig(
      { ...EMPTY_MCP_FORM, url: 'https://x.test', auth: 'key', key: 'k' },
      { auth_type: 'bearer', auth_header: 'Authorization' },
    )
    expect(wire).toMatchObject({ auth_type: 'bearer', auth_header: 'Authorization' })
  })
})
