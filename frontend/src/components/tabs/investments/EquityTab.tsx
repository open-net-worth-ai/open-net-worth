import { Fragment, useState } from 'react'
import {
  Badge,
  Button,
  Cluster,
  ConfirmDialog,
  EmptyState,
  SummaryBar,
  TabSkeleton,
  ErrorState,
} from '../../ui'
import type { BadgeTone } from '../../ui'
import { Building2, Plus, ChevronRight } from '../../../ui/icons'
import { fNum, fCurrency, fDate } from '../../../utils/format'
import type { Grant, PendingReleaseItem, Tranche } from '../../../types/equity'
import type { ComputedRow } from '../../../types/portfolio'
import { useEquityView } from './useEquityView'
import type { GrantVM, TrancheVM, UpcomingVM, VestHistoryVM } from './useEquityView'
import { AddGrantModal } from '../equity/AddGrantModal'
import { EditGrantModal } from '../equity/EditGrantModal'
import { EntityRow } from '../../../lucius/EntityRow'
import { LuciusCta } from '../../intro/LuciusCta'
import { MarkReleasedModal } from '../equity/MarkReleasedModal'
import { MarkVestModal } from '../equity/MarkVestModal'
import { authFetch } from '../../../api/client'
import './equity.css'

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  /** Live portfolio rows, for the "since grant" current-price join on unvested units.
   *  Optional so existing callers/tests that don't need it keep working unchanged. */
  rows?: ComputedRow[]
  onAskLucius?: (text: string) => void
}

const GRANTS_MESSAGE =
  'Help me add my equity grants. Ask which company and grant type (RSU, ESOW or LTIP); I can ' +
  'send a screenshot or statement of my grants instead of typing. Ask me one thing at a time.'

// ── Vest target (which grant + tranche to vest) ───────────────────────────────

interface VestTarget {
  grant: Grant
  tranche: Tranche
}

// ── EqDrawer: expanded tranche timeline inside the grants table ───────────────

interface DrawerProps {
  gvm: GrantVM
  rawGrant: Grant
  onMark: (target: VestTarget) => void
  onRefetch: () => void
}

