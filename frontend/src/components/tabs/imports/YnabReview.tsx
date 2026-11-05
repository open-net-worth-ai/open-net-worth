import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { fAmountNative, fDate } from '../../../utils/format'
import { Button, Cluster, Modal, PageTabs, Skeleton, Stack, Text } from '../../ui'
import { TYPE_LABELS, type AccountType } from '../accounts/categories'
import { PMSelect } from '../accounts/ds'
import { YNAB_APP, type BudgetAppInfo } from '../automations/datasync/budgetApps'
import { Destination, LinkSummary, type LinkProps } from './ReviewLinks'
import { CurrencyRow, SettleAccounts } from './ReviewSettle'
import { useAccountTypes, useImportBlock } from './reviewTypes'
import { BLOCKED_NOTE_ID, Notes, SettleNotes, TYPES_NOTE_ID } from './reviewNotes'
import { ASK, Categories } from './YnabCategories'
import { messageOf, plural, useRun, type Preview, type PreviewTxn } from './runs'
import type { SyncDirection } from './sync'

// The review before a budget app's import, in one window: a line saying what the plan holds, what it
// changes in this book, then the plan itself in three tabs (accounts, categories, transactions)
// over a pane of fixed height, so neither switching tabs nor the read finishing resizes it. It
// shows ONE preview run at a time; the import it starts turns the sync on itself when it succeeds
// (the server does it, so closing the page mid-import loses nothing).
//
// A YNAB account can go into an account already in the book instead of a new one. Choosing one
// re-checks the preview with that link (the server reuses the plan it just read), so what each
// account would match and add is the server's answer, never a guess made here. The window keeps
// the last answer on screen until the new one arrives.
//
// A YNAB category goes with one of the book's categories (pre-selected when one reads the same),
// into one of its envelopes, or becomes a new category. The choice and the line under it (what
// the category's budget will do) are the server's answer for the choice made, so a change is
// re-checked like an account's. A category that matches several of the book's must be picked
// before the import can start.

type SyncChoice = SyncDirection | 'off'
type Pane = 'accounts' | 'categories' | 'transactions'

const TYPES_SETTLE_MS = 400

const syncOptions = (app: BudgetAppInfo): { value: SyncChoice; label: string }[] => [
  { value: 'both', label: 'Keep in sync both ways' },
  { value: 'from_ynab', label: `Sync from ${app.name} only` },
  { value: 'to_ynab', label: `Sync to ${app.name} only` },
  { value: 'off', label: 'Don’t sync, import once' },
]

