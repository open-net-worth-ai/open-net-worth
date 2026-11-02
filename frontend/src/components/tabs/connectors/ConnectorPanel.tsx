import { useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { invalidateQueryCache } from '../../../state/queryCache'
import { useChatComposerOptional } from '../../../state/chatComposer'
import './connectors.css'
import { toEntry } from './adapter'
import { ConnectorConfig, hasConfigForm } from './ConnectorConfig'
import { ConnectorDetail } from './ConnectorDetail'
import { removeNativeConnector, setConnectorEnabled } from './connectorActions'
import { savePermissions } from './permissions'
import { useToast } from './primitives'
import { CONNECTORS_KEY, useConnectors } from './useConnectors'
import type { PermissionLevel } from './model'

// A built-in connector's panel — the SAME ConnectorDetail the Connectors tab shows, opened from
// anywhere else a connector is set up (the YNAB data sync, today), so there is one panel per
// connector rather than a look-alike per screen. Built-in connectors only: an MCP server's
// reconnect and edit flows belong to the Connectors tab. Its styles are scoped to `.cn-page`,
// which the wrapper supplies.

export function ConnectorPanel({
  connectorId,
  configureOnOpen = false,
  onClose,
}: {
  connectorId: string
  /** Open on the credential form rather than the overview. */
  configureOnOpen?: boolean
  onClose: () => void
}) {
  const { connectors, entries, refresh } = useConnectors()
  const [toast, toastNode] = useToast()
  const composer = useChatComposerOptional()
  const navigate = useNavigate()
  // Reconnecting a connector that is already on means fixing its credentials: reopen the panel
  // on its form (a new key remounts it there).
  const [configure, setConfigure] = useState({ on: configureOnOpen, key: 0 })

  const wire = connectors.find((c) => c.id === connectorId)
  const entry = useMemo(
    () => entries.find((e) => e.id === connectorId) ?? (wire ? toEntry(wire) : null),
    [entries, wire, connectorId],
  )

  async function reload() {
    invalidateQueryCache(CONNECTORS_KEY)
    await refresh()
  }

  async function setEnabled(enabled: boolean) {
    if (!entry) return
    const result = await setConnectorEnabled(entry, enabled)
    if (result.ok) await reload()
    toast(result.ok ? result.message : result.error)
  }

  async function persist(tools: string[], level: PermissionLevel): Promise<boolean> {
    if (!entry) return false
    const result = await savePermissions(entry, tools, level)
    if (result.ok) {
      invalidateQueryCache(CONNECTORS_KEY)
      return true
    }
    toast(result.error)
    return false
  }

  async function remove() {
    if (!entry) return
    const result = await removeNativeConnector(entry)
    if (!result.ok) {
      toast(result.error)
      return
    }
    await reload()
    onClose()
  }

  if (!entry) return null
  return (
    <div className="cn-page">
      <ConnectorDetail
        key={configure.key}
        entry={entry}
        onClose={onClose}
        onSetEnabled={setEnabled}
        onReconnect={() =>
          entry.enabled
            ? setConfigure((c) => ({ on: true, key: c.key + 1 }))
            : void setEnabled(true)
        }
        onViewDetails={() =>
          void navigate({ to: '/connectors', search: { connector: connectorId } } as never)
        }
        onEditConnection={() => undefined}
        onRefreshTools={() =>
          toast('Built-in connectors have a fixed set of tools — nothing to rediscover.')
        }
        removable={wire?.pausable !== false}
        alwaysOn={wire?.pausable === false}
        onRemove={remove}
        onUsePrompt={(prompt) => {
          composer?.setPendingText(prompt)
          composer?.requestOpen()
        }}
        onSetToolPermission={(tool, level) => persist([tool], level)}
        onSetGroupPermission={(tools, level) => persist(tools, level)}
        configForm={wire && hasConfigForm(wire) ? <ConnectorConfig connector={wire} /> : undefined}
        configureOnOpen={configure.on}
        onConfigClosed={() => void reload()}
        onSaved={() => toast('Configuration saved')}
      />
      {toastNode}
    </div>
  )
}
