import { fAmountNative, fDate } from '../../../utils/format'
import { Checkbox, Stack } from '../../ui'
import { PMSelect } from '../accounts/ds'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import { plural, type LinkMatch, type PreviewAccount } from './runs'

// Where an account of the budget goes in this book, in a review: a new account or one already
// here, and what that link matches and adds. The server answers each choice with a re-check.

export interface LinkProps {
  app: BudgetAppInfo
  links: Record<string, string>
  checking: boolean
  onLink: (ynabId: string, accountId: string) => void
  sameAs: Record<string, string>
  onSameAs: (next: Record<string, string>) => void
}

// A new account, or one already here of a type and currency the plan's account fits.
export function Destination({
  account: a,
  links,
  checking,
  onLink,
}: { account: PreviewAccount } & LinkProps) {
  if (a.link && !a.chosen) return <span className="ynr-dim">{a.link.name} (linked)</span>
  if (!a.id || !a.candidates?.length) return <span className="ynr-dim">New account</span>
  return (
    <PMSelect
      className="ynr-dest"
      ariaLabel={`Where ${a.name} goes`}
      value={links[a.id] ?? ''}
      disabled={checking}
      options={[
        { value: '', label: 'New account' },
        ...a.candidates.map((c) => ({ value: c.id, label: `Use ${c.name}` })),
      ]}
      onChange={(v) => onLink(a.id!, v)}
    />
  )
}

export function LinkSummary({
  account: a,
  match: m,
  currency,
  app,
  checking,
  sameAs,
  onSameAs,
}: { account: PreviewAccount; match: LinkMatch; currency: string } & LinkProps) {
  if (checking)
    return <span className="ynr-dim">Checking which transactions are already there…</span>
  const here = a.link?.name ?? 'the account'
  const parts = [
    `${plural(m.matched, 'transaction')} already in ${here}`,
    `${m.added.toLocaleString()} added`,
  ]
  if (m.to_check > 0) parts.push(`${m.to_check.toLocaleString()} to check`)
  const money = (n: number) => fAmountNative(n, currency)
  return (
    <Stack gap="--space-2">
      <span>{parts.join('. ')}.</span>
      {m.own_only > 0 && (
        <span className="ynr-dim">
          {plural(m.own_only, 'transaction')} of {here}’s own{' '}
          {m.own_only === 1 ? 'isn’t' : 'aren’t'}
          {` in ${app.name}; a sync to ${app.name} sends`} {m.own_only === 1 ? 'it' : 'them'} there.
        </span>
      )}
      {m.pairs.length > 0 && (
        <>
          <span className="ynr-dim">
            These may be the same transaction. Each is added as a new one unless you tick it.
          </span>
          <ul className="ynr-pairs">
            {m.pairs.map((x) => (
              <li key={x.ref}>
                <Checkbox
                  checked={sameAs[x.ref] === x.existing.id}
                  onChange={(e) => {
                    const next = { ...sameAs }
                    if (e.target.checked) next[x.ref] = x.existing.id
                    else delete next[x.ref]
                    onSameAs(next)
                  }}
                  label={`Same as ${x.existing.payee ?? '(no payee)'}, ${fDate(x.existing.date)}, ${money(x.existing.amount)}`}
                  description={`${app.name}: ${x.payee ?? '(no payee)'}, ${fDate(x.date)}, ${money(x.amount)}`}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Stack>
  )
}
