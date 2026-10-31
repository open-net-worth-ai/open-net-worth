// Settings → Developer. Ported from the DeveloperSection in
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx. Connecting other AI apps
// (Settings → MCP) and iPhone Shortcuts are everyday features with their own sections.
// Experimental (feature flags) is generated from the backend registry — ExperimentalPanel.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { Zap } from '../../../../ui/icons'
import { ExperimentalPanel } from '../ExperimentalPanel'
import { SqlAccessPanel } from '../SqlAccessPanel'
import { Btn, Panel, Select, Toggle, row, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'

interface ExtensionRow {
  id: string
  name: string
  version: string
  type: string
  source: string
  domain?: string | null
  description?: string | null
  enabled: boolean
}

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

export function LogViewer({ toast }: SectionProps) {
  const [level, setLevel] = useState('INFO')
  const [q, setQ] = useState('')
  const key = `/api/logs?lines=500&level=${level}&q=${encodeURIComponent(q)}`
  const { data, refresh } = useCachedFetchState<{ lines: string[] }>(
    key,
    () => getJson<{ lines: string[] }>(key),
    { staleTime: 5_000 },
  )

  return (
    <Panel title="Application logs" sub="Tail of the server log — filter and search live">
      <div style={{ display: 'flex', gap: 8 }}>
        <Select
          value={level}
          style={{ width: 110 }}
          aria-label="Log level"
          onChange={(e) => setLevel(e.target.value)}
        >
          <option value="DEBUG">debug</option>
          <option value="INFO">info</option>
          <option value="WARNING">warn</option>
          <option value="ERROR">error</option>
        </Select>
        <input
          className="rd-input"
          placeholder="Search log lines…"
          style={{ maxWidth: 240 }}
          aria-label="Search logs"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Btn
          size="xs"
          onClick={() => {
            void refresh()
            toast('Refreshed')
          }}
        >
          Refresh
        </Btn>
      </div>
      {/* Server logs carry email addresses, account names and other PII (see
          backend/api/logs.py) — never into the screen snapshot. */}
      <div className="rd-code" style={{ height: 340, overflowY: 'auto' }} data-ctx-secret="">
        {(data?.lines ?? []).join('\n') || 'No matching log lines.'}
      </div>
    </Panel>
  )
}

function CodeExecution({ toast }: SectionProps) {
  const { data, initialLoading, refresh } = useCachedFetchState<{
    enabled: boolean
    auto_run: boolean
  }>(
    '/api/config/code-execution',
    () => getJson<{ enabled: boolean; auto_run: boolean }>('/api/config/code-execution'),
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const codeExec = data?.enabled ?? false
  const autoRun = data?.auto_run ?? false

  async function patch(next: { enabled?: boolean; auto_run?: boolean }, message: string) {
    try {
      const r = await authFetch('/api/config/code-execution', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        // Only what changed — see SecuritySection for why the spread was a race. The one
        // case that genuinely needs both fields is the force-clear, and it passes both.
        body: JSON.stringify(next),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void refresh()
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <Panel title="Code execution" sub="Lucius can write & run short Python on this server">
      <span className="rd-help" style={{ display: 'block', lineHeight: 1.55 }}>
        When on, Lucius can write a small script during a chat and run it in an isolated subprocess
        to do a calculation. The sandbox scrubs secrets from the environment, enforces
        CPU/memory/file limits, and the script sees only the JSON data Lucius passes it — it cannot
        read or write your ledger or accounts, and has no network. Ad-hoc code runs only in
        interactive chat, never from a scheduled task or an MCP client.
      </span>
      {row(
        'Code execution',
        codeExec
          ? "You'll be asked for permission the first time Lucius runs code in each conversation."
          : "Lucius's run_code is blocked. This is the safe default.",
        <Toggle
          on={codeExec}
          disabled={!data}
          onChange={(v) =>
            // The parent force-clears the child: leaving auto_run set while the feature is off
            // would silently re-arm "always allow" the moment it is switched back on.
            void patch(
              v ? { enabled: true } : { enabled: false, auto_run: false },
              v ? 'Code execution enabled' : 'Code execution blocked',
            )
          }
          label="Code execution"
        />,
      )}
      {codeExec
        ? row(
            'Always allow (skip the per-run prompt)',
            autoRun
              ? 'Lucius runs code without asking, in every conversation. Turn this off to be prompted — and shown the code — each conversation.'
              : "You'll be asked for permission and shown the code the first time Lucius runs it in a conversation.",
            <Toggle
              on={autoRun}
              onChange={(v) =>
                void patch({ auto_run: v }, v ? 'Always allowing' : 'Prompting per conversation')
              }
              label="Always allow"
            />,
          )
        : null}
    </Panel>
  )
}

export function Extensions({ toast }: SectionProps) {
  const { data, initialLoading, refresh } = useCachedFetchState<ExtensionRow[]>(
    '/api/extensions',
    () => getJson<ExtensionRow[]>('/api/extensions'),
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const rows = data ?? []

  async function toggle(x: ExtensionRow, enabled: boolean) {
    try {
      const r = await authFetch(
        `/api/extensions/${encodeURIComponent(x.type)}/${encodeURIComponent(x.id)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled }),
        },
      )
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void refresh()
      toast(enabled ? 'Enabled' : 'Disabled — it is no longer consulted')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function uninstall(x: ExtensionRow) {
    try {
      const r = await authFetch(
        `/api/extensions/${encodeURIComponent(x.type)}/${encodeURIComponent(x.id)}`,
        {
          method: 'DELETE',
        },
      )
      if (!r.ok) {
        const body = (await r.json().catch(() => null)) as { detail?: string } | null
        toast(body?.detail || `Could not uninstall (HTTP ${r.status})`)
        return
      }
      void refresh()
      toast('Uninstalled')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <Panel
      title="Extensions"
      sub="Installed instance extensions — each declares its permissions up front"
      flush
    >
      {rows.length ? (
        rows.map((x) => (
          <div className="rd-row" key={x.type + '/' + x.id}>
            <span className="rd-row-ic">
              <Zap size={13} strokeWidth={1.7} aria-hidden="true" />
            </span>
            <div className="rd-row-main">
              <div className="rd-row-title">
                {x.name}{' '}
                <span className="rd-faint" style={{ fontWeight: 500, fontSize: 11 }}>
                  v{x.version}
                </span>
              </div>
              <div className="rd-row-meta">
                <span>{x.description || x.type}</span>
                {x.source === 'shipped' ? <span>Bundled</span> : null}
              </div>
            </div>
            <div className="rd-row-actions" style={{ opacity: 1, pointerEvents: 'auto' }}>
              <Toggle
                on={x.enabled}
                onChange={(v) => void toggle(x, v)}
                label={'Enable ' + x.name}
              />
              {x.source === 'shipped' ? null : (
                <Btn size="xs" variant="ghost" onClick={() => void uninstall(x)}>
                  Uninstall
                </Btn>
              )}
            </div>
          </div>
        ))
      ) : (
        <div style={{ padding: '14px 18px' }}>
          <span className="rd-help">
            Nothing installed — extension packs you install will be listed here.
          </span>
        </div>
      )}
    </Panel>
  )
}

function ScriptSkills({ toast }: SectionProps) {
  const kill = useCachedFetchState<{ enabled: boolean }>(
    '/api/config/script-skills',
    () => getJson<{ enabled: boolean }>('/api/config/script-skills'),
    { staleTime: 0 },
  )
  const unattended = useCachedFetchState<{ enabled: boolean }>(
    '/api/config/script-skills-unattended',
    () => getJson<{ enabled: boolean }>('/api/config/script-skills-unattended'),
    { staleTime: 0 },
  )
  useSectionBusy(
    (kill.initialLoading && !kill.data) || (unattended.initialLoading && !unattended.data),
  )
  const scripts = kill.data?.enabled ?? false
  const unattendedOn = unattended.data?.enabled ?? false

  async function put(path: string, enabled: boolean) {
    const r = await authFetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!r.ok) throw new Error(String(r.status))
  }

  return (
    <Panel
      title="Bundled script skills"
      sub="Two independent switches — permissions sit on the caller, not the skill"
    >
      {row(
        'Bundled script skills',
        'The instance kill switch. When off, no script-bearing skill can run its Python, whatever else is set. Interactive chat turns only need this switch.',
        <Toggle
          on={scripts}
          disabled={!kill.data}
          onChange={(v) => {
            void (async () => {
              try {
                await put('/api/config/script-skills', v)
                // Force-clear the child so turning the kill switch back on never silently
                // re-arms unattended runs.
                if (!v && unattendedOn) await put('/api/config/script-skills-unattended', false)
                void kill.refresh()
                void unattended.refresh()
                toast(v ? 'Script skills enabled' : 'Script skills blocked')
              } catch {
                toast('Could not save — try again')
              }
            })()
          }}
          label="Bundled script skills"
        />,
      )}
      {row(
        'Unattended script runs',
        scripts
          ? 'Lets scheduled, headless runs execute script-bearing skills. Off by default — interactive chat turns are unaffected either way.'
          : 'Enable the kill switch above first.',
        <Toggle
          on={unattendedOn}
          disabled={!scripts}
          onChange={(v) => {
            void (async () => {
              try {
                await put('/api/config/script-skills-unattended', v)
                void unattended.refresh()
                toast(v ? 'Unattended runs allowed' : 'Unattended runs blocked')
              } catch {
                toast('Could not save — try again')
              }
            })()
          }}
          label="Unattended script runs"
        />,
      )}
    </Panel>
  )
}

export function DeveloperSection({ toast }: SectionProps) {
  return (
    <>
      <LogViewer toast={toast} />
      <CodeExecution toast={toast} />
      <SqlAccessPanel toast={toast} />
      <Extensions toast={toast} />
      <ScriptSkills toast={toast} />
      <ExperimentalPanel toast={toast} />
    </>
  )
}
