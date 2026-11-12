// "Link to a bank account…" — link THIS account to one of the accounts a connected bank or
// broker can see, starting from the account rather than from the connector (Krishna,
// 2026-11-12). The list is GET /api/linkable-bank-accounts (every connected connector's own
// account list, the same read the Connectors screen makes); the link is the account's
// `external_ids`, exactly what the connector page's Link… writes. The closest match is offered
// first but nothing is linked without the user's pick; a bank account already feeding another
// account is shown, not offered, so one feed never lands in two accounts by accident.
import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { useAccounts } from '../../../hooks/serverState'
import { Button, Modal, Text } from '../../ui'
import { ConnectorNextStep } from '../connectors/ConnectorNextStep'
import type { Account, ExternalId } from './types'
import { matchScore, type BankAccount } from './linkBankMatch'

interface ConnectorGroup {
  provider: string
  provider_label: string
  status: string
  accounts: BankAccount[]
}

export function LinkBankAccountDialog({
  account,
  onClose,
  onConnectBank,
  onLinked,
}: {
  account: Account
  onClose: () => void
  /** Nothing to link — go connect a bank (the Connectors screen). */
  onConnectBank: () => void
  /** The new link, for a screen that shows the account's links (Edit account). */
  onLinked?: (link: ExternalId) => void
}) {
  // id → name, to say which account a taken bank account already feeds.
  const { data: allAccounts } = useAccounts()
  const accountNames = Object.fromEntries((allAccounts ?? []).map((a) => [a.id, String(a.name)]))
  const [groups, setGroups] = useState<ConnectorGroup[] | null>(null)
  const [loadErr, setLoadErr] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [linked, setLinked] = useState<{ provider: string; label: string; name: string } | null>(
    null,
  )

  useEffect(() => {
    let live = true
    authFetch('/api/linkable-bank-accounts')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d: { connectors: ConnectorGroup[] }) => live && setGroups(d.connectors))
      .catch(() => live && setLoadErr(true))
    return () => {
      live = false
    }
  }, [])

  const candidates = (groups ?? []).flatMap((g) =>
    g.accounts.map((row) => ({
      group: g,
      row,
      score: matchScore(account, row, g.provider_label),
    })),
  )
  const free = candidates.filter((c) => !c.row.linked_account_id)
  const best = [...free].sort((a, b) => b.score - a.score)[0]
  const suggested = best && best.score >= 3 ? best : undefined

  async function link(group: ConnectorGroup, row: BankAccount) {
    setBusy(`${group.provider}:${row.provider_account_id}`)
    setErr(null)
    try {
      const external_ids = [
        ...(account.externalIds ?? []).map(({ source, ref, name, mask, linkedAt }) => ({
          source,
          ref,
          name,
          mask,
          linked_at: linkedAt,
        })),
        {
          source: group.provider,
          ref: row.provider_account_id,
          name: row.name,
          mask: row.mask ?? '',
          ...(row.institution ? { institution: row.institution } : {}),
        },
      ]
      const r = await authFetch(`/api/accounts/${encodeURIComponent(account.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ external_ids }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail || 'Could not link that account — try again.')
        return
      }
      setLinked({ provider: group.provider, label: group.provider_label, name: row.name })
      onLinked?.({
        source: group.provider,
        ref: row.provider_account_id,
        name: row.name,
        mask: row.mask ?? '',
        linkedAt: new Date().toISOString(),
        providerLabel: group.provider_label,
        status: 'connected',
      })
      emitDataChanged(['accounts'])
    } catch {
      setErr('Could not reach the server.')
    } finally {
      setBusy(null)
    }
  }

  const rowView = (c: (typeof candidates)[number]) => {
    const { group, row } = c
    const takenBy = row.linked_account_id
    const key = `${group.provider}:${row.provider_account_id}`
    return (
      <li key={key} className="link-bank-row">
        <div className="link-bank-main">
          <span className="link-bank-name">
            {row.name}
            {suggested === c && <span className="link-bank-tag">Suggested</span>}
          </span>
          <span className="link-bank-sub">
            {[row.mask ? `•${row.mask}` : '', row.currency ?? '', row.institution ?? '']
              .filter(Boolean)
              .join(' · ')}
            {takenBy &&
              ` · ${takenBy === account.id ? 'Linked to this account' : `Linked to “${accountNames[takenBy] ?? 'another account'}”`}`}
          </span>
        </div>
        {!takenBy && (
          <Button
            size="sm"
            variant={suggested === c ? 'primary' : 'secondary'}
            loading={busy === key}
            disabled={busy !== null}
            aria-label={`Link ${account.name} to ${row.name}${row.mask ? ` •${row.mask}` : ''}`}
            onClick={() => void link(group, row)}
          >
            Link
          </Button>
        )}
      </li>
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Link ${account.name} to a bank account`}
      size="md"
      footer={
        <Button size="sm" variant={linked ? 'primary' : 'ghost'} onClick={onClose}>
          {linked ? 'Done' : 'Cancel'}
        </Button>
      }
    >
      {linked ? (
        <div className="link-bank-done">
          <Text size="sm">
            {account.name} is linked to {linked.name} through {linked.label}.
          </Text>
          <ConnectorNextStep
            provider={linked.provider}
            providerLabel={linked.label}
            linkedCount={1}
            unlinkedCount={0}
          />
        </div>
      ) : loadErr ? (
        <Text size="sm" tone="bad">
          Could not load your banks’ accounts.
        </Text>
      ) : groups === null ? (
        <Text size="sm" tone="faint">
          Asking your banks which accounts they can see…
        </Text>
      ) : free.length === 0 ? (
        <div className="link-bank-empty">
          <Text size="sm">
            {groups.length === 0
              ? 'No bank or broker is connected yet.'
              : 'Every account your connected banks can see is already linked.'}
          </Text>
          <Button size="sm" variant="primary" onClick={onConnectBank}>
            Connect a bank
          </Button>
        </div>
      ) : (
        <div className="link-bank-groups">
          {groups.map((g) => (
            <section key={g.provider} aria-label={g.provider_label}>
              <div className="link-bank-group-label">
                {g.provider_label}
                {g.status !== 'ok' && ' · not answering right now'}
              </div>
              <ul className="link-bank-list">
                {candidates.filter((c) => c.group === g).map(rowView)}
              </ul>
            </section>
          ))}
          {err && (
            <Text size="xs" tone="bad">
              {err}
            </Text>
          )}
          <Button size="sm" variant="ghost" onClick={onConnectBank}>
            Connect another bank
          </Button>
        </div>
      )}
    </Modal>
  )
}
