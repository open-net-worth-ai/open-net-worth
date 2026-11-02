import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { fAmountNative, fDate } from '../../../utils/format'
import { Button, Checkbox, Cluster, Modal, PageTabs, Skeleton, Stack, Text } from '../../ui'
import { PMSelect } from '../accounts/ds'
import {
  messageOf,
  useRun,
  type LinkMatch,
  type Preview,
  type PreviewAccount,
  type PreviewTxn,
} from './runs'
import type { SyncDirection } from './sync'

// The review before a YNAB import, in one window: a line saying what the plan holds, what it
// changes in this book, then the plan itself in three tabs (accounts, categories, transactions)
// over a pane of fixed height, so neither switching tabs nor the read finishing resizes it. It
// shows ONE preview run at a time; the import it starts turns the sync on itself when it succeeds
// (the server does it, so closing the page mid-import loses nothing).
//
// A YNAB account can go into an account already in the book instead of a new one. Choosing one
// re-checks the preview with that link (the server reuses the plan it just read), so what each
// account would match and add is the server's answer, never a guess made here. The window keeps
// the last answer on screen until the new one arrives.

type SyncChoice = SyncDirection | 'off'
type Pane = 'accounts' | 'categories' | 'transactions'

const SYNC_OPTIONS: { value: SyncChoice; label: string }[] = [
  { value: 'both', label: 'Keep in sync both ways' },
  { value: 'from_ynab', label: 'Sync from YNAB only' },
  { value: 'to_ynab', label: 'Sync to YNAB only' },
  { value: 'off', label: 'Don’t sync, import once' },
]

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`

export function YnabReview({
  runId,
  planLabel,
  onClose,
  onStarted,
}: {
  runId: string
  /** "Name (CUR)" from the picker, so the window is titled before the plan is read. */
  planLabel: string
  onClose: () => void
  onStarted: () => void
}) {
  const [shownId, setShownId] = useState(runId)
  const [checkId, setCheckId] = useState<string | null>(null)
  const run = useRun(shownId).data
  const check = useRun(checkId ?? undefined).data
  const p = run?.status === 'done' ? run.preview : null
  const reading = !run || run.status === 'running'
  const [pane, setPane] = useState<Pane>('accounts')
  const [sync, setSync] = useState<SyncChoice>('both')
  const [links, setLinks] = useState<Record<string, string>>({})
  const [sameAs, setSameAs] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (checkId && check?.status === 'done') {
    setShownId(checkId)
    setCheckId(null)
  } else if (checkId && check && check.status !== 'running') {
    setError(check.error || 'Could not check that account.')
    setCheckId(null)
  }
  const checking = checkId !== null

  async function relink(ynabId: string, accountId: string) {
    if (!run) return
    const next = { ...links }
    if (accountId) next[ynabId] = accountId
    else delete next[ynabId]
    setLinks(next)
    setError(null)
    try {
      const r = await authFetch('/api/ynab/migration/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: run.plan_id, account_links: next, recheck_of: shownId }),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setError(messageOf(body, 'Could not check that account.'))
        return
      }
      setCheckId((body as { id: string }).id)
    } catch {
      setError('Could not reach the server.')
    }
  }

  async function importNow() {
    if (!run || !p || p.currency_blocked || checking) return
    // Only an answer about a pair the shown preview still lists counts.
    const listed = new Set(p.accounts.flatMap((a) => a.match?.pairs ?? []).map((x) => x.ref))
    const same = Object.fromEntries(Object.entries(sameAs).filter(([ref]) => listed.has(ref)))
    setBusy(true)
    setError(null)
    try {
      const r = await authFetch('/api/ynab/migration/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The note above the tabs says the plan goes in beside the book's own accounts;
        // importing after reading it is the confirmation.
        body: JSON.stringify({
          plan_id: run.plan_id,
          confirm_nonempty: p.book_state === 'foreign',
          sync_direction: sync === 'off' ? null : sync,
          ...(Object.keys(links).length > 0 && { account_links: links }),
          ...(Object.keys(same).length > 0 && { same_as: same }),
        }),
      })
      if (!r.ok) {
        setError(messageOf(await r.json().catch(() => null), 'Could not start the import.'))
        return
      }
      onStarted()
      onClose()
    } catch {
      setError('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  const failed = !reading && !p
  return (
    <Modal
      open
      size="xl"
      onClose={onClose}
      title={`Import ${p ? p.plan_name : planLabel}`}
      subtitle={reading ? 'Reading it from YNAB…' : p ? <Meta preview={p} /> : undefined}
      footer={
        <Cluster justify="between" gap="--space-3" className="ynr-footer">
          <PMSelect
            className="ynr-sync"
            ariaLabel="After import"
            value={sync}
            options={SYNC_OPTIONS}
            onChange={(v) => setSync(v as SyncChoice)}
          />
          <Cluster gap="--space-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!p || p.currency_blocked || checking}
              loading={busy}
              onClick={importNow}
            >
              Import
            </Button>
          </Cluster>
        </Cluster>
      }
    >
      <Stack gap="--space-4">
        {failed ? (
          <Text size="sm" tone="bad">
            {run?.error || 'The plan could not be read.'}
          </Text>
        ) : (
          p && <Notes preview={p} />
        )}
        {error && (
          <Text size="sm" tone="bad">
            {error}
          </Text>
        )}
        {!failed && (
          <div>
            <PageTabs
              ariaLabel="What the plan holds"
              value={pane}
              onChange={setPane}
              tabs={[
                { key: 'accounts', label: 'Accounts', badge: p?.accounts.length },
                { key: 'categories', label: 'Categories', badge: p?.categories },
                { key: 'transactions', label: 'Transactions', badge: p?.transactions },
              ]}
            />
            <div
              className="ynr-pane"
              role="tabpanel"
              aria-labelledby={`ui-page-tab-${pane}`}
              aria-busy={reading}
            >
              {!p ? (
                <Placeholder />
              ) : pane === 'accounts' ? (
                <Accounts
                  preview={p}
                  links={links}
                  checking={checking}
                  onLink={(id, to) => void relink(id, to)}
                  sameAs={sameAs}
                  onSameAs={setSameAs}
                />
              ) : pane === 'categories' ? (
                <Categories preview={p} />
              ) : (
                <Transactions preview={p} />
              )}
            </div>
          </div>
        )}
      </Stack>
    </Modal>
  )
}

function Meta({ preview: p }: { preview: Preview }) {
  return (
    <span className="ynr-meta">
      {[
        p.currency,
        plural(p.accounts.length, 'account'),
        plural(p.transactions, 'transaction'),
        plural(p.categories, 'category', 'categories'),
        plural(p.budget_months, 'budget month'),
      ].join(' · ')}
    </span>
  )
}

// What the import does to this book beyond adding the plan — each one sentence, only when true.
function Notes({ preview: p }: { preview: Preview }) {
  const lines: { text: string; bad?: boolean }[] = []
  if (p.currency_blocked)
    lines.push({
      bad: true,
      text: `Can’t import: this book is in ${p.base_currency} and already has data, and the plan is in ${p.currency}. Import into an empty book, or one in ${p.currency}.`,
    })
  if (p.book_state === 'foreign' && !p.currency_blocked)
    lines.push({
      text: 'This book already has accounts of its own. They stay as they are, and each YNAB account is added as a new account beside them; nothing is merged.',
    })
  if (p.book_state === 'rerun')
    lines.push({ text: 'This plan was imported before; importing again adds only what is new.' })
  if (p.currency_change)
    lines.push({
      text: `The book’s base currency changes from ${p.base_currency} to ${p.currency}.`,
    })
  const pruned = [...p.prune.categories, ...p.prune.groups]
  if (pruned.length > 0)
    lines.push({ text: `Unused starter items are removed: ${pruned.join(', ')}.` })
  for (const w of p.warnings) lines.push({ text: w })
  if (lines.length === 0) return null
  return (
    <ul className="ynr-notes">
      {lines.map((l) => (
        <li key={l.text} className={l.bad ? 'ynr-note ynr-note--bad' : 'ynr-note'}>
          {l.text}
        </li>
      ))}
    </ul>
  )
}

function Placeholder() {
  return (
    <div className="ynr-placeholder">
      <Stack gap="--space-4">
        {[92, 78, 85, 64, 88, 70].map((w) => (
          <Skeleton key={w} width={`${w}%`} height={12} />
        ))}
      </Stack>
    </div>
  )
}

function Empty({ children }: { children: string }) {
  return <p className="ynr-empty">{children}</p>
}

interface LinkProps {
  links: Record<string, string>
  checking: boolean
  onLink: (ynabId: string, accountId: string) => void
  sameAs: Record<string, string>
  onSameAs: (next: Record<string, string>) => void
}

function Accounts({ preview: p, ...link }: { preview: Preview } & LinkProps) {
  if (p.accounts.length === 0) return <Empty>This plan has no accounts.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Account</th>
          <th>Type</th>
          <th>In this book</th>
          <th className="imp-table__num">Transactions</th>
          <th className="imp-table__num">Opening balance</th>
        </tr>
      </thead>
      {p.accounts.map((a) => (
        <tbody key={a.id ?? a.name}>
          <tr>
            <td data-label="Account">
              {a.name}
              {a.closed && <span className="ynr-dim"> · closed</span>}
            </td>
            <td data-label="Type" className="ynr-dim">
              {a.type}
              {a.on_budget === false ? ' · tracking' : ''}
            </td>
            <td data-label="In this book">
              <Destination account={a} {...link} />
            </td>
            <td data-label="Transactions" className="imp-table__num">
              {a.transactions}
            </td>
            <td data-label="Opening balance" className="imp-table__num">
              {a.link ? (
                <span className="ynr-dim">Its own</span>
              ) : (
                fAmountNative(a.opening_balance, p.currency)
              )}
            </td>
          </tr>
          {a.match && a.chosen && (
            <tr className="ynr-link">
              <td colSpan={5}>
                <LinkSummary account={a} match={a.match} currency={p.currency} {...link} />
              </td>
            </tr>
          )}
        </tbody>
      ))}
    </table>
  )
}

// A new account, or one already here of a type and currency the plan's account fits.
function Destination({
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

function LinkSummary({
  account: a,
  match: m,
  currency,
  checking,
  sameAs,
  onSameAs,
}: { account: PreviewAccount; match: LinkMatch; currency: string } & LinkProps) {
  if (checking)
    return <span className="ynr-dim">Checking which transactions are already there…</span>
  const here = a.link?.name ?? 'the account'
  const parts = [
    `${plural(m.matched, 'transaction')} already in ${here}, matched`,
    `${m.added.toLocaleString()} added`,
  ]
  if (m.to_check > 0) parts.push(`${m.to_check.toLocaleString()} to check`)
  const money = (n: number) => fAmountNative(n, currency)
  return (
    <Stack gap="--space-2">
      <span>{parts.join(' · ')}.</span>
      {m.own_only > 0 && (
        <span className="ynr-dim">
          {plural(m.own_only, 'transaction')} of {here}’s own{' '}
          {m.own_only === 1 ? 'isn’t' : 'aren’t'} in YNAB; a sync to YNAB sends{' '}
          {m.own_only === 1 ? 'it' : 'them'} there.
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
                  description={`YNAB: ${x.payee ?? '(no payee)'}, ${fDate(x.date)}, ${money(x.amount)}`}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Stack>
  )
}

function Categories({ preview: p }: { preview: Preview }) {
  const groups = p.category_groups
  if (!groups) return <Empty>Review again to see the categories by name.</Empty>
  if (groups.length === 0) return <Empty>This plan has no categories of its own.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Category</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.name}>
          <tr className="ynr-group">
            <th scope="rowgroup">
              {g.name}
              <span className="ynr-dim"> · {g.categories.length}</span>
            </th>
          </tr>
          {g.categories.map((c) => (
            <tr key={c}>
              <td data-label="Category">{c}</td>
            </tr>
          ))}
        </tbody>
      ))}
    </table>
  )
}

// A blank category on an inflow is money to assign, as in YNAB; on a spend it is uncategorized.
const NO_CATEGORY: Record<PreviewTxn['kind'], string> = {
  income: 'Ready to Assign',
  expense: 'Uncategorized',
  transfer: '',
}

function Transactions({ preview: p }: { preview: Preview }) {
  const rows = p.transaction_list
  if (!rows) return <Empty>Review again to see the transactions.</Empty>
  if (rows.length === 0) return <Empty>This plan has no transactions.</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Account</th>
          <th>Payee</th>
          <th>Category</th>
          <th className="imp-table__num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((t, i) => (
          <tr key={i}>
            <td data-label="Date" className="ynr-dim ynr-nowrap">
              {fDate(t.date)}
            </td>
            <td data-label="Account">
              {t.kind === 'transfer' ? `${t.account ?? '?'} → ${t.to_account ?? '?'}` : t.account}
            </td>
            <td data-label="Payee" className="imp-table__wrap">
              {t.payee ?? ''}
              {t.memo && <span className="ynr-dim"> · {t.memo}</span>}
            </td>
            <td data-label="Category" className="ynr-dim">
              {t.category ?? NO_CATEGORY[t.kind]}
            </td>
            <td data-label="Amount" className="imp-table__num ynr-nowrap">
              {fAmountNative(t.amount, p.currency)}
            </td>
          </tr>
        ))}
      </tbody>
      {rows.length < p.transactions && (
        <tfoot>
          <tr>
            <td colSpan={5} className="ynr-dim">
              The newest {rows.length} of {p.transactions.toLocaleString()}. The import brings them
              all.
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}