function EqDrawer({ gvm, rawGrant, onMark, onRefetch }: DrawerProps) {
  const [deleteState, setDeleteState] = useState<'idle' | 'confirm'>('idle')
  const [editOpen, setEditOpen] = useState(false)
  // Undo is confirmed in a Modal, never inline: the timeline row's action column is
  // 84px wide and sits inside a horizontally-scrolled panel on a phone, so a second
  // inline button has nowhere to go.
  const [confirmUndo, setConfirmUndo] = useState<Tranche | null>(null)
  const [undoingId, setUndoingId] = useState<string | null>(null)
  const [undoErrors, setUndoErrors] = useState<Record<string, string>>({})
  const nextT = gvm.tranches.find((t) => !t.vested)

  async function handleDelete() {
    try {
      const res = await authFetch(`/api/equity/grants/${gvm.id}/cancel`, { method: 'POST' })
      if (res.ok) onRefetch()
    } catch {
      /* best-effort */
    } finally {
      setDeleteState('idle')
    }
  }

  function requestUndo(trancheId: string) {
    const raw = rawGrant.tranches.find((rt) => rt.id === trancheId)
    if (raw?.vest_event_id) setConfirmUndo(raw)
  }

  async function handleUndoConfirmed(tranche: Tranche) {
    setConfirmUndo(null)
    setUndoingId(tranche.id)
    setUndoErrors((prev) => ({ ...prev, [tranche.id]: '' }))
    try {
      const res = await authFetch(`/api/equity/vest-events/${tranche.vest_event_id}/undo`, {
        method: 'POST',
      })
      if (res.ok) {
        onRefetch()
        return
      }
      const detail = await res.json().catch(() => ({}))
      const msg =
        (detail as { detail?: string }).detail ||
        (res.status === 409
          ? 'Cannot undo — the lot has already been sold or disposed.'
          : `Error ${res.status}`)
      setUndoErrors((prev) => ({ ...prev, [tranche.id]: msg }))
    } catch {
      setUndoErrors((prev) => ({ ...prev, [tranche.id]: 'Undo failed — try again.' }))
    } finally {
      setUndoingId(null)
    }
  }

  return (
    <>
      <div className="eq-dr-top">
        <div className="eq-dr-prog">
          <div className="eq-dr-prog-head">
            <span className="eq-dr-prog-pct">{gvm.pct}% vested</span>
            <span className="eq-dr-prog-sub">
              {fNum(gvm.vested)} of {fNum(gvm.total)} units
            </span>
          </div>
          <div className="eq-dr-bar">
            {gvm.tranches.map((t) => (
              <span
                key={t.id}
                className="eq-dr-seg"
                style={{
                  flex: t.units,
                  background: t.vested
                    ? 'var(--good)'
                    : nextT && t.id === nextT.id
                      ? 'color-mix(in srgb, var(--accent) 55%, var(--surface2))'
                      : 'var(--surface2)',
                }}
                title={`${fDate(t.date, 'short', 'always')} • ${fNum(t.units)} units`}
              />
            ))}
          </div>
          {gvm.sinceGrantLine && <div className="eq-dr-since">{gvm.sinceGrantLine}</div>}
        </div>
        <div className="eq-dr-meta">
          <div className="eq-dr-meta-k">Next vest</div>
          <div className="eq-dr-meta-v">
            {nextT ? fDate(nextT.date, 'short', 'always') : 'Fully vested'}
          </div>
          {nextT && (
            <div className="eq-dr-meta-sub">
              {nextT.sub} • {fNum(nextT.units)} units
            </div>
          )}
        </div>
      </div>

      <div className="eq-tl">
        {gvm.tranches.map((t) => (
          <TlRow
            key={t.id}
            t={t}
            rawGrant={rawGrant}
            onMark={onMark}
            onUndo={requestUndo}
            undoing={undoingId === t.id}
            undoError={undoErrors[t.id]}
          />
        ))}
      </div>

      <div className="eq-drawer-actions">
        {deleteState === 'idle' ? (
          <>
            <button className="eq-link" onClick={() => setEditOpen(true)}>
              Edit grant
            </button>
            <button className="eq-link eq-link--danger" onClick={() => setDeleteState('confirm')}>
              Delete grant
            </button>
          </>
        ) : (
          <>
            <Button variant="danger" size="sm" onClick={handleDelete}>
              Confirm delete?
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setDeleteState('idle')}>
              Cancel
            </Button>
          </>
        )}
      </div>

      {editOpen && (
        <EditGrantModal
          grant={rawGrant}
          onClose={() => setEditOpen(false)}
          onDone={() => {
            setEditOpen(false)
            onRefetch()
          }}
        />
      )}

      <ConfirmDialog
        data={
          confirmUndo
            ? {
                title: 'Undo this vest?',
                body: `Reverses the ${fNum(confirmUndo.units)}-unit vest recorded on ${fDate(confirmUndo.vest_date, 'short', 'always')} — re-locks the units and removes the FMV lot (and the income row, if this vest was recorded before 22 Sep 2026).`,
                confirmLabel: 'Undo vest',
                danger: true,
              }
            : null
        }
        onConfirm={() => confirmUndo && handleUndoConfirmed(confirmUndo)}
        onCancel={() => setConfirmUndo(null)}
      />
    </>
  )
}

// ── TlRow: one row in the vesting timeline ────────────────────────────────────

interface TlRowProps {
  t: TrancheVM
  rawGrant: Grant
  onMark: (target: VestTarget) => void
  onUndo: (trancheId: string) => void
  undoing: boolean
  undoError?: string
}

function TlRow({ t, rawGrant, onMark, onUndo, undoing, undoError }: TlRowProps) {
  function handleMark() {
    const rawTranche = rawGrant.tranches.find((rt) => rt.id === t.id)
    if (rawTranche) onMark({ grant: rawGrant, tranche: rawTranche })
  }

  return (
    <div className={`eq-tl-row ${t.cls}`}>
      <div className="eq-tl-node">
        <span className="eq-tl-dot" />
      </div>
      <div>
        <div className="eq-tl-date">{fDate(t.date, 'short', 'always')}</div>
        <div className="eq-tl-sub">
          {fNum(t.units)} units • {t.sub}
        </div>
        {undoError && <div className="eq-tl-err">{undoError}</div>}
      </div>
      <Badge tone={t.badgeTone as BadgeTone}>{t.badgeLabel}</Badge>
      <div className="eq-tl-act">
        {t.vested ? (
          <button className="eq-link" onClick={() => onUndo(t.id)} disabled={undoing}>
            {undoing ? 'Undoing…' : 'Undo'}
          </button>
        ) : (
          <button className="eq-link" onClick={handleMark}>
            Mark vested
          </button>
        )}
      </div>
    </div>
  )
}

