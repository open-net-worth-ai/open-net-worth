// Connectors — the redesigned tab shell (spec §9).
//
// Two-tier masthead: title + sub on their own row with a divider beneath, then a toolbar
// row with search pinned left and the primary action pinned right. Cramming title, search
// and button into one row overflowed narrow viewports. The family tabs and the status
// filter live in a THIRD row inside the same sticky masthead (`subrow`), so they pin with
// it and need no offset arithmetic.
//
// This file owns the shell and the screen state; the list, the detail page and the
// directory are their own modules. The nav rail and the Lucius panel are NOT part of it —
// RouterShell and LuciusDock host this tab, and every "Ask Lucius" affordance routes to
// the real composer prefill.

import { useEffect, useMemo, useState, type JSX } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { authFetch } from '../../../api/client'
import { invalidateQueryCache } from '../../../state/queryCache'
import { useChatComposerOptional } from '../../../state/chatComposer'
import { ErrorState, TabSkeleton } from '../../ui'
import './connectors.css'
import { Icons } from './icons'
import { Btn, Tabs, useToast } from './primitives'
import { ConnectorList } from './ConnectorList'
import { ConnectorDetail } from './ConnectorDetail'
import { ConnectorConfig, hasConfigForm } from './ConnectorConfig'
import { DirectoryModal } from './DirectoryModal'
import { filterDirectory, skipsPreview } from './catalog'
import { resolveDirectory, suggestionsBySection } from './directoryRows'
import { InstitutionPickerModal } from './InstitutionPickerModal'
import { McpEditModal } from './McpEditModal'
import { deleteMcpServer } from './mcpApi'
import { signInWithOAuth } from './oauthSignIn'
import { savePermissions } from './permissions'
import { mcpAuthType, toEntry, toMcpEditTarget } from './adapter'
import { CONNECTORS_KEY, useConnectors } from './useConnectors'
import { useRegisterScreenFacts } from '../../../lucius/useScreenContract'
import {
  FAMILY_SECTIONS,
  STATUS_FILTERS,
  type ConnectorDetailEntry,
  type FamilyTab,
  type FocusConnectorOptions,
  type PermissionLevel,
  type StatusFilter,
} from './model'

const MCP_PREFIX = 'mcp:'

