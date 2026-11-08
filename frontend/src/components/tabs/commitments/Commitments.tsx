// Commitments tab — faithful port of frontend/design_handoffs/commitments_handoff (v3
// prototype). See docs/adr/2026-07-22-commitments-tab-redesign-faithful-port.md for the port
// strategy: the coverage/proposal engine is client-side pure math over the REAL backend's
// coverage() response (never a second projection engine); every Lucius touchpoint opens the
// app's REAL dock via onAskLucius (never a scripted local chat panel).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { Commitment } from '../../../types/commitments'
import { authFetch } from '../../../api/client'
import { useCommitments } from '../../../hooks/useCommitments'
import { useCommitmentsSummary } from '../../../hooks/serverState'
import {
  KIND_LABELS,
  daysUntil,
  fDual,
  fmtDate,
  isTracker,
  nextInstallment,
  remainingBase,
} from '../../../utils/commitments'
import { fBase } from '../../../utils/format'
import { useRegisterScreenFacts } from '../../../lucius/useScreenContract'
import {
  Button,
  Cluster,
  Container,
  EmptyState,
  Modal,
  PageHeader,
  Skeleton,
  TabSkeleton,
} from '../../ui'
import { CalendarClock } from '../../../ui/icons'
import { useCommitmentsCoverage } from './useCommitmentsCoverage'
import { deriveClientCoverage, buildProposals } from './coverageEngine'
import { Masthead } from './masthead'
import type { HorizonOption } from './masthead'
import { NeedsLane } from './needsLane'
import type { NeedItem } from './needsLane'
import { CommitmentRows } from './rows'
import type { CommitmentFilter, CommitmentSort } from './rows'
import { CommitmentDetail } from './detail'
import { CarIcon, CoinsIcon, DocIcon, PlusIcon, Spark, TargetIcon } from './icons'
import './commitments.css'

interface Props {
  fx: Record<string, number>
  onAskLucius: (text: string) => void
  selectedId?: string | null
  onSelect?: (id: string | null) => void
}

const COMMITMENTS_SUBTITLE = "Big payments you've committed to — and how you'll fund them"
const NEEDS_CAP = 3

/** Every write below goes through this — a non-ok response (404/422) throws instead of the
 *  caller silently treating it as a success (a real, if rare, regression a review pass caught:
 *  see docs/adr/2026-07-22-commitments-tab-redesign-faithful-port.md). Callers still call
 *  `onChanged()` unconditionally on success, and show a failure toast on rejection. */
