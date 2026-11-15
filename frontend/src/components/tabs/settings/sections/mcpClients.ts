// The ONE source of the copy-paste configs Settings → MCP hands to other AI apps.
//
// The shapes genuinely differ per client (VS Code uses "servers" + "type"; hosted clients need
// a PUBLIC url, not localhost), so one generic snippet would be wrong for most of them. Every
// JSON block is built with JSON.stringify so a Windows path (backslashes) is escaped correctly.

/** What `GET /api/mcp/connect-info` says about launching the stdio bridge on this machine. */
export interface McpLaunch {
  desktop: boolean
  command: string
  args: string[]
  /** `command` is an absolute path the server verified exists. */
  command_found: boolean
  /** Desktop only: the book folder the bridge reads the app's current port from. */
  root: string | null
  /** The checkout this instance runs from, when it runs from one. */
  source_root: string | null
  /** This instance runs in a container, so none of its paths exist on the user's computer. */
  container: boolean
}

export const FALLBACK_LAUNCH: McpLaunch = {
  desktop: false,
  command: 'onw',
  args: ['mcp'],
  command_found: false,
  root: null,
  source_root: null,
  container: false,
}

/** `remote`: the AI app's computer runs its own `onw`, pointed at this instance's address —
 *  the only form that works for Docker, where nothing in the container exists on the host. */
export type StdioVariant = 'installed' | 'source' | 'remote'

export const STDIO_VARIANTS: ReadonlyArray<readonly [StdioVariant, string]> = [
  ['installed', 'Installed (onw)'],
  ['source', 'Running from source'],
  ['remote', 'Docker or another computer'],
]

export const defaultVariant = (launch: McpLaunch): StdioVariant =>
  launch.container ? 'remote' : 'installed'

export interface SnippetContext {
  origin: string
  token: string
  launch: McpLaunch
  variant: StdioVariant
}

export interface McpClientGuide {
  id: string
  label: string
  transport: 'local' | 'remote'
  where: string
  snippet: (ctx: SnippetContext) => string
}

export const SOURCE_CWD_PLACEHOLDER = '/absolute/path/to/open-net-worth'

const NAME = 'open-net-worth'
/** Pretty JSON with string arrays kept on one line — `"args": ["mcp"]`, as clients document it. */
const json = (value: unknown) =>
  JSON.stringify(value, null, 2).replace(
    /\[\n\s+("(?:[^"\\\n]|\\.)*"(?:,\n\s+"(?:[^"\\\n]|\\.)*")*)\n\s+\]/g,
    (_, items: string) => '[' + items.replace(/,\n\s+/g, ', ') + ']',
  )

function stdioServer(ctx: SnippetContext, type?: 'stdio'): Record<string, unknown> {
  const head = type ? { type } : {}
  if (ctx.variant === 'source' && !ctx.launch.desktop) {
    return {
      ...head,
      command: 'python3',
      args: ['-m', 'backend.mcp.stdio_bridge'],
      cwd: ctx.launch.source_root ?? SOURCE_CWD_PLACEHOLDER,
      env: { ONW_BASE_URL: ctx.origin, ONW_API_TOKEN: ctx.token },
    }
  }
  // The desktop app binds a new port on every launch, so its URL would go stale: the bridge
  // finds the live one from the book folder instead.
  const env = ctx.launch.desktop
    ? { ONW_ROOT: ctx.launch.root ?? '', ONW_API_TOKEN: ctx.token }
    : { ONW_BASE_URL: ctx.origin, ONW_API_TOKEN: ctx.token }
  // A path this server found is a path on THIS machine, never on the AI app's.
  const remote = ctx.variant === 'remote' && !ctx.launch.desktop
  const command = remote ? 'onw' : ctx.launch.command
  const args = remote ? ['mcp'] : ctx.launch.args
  return { ...head, command, args, env }
}

const remoteServer = (ctx: SnippetContext) => ({
  url: `${ctx.origin}/mcp`,
  headers: { Authorization: `Bearer ${ctx.token}` },
})

/** stdio first; the remote-URL alternative only where the URL outlives a restart. */
function stdioOrRemote(ctx: SnippetContext, remoteLabel: string): string {
  const stdio = json({ mcpServers: { [NAME]: stdioServer(ctx) } })
  if (ctx.launch.desktop) return stdio
  return `// stdio (local — recommended)
${stdio}

// — or — ${remoteLabel}
${json({ mcpServers: { [NAME]: remoteServer(ctx) } })}`
}

