// The banks linked through the one Enable Banking connector — each its own consent (session),
// listed by name with Renew and Remove, the same rows GoCardless shows (GoCardlessBanks).
// Renew starts a fresh consent for that bank; the server swaps it in and revokes the old one
// once the user approves, so the row stays until then.
import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { Button, ConfirmDialog, Text } from '../../ui'
import { fDate } from '../../../utils/format'
import { accessEnded, type EnableBankingBank } from './enableBankingBank'

function bankLine(b: EnableBankingBank, country: string): { text: string; warn: boolean } {
  if (accessEnded(b))
    return { text: `${country} · access ended — renew to keep syncing`, warn: true }
  const n = `${b.accounts} account${b.accounts === 1 ? '' : 's'}`
  const until = b.valid_until ? ` · access until ${fDate(b.valid_until)}` : ''
  return { text: `${country} · ${n}${until}`, warn: false }
}

export function EnableBankingBanks({
  banks,
  countryName,
  renewing,
  busy,
  onRenew,
  onChanged,
}: {
  banks: EnableBankingBank[]
  countryName: (code: string) => string
  /** The bank whose renewal is starting (its button spins), if any. */
  renewing: string | null
  /** A link is in flight — every button waits. */
  busy: boolean
  onRenew: (bank: EnableBankingBank) => void
  /** A bank left the list — re-read the status. */
  onChanged: () => void
}) {
  const [removing, setRemoving] = useState<EnableBankingBank | null>(null)
  const [removingBusy, setRemovingBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Two banks can share a name (one per country), so the country is part of how a bank is named.
  const name = (b: EnableBankingBank) =>
    b.aspsp ? `${b.aspsp} (${countryName(b.country)})` : 'this bank'

  async function remove(bank: EnableBankingBank) {
    setRemovingBusy(true)
    setError(null)
    try {
      const r = await authFetch(
        `/api/enable-banking/banks/${encodeURIComponent(bank.key)}/remove`,
        { method: 'POST' },
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Could not remove that bank.')
        return
      }
      onChanged()
      emitDataChanged(['config'])
    } catch {
      setError('Could not reach the server.')
    } finally {
      setRemovingBusy(false)
      setRemoving(null)
    }
  }

  return (
    <>
      <ul className="plaid-banks" aria-label="Linked banks">
        {banks.map((b) => {
          const line = bankLine(b, countryName(b.country))
          return (
            <li key={b.key} className="plaid-bank">
              <div className="plaid-bank-main">
                <span className="plaid-bank-name">{b.aspsp || 'Linked bank'}</span>
                <span
                  className={line.warn ? 'plaid-bank-sub plaid-bank-sub--warn' : 'plaid-bank-sub'}
                >
                  {line.text}
                </span>
              </div>
              <Button
                size="sm"
                variant={accessEnded(b) ? 'primary' : 'secondary'}
                loading={renewing === b.key}
                disabled={busy || removingBusy}
                aria-label={`Renew access to ${name(b)}`}
                onClick={() => onRenew(b)}
              >
                Renew
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="plaid-bank-remove"
                disabled={busy || removingBusy}
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
            body: 'Its access is revoked at Enable Banking and its accounts stop syncing. Transactions already imported stay. Your other banks are not affected.',
            confirmLabel: 'Remove',
            danger: true,
            confirmDisabled: removingBusy,
          }
        }
        onConfirm={() => removing && void remove(removing)}
        onCancel={() => setRemoving(null)}
      />
    </>
  )
}
