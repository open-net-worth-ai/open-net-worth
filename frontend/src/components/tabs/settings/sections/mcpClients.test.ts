import { describe, expect, it } from 'vitest'
import {
  FALLBACK_LAUNCH,
  MCP_CLIENTS,
  SOURCE_CWD_PLACEHOLDER,
  defaultVariant,
  launchNote,
} from './mcpClients'
import type { McpLaunch, SnippetContext } from './mcpClients'

const ORIGIN = 'http://127.0.0.1:8080'
const DESKTOP: McpLaunch = {
  desktop: true,
  command: 'C:\\Program Files\\Open Net Worth\\runtime\\python\\python.exe',
  args: ['-m', 'backend.cli', 'mcp'],
  command_found: true,
  root: 'C:\\Users\\me\\Open Net Worth',
  source_root: null,
  container: false,
}

const ctx = (over: Partial<SnippetContext> = {}): SnippetContext => ({
  origin: ORIGIN,
  token: 'TOK',
  launch: FALLBACK_LAUNCH,
  variant: 'installed',
  ...over,
})
const snippet = (id: string, c: SnippetContext) => MCP_CLIENTS.find((x) => x.id === id)!.snippet(c)
/** The first JSON block of a snippet (some carry a `// — or —` alternative after it). */
const firstBlock = (text: string) => JSON.parse(text.replace(/^\/\/.*\n/, '').split('\n\n//')[0]!)

describe('MCP client snippets', () => {
  it('the default stdio config runs `onw mcp` with no cwd', () => {
    const server = firstBlock(snippet('claude-desktop', ctx())).mcpServers['open-net-worth']
    expect(server).toEqual({
      command: 'onw',
      args: ['mcp'],
      env: { ONW_BASE_URL: ORIGIN, ONW_API_TOKEN: 'TOK' },
    })
  })

  it('uses the absolute onw path the server verified', () => {
    const launch = { ...FALLBACK_LAUNCH, command: '/home/me/.local/bin/onw', command_found: true }
    const server = firstBlock(snippet('cursor', ctx({ launch }))).mcpServers['open-net-worth']
    expect(server.command).toBe('/home/me/.local/bin/onw')
  })

  it('desktop: bundled interpreter + ONW_ROOT, never the per-launch URL, escaped for Windows', () => {
    for (const id of ['claude-desktop', 'cursor', 'other']) {
      const text = snippet(id, ctx({ launch: DESKTOP }))
      const server = JSON.parse(text).mcpServers['open-net-worth']
      expect(server).toEqual({
        command: DESKTOP.command,
        args: ['-m', 'backend.cli', 'mcp'],
        env: { ONW_ROOT: DESKTOP.root, ONW_API_TOKEN: 'TOK' },
      })
      expect(text).not.toContain(ORIGIN)
    }
    const vscode = JSON.parse(snippet('vscode', ctx({ launch: DESKTOP })))
    expect(vscode.servers['open-net-worth'].type).toBe('stdio')
  })

  it('"Running from source" keeps the python3 + cwd form, filled when known', () => {
    const server = firstBlock(snippet('claude-desktop', ctx({ variant: 'source' }))).mcpServers[
      'open-net-worth'
    ]
    expect(server.command).toBe('python3')
    expect(server.args).toEqual(['-m', 'backend.mcp.stdio_bridge'])
    expect(server.cwd).toBe(SOURCE_CWD_PLACEHOLDER)
    const launch = { ...FALLBACK_LAUNCH, source_root: '/src/onw' }
    const filled = firstBlock(snippet('claude-desktop', ctx({ variant: 'source', launch })))
    expect(filled.mcpServers['open-net-worth'].cwd).toBe('/src/onw')
  })

  it('"Docker or another computer" runs a bare onw at this address, never a server-side path', () => {
    const launch = { ...FALLBACK_LAUNCH, command: '/home/me/.local/bin/onw', command_found: true }
    for (const id of ['claude-desktop', 'cursor', 'other']) {
      const server = firstBlock(snippet(id, ctx({ launch, variant: 'remote' }))).mcpServers[
        'open-net-worth'
      ]
      expect(server).toEqual({
        command: 'onw',
        args: ['mcp'],
        env: { ONW_BASE_URL: ORIGIN, ONW_API_TOKEN: 'TOK' },
      })
    }
  })

  it('"Use npx instead" builds the mcp-remote config, token in env, never the onw bridge', () => {
    const server = firstBlock(snippet('claude-desktop', ctx({ variant: 'remote', npx: true })))
      .mcpServers['open-net-worth']
    expect(server).toEqual({
      command: 'npx',
      args: [
        '-y',
        'mcp-remote',
        `${ORIGIN}/mcp`,
        '--allow-http',
        '--header',
        'Authorization:${ONW_AUTH}',
      ],
      env: { ONW_AUTH: 'Bearer TOK' },
    })
    const https = ctx({ origin: 'https://onw.example.com', variant: 'remote', npx: true })
    const httpsServer = firstBlock(snippet('cursor', https)).mcpServers['open-net-worth']
    expect(httpsServer.args).not.toContain('--allow-http')
    // npx applies to the "another computer" choice only.
    const installed = firstBlock(snippet('claude-desktop', ctx({ npx: true })))
    expect(installed.mcpServers['open-net-worth'].command).toBe('onw')
    expect(launchNote(FALLBACK_LAUNCH, 'remote', true)).toMatch(/Node\.js/)
  })

  it('a container install defaults to "Docker or another computer"', () => {
    expect(defaultVariant({ ...FALLBACK_LAUNCH, container: true })).toBe('remote')
    expect(defaultVariant(FALLBACK_LAUNCH)).toBe('installed')
    expect(launchNote(FALLBACK_LAUNCH, 'remote')).toMatch(/uv tool install open-net-worth/)
  })

  it('no client still ships the old checkout-only command as its default', () => {
    for (const c of MCP_CLIENTS) expect(c.snippet(ctx())).not.toContain('stdio_bridge')
  })

  it('remote clients point at /mcp with a Bearer header', () => {
    expect(JSON.parse(snippet('http', ctx()))).toEqual({
      url: `${ORIGIN}/mcp`,
      headers: { Authorization: 'Bearer TOK' },
    })
  })

  it('the launch note says what path to use when the server could not find onw', () => {
    expect(launchNote(FALLBACK_LAUNCH, 'installed')).toMatch(/which onw/)
    expect(launchNote(DESKTOP, 'installed')).toMatch(/ONW_ROOT/)
  })
})

it('keeps args on one line, the way client docs show it', () => {
  expect(MCP_CLIENTS[0]!.snippet(ctx({ launch: DESKTOP }))).toContain(
    '"args": ["-m", "backend.cli", "mcp"]',
  )
  expect(MCP_CLIENTS[0]!.snippet(ctx())).toContain('"args": ["mcp"]')
})
