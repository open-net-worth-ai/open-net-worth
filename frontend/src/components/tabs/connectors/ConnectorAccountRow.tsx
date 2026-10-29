// One provider account in the connector "Accounts" list: what it is (type icon, name, last four,
// balance) and its next step — Add it as a new account or Link… it to an existing one, or, once
// linked, show where it lands with a quiet Unlink.
import { useState } from 'react'
import { Button } from '../../ui'
import { AccIcon } from '../accounts/ds'
import { AddAccountSelect } from '../accounts/pickers'
import type { Account } from '../accounts/types'
import { fCur } from '../accounts/format'
import { TYPE_COLOR, TYPE_ICON } from '../accounts/categories'
import { isOwed, type ProviderAccountRow } from './accountRows'

interface Props {
  row: ProviderAccountRow
  linkedName: string
  // The app's own accounts, for Link… — the same searchable, type-grouped picker as the ledger.
  accounts: Account[]
  // False when the connector isn't answering — a linked row is then a leftover, shown muted.
  live?: boolean
  busy: boolean
  onAdd: () => void
  onLink: (accountId: string) => void
  onUnlink: () => void
}

export function ConnectorAccountRow({
  row,
  linkedName,
  accounts,
  live = true,
  busy,
  onAdd,
  onLink,
  onUnlink,
}: Props) {
  const [picking, setPicking] = useState(false)
  const type = row.account_type
  const linked = !!row.linked_account_id

  return (
    <div className={`conn-acct-row${linked ? ' conn-acct-row--linked' : ''}`}>
      <span
        className="conn-acct-ico"
        style={type ? { color: TYPE_COLOR[type] } : undefined}
        aria-hidden="true"
      >
        <AccIcon name={type ? TYPE_ICON[type] : 'banknote'} size={16} />
      </span>
      <div className="conn-acct-main">
        <div className="conn-acct-name">{row.name}</div>
        <div className="conn-acct-sub">
          {row.mask ? `•${row.mask}` : ''}
          {linked && row.mask ? ' · ' : ''}
          {linked && (
            <span className={live ? 'conn-acct-linked' : 'conn-acct-linked conn-acct-linked--off'}>
              Linked to “{linkedName}”
            </span>
          )}
        </div>
      </div>
      {row.balance != null && (
        <div className="conn-acct-bal">
          {fCur(row.balance, row.currency, { cents: true })}
          {isOwed(row) && <span className="conn-acct-owed"> owed</span>}
        </div>
      )}
      <div className="conn-acct-actions">
        {linked ? (
          <Button size="sm" variant="ghost" loading={busy} onClick={onUnlink}>
            Unlink
          </Button>
        ) : picking ? (
          <>
            <div
              className="conn-acct-pick"
              role="group"
              aria-label={`Link ${row.name} to an account`}
            >
              <AddAccountSelect
                accounts={accounts}
                value=""
                autoOpen
                advance={() => {}}
                onChange={(id) => {
                  setPicking(false)
                  onLink(id)
                }}
              />
            </div>
            <Button size="sm" variant="ghost" onClick={() => setPicking(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button size="sm" loading={busy} onClick={onAdd}>
              Add
            </Button>
            {accounts.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Link ${row.name} to an existing account`}
                onClick={() => setPicking(true)}
              >
                Link…
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
