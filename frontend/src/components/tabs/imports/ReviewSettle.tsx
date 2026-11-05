import { Fragment } from 'react'
import { fAmountNative } from '../../../utils/format'
import { CURRENCY_OPTIONS } from '../../firstrun/currencies'
import { Cluster, Text } from '../../ui'
import { TYPE_LABELS, type AccountType } from '../accounts/categories'
import { PMSelect } from '../accounts/ds'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import { Destination, LinkSummary, type LinkProps } from './ReviewLinks'
import type { AccountTypes } from './reviewTypes'
import type { Preview, PreviewAccount } from './runs'

// What only an app that doesn't say what its accounts are needs in the review: a type for each new
// account, grouped by the band that limits it, and a currency when the budget states none.

export function CurrencyRow({
  app,
  value,
  disabled,
  onChange,
}: {
  app: BudgetAppInfo
  value: string
  disabled: boolean
  onChange: (code: string) => void
}) {
  return (
    <Cluster gap="--space-3">
      <span className="form-label">Budget currency</span>
      <PMSelect
        className="ynr-dest"
        ariaLabel={`Budget currency: ${value}`}
        value={value}
        options={CURRENCY_OPTIONS}
        disabled={disabled}
        searchable
        searchPlaceholder="Search currencies…"
        emptyText="No currency matches."
        menuWidth={280}
        onChange={onChange}
      />
      <Text size="sm" tone="faint">
        {app.name} doesn’t say, so this starts from this book’s currency.
      </Text>
    </Cluster>
  )
}

const BANDS = [
  {
    on: true,
    name: 'On budget',
    note: 'Cash or credit. A card must be Credit, or its spending counts as cash.',
  },
  { on: false, name: 'Off budget', note: 'Kept outside the budget. Pick what each one is.' },
]

function TypeSelect({ account: a, types }: { account: PreviewAccount; types: AccountTypes }) {
  const label = (t: string) => TYPE_LABELS[t as AccountType] ?? t
  const value = types.valueOf(a)
  const suggesting = !types.isSettled(a) && value !== '' && value === types.suggestionOf(a)
  return (
    <PMSelect
      className="ynr-dest"
      ariaLabel={`Type for ${a.name}: ${value ? label(value) : 'Choose…'}${suggesting ? ', suggested' : ''}`}
      value={value}
      placeholder="Choose…"
      options={(a.type_options ?? []).map((t) => ({
        value: t,
        label:
          !types.isSettled(a) && t === types.suggestionOf(a) ? `${label(t)} (suggested)` : label(t),
      }))}
      onChange={(type) => types.choose(a, type)}
    />
  )
}

function AccountRow({
  account: a,
  types,
  currency,
  link,
}: {
  account: PreviewAccount
  types: AccountTypes
  currency: string
  link: LinkProps
}) {
  const opening = a.link ? (
    <span className="ynr-dim">Its own</span>
  ) : (
    fAmountNative(a.opening_balance, currency)
  )
  const staticNew = !a.link && (!a.id || !a.candidates?.length)
  return (
    <Fragment>
      <tr>
        <td>
          <span>
            {a.name}
            {a.closed && <span className="ynr-dim"> (closed)</span>}
          </span>
          <span className="ynr-phone-only">{opening}</span>
        </td>
        <td data-label="Type">
          {a.link ? (
            <span className="ynr-dim">Keeps its own</span>
          ) : (
            <TypeSelect account={a} types={types} />
          )}
        </td>
        <td data-label="In this book" className={staticNew ? 'ynr-wide-only' : undefined}>
          <Destination account={a} {...link} />
        </td>
        <td data-label="Transactions" className="imp-table__num ynr-roomy">
          {a.transactions}
        </td>
        <td data-label="Opening balance" className="imp-table__num ynr-wide-only">
          {opening}
        </td>
      </tr>
      {a.match && a.chosen && (
        <tr className="ynr-link">
          <td colSpan={5}>
            <LinkSummary account={a} match={a.match} currency={currency} {...link} />
          </td>
        </tr>
      )}
    </Fragment>
  )
}

export function SettleAccounts({
  preview: p,
  types,
  ...link
}: { preview: Preview; types: AccountTypes } & LinkProps) {
  if (p.accounts.length === 0)
    return <p className="ynr-empty">This {link.app.planWord} has no accounts.</p>
  return (
    <table className="imp-table ynr-table ynr-accts">
      <thead>
        <tr>
          <th>Account</th>
          <th>Type</th>
          <th>In this book</th>
          <th className="imp-table__num ynr-roomy">Transactions</th>
          <th className="imp-table__num">Opening balance</th>
        </tr>
      </thead>
      {BANDS.map((band) => {
        const rows = p.accounts.filter((a) => (a.on_budget !== false) === band.on)
        if (rows.length === 0) return null
        return (
          <tbody key={band.name}>
            <tr className="ynr-group">
              <th scope="rowgroup" colSpan={5}>
                {band.name}
                <span className="ynr-count">{rows.length}</span>
                <span className="ynr-groupnote">{band.note}</span>
              </th>
            </tr>
            {rows.map((a) => (
              <AccountRow
                key={a.id ?? a.name}
                account={a}
                types={types}
                currency={p.currency}
                link={link}
              />
            ))}
          </tbody>
        )
      })}
    </table>
  )
}
