// Step ② of Plaid setup — the banks linked through this one Plaid connector. Each bank is its
// own Plaid Item: listed by name with its own Remove, and "Link another bank" adds one more
// (re-linking the same bank replaces its entry server-side). Link bank saves unsaved keys first
// (the caller's `onLink`).
import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { emitDataChanged } from '../../../lucius/bus'
import { Button, Cluster, ConfirmDialog, Stack, Text } from '../../ui'
import { fDate } from '../../../utils/format'
import { SetupStep, type SetupStepState } from './SetupStep'
import type { PlaidLink } from './usePlaidLink'

export interface PlaidBank {
  key: string
  institution_name: string
  linked_at: string | null
  /** Plaid says this bank's sign-in must be renewed — offer Reconnect (update mode). */
  needs_reconnect?: boolean
}

export function PlaidBanksStep({
  banks,
  caution,
  state,
  link,
  keysEntered,
  saving,
  onLink,
  onChanged,
}: {
  banks: PlaidBank[]
  /** The last fetch failed for some banks (an expired login, say) — shown so it isn't silent. */
  caution?: string | null
  state: SetupStepState
  link: PlaidLink
  keysEntered: boolean
  saving: boolean
  onLink: () => void
  /** A bank left the list — re-read the status. */
  onChanged: () => void
}) {
  // Renew one linked bank in place (Plaid update mode) — same accounts, so every link survives.
  const renew = (b: PlaidBank, addAccounts: boolean) =>
    void link.start({ bank: b.key, addAccounts })
  const [removing, setRemoving] = useState<PlaidBank | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const any = banks.length > 0

  async function remove(bank: PlaidBank) {
    setBusy(true)
    setError(null)
    try {
      const r = await authFetch(`/api/plaid/banks/${encodeURIComponent(bank.key)}/remove`, {
        method: 'POST',
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setError(d.detail || 'Could not remove that bank.')
        return
      }
      onChanged()
      emitDataChanged(['config'])
    } finally {
      setBusy(false)
      setRemoving(null)
    }
  }

  return (
    <SetupStep n={2} title={any ? 'Link your banks' : 'Link your bank'} state={state}>
      <Stack gap="--space-3">
        {any && (
          <ul className="plaid-banks" aria-label="Linked banks">
            {banks.map((b) => (
              <li key={b.key} className="plaid-bank">
                <div className="plaid-bank-main">
                  <span className="plaid-bank-name">{b.institution_name || 'Linked bank'}</span>
                  {b.needs_reconnect ? (
                    <span className="plaid-bank-sub plaid-bank-sub--warn">
                      Sign-in expired — nothing syncs until you reconnect
                    </span>
                  ) : (
                    b.linked_at && (
                      <span className="plaid-bank-sub">Linked {fDate(b.linked_at, 'short')}</span>
                    )
                  )}
                </div>
                {b.needs_reconnect ? (
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={link.waiting || link.starting}
                    onClick={() => renew(b, false)}
                  >
                    Reconnect
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={link.waiting || link.starting}
                    aria-label={`Add accounts at ${b.institution_name || 'this bank'}`}
                    onClick={() => renew(b, true)}
                  >
                    Add accounts
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove ${b.institution_name || 'this bank'}`}
                  onClick={() => setRemoving(b)}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}

        {caution && (
          <Text size="xs" tone="warn">
            Last fetch couldn&apos;t reach every bank — {caution}. If a bank&apos;s sign-in expired,
            link it again.
          </Text>
        )}

        {link.waiting ? (
          <Cluster gap="--space-3" align="center" wrap>
            <Text size="xs" tone="muted">
              Waiting for you to finish at Plaid… this page finishes on its own.
            </Text>
            <Button size="sm" variant="secondary" loading={link.checking} onClick={link.checkNow}>
              Check now
            </Button>
          </Cluster>
        ) : (
          <>
            {!any && (
              <Text size="xs" tone="muted">
                Sign in at your bank in your browser. When Plaid says you can close the window, come
                back — this page finishes the link on its own.
              </Text>
            )}
            {link.justLinked && (
              <Text size="xs" tone="good">
                {!link.renewed
                  ? 'Bank linked. Choose its accounts below.'
                  : link.renewedKind === 'accounts'
                    ? `${link.renewed} updated — any accounts you added are listed below; existing links are unchanged.`
                    : `${link.renewed} reconnected — its accounts and links are unchanged.`}
              </Text>
            )}
            <Cluster gap="--space-3" align="center" wrap>
              <Button
                size="sm"
                variant={any ? 'secondary' : 'primary'}
                loading={link.starting || saving}
                disabled={!keysEntered}
                onClick={onLink}
              >
                {any ? 'Link another bank' : 'Link bank'}
              </Button>
              {!keysEntered && (
                <Text size="xs" tone="faint">
                  Enter your Client ID and Secret first.
                </Text>
              )}
            </Cluster>
          </>
        )}
        {(link.error || error) && (
          <Text size="xs" tone="bad">
            {link.error || error}
          </Text>
        )}
      </Stack>

      <ConfirmDialog
        data={
          removing && {
            title: `Remove ${removing.institution_name || 'this bank'}?`,
            body: 'Its accounts stop syncing and leave the list. Transactions already imported stay. Your other banks are not affected.',
            confirmLabel: 'Remove',
            danger: true,
            confirmDisabled: busy,
          }
        }
        onConfirm={() => removing && void remove(removing)}
        onCancel={() => setRemoving(null)}
      />
    </SetupStep>
  )
}
