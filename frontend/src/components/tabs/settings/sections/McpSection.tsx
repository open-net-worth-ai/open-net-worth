// Settings → MCP. This instance IS an MCP server: other AI apps (Claude Desktop, Cursor, VS
// Code, ChatGPT…) connect to it and use your book. The reverse — Lucius using someone else's
// server — lives in Connectors.
//
// Snippets come from mcpClients.ts (the one source), filled with this instance's real origin,
// its real API token on copy, and the launch command the server reports for this install.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { Btn, Panel, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import {
  FALLBACK_LAUNCH,
  MCP_CLIENTS,
  STDIO_VARIANTS,
  defaultVariant,
  launchNote,
} from './mcpClients'
import type { McpLaunch, StdioVariant } from './mcpClients'

/** What the rendered snippet shows in place of the real bearer. */
export const TOKEN_MASK = 'ONW_API_TOKEN_PASTED_ON_COPY'

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

export function McpSection({ toast, openSection }: SectionProps) {
  const [client, setClient] = useState('claude-desktop')
  // null until the user picks: the default follows what the server reports (Docker → remote).
  const [picked, setPicked] = useState<StdioVariant | null>(null)
  const { data: tokenData, initialLoading: loadingToken } = useCachedFetchState<{ token: string }>(
    '/api/auth/api-token',
    () => getJson<{ token: string }>('/api/auth/api-token'),
    { staleTime: 0 },
  )
  const { data: launchData, initialLoading: loadingLaunch } = useCachedFetchState<McpLaunch>(
    '/api/mcp/connect-info',
    () => getJson<McpLaunch>('/api/mcp/connect-info'),
    { staleTime: 0 },
  )
  useSectionBusy((loadingToken && !tokenData) || (loadingLaunch && !launchData))
  const launch = launchData ?? FALLBACK_LAUNCH
  const variant = picked ?? defaultVariant(launch)
  const origin = window.location.origin
  const token = tokenData?.token || 'YOUR_API_TOKEN'
  const g = MCP_CLIENTS.find((c) => c.id === client) || MCP_CLIENTS[0]!
  const local = g.transport === 'local'
  const showVariants = local && !launch.desktop
  const ctx = { origin, launch, variant: showVariants ? variant : ('installed' as const) }
  // The DISPLAYED snippet masks the token; only the clipboard gets the real one. Rendering
  // a full-access bearer as page text put it in reach of the Lucius screen snapshot, which
  // walks visible text and ships it to the model provider and into the saved transcript.
  const snippet = g.snippet({ ...ctx, token: TOKEN_MASK })
  const copyable = g.snippet({ ...ctx, token })

  return (
    <Panel
      title="Use your book from other AI apps"
      sub="This instance is an MCP server — point Claude Desktop, Cursor, VS Code or ChatGPT at it"
    >
      <span className="rd-help" style={{ display: 'block', lineHeight: 1.55 }}>
        An app holding your API token acts as you: it can read your book and make changes. Every
        change it makes is recorded and can be undone. This is the reverse of Connectors → MCP,
        where Lucius uses someone else&apos;s server.
      </span>
      <div>
        <div className="rd-bars-lbl" style={{ marginBottom: 7 }}>
          Connect from…
        </div>
        <div className="rd-seg rd-seg--scroll" role="tablist" aria-label="MCP client">
          {MCP_CLIENTS.map((c) => (
            <button
              type="button"
              key={c.id}
              role="tab"
              aria-selected={client === c.id}
              className={'rd-seg-b' + (client === c.id ? ' on' : '')}
              onClick={() => setClient(c.id)}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>
      <div>
        <span className="rd-help" style={{ display: 'block', lineHeight: 1.55 }}>
          <span className={'rd-badge ' + (local ? '' : 'warn')}>
            {local ? 'Local (stdio)' : 'Needs a public URL'}
          </span>{' '}
          {g.where}
        </span>
        {!local && LOOPBACK.test(origin) ? (
          <span className="rd-help" style={{ display: 'block', lineHeight: 1.55, marginTop: 6 }}>
            This instance is on {window.location.host}, which a hosted app can&apos;t reach — expose
            it through a tunnel or a public deployment first.
          </span>
        ) : null}
        {showVariants ? (
          <div
            className="rd-seg"
            role="tablist"
            aria-label="How Open Net Worth runs"
            style={{ marginTop: 9 }}
          >
            {STDIO_VARIANTS.map(([key, label]) => (
              <button
                type="button"
                key={key}
                role="tab"
                aria-selected={variant === key}
                className={'rd-seg-b' + (variant === key ? ' on' : '')}
                onClick={() => setPicked(key)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
        {local ? (
          <span className="rd-help" style={{ display: 'block', lineHeight: 1.55, marginTop: 9 }}>
            {launchNote(launch, ctx.variant)}
          </span>
        ) : null}
        {/* data-ctx-secret keeps this region out of the screen snapshot entirely — belt as
            well as braces, since the visible text is already masked. */}
        <div className="rd-code" style={{ marginTop: 9 }} data-ctx-secret="">
          {snippet}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 9, flexWrap: 'wrap' }}>
          <Btn
            size="xs"
            onClick={() => {
              void navigator.clipboard
                .writeText(copyable)
                .then(() => toast('Copied ' + g.label + ' config — with your real token'))
                .catch(() => toast('Could not copy — select the snippet manually'))
            }}
          >
            Copy config
          </Btn>
          <Btn size="xs" variant="ghost" onClick={() => openSection?.('security')}>
            Manage API token
          </Btn>
        </div>
        <span className="rd-help" style={{ display: 'block', lineHeight: 1.55, marginTop: 9 }}>
          One token for every app, kept in Security. Rotating it there disconnects every app
          configured with the old one.
        </span>
      </div>
    </Panel>
  )
}
