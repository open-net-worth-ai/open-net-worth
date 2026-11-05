// Commitment detail screen — the full port of detail3.jsx: header (inline name/note edit,
// tags, risk chip, Mark completed / kebab menu with cancel+delete confirm strips), stat strip,
// insight ribbon (at-risk fix / loan early-payoff / autopilot / fully-funded), the payments-on-
// the-timeline strip, and the 4-tab body (Overview/Schedule/Funding/Actions & events). See
// docs/adr/2026-07-22-commitments-tab-redesign-faithful-port.md for the port strategy — every
// write is real (detailMutations.ts), every Lucius touchpoint opens the real dock
// (onAskLucius), and the funding-waterfall/loan math is pure client-side reuse of
// utils/commitments.ts over data hydrated by fundingValue.ts.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { Commitment } from '../../../types/commitments'
import {
  fBase,
  fPct,
  getBaseCurrency,
  getCurrencySymbol,
  moneyClass,
  toBase,
} from '../../../utils/format'
import {
  KIND_LABELS,
  fmtDate,
  isTracker,
  loanIntel,
  nativeTotalBase,
  nextInstallment,
  paidBase,
  paidPct,
  remainingBase,
} from '../../../utils/commitments'
import { Button, Container, Modal, Stack } from '../../ui'
import { DotsIcon, PencilIcon, Spark } from './icons'
import { useNarrow } from './useNarrow'
import { useCommitmentDetailData } from './fundingValue'
import { useDetailMutations } from './detailMutations'
import type { DetailMutations } from './detailTypes'
import { computeCoverage } from './detailFunding'
import { lateFundingHint } from './coverageEngine'
import { entityRefProps } from '../../../utils/mentionable'
import { buildCtxFields } from '../../../lucius/ctxFields'
import { filterByDescriptor } from '../../../lucius/entityRegistry'
import { TimelineStrip } from './timelineStrip'
import { OverviewTab } from './detailOverview'
import { Schedule } from './detailSchedule'
import { Funding } from './detailFunding'
import { Actions } from './detailActions'

interface CommitmentDetailProps {
  commitment: Commitment
  fx: Record<string, number>
  onBack: () => void
  onAskLucius: (text: string) => void
  onChanged: () => void
  onOpenCommitment: (id: string) => void
}

type TabKey = 'overview' | 'schedule' | 'funding' | 'actions'

