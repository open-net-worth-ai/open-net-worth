// The banks linked through the one GoCardless connector — each its own requisition, listed by
// name with its own Link again / Renew and Remove. Renewing a bank that still works keeps it
// fetching until the new consent is linked (server-side), so the old row stays until then.
import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { Button, ConfirmDialog, Text } from '../../ui'
import { fDate } from '../../../utils/format'
import { ENDED, type GoCardlessBank } from './gocardlessBank'

function bankLine(b: GoCardlessBank): { text: string; warn: boolean } {
  if (b.status === 'LN') {
    const n = `${b.accounts} account${b.accounts === 1 ? '' : 's'}`
    const until = b.consent_expires_at ? ` · access until ${fDate(b.consent_expires_at)}` : ''
    return { text: `Linked · ${n}${until}`, warn: false }
  }
  const ended = ENDED[b.status]
  if (ended) return { text: `${ended} — not syncing until you link it again`, warn: true }
  return {
    text: b.renewing
      ? 'Renewal waiting for you to finish at the bank'
      : 'Waiting for you to finish at the bank',
    warn: true,
  }
}

export function GoCardlessBanks({
  banks,
  onChanged,
}: {
  banks: GoCardlessBank[]
  /** A bank was renewed or left the list — re-read the status. */
  onChanged: () => void
}) {
  const [removing, setRemoving] = useState<GoCardlessBank | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function post(bank: GoCardlessBank, action: 'relink' | 'remove') {
    setBusy(bank.key)
    setError(null)
    try {
      const r = await authFetch(`/api/gocardless/banks/${encodeURIComponent(bank.key)}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body:
          action === 'relink' ? JSON.stringify({ redirect: window.location.origin }) : undefined,
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        setError(
          d.detail ||
            (action === 'relink'
              ? 'Could not start the bank link.'
              : 'Could not remove that bank.'),
        )
        return
      }
      if (action === 'relink' && d.link) window.open(d.link, '_blank', 'noopener,noreferrer')
      onChanged()
      emitDataChanged(['config'])
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(null)
      setRemoving(null)
    }
  }

  const name = (b: GoCardlessBank) => b.institution_name || 'this bank'

  return (
    <>
      <ul className="plaid-banks" aria-label="Linked banks">
        {banks.map((b) => {
          const line = bankLine(b)
          const ended = !!ENDED[b.status]
          return (
            <li key={b.key} className="plaid-bank">
              <div className="plaid-bank-main">
                <span className="plaid-bank-name">{b.institution_name || 'Linked bank'}</span>
                <span
                  className={line.warn ? 'plaid-bank-sub plaid-bank-sub--warn' : 'plaid-bank-sub'}
                >
                  {line.text}
                </span>
              </div>
              {!b.renewing && (
                <Button
                  size="sm"
                  variant={ended ? 'primary' : 'ghost'}
                  loading={busy === b.key}
                  disabled={busy !== null}
                  aria-label={`${b.status === 'LN' ? 'Renew access to' : 'Link again'} ${name(b)}`}
                  onClick={() => void post(b, 'relink')}
                >
                  {b.status === 'LN' ? 'Renew' : 'Link again'}
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                aria-label={`Remove ${name(b)}`}
                onClick={() => setRemoving(b)}
              >
                Remove
              </Button>
            </li>
          )
        })}
      </ul>
      {error && (
        <Text size="xs" tone="bad">
          {error}
        </Text>
      )}
      <ConfirmDialog
        data={
          removing && {
            title: `Remove ${name(removing)}?`,
            body: 'Its access is revoked at GoCardless and its accounts stop syncing. Transactions already imported stay. Your other banks are not affected.',
            confirmLabel: 'Remove',
            danger: true,
            confirmDisabled: busy !== null,
          }
        }
        onConfirm={() => removing && void post(removing, 'remove')}
        onCancel={() => setRemoving(null)}
      />
    </>
  )
}