export const MCP_CLIENTS: McpClientGuide[] = [
  {
    id: 'claude-desktop',
    label: 'Claude Desktop',
    transport: 'local',
    where:
      'In Claude Desktop: Settings → Developer → Edit Config opens claude_desktop_config.json. Paste this in, then restart Claude Desktop. Open Net Worth must be running.',
    snippet: (ctx) => json({ mcpServers: { [NAME]: stdioServer(ctx) } }),
  },
  {
    id: 'cursor',
    label: 'Cursor',
    transport: 'local',
    where:
      'Add to ~/.cursor/mcp.json (global) or .cursor/mcp.json (per-project). Cursor takes a stdio command OR a remote url.',
    snippet: (ctx) => stdioOrRemote(ctx, 'remote URL (a reachable/hosted instance)'),
  },
  {
    id: 'vscode',
    label: 'VS Code (Copilot)',
    transport: 'local',
    where:
      'Add to .vscode/mcp.json (workspace) or your user mcp.json. VS Code uses "servers" — not "mcpServers" — and needs a "type" field.',
    snippet: (ctx) => {
      const stdio = json({ servers: { [NAME]: stdioServer(ctx, 'stdio') } })
      if (ctx.launch.desktop) return stdio
      return `${json({ servers: { [NAME]: { type: 'http', ...remoteServer(ctx) } } })}

// — or — stdio (local command)
${stdio}`
    },
  },
  {
    id: 'chatgpt',
    label: 'ChatGPT',
    transport: 'remote',
    where:
      "ChatGPT → Settings → Connectors (Developer mode on) → Add custom connector. ChatGPT runs in OpenAI's cloud, so it needs a PUBLIC url — localhost is not reachable.",
    snippet: (ctx) => `MCP server URL:
${ctx.origin}/mcp

Authentication: Bearer token (Authorization header)
Authorization: Bearer ${ctx.token}`,
  },
  {
    id: 'http',
    label: 'Generic HTTP / remote',
    transport: 'remote',
    where:
      'Any remote-MCP client (Perplexity, hosted agents, custom). Point it at /mcp with a Bearer token. Hosted clients need a PUBLIC url.',
    snippet: (ctx) => json(remoteServer(ctx)),
  },
  {
    id: 'other',
    label: 'Cline / Continue / other',
    transport: 'local',
    where:
      'Most other MCP clients accept the same mcpServers (stdio) form, or a remote url + Bearer header.',
    snippet: (ctx) => stdioOrRemote(ctx, 'remote URL'),
  },
]

/** One plain sentence on what the stdio `command` is and when it needs a hand. */
export function launchNote(launch: McpLaunch, variant: StdioVariant): string {
  if (launch.desktop) {
    return (
      'The command is the Python bundled inside this app — nothing else to install. This app ' +
      'picks a new address each time it starts, so the bridge finds it through ONW_ROOT (your ' +
      'book folder); Open Net Worth must be running. If you move the app, copy the config again.'
    )
  }
  if (variant === 'remote') {
    return (
      'For Docker, or an AI app on another computer: on the computer running the AI app, install ' +
      'the bridge with uv tool install open-net-worth — no server needed there. It only passes ' +
      'messages to this address, so it needs no update when this instance updates. Copy this ' +
      "config from that computer, so the address is one it can reach. If your AI app can't find " +
      '"onw", use the full path printed by `which onw` (Windows: `where onw`).'
    )
  }
  if (variant === 'source') {
    return launch.source_root
      ? 'For a git checkout: cwd is the checkout this instance runs from. Needs python3 on your PATH.'
      : `For a git checkout: replace ${SOURCE_CWD_PLACEHOLDER} with your checkout's absolute path. Needs python3 on your PATH.`
  }
  if (launch.command_found) {
    return (
      'The command is the full path to onw on the computer running Open Net Worth — AI apps ' +
      "often don't search your shell's PATH. Use this config on that same computer."
    )
  }
  return (
    'Needs the onw command on the computer running Open Net Worth (uv tool install ' +
    'open-net-worth). If your AI app reports it can\'t find "onw", replace it with the full path ' +
    'printed by `which onw` (Windows: `where onw`).'
  )
}