export function YnabReview({
  app = YNAB_APP,
  runId,
  planLabel,
  blockedAtStart = null,
  onClose,
  onStarted,
}: {
  app?: BudgetAppInfo
  runId: string
  /** "Name (CUR)" from the picker, so the window is titled before the plan is read. */
  planLabel: string
  /** What the preview's own answer said an import would be refused with. */
  blockedAtStart?: string | null
  onClose: () => void
  onStarted: () => void
}) {
  const [shownId, setShownId] = useState(runId)
  const [checkId, setCheckId] = useState<string | null>(null)
  const run = useRun(shownId).data
  const check = useRun(checkId ?? undefined).data
  const first = useRun(runId).data?.preview
  const p = run?.status === 'done' ? run.preview : null
  const reading = !run || run.status === 'running'
  const [pane, setPane] = useState<Pane>('accounts')
  const [sync, setSync] = useState<SyncChoice>('both')
  const [links, setLinks] = useState<Record<string, string>>({})
  const [sameAs, setSameAs] = useState<Record<string, string>>({})
  const [catLinks, setCatLinks] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currency, setCurrency] = useState<string | null>(null)
  const [posting, setPosting] = useState(false)
  const [checkedTypes, setCheckedTypes] = useState<string | null>(null)
  const types = useAccountTypes(first, p)
  const block = useImportBlock(app, run?.plan_id, blockedAtStart)
  const asks = app.asksTypeAndCurrency
  const askedCurrency = asks && p?.currency_asked ? (currency ?? p.currency) : null
  const settling = asks ? types.unsettled : 0

  if (checkId && check?.status === 'done') {
    setShownId(checkId)
    setCheckId(null)
  } else if (checkId && check && check.status !== 'running') {
    setError(check.error || 'Could not check that choice.')
    setCheckId(null)
  }
  const checking = checkId !== null
  // What the first answer proposed for each category: the choice the person did not make.
  const proposals = Object.fromEntries(
    (first?.category_groups ?? [])
      .flatMap((g) => g.categories)
      .map((c) => [c.id, c.ask ? ASK : c.link && c.link.kind !== 'linked' ? c.link.ref : '']),
  )
  const asking = (p?.category_groups ?? []).flatMap((g) => g.categories).filter((c) => c.ask)

  async function recheck(
    accounts: Record<string, string>,
    categories: Record<string, string>,
    asked = askedCurrency,
  ) {
    if (!run) return
    setError(null)
    setPosting(true)
    try {
      const r = await authFetch(`${app.apiPrefix}/migration/preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          plan_id: run.plan_id,
          account_links: accounts,
          ...(Object.keys(categories).length > 0 && { category_links: categories }),
          recheck_of: shownId,
          ...(asks && { account_types: types.forRequest(accounts) }),
          ...(asked && { currency: asked }),
        }),
      })
      const body = await r.json().catch(() => null)
      if (!r.ok) {
        setError(messageOf(body, 'Could not check that choice.'))
        return
      }
      setCheckId((body as { id: string }).id)
    } catch {
      setError('Could not reach the server.')
    } finally {
      setPosting(false)
    }
  }

  // A type changes what the shown preview says only through a re-check, which must not freeze a
  // long column of dropdowns. So a choice lands at once, and once the changes pause and no re-check
  // is running, the newest one the preview doesn't reflect goes out: a burst, or changes made during
  // a re-check, become one. The key keeps a choice the server refused from going out again.
  const typesAhead = asks && types.ahead !== checkedTypes ? types.ahead : null
  useEffect(() => {
    if (!typesAhead || checking || posting) return
    const timer = setTimeout(() => {
      setCheckedTypes(typesAhead)
      void recheck(links, catLinks)
    }, TYPES_SETTLE_MS)
    return () => clearTimeout(timer)
    // Only a new state of the choices fires this; the closure is that render's own.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typesAhead, checking, posting])

  function relink(ynabId: string, accountId: string) {
    const next = { ...links }
    if (accountId) next[ynabId] = accountId
    else delete next[ynabId]
    setLinks(next)
    void recheck(next, catLinks)
  }

  function recategorize(ynabId: string, ref: string) {
    const next = { ...catLinks }
    if (ref === proposals[ynabId]) delete next[ynabId]
    else next[ynabId] = ref
    setCatLinks(next)
    void recheck(links, next)
  }

  const cannotImport =
    !p ||
    p.currency_blocked ||
    checking ||
    asking.length > 0 ||
    (asks && (posting || !!typesAhead || settling > 0 || !!block.blocked))

  async function importNow() {
    if (!run || !p || cannotImport) return
    // Only an answer about a pair the shown preview still lists counts.
    const listed = new Set(p.accounts.flatMap((a) => a.match?.pairs ?? []).map((x) => x.ref))
    const same = Object.fromEntries(Object.entries(sameAs).filter(([ref]) => listed.has(ref)))
    setBusy(true)
    setError(null)
    try {
      const r = await authFetch(`${app.apiPrefix}/migration/start`, {
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
          ...(Object.keys(catLinks).length > 0 && { category_links: catLinks }),
          ...(asks && { account_types: types.forRequest(links) }),
          ...(askedCurrency && { currency: askedCurrency }),
        }),
      })
      if (!r.ok) {
        setError(messageOf(await r.json().catch(() => null), 'Could not start the import.'))
        block.refresh()
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
      subtitle={reading ? `Reading it from ${app.name}…` : p ? <Meta preview={p} /> : undefined}
      footer={
        <Cluster justify="between" gap="--space-3" className="ynr-footer">
          <PMSelect
            className="ynr-sync"
            ariaLabel="After import"
            value={sync}
            options={syncOptions(app)}
            onChange={(v) => setSync(v as SyncChoice)}
          />
          <Cluster gap="--space-2">
            <Button size="sm" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={cannotImport}
              loading={busy}
              onClick={importNow}
              {...importBlockedProps(block.blocked, settling)}
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
            {run?.error || `The ${app.planWord} could not be read.`}
          </Text>
        ) : p && asks ? (
          <>
            {askedCurrency && (
              <CurrencyRow
                app={app}
                value={askedCurrency}
                disabled={checking || posting}
                onChange={(code) => {
                  setCurrency(code)
                  if (code !== p.currency) void recheck(links, catLinks, code)
                }}
              />
            )}
            <SettleNotes
              app={app}
              preview={p}
              asking={asking}
              blocked={block.blocked}
              unsettled={types.unsettled}
              suggested={types.acceptable}
              onAccept={() => {
                // The button is about to go, and focus with it: back to the top of the window.
                document.getElementById('ui-page-tab-accounts')?.focus()
                types.accept()
              }}
            />
          </>
        ) : (
          p && <Notes app={app} preview={p} asking={asking} />
        )}
        {error && (
          <div role="alert">
            <Text size="sm" tone="bad">
              {error}
            </Text>
          </div>
        )}
        {!failed && (
          <div>
            <PageTabs
              ariaLabel={`What the ${app.planWord} holds`}
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
              ) : pane === 'accounts' && asks ? (
                <SettleAccounts
                  app={app}
                  preview={p}
                  types={types}
                  links={links}
                  checking={checking}
                  onLink={relink}
                  sameAs={sameAs}
                  onSameAs={setSameAs}
                />
              ) : pane === 'accounts' ? (
                <Accounts
                  app={app}
                  preview={p}
                  links={links}
                  checking={checking}
                  onLink={relink}
                  sameAs={sameAs}
                  onSameAs={setSameAs}
                />
              ) : pane === 'categories' ? (
                <Categories
                  app={app}
                  preview={p}
                  catLinks={catLinks}
                  proposals={proposals}
                  checking={checking}
                  onChoose={recategorize}
                />
              ) : (
                <Transactions app={app} preview={p} />
              )}
            </div>
          </div>
        )}
      </Stack>
    </Modal>
  )
}

/** Why Import is off, for the button: a reason said once, in the notes above it. */
function importBlockedProps(blocked: string | null, settling: number) {
  if (!blocked && settling === 0) return {}
  return {
    title: settling > 0 ? 'Check the account types first' : undefined,
    'aria-describedby': [blocked && BLOCKED_NOTE_ID, settling > 0 && TYPES_NOTE_ID]
      .filter(Boolean)
      .join(' '),
  }
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
      ].join(', ')}
    </span>
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

function Accounts({ preview: p, ...link }: { preview: Preview } & LinkProps) {
  if (p.accounts.length === 0) return <Empty>{`This ${link.app.planWord} has no accounts.`}</Empty>
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
              {a.closed && <span className="ynr-dim"> (closed)</span>}
            </td>
            <td data-label="Type" className="ynr-dim">
              {TYPE_LABELS[a.type as AccountType] ?? a.type}
              {a.on_budget === false ? ' (tracking)' : ''}
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

// A blank category on an inflow is money to assign, as in YNAB; on a spend it is uncategorized.
const NO_CATEGORY: Record<PreviewTxn['kind'], string> = {
  income: 'Ready to Assign',
  expense: 'Uncategorized',
  transfer: '',
}

function Transactions({ app, preview: p }: { app: BudgetAppInfo; preview: Preview }) {
  const rows = p.transaction_list
  if (!rows) return <Empty>Review again to see the transactions.</Empty>
  if (rows.length === 0) return <Empty>{`This ${app.planWord} has no transactions.`}</Empty>
  return (
    <table className="imp-table ynr-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Account</th>
          <th>Payee</th>
          <th>Memo</th>
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
            </td>
            <td data-label="Memo" className="imp-table__wrap ynr-dim">
              {t.memo ?? ''}
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
            <td colSpan={6} className="ynr-dim">
              The newest {rows.length} of {p.transactions.toLocaleString()}. The import brings them
              all.
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  )
}