async function writeJson(url: string, method: 'PATCH' | 'PUT', body: unknown): Promise<void> {
  const res = await authFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Commitments write failed (${res.status})`)
}

const KIND_OPTS: Array<{
  kind: Commitment['kind']
  Icon: () => ReactElement
  title: string
  desc: string
}> = [
  {
    kind: 'purchase',
    Icon: CoinsIcon,
    title: 'Purchase',
    desc: 'Property, a car, a big-ticket buy in installments',
  },
  {
    kind: 'loan_repayment',
    Icon: CarIcon,
    title: 'Loan repayment',
    desc: 'Pay down financing on a fixed schedule',
  },
  {
    kind: 'auto_pay',
    Icon: DocIcon,
    title: 'Scheduled payments',
    desc: 'A fixed series of dated payments — school year fees, insurance installments',
  },
  {
    kind: 'goal',
    Icon: TargetIcon,
    title: 'Savings & investing goal',
    desc: 'Build toward a target — a savings fund or a monthly DCA plan',
  },
]

const KIND_PROMPT: Record<Commitment['kind'], string> = {
  purchase:
    "Help me set up a new purchase commitment — I want to plan a big-ticket buy paid in installments, and how I'll fund it.",
  loan_repayment:
    "Help me set up a new loan repayment commitment — I want to model a loan's payback schedule.",
  auto_pay:
    'Help me set up a new scheduled-payments commitment — a fixed series of dated payments (school fees, insurance).',
  goal: 'Help me set up a new savings or investing goal — a target amount with regular contributions.',
}

function AddSheet({
  onClose,
  onAdd,
}: {
  onClose: () => void
  onAdd: (kind: Commitment['kind']) => void
}) {
  return (
    <Modal
      open
      onClose={onClose}
      title="New commitment"
      subtitle="What are you planning to fund? Pick a type and Lucius will draft the schedule and funding plan with you."
      size="lg"
      className="c2-sheet"
      footer={
        <div className="c2-sheet-foot">
          <span className="c2-sheet-lucius">
            <Spark size={13} /> Lucius drafts it — you confirm every number.
          </span>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
        </div>
      }
    >
      <div className="c2-sheet-body">
        <div className="c2-kindpick">
          {KIND_OPTS.map(({ kind, Icon, title, desc }) => (
            <button key={kind} className="c2-kindopt" onClick={() => onAdd(kind)}>
              <Icon />
              <div>
                <div className="c2-kindopt-t">{title}</div>
                <div className="c2-kindopt-d">{desc}</div>
              </div>
            </button>
          ))}
        </div>
        <div className="c2-sheet-note">
          Ongoing bills with no end date — rent, memberships, utilities — belong in Budget →
          recurring targets.
        </div>
      </div>
    </Modal>
  )
}

function EmptyStateHero({ onAdd }: { onAdd: () => void }) {
  return (
    <EmptyState
      fill
      icon={<CalendarClock size={28} strokeWidth={1.5} aria-hidden="true" />}
      title="Plan the big payments ahead"
      body="Property installments, a car, school fees, a loan — or a monthly investing plan. See whether every payment is funded."
      action={
        <Cluster gap="--space-3" justify="center">
          <Button
            variant="primary"
            size="sm"
            className="fi-lucius"
            iconLeft={<Spark size={15} />}
            onClick={onAdd}
          >
            Draft a commitment with Lucius
          </Button>
        </Cluster>
      }
    />
  )
}

function FundingOutlookSkeleton() {
  return (
    <section className="c2-sum c2-sum-loading c2-sum-skeleton" aria-label="Loading funding outlook">
      <div className="c2-sum-skeleton__hero">
        <Skeleton width="42%" height={13} />
        <Skeleton width="52%" height={34} />
        <Skeleton width="74%" height={12} />
      </div>
      <div className="c2-sum-skeleton__stats">
        {[0, 1, 2, 3].map((index) => (
          <div key={index}>
            <Skeleton width="56%" height={11} />
            <Skeleton width="78%" height={21} />
          </div>
        ))}
      </div>
    </section>
  )
}

export function Commitments({ fx, onAskLucius, selectedId, onSelect }: Props) {
  const { commitments, loading, refresh } = useCommitments()
  // What is overdue and what is due in the next 30 days are the server's figures — the same
  // object the Dashboard's Next-30-days card and the daily briefing read — never a filter of
  // this screen's own.
  const { data: dueSummary, refresh: refreshDueSummary } = useCommitmentsSummary()
  const [localSelected, setLocalSelected] = useState<string | null>(null)
  const urlControlled = onSelect !== undefined
  const selected = urlControlled ? (selectedId ?? null) : localSelected
  const setSelected = (id: string | null) => (urlControlled ? onSelect!(id) : setLocalSelected(id))

  // The app shell's own `.topbar` ("As of … · Prices" stamp) is ALSO `position: sticky; top: 0`
  // — this page's own sticky header needs to stick BELOW it, not at the same top:0, which just
  // pins ours invisibly behind the topbar (reads as "not sticky" at all). Same fix as
  // Overview/Plan (see Overview.tsx's identical comment) — measured via ResizeObserver, not
  // hardcoded, since the topbar wraps to a second row at narrow widths.
  //
  // Depends on `loading`: this component's FIRST render (while `loading` is still true) returns
  // a bare `<Loading />` with no `.c2-page` at all, so a `[]`-dep effect queries for the header
  // before it exists, finds nothing, and — having already run its one and only time — never
  // looks again once the real header mounts. Re-running when `loading` flips to false catches
  // it at the first render that actually has one.
  useEffect(() => {
    const topbar = document.querySelector<HTMLElement>('.topbar')
    const header = document.querySelector<HTMLElement>('.c2-page > .ui-page-header')
    if (!topbar || !header) return
    const root = document.documentElement
    const measure = () => {
      // 2026-08-24: .topbar is no longer sticky INSIDE this scroller — it is a SIBLING of it
      // (.app-main > .topbar + .app-layout), so it cannot overlap the scrolled content and
      // the offset is 0. Measuring its height here pinned this block a full bar-height down
      // the page, stranded mid-content with text scrolling above AND below it.
      root.style.setProperty('--c2-topbar-h', '0px')
    }
    const ro = new ResizeObserver(measure)
    measure()
    ro.observe(topbar)
    return () => {
      ro.disconnect()
      root.style.removeProperty('--c2-topbar-h')
    }
  }, [loading])

  // This card is the LAST thing on the page. Switching to a filter with fewer rows shrinks
  // total page height — if that drops scrollHeight below the current scrollTop, the browser
  // clamps the scroll position, and since reaching the filter row required scrolling down to
  // it, the clamp yanks the viewport back up. Trailing spacer sized to `max height this card
  // has ever rendered at − its CURRENT height`, so the page's total height never dips below
  // whatever it was at the tallest filter — zero-height (invisible) whenever the card IS at
  // its own historical max, unlike a min-height on the card itself, which stays visibly empty
  // inside the card's own bordered box for every OTHER filter.
  const [cardHeight, setCardHeight] = useState(0)
  const [maxCardHeight, setMaxCardHeight] = useState(0)
  const onCardHeightChange = useCallback((px: number) => {
    setCardHeight(px)
    setMaxCardHeight((m) => Math.max(m, px))
  }, [])

  const [horizon, setHorizon] = useState(12)
  const [filter, setFilter] = useState<CommitmentFilter>('all')
  // iOS Safari can still visibly snap the scroll pane on a filter tap even with the height
  // spacer above: a finger-thrown pane still has momentum decelerating when the tap lands, and
  // WebKit reconciles that inertia against the mid-flight layout change with a visible jump
  // instead of a clean clamp. Pinning scrollTop back explicitly (which also kills the leftover
  // momentum) is the only reliable fix for that case.
  const pendingScrollRef = useRef<number | null>(null)
  const pinFilter = useCallback((f: CommitmentFilter) => {
    pendingScrollRef.current = document.querySelector<HTMLElement>('.app-layout')?.scrollTop ?? null
    setFilter(f)
  }, [])
  useLayoutEffect(() => {
    if (pendingScrollRef.current == null) return
    const el = document.querySelector<HTMLElement>('.app-layout')
    if (el) el.scrollTop = pendingScrollRef.current
    pendingScrollRef.current = null
  }, [filter])
  const [sort, setSort] = useState<CommitmentSort>('next')
  const [addOpen, setAddOpen] = useState(false)
  const [dismissedUnmarked, setDismissedUnmarked] = useState<string[]>([])
  const [doneActionKeys, setDoneActionKeys] = useState<string[]>([])
  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showToast = (msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 2600)
  }

  const { coverage, unmarked, refresh: refreshCoverage } = useCommitmentsCoverage(horizon)

  const maxHorizon = useMemo(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    let last = new Date(today)
    for (const c of commitments) {
      if (c.status !== 'active') continue
      for (const i of c.installments) {
        if (i.status === 'paid') continue
        const dt = new Date(i.due_date)
        if (dt > last) last = dt
      }
    }
    const mo =
      (last.getFullYear() - today.getFullYear()) * 12 + (last.getMonth() - today.getMonth()) + 1
    return Math.max(6, mo)
  }, [commitments])
  const hzOptions: HorizonOption[] = useMemo(() => {
    const opts = [6, 12, 24, 36]
      .filter((n) => n < maxHorizon)
      .map((n) => ({ label: `${n}m`, value: n }))
    opts.push({ label: 'All', value: maxHorizon })
    return opts
  }, [maxHorizon])

  const onChanged = () => {
    refresh()
    refreshCoverage()
    void refreshDueSummary()
  }

  async function askLuciusFix() {
    // Prefer the top COMPUTED proposal's specific prompt (names the exact fix, the amount, the
    // date) over a generic ask — proposals is declared later in this same function body via
    // useMemo, but this closure only runs on a later click, by which point it's populated.
    if (proposals[0]) {
      onAskLucius(proposals[0].luciusPrompt)
      return
    }
    onAskLucius(
      "I'm short on cash coverage for my commitments — help me look at the funding gap and suggest a fix.",
    )
  }

  const derived = useMemo(() => (coverage ? deriveClientCoverage(coverage) : null), [coverage])
  const proposals = useMemo(() => {
    if (!derived || !coverage) return []
    return buildProposals({
      list: commitments,
      cov: derived,
      fx,
      patchFunding: async (cid, fid, changes) => {
        await writeJson(`/api/commitments/${cid}/funding/${fid}`, 'PATCH', { changes })
        onChanged()
      },
      patchInstallment: async (cid, iid, changes) => {
        await writeJson(`/api/commitments/${cid}/installments/${iid}`, 'PATCH', { changes })
        onChanged()
      },
      addFunding: async (cid, source) => {
        const c = commitments.find((x) => x.id === cid)
        if (!c) return
        await writeJson(`/api/commitments/${cid}/funding`, 'PUT', {
          funding_plan: [...c.funding_plan, source],
        })
        onChanged()
      },
      addAction: async (cid, action) => {
        const c = commitments.find((x) => x.id === cid)
        if (!c) return
        await writeJson(`/api/commitments/${cid}/actions`, 'PUT', {
          actions: [...(c.actions || []), action],
        })
        onChanged()
      },
    })
    // onChanged/refresh/refreshCoverage are stable across renders; only re-derive proposals
    // when the underlying data actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [derived, coverage, commitments, fx])

  // ONE registration for both the list and the detail screen. `setScreenFacts` is a single
  // global slot (useScreenContract.ts) — a second useRegisterScreenFacts inside the detail
  // would race the list's on mount and null it out on unmount, so the detail branches HERE
  // instead. Without this, opening a commitment left Lucius reading list-level facts with no
  // indication of which commitment was on screen.
  useRegisterScreenFacts(() => {
    const open = selected ? commitments.find((c) => c.id === selected) : undefined
    if (open) {
      const facts: [string, string][] = [
        ['Viewing commitment', open.name || '(unnamed)'],
        ['Kind', KIND_LABELS[open.kind] ?? open.kind],
        ['Status', open.status],
        ['Remaining', fBase(remainingBase(open, fx))],
      ]
      const ni = nextInstallment(open)
      if (ni) facts.push(['Next installment', `${fBase(ni.amount)} due ${fmtDate(ni.due_date)}`])
      return facts
    }
    if (!coverage || !derived) return []
    const active = commitments.filter((c) => c.status === 'active')
    const facts: [string, string][] = [['Active commitments', String(active.length)]]
    if (derived.minBal < derived.floor) {
      facts.push(['Household cash outlook', `Short ${fBase(derived.floor - derived.minBal)}`])
    } else {
      facts.push(['Household cash outlook', 'Above floor'])
    }
    return facts
  })

  if (loading) {
    return (
      <Container size="wide">
        <div className="c2-page">
          <TabSkeleton variant="commitments" label="Loading commitments…" />
        </div>
      </Container>
    )
  }

  const selectedCommitment = selected ? commitments.find((c) => c.id === selected) : undefined
  if (selectedCommitment) {
    return (
      <CommitmentDetail
        commitment={selectedCommitment}
        fx={fx}
        onBack={() => setSelected(null)}
        onAskLucius={onAskLucius}
        onChanged={onChanged}
        onOpenCommitment={setSelected}
      />
    )
  }

  if (!commitments.length) {
    return (
      <Container size="wide">
        <div className="c2-page" data-screen-label="Commitments">
          <PageHeader title="Commitments" subtitle={COMMITMENTS_SUBTITLE} />
          <EmptyStateHero onAdd={() => setAddOpen(true)} />
        </div>
        {addOpen && (
          <AddSheet
            onClose={() => setAddOpen(false)}
            onAdd={(kind) => {
              setAddOpen(false)
              onAskLucius(KIND_PROMPT[kind])
            }}
          />
        )}
      </Container>
    )
  }

  // ── "Needs your attention" — overdue installments, "looks paid" matches, due-soon actions ──
  const needs: NeedItem[] = []
  for (const e of dueSummary?.overdue ?? []) {
    if (e.kind !== 'installment' || !e.installment_id) continue
    const c = commitments.find((x) => x.id === e.commitment_id)
    const i = c?.installments.find((x) => x.id === e.installment_id)
    if (!c || !i) continue
    if (unmarked.some((s) => s.commitment_id === c.id && s.installment_id === i.id)) continue
    const d = daysUntil(i.due_date)
    needs.push({
      key: `od-${c.id}-${i.id}`,
      kind: 'overdue',
      tone: 'overdue',
      cid: c.id,
      iid: i.id,
      amt: i.amount,
      payLabel: isTracker(c) ? 'Mark done' : 'Mark paid',
      title: (
        <>
          <b>{c.name}</b> — {i.label}
        </>
      ),
      desc: `Overdue ${-d} day${d === -1 ? '' : 's'} • ${fDual(i.amount, c.currency, fx)} was due ${fmtDate(i.due_date)}`,
    })
  }
  for (const s of unmarked) {
    if (dismissedUnmarked.includes(s.txn_id)) continue
    if (!commitments.some((c) => c.id === s.commitment_id)) continue
    needs.push({
      key: `u-${s.txn_id}`,
      kind: 'confirm',
      tone: 'pay',
      cid: s.commitment_id,
      sug: s,
      title: (
        <>
          <b>{s.commitment_name}</b> — {s.installment_label}
        </>
      ),
      desc: `Looks paid • a ${fDual(s.amount_base, '', fx)} payment on ${fmtDate(s.txn_date)} matches this installment`,
    })
  }
  for (const c of commitments) {
    if (c.status !== 'active') continue
    for (const a of c.actions || []) {
      if (a.status !== 'pending') continue
      const key = `a-${c.id}-${a.id}`
      if (doneActionKeys.includes(key)) continue
      const d = daysUntil(a.due_date)
      if (d > 21) continue
      needs.push({
        key,
        kind: 'action',
        tone: d < 0 ? 'overdue' : 'task',
        cid: c.id,
        aid: a.id,
        fundingId: a.funding_id,
        title: (
          <>
            <b>{c.name}</b> — {a.label}
          </>
        ),
        desc: `${d < 0 ? `Overdue ${-d} day${d === -1 ? '' : 's'}` : d === 0 ? 'Due today' : `Due in ${d} day${d === 1 ? '' : 's'}`} • ${a.counterparty || a.category}${a.funding_id ? ' • unlocks funding' : ''}`,
      })
    }
  }

  async function confirmNeed(it: NeedItem) {
    if (!it.sug) return
    try {
      await writeJson(
        `/api/commitments/${it.sug.commitment_id}/installments/${it.sug.installment_id}`,
        'PATCH',
        {
          changes: { status: 'paid', paid_txn_id: it.sug.txn_id },
        },
      )
      showToast('Linked and marked paid')
    } catch {
      showToast('Could not link the payment — try again')
    }
    onChanged()
  }
  function dismissNeed(it: NeedItem) {
    if (!it.sug) return
    setDismissedUnmarked((d) => [...d, it.sug!.txn_id])
  }
  async function payNeed(it: NeedItem) {
    if (!it.cid || !it.iid) return
    try {
      await writeJson(`/api/commitments/${it.cid}/installments/${it.iid}`, 'PATCH', {
        changes: { status: 'paid' },
      })
      showToast('Installment marked paid')
    } catch {
      showToast('Could not mark it paid — try again')
    }
    onChanged()
  }
  async function actionNeed(it: NeedItem) {
    if (!it.cid || !it.aid) return
    try {
      await writeJson(`/api/commitments/${it.cid}/actions/${it.aid}`, 'PATCH', {
        changes: { status: 'done' },
      })
      setDoneActionKeys((d) => [...d, it.key])
      showToast('Action marked done')
    } catch {
      showToast('Could not mark it done — try again')
    }
    onChanged()
  }
  async function missNeed(it: NeedItem) {
    if (!it.cid || !it.aid) return
    try {
      await writeJson(`/api/commitments/${it.cid}/actions/${it.aid}`, 'PATCH', {
        changes: { status: 'missed' },
      })
      if (it.fundingId) {
        await writeJson(`/api/commitments/${it.cid}/funding/${it.fundingId}`, 'PATCH', {
          changes: { disabled: true },
        })
      }
      showToast(
        it.fundingId
          ? 'Marked not happening — funding pulled, planning a fix'
          : 'Marked not happening',
      )
      onAskLucius(
        `I just marked "${typeof it.title === 'string' ? it.title : 'an action'}" as not happening on one of my commitments${it.fundingId ? ' — it was funding an installment' : ''}. Help me look at the impact and suggest a recovery plan.`,
      )
    } catch {
      showToast('Could not mark it not happening — try again')
    }
    onChanged()
  }

  return (
    <Container size="wide">
      <div className="c2-page" data-screen-label="Commitments">
        <PageHeader
          title="Commitments"
          subtitle={COMMITMENTS_SUBTITLE}
          actions={
            <Button
              variant="primary"
              size="sm"
              iconLeft={<PlusIcon />}
              onClick={() => setAddOpen(true)}
              data-tour="setup-commitments"
            >
              {/* "New commitment" → "New" on phones (.c2-new-rest is hidden ≤640px) so the
                  action fits on the title's own row instead of claiming a third one. The full
                  label stays in the DOM, so the accessible name is unchanged at every width. */}
              <span>
                New<span className="c2-new-rest">&nbsp;commitment</span>
              </span>
            </Button>
          }
        />

        {coverage && derived ? (
          <Masthead
            list={commitments}
            fx={fx}
            cov={coverage}
            dueSoon={dueSummary?.strip_total_base ?? 0}
            derived={derived}
            horizon={horizon}
            onHorizon={setHorizon}
            hzOptions={hzOptions}
            topProposal={proposals[0] || null}
            onReview={setSelected}
            onFix={askLuciusFix}
          />
        ) : (
          <FundingOutlookSkeleton />
        )}

        {needs.length > 0 && (
          <NeedsLane
            items={needs}
            overdue={
              dueSummary && dueSummary.overdue_count > 0
                ? { count: dueSummary.overdue_count, total: dueSummary.overdue_total_base }
                : null
            }
            cap={NEEDS_CAP}
            onConfirm={confirmNeed}
            onDismiss={dismissNeed}
            onAction={actionNeed}
            onMiss={missNeed}
            onPay={payNeed}
            onOpenC={setSelected}
          />
        )}

        <CommitmentRows
          list={commitments}
          fx={fx}
          onOpen={setSelected}
          filter={filter}
          setFilter={pinFilter}
          sort={sort}
          setSort={setSort}
          onHeightChange={onCardHeightChange}
        />
        {maxCardHeight > cardHeight && (
          <div
            className="c2-scroll-spacer"
            style={{ height: maxCardHeight - cardHeight }}
            aria-hidden="true"
          />
        )}
      </div>

      {addOpen && (
        <AddSheet
          onClose={() => setAddOpen(false)}
          onAdd={(kind) => {
            setAddOpen(false)
            onAskLucius(KIND_PROMPT[kind])
          }}
        />
      )}
      {toast && <div className="c2-toast mb-anchored">{toast}</div>}
    </Container>
  )
}