// ── UpcomingVestsList: eq-up panel ────────────────────────────────────────────

interface UpcomingProps {
  upcoming: UpcomingVM[]
  rawGrants: Grant[]
  onMark: (target: VestTarget) => void
}

const MAX_UPCOMING = 6

function UpcomingVestsList({ upcoming, rawGrants, onMark }: UpcomingProps) {
  if (upcoming.length === 0) {
    return <div className="mk-empty-filter">No upcoming vests.</div>
  }

  return (
    <div className="eq-up">
      {upcoming.slice(0, MAX_UPCOMING).map((u) => (
        <div className="eq-up-row" key={`${u.grantId}-${u.trancheId}`}>
          <div className="eq-up-l">
            {u.overdue && <Badge tone="neg">Overdue</Badge>}
            <span className="eq-up-date">{fDate(u.date, 'short', 'always')}</span>
            <span className="eq-up-meta">
              {u.symbol} • {fNum(u.units)} units
            </span>
          </div>
          <button
            className="eq-link"
            onClick={() => {
              const grant = rawGrants.find((g) => g.id === u.grantId)
              const tranche = grant?.tranches.find((t) => t.id === u.trancheId)
              if (grant && tranche) onMark({ grant, tranche })
            }}
          >
            Mark vested
          </button>
        </div>
      ))}
      {upcoming.length > MAX_UPCOMING && (
        <div className="mk-field-help" style={{ marginTop: 10 }}>
          +{upcoming.length - MAX_UPCOMING} more — open each grant's tranches for the full schedule.
        </div>
      )}
    </div>
  )
}

// ── Vest history: persisted vest events, including release state ─────────────

const MAX_HISTORY = 8