export function ConnectorsTab(): JSX.Element {
  const { connectors, entries, initialLoading, error, refresh } = useConnectors()
  const [tab, setTab] = useState<FamilyTab>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [query, setQuery] = useState('')
  // A search also lists matching connectors not added yet, so it never dead-ends in "No
  // connectors match" for something the app does support.
  const suggestions = useMemo(
    () =>
      query.trim()
        ? filterDirectory(resolveDirectory(connectors), tab, query, 'popular').filter(
            (d) => !d.installed && !d.generic,
          )
        : [],
    [connectors, tab, query],
  )
  // The unsearched, unfiltered list also offers up to three popular connectors per section
  // that aren't added yet — the Connectors twin of the Assets screen's "Add more".
  const sectionSuggestions = useMemo(
    () => (query.trim() || statusFilter !== 'all' ? undefined : suggestionsBySection(connectors)),
    [connectors, query, statusFilter],
  )
  const [detailId, setDetailId] = useState<string | null>(null)
  /** The MCP server whose connection is being edited — the add flow, reopened pre-filled. */
  const [editMcpId, setEditMcpId] = useState<string | null>(null)
  /** `null` = the directory is closed; otherwise the family it opened pre-filtered to. */
  const [directoryFamily, setDirectoryFamily] = useState<FamilyTab | null>(null)
  const [directoryPreviewId, setDirectoryPreviewId] = useState<string | null>(null)
  const [bankPickerOpen, setBankPickerOpen] = useState(false)
  /** The MCP server a Reconnect click is currently signing in for — an OAuth reconnect is a
   *  popup + poll, not an instant call, so this both guards against a rapid double-click
   *  opening a second popup and lets the detail page's big button say so while it waits. */
  const [reconnectingId, setReconnectingId] = useState<string | null>(null)
  /** Carried from the institution search into Enable Banking's form, which asks for a
   *  country the user has already typed once. */
  const [routedCountry, setRoutedCountry] = useState<string | undefined>(undefined)
  /** Whether the detail page opens ON its configuration form. Set only by a caller that has
   *  already declared intent to set the connector up; re-decided on every `openConnector`,
   *  so it can never leak into the next connector focused. */
  const [configureOnOpen, setConfigureOnOpen] = useState(false)
  const [toast, toastNode] = useToast()
  const composer = useChatComposerOptional()
  const navigate = useNavigate()

  // The detail page resolves against the WIRE rows, not just the installed entries: a
  // connector that holds no credentials is filtered out of the list on purpose (a fresh
  // instance would otherwise show 13 rows of nothing), but it is still reachable — the
  // directory's Add routes a native connector straight here, onto the configuration view of
  // its detail page, which is where it gets set up.
  const detail = useMemo<ConnectorDetailEntry | null>(() => {
    if (!detailId) return null
    const installed = entries.find((e) => e.id === detailId)
    if (installed) return installed
    const wire = connectors.find((c) => c.id === detailId)
    return wire ? toEntry(wire) : null
  }, [detailId, entries, connectors])
  const detailWire = detail ? connectors.find((c) => c.id === detail.id) : undefined

  // Built from the row the tab already holds, so the form opens pre-filled with no second
  // fetch — and with `null` for a connector that has no add flow to reopen.
  const editTarget = useMemo(() => {
    const wire = editMcpId ? connectors.find((c) => c.id === editMcpId) : undefined
    return wire ? toMcpEditTarget(wire) : null
  }, [editMcpId, connectors])

  const openDirectory = (family: FamilyTab) => {
    setDetailId(null)
    setDirectoryPreviewId(null)
    setDirectoryFamily(family)
  }

  /** The detail page's "View details" — open the directory ON this connector's landing page,
   *  not on a browse of everything else. */
  const openLandingPage = (connectorId: string) => {
    setDetailId(null)
    setDirectoryPreviewId(connectorId)
    setDirectoryFamily('all')
  }

  /** Leave the edit flow the way it was entered — back to that server's detail page. */
  function backFromEdit() {
    setDetailId(editMcpId)
    setEditMcpId(null)
  }

  /** The ONE way any surface reaches a connector's detail page. `opts` is what the caller
   *  wants beyond the page itself; omitting it — a list row, the directory's gear — is a
   *  plain focus and lands exactly where it always did. */
  function openConnector(connectorId: string, opts?: FocusConnectorOptions) {
    setDirectoryFamily(null)
    setBankPickerOpen(false)
    setRoutedCountry(opts?.country)
    setConfigureOnOpen(opts?.configure === true)
    setDetailId(connectorId)
  }

  // A link straight to one connector (/connectors?connector=email — Lucius sends these): open
  // its settings, or its page in Browse & add when it is not a connector the app has yet.
  // Read from the router, not once at mount: the same link tapped while Connectors is already
  // open only changes the URL, and has to open the connector all the same.
  const linkedConnector = useRouterState({
    select: (st) => (st.location.search as { connector?: unknown }).connector,
  })
  useEffect(() => {
    if (initialLoading || typeof linkedConnector !== 'string' || !linkedConnector) return
    const id = linkedConnector
    const raf = requestAnimationFrame(() => {
      if (connectors.some((c) => c.id === id)) {
        openConnector(id, { configure: !entries.some((e) => e.id === id) })
      } else openLandingPage(id)
      void navigate({ to: '/connectors', search: {}, replace: true } as never)
    })
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialLoading, linkedConnector])

  async function reload() {
    invalidateQueryCache(CONNECTORS_KEY)
    await refresh()
  }

  async function patchEnabled(entry: ConnectorDetailEntry, enabled: boolean) {
    const res = await authFetch(`/api/connectors/${encodeURIComponent(entry.id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    })
    if (!res.ok) {
      toast(`Couldn't ${enabled ? 'connect' : 'disconnect'} ${entry.name}`)
      return
    }
    // Telegram only: whether the live bot actually picked up the change (best-effort —
    // never a hard failure, see `reload_best_effort`). Every other connector's response
    // carries no `reloaded` field at all, so this is a no-op for them.
    const body = (await res.json().catch(() => null)) as { reloaded?: boolean } | null
    await reload()
    if (body?.reloaded === false) {
      toast(
        `${entry.name} ${enabled ? 'connected' : 'disconnected'} in settings, but the live ` +
          `bot didn't confirm the change — it may still be running the old configuration. ` +
          `Try again, or restart the app.`,
      )
      return
    }
    toast(`${entry.name} ${enabled ? 'connected' : 'disconnected'}`)
  }

  /** An OAuth MCP server reconnects with a fresh sign-in (RFC `2026-08-14-mcp-oauth` §4.7's
   *  "Recovery is one button"); any other MCP server reconnects by re-running
   *  `initialize` + `tools/list`; a native connector re-arms by being switched back on —
   *  UNLESS it's already on, which is every case this button actually shows for (`attention`
   *  — and so this row action — is only ever true for `error`/`auth_error`, and both require
   *  `enabled` already true, see `services/connectors.py::_status`). Re-PATCHing the same
   *  `enabled: true` there is a genuine no-op, so it used to still toast "connected" without
   *  verifying or fixing anything. There is no per-connector connection TEST yet
   *  (`EmailUnifiedSection`'s own note: "a real cross-connector test flow is a future
   *  addition") — what actually needs fixing is the credentials, so open the config form
   *  instead of lying about success. */
  async function reconnect(entry: ConnectorDetailEntry) {
    if (!entry.mcp) {
      if (entry.enabled) {
        openConnector(entry.id, { configure: true })
        return
      }
      await patchEnabled(entry, true)
      return
    }
    if (reconnectingId === entry.id) return // a popup is already open for this one
    const serverId = entry.id.slice(MCP_PREFIX.length)
    const wire = connectors.find((c) => c.id === entry.id)
    setReconnectingId(entry.id)
    try {
      if (wire && mcpAuthType(wire) === 'oauth') {
        // Registration is reused; only the user's consent is re-obtained (§4.5's "a background
        // path never re-registers" — this is the one human-initiated `begin` that may).
        const outcome = await signInWithOAuth({ server_id: serverId })
        await reload()
        toast(
          outcome.ok
            ? `${entry.name} reconnected`
            : outcome.error || `Couldn't reconnect ${entry.name}`,
        )
        return
      }
      const res = await authFetch(`/api/mcp-clients/${encodeURIComponent(serverId)}/test`, {
        method: 'POST',
      })
      await reload()
      toast(res.ok ? `${entry.name} reconnected` : `Couldn't reach ${entry.name}`)
    } finally {
      setReconnectingId((id) => (id === entry.id ? null : id))
    }
  }

  /** Returns whether the level LANDED — the ladder rolls its optimistic change back on
   *  `false`, so the grid can never show a level the backend refused. */
  async function persist(
    entry: ConnectorDetailEntry,
    tools: string[],
    level: PermissionLevel,
  ): Promise<boolean> {
    const result = await savePermissions(entry, tools, level)
    if (result.ok) {
      invalidateQueryCache(CONNECTORS_KEY)
      return true
    }
    toast(result.error)
    return false
  }

  /** `DELETE /api/connectors/{id}` — the native-connector half of removal. Mirrors
   *  `deleteMcpServer`'s result shape so `removeConnector` below can treat both paths
   *  identically. For Telegram, the backend REFUSES (502) rather than clearing
   *  credentials if it couldn't confirm the live bot actually stopped first — surfacing
   *  that real detail here (not a generic "Couldn't remove") is the point: a silent
   *  generic failure would hide exactly the fail-open risk the backend ordering fix
   *  exists to prevent. */
  async function removeNativeConnector(
    entry: ConnectorDetailEntry,
  ): Promise<{ ok: true } | { ok: false; error: string }> {
    const res = await authFetch(`/api/connectors/${encodeURIComponent(entry.id)}`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
      const detail = body?.detail
      const message =
        typeof detail === 'string' && detail ? detail : `Couldn't remove ${entry.name}`
      return { ok: false, error: message }
    }
    return { ok: true }
  }

  /** Remove a connector for real (spec §3's kebab, now offered — with a confirmation —
   *  for every connector, not just MCP). An MCP server goes through its dedicated delete
   *  route (also clears every permission granted to it, so a re-add starts cautious); a
   *  native connector goes through the generic connector-remove route, which resets its
   *  store back to defaults — clearing every saved credential — and disables it. */
  async function removeConnector(entry: ConnectorDetailEntry) {
    const res = entry.mcp
      ? await deleteMcpServer(entry.id.slice(MCP_PREFIX.length))
      : await removeNativeConnector(entry)
    if (!res.ok) {
      toast(res.error)
      return
    }
    setDetailId(null)
    await reload()
    toast(`${entry.name} removed`)
  }

  useRegisterScreenFacts(() => {
    const on = entries.filter((e) => e.enabled)
    const facts: [string, string][] = [
      ['Connectors', String(entries.length)],
      ['Connected', String(on.length)],
    ]
    const attention = entries.filter((e) => e.attention)
    if (attention.length > 0) {
      facts.push(['Needs attention', attention.map((e) => e.name).join(', ')])
    }
    if (detailId) {
      const open = entries.find((e) => e.id === detailId)
      if (open) facts.push(['Viewing connector', open.name])
    }
    return facts
  })

  return (
    <div className="cn-page" data-ctx-region="content" data-ctx-title="Connectors">
      {/* Static chrome — up during the first load too, so only the list is a skeleton. */}
      <header className="rd-masthead">
        <div className="rd-masthead-head">
          <div className="rd-masthead-title">Connectors</div>
          <div className="rd-masthead-sub">
            Channels, data feeds, and tools wired into this instance
          </div>
        </div>
        <div className="rd-masthead-pinned">
          <div className="rd-masthead-toolbar">
            <span className="rd-search">
              <Icons.search />
              <input
                className="rd-input"
                type="search"
                placeholder="Search connectors…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search connectors"
              />
            </span>
            <Btn variant="primary" onClick={() => openDirectory('all')}>
              <Icons.plus /> Browse &amp; add
            </Btn>
          </div>
          <div className="rd-masthead-subrow">
            <Tabs<FamilyTab>
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'all', label: 'All', count: initialLoading ? undefined : entries.length },
                ...FAMILY_SECTIONS.map((s) => ({ key: s.key, label: s.title })),
              ]}
            />
            <div className="rd-seg cn-statusseg">
              {STATUS_FILTERS.map(([k, label]) => (
                <button
                  key={k}
                  className={'rd-seg-b' + (statusFilter === k ? ' on' : '')}
                  aria-pressed={statusFilter === k}
                  onClick={() => setStatusFilter(k)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className="rd-content">
        {initialLoading ? (
          <TabSkeleton variant="connectors" label="Loading connectors…" header={false} />
        ) : error ? (
          <ErrorState message="Couldn't load connectors." onRetry={() => void refresh()} />
        ) : (
          <ConnectorList
            entries={entries}
            filters={{ query, tab, statusFilter }}
            onOpen={(entry) => openConnector(entry.id)}
            onReconnect={(entry) => void reconnect(entry as ConnectorDetailEntry)}
            onBrowse={openDirectory}
            onAddBank={() => setBankPickerOpen(true)}
            suggestions={suggestions}
            sectionSuggestions={sectionSuggestions}
            onSuggest={(d) => (skipsPreview(d) ? openDirectory('mcp') : openLandingPage(d.id))}
          />
        )}

        <span className="rd-help">
          Want Claude Desktop, Cursor or ChatGPT to use your book? That&apos;s the other direction —
          set it up in{' '}
          <a
            href="/settings?sub=mcp"
            onClick={(e) => {
              e.preventDefault()
              void navigate({ to: '/settings', search: { sub: 'mcp' } })
            }}
          >
            Settings → MCP
          </a>
          .
        </span>
      </div>

      {detail ? (
        <ConnectorDetail
          entry={detail}
          onClose={() => setDetailId(null)}
          // Not `void`-discarded: the Pause confirmation awaits this so it can close only
          // once the write settles, exactly like `onRemove`.
          onSetEnabled={(enabled) => patchEnabled(detail, enabled)}
          onReconnect={() => void reconnect(detail)}
          reconnecting={reconnectingId === detail.id}
          onViewDetails={() => openLandingPage(detail.id)}
          // MCP only: a connector WITH a credential form reopens that form in place and never
          // reaches this. Opening the directory here was a dead end — it browsed what you
          // could add, not what you had.
          onEditConnection={() => {
            setDetailId(null)
            setEditMcpId(detail.id)
          }}
          onRefreshTools={() =>
            detail.mcp
              ? void reconnect(detail)
              : toast('Built-in connectors have a fixed set of tools — nothing to rediscover.')
          }
          // Every connector now gets the real confirm-and-remove flow — MCP's own delete
          // route or the native reset-to-defaults route, dispatched inside removeConnector —
          // EXCEPT a connector that is not `pausable` (Yahoo Finance, CoinGecko, and any future one):
          // it keeps running unconditionally, so a destructive "Remove" that reads as "this
          // connector is gone" is never offered, credential or not. The kebab item calls
          // onRemove straight through instead of confirming first, and the backend's honest
          // 422 (naming why) is what the operator sees as a toast.
          //
          // Previously this also asked `secrets_public` whether the connector held any key —
          // which made CoinGecko (an always-on connector that DOES hold a credential, the
          // API key) incorrectly read as removable, confirmation dialog and all, even though
          // the backend has always refused Yahoo Finance the same way. `pausable` alone is
          // the right predicate: whether a connector holds a secret has no bearing on whether
          // deleting it is a coherent action for a connector this app runs unconditionally.
          removable={detailWire?.pausable !== false}
          alwaysOn={detailWire?.pausable === false}
          onRemove={() => removeConnector(detail)}
          onUsePrompt={(prompt) => {
            composer?.setPendingText(prompt)
            composer?.requestOpen()
          }}
          onSetToolPermission={(tool, level) => persist(detail, [tool], level)}
          onSetGroupPermission={(tools, level) => persist(detail, tools, level)}
          configForm={
            detailWire && hasConfigForm(detailWire) ? (
              <ConnectorConfig connector={detailWire} initialCountry={routedCountry} />
            ) : undefined
          }
          configureOnOpen={configureOnOpen}
          onConfigClosed={() => void reload()}
          onSaved={() => toast('Configuration saved')}
        />
      ) : null}

      {/* Browse & add (spec §6) — the directory, its preview page and the MCP add flow.
          `DirectoryFamilyFilter` IS `FamilyTab`, so the pre-filter passes straight through. */}
      {directoryFamily !== null ? (
        <DirectoryModal
          open
          connectors={connectors}
          family={directoryFamily}
          previewId={directoryPreviewId ?? undefined}
          onClose={() => setDirectoryFamily(null)}
          onFocusConnector={openConnector}
          onAdded={() => void reload()}
          toast={toast}
        />
      ) : null}

      {/* Edit configuration (spec §3 called it Edit connection, renamed 2026-08-18 — see
          ConnectorDetail.tsx) — the add flow reopened on a saved server. Shown INSTEAD
          of the detail page, not stacked on it, and the back control returns there. */}
      {editTarget ? (
        <McpEditModal
          target={editTarget}
          onClose={() => backFromEdit()}
          onSaved={(name) => {
            toast(`${name} updated`)
            void reload()
            backFromEdit()
          }}
        />
      ) : null}

      {/* The institution-search front door (P3-LINK): resolves a country + bank name to the
          provider that covers it, then lands the user in THAT provider's form — the one
          question the per-provider cards cannot answer for themselves. `configure` is what
          makes that last clause true rather than aspirational, and it is what the country it
          carries is FOR: the hint is only visible once the form is open. A provider that is
          already linked has nothing to set up, so it lands on its detail page instead. */}
      <InstitutionPickerModal
        open={bankPickerOpen}
        onClose={() => setBankPickerOpen(false)}
        onRouteToProvider={(connectorId, country) =>
          openConnector(connectorId, { country, configure: true })
        }
      />

      {toastNode}
    </div>
  )
}