export function CommitmentDetail({
  commitment,
  fx,
  onBack,
  onAskLucius,
  onChanged,
  onOpenCommitment,
}: CommitmentDetailProps) {
  const [nameDraft, setNameDraft] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [menu, setMenu] = useState(false)
  const [tab, setTab] = useState<TabKey>('overview')
  const bodyRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState(0)
  const [maxBodyHeight, setMaxBodyHeight] = useState(0)
  useLayoutEffect(() => {
    setMaxBodyHeight(0)
  }, [tab])
  useLayoutEffect(() => {
    const h = bodyRef.current?.offsetHeight
    if (h == null) return
    setBodyHeight(h)
    setMaxBodyHeight((m) => Math.max(m, h))
  })
  // iOS Safari can still visibly snap the scroll pane on a sub-tab tap even with the height
  // spacer above: a finger-thrown pane still has momentum decelerating when the tap lands, and
  // WebKit reconciles that inertia against the mid-flight layout change with a visible jump
  // instead of a clean clamp. Pinning scrollTop back explicitly (which also kills the leftover
  // momentum) is the only reliable fix for that case.
  const pendingScrollRef = useRef<number | null>(null)
  const pinTab = (k: TabKey) => {
    pendingScrollRef.current = document.querySelector<HTMLElement>('.app-layout')?.scrollTop ?? null
    setTab(k)
  }
  useLayoutEffect(() => {
    if (pendingScrollRef.current == null) return
    const el = document.querySelector<HTMLElement>('.app-layout')
    if (el) el.scrollTop = pendingScrollRef.current
    pendingScrollRef.current = null
  }, [tab])
  const narrow = useNarrow()

  // The list and detail screens share ONE scrolling element (.app-layout) — swapping to this
  // component doesn't reset it, so opening a commitment while the list was scrolled down lands
  // here mid-scroll instead of at the top. Reset on mount and again whenever a DIFFERENT
  // commitment opens (cross-links like a loan's "repayment plan →" swap the id without
  // unmounting this component).
  useEffect(() => {
    document.querySelector('.app-layout')?.scrollTo(0, 0)
  }, [commitment.id])

  const navigate = useNavigate()
  const { hydrated: c, positions, envelope } = useCommitmentDetailData(commitment, fx)
  const writes = useDetailMutations(commitment, onChanged)
  const m: DetailMutations = {
    ...writes,
    onAskLucius,
    onOpenCommitment,
    // Deep-links the SAME transaction drawer the register opens (mirrors the write-receipt
    // chip's /accounts?txn=<id> precedent) — a real navigation, not a chat-mediated lookup.
    onOpenTxn: (txnId) =>
      void navigate({
        to: '/accounts',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        search: ((prev: any) => ({ ...prev, txn: txnId })) as any,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any),
  }

  const next = nextInstallment(c)
  const tracker = isTracker(c)
  const remB = remainingBase(c, fx)
  const { gap } = computeCoverage(c, fx, envelope)
  const funded = remB - gap
  const atRisk = !!c.coverage?.at_risk && c.status === 'active'
  const base = getBaseCurrency()
  const nonBase = c.currency !== base
  const loan = loanIntel(c)

  let insight: React.ReactNode
  let insightBtnLabel = 'Lucius'
  // " with Lucius" is dropped from the button on phone (kept in the DOM via
  // .c2-ribbon-btn-rest, same idiom as the list masthead's "Fix with Lucius" → "Fix") — the
  // Spark icon beside the button and the ribbon's own Lucius-tinted border already say whose
  // suggestion this is, so spelling it out again in the button itself is the one truly
  // redundant word here. Only set for the two branches whose CORE label needs it restored on
  // desktop ("Fix", "Draft early payoff"); the bare 'Lucius' default has nothing to append.
  let insightBtnSuffix: string | null = null
  let insightHot = true
  let insightPrompt: string

  if (atRisk && c.coverage) {
    const lateDraw = (c.funding_plan || []).find(
      (f) =>
        f.available_date &&
        c.coverage?.earliest_shortfall_date &&
        f.available_date > c.coverage.earliest_shortfall_date,
    )
    const weeks =
      lateDraw && c.coverage.earliest_shortfall_date
        ? Math.round(
            (new Date(lateDraw.available_date as string).getTime() -
              new Date(c.coverage.earliest_shortfall_date).getTime()) /
              (7 * 86400000),
          )
        : null
    insight = (
      <>
        {/* .c2-ribbon-core (desktop) / .c2-ribbon-lite (phone) — same toggle the list masthead's
            ribbon already uses (global CSS, no new rule needed). On phone the mechanism-level
            elaboration ("the funding arrives ~4 weeks late, pulling it forward closes the
            gap...") was costing 2 extra lines on a screen where this ribbon is now the FIRST
            substantive content the reader hits (owner: "too much space is consumed here") — the
            headline number+date is the part worth reading at a glance; the reasoning is what
            tapping "Fix" is for. */}
        <span className="c2-ribbon-core">
          This plan&apos;s funding runs <b className="money">{fBase(c.coverage.shortfall_base)}</b>{' '}
          short of its payments by{' '}
          <b>
            {c.coverage.earliest_shortfall_date ? fmtDate(c.coverage.earliest_shortfall_date) : ''}
          </b>
          {lateDraw && weeks != null
            ? `. ${lateFundingHint(lateDraw.label, lateDraw.available_date as string, weeks, lateDraw.type === 'asset_sale')}`
            : '.'}{' '}
          This counts only funding added to this plan, not your account balances.
        </span>
        <span className="c2-ribbon-lite">
          <b className="money">{fBase(c.coverage.shortfall_base)}</b> unfunded by{' '}
          <b>
            {c.coverage.earliest_shortfall_date ? fmtDate(c.coverage.earliest_shortfall_date) : ''}
          </b>
          .
        </span>
      </>
    )
    insightBtnLabel = 'Fix'
    insightBtnSuffix = ' with Lucius'
    insightPrompt = `The "${c.name}" commitment's funding runs ${fBase(c.coverage.shortfall_base)} short of its payments by ${c.coverage.earliest_shortfall_date ? fmtDate(c.coverage.earliest_shortfall_date) : 'an upcoming date'}${lateDraw && weeks != null ? `. The "${lateDraw.label}" funding does not arrive until about ${weeks} weeks after the payment` : ''}. This counts only the funding sources on this commitment, not my account balances. Help me close this gap.`
  } else if (loan && c.status === 'active') {
    insight = (
      <>
        <span className="c2-ribbon-core">
          {loan.paidCount} of {loan.term} payments made —{' '}
          <b className="money">{fBase(loan.balance)}</b> balance at <b>{loan.apr}%</b>. An extra{' '}
          <b className="money">{fBase(loan.extra)}/mo</b> clears it{' '}
          <b>~{loan.monthsSaved} months early</b>, saving about{' '}
          <b className="money">{fBase(loan.interestSaved)}</b> in interest.
        </span>
        <span className="c2-ribbon-lite">
          {loan.paidCount} of {loan.term} paid — <b className="money">{fBase(loan.balance)}</b> at{' '}
          <b>{loan.apr}%</b>.
        </span>
      </>
    )
    insightBtnLabel = 'Draft early payoff'
    insightBtnSuffix = ' with Lucius'
    insightHot = false
    insightPrompt = `Help me draft an early-payoff plan for my "${c.name}" loan — paying an extra ${fBase(loan.extra)}/mo would clear it about ${loan.monthsSaved} months early and save roughly ${fBase(loan.interestSaved)} in interest.`
  } else {
    const envSource = envelope
      ? (c.funding_plan || []).find((f) => f.type === 'income' && !f.disabled)
      : null
    insight =
      envSource && envelope ? (
        <>
          On autopilot — <b className="money">{fBase(envSource.value_base ?? 0)}/mo</b> flows into
          your <b>{envelope.envelope}</b> target;{' '}
          <b className="money">{fBase(envelope.accumulated)}</b> saved so far.
        </>
      ) : (
        <>
          Fully funded — every remaining {tracker ? 'contribution' : 'installment'} is covered by
          earmarked cash and dated draws.
        </>
      )
    insightPrompt = `My "${c.name}" commitment looks fully funded. Take a look and let me know if there's anything worth tightening up.`
  }

  const unpaidN = c.installments.filter((i) => i.status !== 'paid').length
  const srcN = (c.funding_plan || []).length
  const pendingActsN = (c.actions || []).filter((a) => a.status === 'pending').length
  const actsOverdue = (c.actions || []).some(
    (a) =>
      a.status === 'pending' &&
      a.due_date &&
      new Date(a.due_date + 'T00:00:00') < new Date(new Date().toDateString()),
  )
  const fundGap = !tracker && funded < remB - 0.5

  const fxCcy = fx[c.currency]
  const fxBase = fx[base]
  const nativePerBase = fxCcy && fxBase ? fxBase / fxCcy : null

  // On phone, everything the standalone Mark completed button and the inline rename/note
  // pencils used to do folds into this ONE menu instead — desktop keeps those as separate,
  // always-visible controls (see the `!narrow` checks around this component's icon buttons
  // and the primary button below).
  const menuItems = (
    <>
      {narrow && (
        <>
          <button
            className="c3-menu-it"
            role="menuitem"
            onClick={() => {
              setMenu(false)
              setNameDraft(c.name)
            }}
          >
            Rename
          </button>
          <button
            className="c3-menu-it"
            role="menuitem"
            onClick={() => {
              setMenu(false)
              setNoteDraft(c.note || '')
            }}
          >
            {c.note ? 'Edit note' : 'Add note'}
          </button>
          <button
            className="c3-menu-it"
            role="menuitem"
            onClick={() => {
              setMenu(false)
              void writes.setStatus(c.status === 'active' ? 'completed' : 'active')
            }}
          >
            {c.status === 'active' ? 'Mark completed' : 'Reactivate'}
          </button>
          <div className="c3-menu-divider" />
        </>
      )}
      {c.status === 'active' && (
        <button
          className="c3-menu-it"
          role="menuitem"
          onClick={() => {
            setMenu(false)
            setConfirmCancel(true)
            setConfirmDel(false)
          }}
        >
          Cancel commitment
        </button>
      )}
      <button
        className="c3-menu-it danger"
        role="menuitem"
        onClick={() => {
          setMenu(false)
          setConfirmDel(true)
          setConfirmCancel(false)
        }}
      >
        Delete commitment
      </button>
    </>
  )

  const kebabTrigger = (
    <button
      className={'c3-kebab' + (menu ? ' open' : '')}
      aria-label="More actions"
      aria-expanded={menu}
      onClick={() => setMenu(!menu)}
    >
      <DotsIcon />
    </button>
  )

  // Desktop: an anchored dropdown (position:relative wrapper + absolute popup), same as
  // before. Phone: a small floating "..." button is a poor anchor for a menu, and Modal
  // already owns the phone bottom-sheet geometry (see note editor above) — reuse it instead
  // of a bespoke dropdown, so the menu opens as a full-width sheet with normal-sized tap
  // targets rather than a cramped popup pinned to a tiny trigger.
  const kebab = narrow ? (
    <>
      {kebabTrigger}
      {menu && (
        <Modal open onClose={() => setMenu(false)} title={c.name}>
          <div className="c3-menu-sheet" role="menu">
            {menuItems}
          </div>
        </Modal>
      )}
    </>
  ) : (
    <div className="c3-menu-wrap">
      {kebabTrigger}
      {menu && (
        <>
          <div className="c3-menu-veil" onClick={() => setMenu(false)} />
          <div className="c3-menu" role="menu">
            {menuItems}
          </div>
        </>
      )}
    </div>
  )

  return (
    <Container size="wide" data-ctx-region="content" data-ctx-title={'Commitment: ' + c.name}>
      <Stack>
        {/* The open commitment is itself mentionable: data-entity-id makes it right-clickable
            ("Ask Lucius about this") and draggable into the dock, and data-ctx-* puts it in the
            screen snapshot so "this commitment" resolves while the detail is open. */}
        <section className="c2-det c3-flat" data-screen-label={'Commitment: ' + c.name}>
          <div className="c2-det-pad">
            {/* The commitment identity lives on the HEADER, not the whole section: a
                draggable ancestor made every child without its own entity — the Overview
                preview rows — drag as the commitment. */}
            <div
              className="c2-det-head"
              {...entityRefProps(
                'commitment',
                c.id,
                c.name || '(unnamed)',
                `${c.name} ${KIND_LABELS[c.kind]} ${c.note}`,
                `${KIND_LABELS[c.kind]} • ${c.status}`,
              )}
              data-ctx=""
              data-ctx-type="commitment"
              data-ctx-fields={buildCtxFields(
                filterByDescriptor('commitment', {
                  name: c.name,
                  kind: c.kind,
                  currency: c.currency,
                  status: c.status,
                  note: c.note,
                  created_at: c.created_at,
                }),
              )}
              draggable
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                {nameDraft != null ? (
                  <input
                    className="c2-input c2-det-name-input"
                    type="text"
                    value={nameDraft}
                    autoFocus
                    onChange={(e) => setNameDraft(e.target.value)}
                    onBlur={() => {
                      if (nameDraft.trim()) void writes.rename(nameDraft.trim())
                      setNameDraft(null)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                      if (e.key === 'Escape') setNameDraft(null)
                    }}
                  />
                ) : (
                  <div className="c2-det-name">
                    {/* Truncates instead of wrapping on phone (see .c2-det-name-text), so a
                        long name never pushes the kebab onto a second line — it stays pinned
                        at the end of the SAME row as the title, not floating below it. */}
                    <span className="c2-det-name-text">{c.name}</span>
                    {!narrow && (
                      <button
                        className="c2-iconbtn on"
                        aria-label="Rename"
                        onClick={() => setNameDraft(c.name)}
                      >
                        <PencilIcon />
                      </button>
                    )}
                    {narrow && kebab}
                  </div>
                )}
                <div className="c2-det-tags">
                  <span className="c2-tag">{KIND_LABELS[c.kind]}</span>
                  <span className="c2-tag">{c.currency}</span>
                  {nonBase && nativePerBase != null && (
                    <span
                      className="c2-tag c3-fx"
                      title="Base amounts use today's rate — shortfall verdicts move with FX"
                    >
                      {getCurrencySymbol(c.currency)}
                      {nativePerBase.toLocaleString('en-US', {
                        maximumFractionDigits: nativePerBase >= 100 ? 0 : 2,
                      })}{' '}
                      / {getCurrencySymbol(base)} • today
                    </span>
                  )}
                  <span className={`c2-tag status-${c.status}`}>{c.status}</span>
                  {atRisk && c.coverage && (
                    <span className="c2-chip short">
                      <span className="money">{fBase(c.coverage.shortfall_base)}</span> unfunded by{' '}
                      {c.coverage.earliest_shortfall_date
                        ? fmtDate(c.coverage.earliest_shortfall_date)
                        : ''}
                    </span>
                  )}
                  {c.coverage?.assessed && !atRisk && c.status === 'active' && (
                    <span className="c2-chip ok">On track to fund</span>
                  )}
                </div>
                {narrow ? (
                  c.note && <div className="c2-det-note">{c.note}</div>
                ) : noteDraft != null ? (
                  <div className="c2-edit" style={{ marginTop: 8, maxWidth: 560 }}>
                    <input
                      className="c2-input grow"
                      style={{ width: '100%' }}
                      type="text"
                      value={noteDraft}
                      autoFocus
                      placeholder="Add a note about this commitment"
                      onChange={(e) => setNoteDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          void writes.updateNote(noteDraft.trim())
                          setNoteDraft(null)
                        }
                        if (e.key === 'Escape') setNoteDraft(null)
                      }}
                    />
                    <div className="c2-edit-btns">
                      <button className="c2-mini-btn" onClick={() => setNoteDraft(null)}>
                        Cancel
                      </button>
                      <button
                        className="c2-mini-btn pri"
                        onClick={() => {
                          void writes.updateNote(noteDraft.trim())
                          setNoteDraft(null)
                        }}
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="c2-det-note c2-note-row">
                    {c.note || <span style={{ fontStyle: 'italic' }}>No note</span>}
                    <button
                      className="c2-iconbtn on"
                      aria-label="Edit note"
                      onClick={() => setNoteDraft(c.note || '')}
                    >
                      <PencilIcon />
                    </button>
                  </div>
                )}
              </div>
              <div className="c2-det-acts">
                {!narrow && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() =>
                      void writes.setStatus(c.status === 'active' ? 'completed' : 'active')
                    }
                  >
                    {c.status === 'active' ? 'Mark completed' : 'Reactivate'}
                  </Button>
                )}
                {!narrow && kebab}
              </div>
            </div>

            {narrow && noteDraft != null && (
              <Modal
                open
                onClose={() => setNoteDraft(null)}
                title="Note"
                footer={
                  <>
                    <Button variant="secondary" size="sm" onClick={() => setNoteDraft(null)}>
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => {
                        void writes.updateNote(noteDraft.trim())
                        setNoteDraft(null)
                      }}
                    >
                      Save
                    </Button>
                  </>
                }
              >
                <textarea
                  className="c2-input c2-note-sheet-textarea"
                  autoFocus
                  rows={4}
                  placeholder="Add a note about this commitment"
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                />
              </Modal>
            )}

            {(confirmDel || confirmCancel) && (
              <div className="c3-confirmbar">
                {confirmDel ? (
                  <span className="c2-del-confirm">
                    Delete "{c.name}"? Its synced budget targets are removed too.
                    <button className="c2-mini-btn" onClick={() => setConfirmDel(false)}>
                      Keep
                    </button>
                    <button
                      className="c2-mini-btn danger"
                      onClick={() => {
                        void writes.deleteCommitment().then(onBack)
                      }}
                    >
                      Delete
                    </button>
                  </span>
                ) : (
                  <span className="c2-del-confirm">
                    Cancel "{c.name}"? History is kept; tracking and funding stop.
                    <button className="c2-mini-btn" onClick={() => setConfirmCancel(false)}>
                      Keep
                    </button>
                    <button
                      className="c2-mini-btn danger"
                      onClick={() => {
                        void writes.setStatus('cancelled')
                        setConfirmCancel(false)
                      }}
                    >
                      Cancel commitment
                    </button>
                  </span>
                )}
              </div>
            )}

            <div className="c2-exp-figs">
              <div className="c2-stat">
                <span className="k">{tracker ? 'Contributed' : 'Paid'}</span>
                <span className={moneyClass('v')}>{fBase(paidBase(c, fx))}</span>
                <span className="u">
                  <span className="money">{fPct(paidPct(c))}</span> of{' '}
                  <span className="money">{fBase(nativeTotalBase(c, fx))}</span>
                </span>
              </div>
              <div className="c2-stat">
                <span className="k">Remaining</span>
                <span className={moneyClass('v')}>{fBase(remB)}</span>
                <span className="u">
                  {c.installments.filter((i) => i.status !== 'paid').length} installments left
                </span>
              </div>
              <div className="c2-stat">
                <span className="k">{tracker ? 'Next contribution' : 'Next payment'}</span>
                <span className={moneyClass('v')}>
                  {next ? fBase(toBase(next.amount, c.currency, fx)) : '—'}
                </span>
                <span className="u">{next ? fmtDate(next.due_date) : 'Complete'}</span>
              </div>
              {loan && (
                <div className="c2-stat">
                  <span className="k">Loan balance</span>
                  <span className={moneyClass('v')}>{fBase(loan.balance)}</span>
                  <span className="u">
                    <span className="money">{loan.apr}%</span> APR • {loan.monthsLeft} months left
                  </span>
                </div>
              )}
              {!tracker && !loan && (
                <div className="c2-stat">
                  <span className="k">Funding lined up</span>
                  <span className={moneyClass('v', fundGap ? 'warn' : undefined)}>
                    {fBase(funded)}
                  </span>
                  <span className="u">
                    {fundGap ? (
                      <>
                        <span className="money">{fBase(remB - funded)}</span> short of remaining
                      </>
                    ) : (
                      'covers remaining'
                    )}
                  </span>
                </div>
              )}
            </div>

            <div className="c2-ribbon c2-det-ribbon">
              <span className="c2-ribbon-spark">
                <Spark />
              </span>
              <span className="c2-ribbon-text">{insight}</span>
              <span className="c2-ribbon-acts">
                <button
                  className={'c2-ribbon-act' + (insightHot ? ' hot' : '')}
                  onClick={() => onAskLucius(insightPrompt)}
                >
                  <Spark size={13} />
                  {insightBtnLabel}
                  {insightBtnSuffix && (
                    <span className="c2-ribbon-btn-rest">{insightBtnSuffix}</span>
                  )}
                </button>
              </span>
            </div>

            {!tracker && c.status === 'active' && <TimelineStrip commitment={c} fx={fx} />}

            <div className="c2-seg c3-dettabs" role="tablist">
              <TabButton k="overview" label="Overview" tab={tab} setTab={pinTab} />
              <TabButton
                k="schedule"
                label={tracker ? 'Contributions' : 'Schedule'}
                n={unpaidN || null}
                tab={tab}
                setTab={pinTab}
              />
              <TabButton
                k="funding"
                label="Funding"
                n={srcN || null}
                dot={fundGap ? 'warn' : null}
                tab={tab}
                setTab={pinTab}
              />
              <TabButton
                k="actions"
                label="Actions & events"
                n={pendingActsN || null}
                dot={actsOverdue ? 'bad' : null}
                tab={tab}
                setTab={pinTab}
              />
            </div>

            <div className="c3-det-body" ref={bodyRef}>
              {tab === 'overview' && (
                <OverviewTab
                  commitment={c}
                  fx={fx}
                  envelope={envelope}
                  positions={positions}
                  goTo={pinTab}
                  m={m}
                />
              )}
              {tab === 'schedule' && <Schedule commitment={c} fx={fx} envelope={envelope} m={m} />}
              {tab === 'funding' && (
                <Funding commitment={c} fx={fx} envelope={envelope} positions={positions} m={m} />
              )}
              {tab === 'actions' && <Actions commitment={c} fx={fx} m={m} />}
            </div>
            {maxBodyHeight > bodyHeight && (
              <div
                className="c2-scroll-spacer"
                style={{ height: maxBodyHeight - bodyHeight, order: 8 }}
                aria-hidden="true"
              />
            )}
          </div>
        </section>
      </Stack>
    </Container>
  )
}

function TabButton({
  k,
  label,
  n,
  dot,
  tab,
  setTab,
}: {
  k: TabKey
  label: string
  n?: number | null
  dot?: 'warn' | 'bad' | null
  tab: TabKey
  setTab: (k: TabKey) => void
}) {
  return (
    <button
      className={tab === k ? 'on' : ''}
      role="tab"
      aria-selected={tab === k}
      onClick={() => setTab(k)}
    >
      {label}
      {n != null && <span className="c3-tabn">{n}</span>}
      {dot && <span className={'c3-tabdot' + (dot === 'bad' ? ' bad' : '')} />}
    </button>
  )
}