function VestHistoryPanel({
  rows,
  onRelease,
}: {
  rows: VestHistoryVM[]
  onRelease: (item: PendingReleaseItem) => void
}) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? rows : rows.slice(0, MAX_HISTORY)

  return (
    <div className="mk-panel">
      <div className="mk-panel-head">
        <span className="mk-panel-title">Vest history</span>
        <span className="mk-panel-spacer" />
        <span className="mk-panel-sub">{rows.length} recorded</span>
      </div>
      <div className="mk-panel-body">
        {rows.length === 0 ? (
          <EmptyState
            title="No vest history yet"
            body="Vests appear here after you mark a tranche vested."
          />
        ) : (
          <>
            <div className="inv-event-table inv-event-table--vest">
              <div className="inv-event-head" aria-hidden="true">
                <span>Date vested</span>
                <span>Symbol</span>
                <span>Units</span>
                <span>Price at vest</span>
                <span>Status</span>
              </div>
              {/* EntityRow renders the host <div> itself and stamps data-entity-id + draggable
                  + data-ctx-*, making a recorded vest @-mentionable, right-clickable
                  ("Ask Lucius about this") and draggable into the dock. `vest` is a registered
                  mention type (descriptors/equity_lots.py, order=22) keyed on the vest EVENT id
                  — the upcoming-tranche rows have no event id yet, so they stay unwired. */}
              {shown.map((row) => (
                <EntityRow
                  as="div"
                  className="inv-event-row"
                  key={row.vestEventId}
                  type="vest"
                  id={row.vestEventId}
                  label={row.symbol}
                  item={row}
                  toFields={(it) => ({
                    symbol: it.symbol,
                    date: it.date,
                    units: it.units,
                  })}
                  search={`${row.symbol} vest ${row.date}`}
                  sublabel={`${fNum(row.units)} units • ${fDate(row.date, 'short', 'always')}`}
                >
                  <time className="inv-event-date" dateTime={row.date}>
                    {fDate(row.date, 'short', 'always')}
                  </time>
                  <span className="inv-event-holding">
                    <strong>{row.symbol}</strong>
                    <span className="inv-event-mobile-meta">
                      {' '}
                      • {fNum(row.units)} units
                      {row.fmvPerUnit != null && row.fmvCurrency
                        ? ` • ${fCurrency(row.fmvPerUnit, row.fmvCurrency)}`
                        : ''}
                    </span>
                  </span>
                  <span className="inv-event-units">{fNum(row.units)}</span>
                  <span className="inv-event-price">
                    {row.fmvPerUnit != null && row.fmvCurrency
                      ? fCurrency(row.fmvPerUnit, row.fmvCurrency)
                      : '—'}
                  </span>
                  <span className="inv-event-status">
                    <span
                      className="inv-event-badges"
                      title={
                        row.releaseStatus === 'released' && row.releasedOn
                          ? `Released ${fDate(row.releasedOn, 'short', 'always')}`
                          : row.releaseStatus === 'not_released'
                            ? 'Release not recorded'
                            : row.expectedReleaseDate
                              ? `Expected ${fDate(row.expectedReleaseDate, 'short', 'always')}`
                              : 'Release date not set'
                      }
                    >
                      <Badge tone={row.releaseStatus === 'released' ? 'pos' : 'warn'}>
                        {row.releaseStatus === 'released'
                          ? 'Released'
                          : row.releaseStatus === 'awaiting_release'
                            ? 'Awaiting release'
                            : 'Not released'}
                      </Badge>
                      {row.releaseStatus === 'awaiting_release' && row.overdue && (
                        <Badge tone="neg">Overdue</Badge>
                      )}
                    </span>
                    <span className="inv-event-action">
                      {row.releaseItem && (
                        <Button
                          variant="ghost"
                          size="dense"
                          onClick={() => onRelease(row.releaseItem!)}
                        >
                          Mark released
                        </Button>
                      )}
                    </span>
                  </span>
                </EntityRow>
              ))}
            </div>
            {rows.length > MAX_HISTORY && (
              <Button
                variant="ghost"
                size="sm"
                className="inv-event-toggle"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? 'Show top 8 only' : `Show all ${rows.length} vests`}
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Empty state (no grants yet) ───────────────────────────────────────────────

function EquityEmpty({
  onAdd,
  onAskLucius,
}: {
  onAdd: () => void
  onAskLucius?: (text: string) => void
}) {
  return (
    <EmptyState
      fill
      icon={<Building2 size={28} strokeWidth={1.5} aria-hidden="true" />}
      title="No equity grants yet"
      body="Add an RSU, ESOW or LTIP grant and Lucius tracks your vesting schedule, upcoming tranches, and the gain at each vest."
      action={
        <Cluster gap="--space-3" justify="center">
          <Button variant="primary" size="sm" onClick={onAdd}>
            Add grant
          </Button>
          <LuciusCta label="Add with Lucius" message={GRANTS_MESSAGE} onAskLucius={onAskLucius} />
        </Cluster>
      }
    />
  )
}

// ── EquityTab root ────────────────────────────────────────────────────────────

export function EquityTab({ rows = [], onAskLucius }: Props) {
  const vm = useEquityView(rows)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [vestTarget, setVestTarget] = useState<VestTarget | null>(null)
  const [releaseTarget, setReleaseTarget] = useState<PendingReleaseItem | null>(null)
  const [addOpen, setAddOpen] = useState(false)

  function toggleExpanded(id: string) {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  const isLoading = vm.loading
  const hasError = vm.error
  const isEmpty =
    !isLoading &&
    !hasError &&
    vm.grants.length === 0 &&
    vm.vestHistory.length === 0 &&
    vm.upcoming.length === 0

  return (
    <div className="mk-subt">
      {!isEmpty && <SummaryBar cards={vm.summaryCards} />}

      {/* Grants panel */}
      <div className="mk-panel">
        <div className="mk-panel-head">
          <span className="mk-panel-title">Grants</span>
          <span className="mk-panel-spacer" />
          {!isEmpty && (
            <Button
              variant="primary"
              size="sm"
              iconLeft={<Plus size={14} aria-hidden="true" />}
              onClick={() => setAddOpen(true)}
            >
              Add grant
            </Button>
          )}
        </div>
        <div className="mk-panel-body" style={{ overflowX: 'auto' }}>
          {isLoading && <TabSkeleton variant="equity" label="Loading grants…" header={false} />}

          {hasError && <ErrorState message="Couldn't load grants." onRetry={vm.refetch} />}

          {isEmpty && <EquityEmpty onAdd={() => setAddOpen(true)} onAskLucius={onAskLucius} />}

          {!isLoading && !hasError && vm.grants.length > 0 && (
            <table className="eq-table">
              <thead>
                <tr>
                  <th className="l">Symbol</th>
                  <th className="l">Granted</th>
                  <th>Grant price</th>
                  <th>Total</th>
                  <th>Vested</th>
                  <th>Unvested</th>
                  <th className="l">Next vest</th>
                  <th className="l">Status</th>
                  <th aria-label="Expand" />
                </tr>
              </thead>
              <tbody>
                {vm.grants.map((g) => {
                  const open = expandedId === g.id
                  const rawGrant = vm.rawGrants.find((r) => r.id === g.id)!
                  return (
                    <Fragment key={g.id}>
                      <EntityRow
                        as="tr"
                        type="grant"
                        id={g.id}
                        label={g.symbol}
                        search={[g.symbol, rawGrant.type, rawGrant.note].filter(Boolean).join(' ')}
                        item={rawGrant}
                        toFields={(it) => ({
                          symbol: it.symbol || null,
                          type: it.type || null,
                          grant_date: it.grant_date || null,
                          total_units: it.total_units,
                          currency: it.currency || null,
                          tax_event: it.tax_event || null,
                          status: it.status || null,
                          note: it.note || null,
                          created_at: it.created_at || null,
                        })}
                        className="eq-row--click"
                        onClick={() => toggleExpanded(g.id)}
                        aria-expanded={open}
                      >
                        <td className="l">
                          <strong>{g.symbol}</strong>
                        </td>
                        <td className="l">{fDate(g.grantDate, 'short', 'always')}</td>
                        <td>{g.grantPrice != null ? fCurrency(g.grantPrice, g.currency) : '—'}</td>
                        <td>{fNum(g.total)}</td>
                        <td>{fNum(g.vested)}</td>
                        <td>{fNum(g.unvested)}</td>
                        <td className="l">
                          {g.nextVest ? (
                            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                              {fDate(g.nextVest, 'short', 'always')}
                              {g.hasOverdue && <Badge tone="neg">Overdue</Badge>}
                            </span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="l">
                          <Badge tone={g.badgeTone as BadgeTone}>{g.badgeLabel}</Badge>
                        </td>
                        <td>
                          <span className={`eq-caret${open ? ' open' : ''}`} aria-hidden="true">
                            <ChevronRight size={14} />
                          </span>
                        </td>
                      </EntityRow>
                      {open && (
                        <tr className="eq-drawer-row">
                          <td className="eq-drawer-cell" colSpan={9}>
                            <div className="eq-drawer">
                              <EqDrawer
                                gvm={g}
                                rawGrant={rawGrant}
                                onMark={setVestTarget}
                                onRefetch={vm.refetch}
                              />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Upcoming vests panel */}
      {!isLoading && !hasError && !isEmpty && (
        <div className="mk-panel">
          <div className="mk-panel-head">
            <span className="mk-panel-title">Upcoming vests</span>
          </div>
          <div className="mk-panel-body">
            <UpcomingVestsList
              upcoming={vm.upcoming}
              rawGrants={vm.rawGrants}
              onMark={setVestTarget}
            />
          </div>
        </div>
      )}

      {!isLoading && !hasError && !isEmpty && (
        <VestHistoryPanel rows={vm.vestHistory} onRelease={setReleaseTarget} />
      )}

      {/* Modals */}
      {addOpen && (
        <AddGrantModal
          onClose={() => setAddOpen(false)}
          onDone={() => {
            setAddOpen(false)
            vm.refetch()
          }}
        />
      )}

      {vestTarget && (
        <MarkVestModal
          grant={vestTarget.grant}
          tranche={vestTarget.tranche}
          onClose={() => setVestTarget(null)}
          onDone={() => {
            setVestTarget(null)
            vm.refetch()
          }}
        />
      )}

      {releaseTarget && (
        <MarkReleasedModal
          item={releaseTarget}
          onClose={() => setReleaseTarget(null)}
          onDone={() => {
            setReleaseTarget(null)
            vm.refetch()
          }}
        />
      )}
    </div>
  )
}
