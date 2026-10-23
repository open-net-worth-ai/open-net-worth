// Funding tab — ported from detail3.jsx's `Funding`/`FundRow`/`TargetLink`/`AddSourceForm`/
// `computeCoverage`. `computeCoverage` is also used by `detailOverview.tsx`'s condensed digest
// so the two can never disagree (same rule as the design handoff).
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { Commitment, FundingSource, FundingType } from '../../../types/commitments'
import { fBase, getCurrencySymbol, toBase } from '../../../utils/format'
import {
  FUNDING_LABELS,
  fmtDate,
  installmentFunding,
  isEnvelopeSource,
  isTracker,
  remainingBase,
  type EnvelopeAccrual,
} from '../../../utils/commitments'
import { AssetLink } from '../../intel/AssetLink'
import { FilterIcon, PencilIcon, SyncIcon } from './icons'
import { EditRow, type EditDraft } from './editors'
import { FundingCoverageBar } from './FundingCoverageBar'
import { EntityRow } from '../../../lucius/EntityRow'
import type { DetailMutations } from './detailTypes'
import type { PositionMeta } from './fundingValue'
import type { EnvelopeAccrualState } from './fundingValue'
import { useNarrow } from './useNarrow'
import { ArrowRight } from '../../../ui/icons'

const TYPE_ORDER = ['income', 'cash', 'loan', 'asset_sale', 'windfall']

export interface CoverageBarSource {
  id: string
  label: string
  base: number
}

export interface CoverageBar {
  remaining: number
  gap: number
  scaleMax: number
  segs: Array<{
    type: string
    pct: number
    val: number
    label: string
    sources: CoverageBarSource[]
  }>
}

/** Shared coverage math for the funding bar (Funding tab + Overview digest) — Σ what the
 *  per-installment waterfall actually covers vs. what's left, segmented by funding type. */
export function computeCoverage(
  c: Commitment,
  fx: Record<string, number>,
  envelope: EnvelopeAccrual | null,
): CoverageBar {
  const remaining = remainingBase(c, fx)
  const funding = installmentFunding(c, (amt) => toBase(amt, c.currency, fx), envelope)
  const byType: Record<string, number> = {}
  const bySourceByType: Record<string, Record<string, CoverageBarSource>> = {}
  let coveredTotal = 0
  for (const { alloc, covered } of Object.values(funding.byInst)) {
    coveredTotal += covered
    for (const a of alloc) {
      byType[a.type] = (byType[a.type] || 0) + a.base
      const bySource = (bySourceByType[a.type] ??= {})
      const existing = bySource[a.id]
      if (existing) existing.base += a.base
      else bySource[a.id] = { id: a.id, label: a.label, base: a.base }
    }
  }
  const gap = Math.max(0, remaining - coveredTotal)
  const scaleMax = Math.max(remaining, 1)
  const segs = Object.entries(byType)
    .filter(([, val]) => val > 0.5)
    .sort((a, b) => TYPE_ORDER.indexOf(a[0]) - TYPE_ORDER.indexOf(b[0]))
    .map(([type, val]) => ({
      type,
      pct: (val / scaleMax) * 100,
      val,
      label: FUNDING_LABELS[type] || type,
      sources: Object.values(bySourceByType[type] || {}).sort((a, b) => b.base - a.base),
    }))
  return { remaining, gap, scaleMax, segs }
}

function TargetLink({ t, onOpen }: { t: EnvelopeAccrualState; onOpen: () => void }) {
  const under = t.target_this_month > 0 && t.per_period < t.target_this_month
  const pct =
    t.target_this_month > 0
      ? Math.min(100, Math.round((t.per_period / t.target_this_month) * 100))
      : 100
  return (
    <div className="c2-target">
      <div className="c2-target-top">
        <span className="c2-synced">
          <SyncIcon /> Synced target
        </span>
        <span className="c2-target-lbl">
          Funds <b>{t.envelope}</b> • <span className="money">{fBase(t.accumulated)}</span> saved so
          far
        </span>
        <button className="c2-link" onClick={onOpen}>
          Manage in Budget <ArrowRight size={13} aria-hidden="true" />
        </button>
      </div>
      {t.target_this_month > 0 && (
        <div className="c2-target-prog">
          <span className="c2-target-meter">
            <i className={under ? 'partial' : ''} style={{ width: pct + '%' }} />
          </span>
          <span className="c2-target-figs">
            {under ? (
              <span className="under money">{fBase(t.per_period)}</span>
            ) : (
              <b className="money">{fBase(t.per_period)}</b>
            )}
            {' / '}
            <span className="money">{fBase(t.target_this_month)}</span> this month
          </span>
        </div>
      )}
    </div>
  )
}

export function fundingWhen(f: FundingSource, pos: PositionMeta | undefined): string {
  if (f.type === 'income') return `${fBase(f.value_base ?? 0)} /mo`
  if (f.type === 'asset_sale' && f.available_date) return `avail ${fmtDate(f.available_date)}`
  if (f.type === 'asset_sale' && f.ref_position_id) {
    return pos
      ? `${f.sell_quantity ?? 0} × ${getCurrencySymbol(pos.priceCurrency)}${pos.price.toFixed(2)} live`
      : 'asset sale'
  }
  if (f.type === 'asset_sale')
    return f.sell_quantity != null ? `${f.sell_quantity} units` : 'asset sale'
  return f.available_date ? `avail ${fmtDate(f.available_date)}` : 'available now'
}

interface FundRowProps {
  c: Commitment
  f: FundingSource
  pos: PositionMeta | undefined
  m: DetailMutations
}

function FundRow({ c, f, pos, m }: FundRowProps) {
  const [draft, setDraft] = useState<EditDraft | null>(null)
  const isAssetSale = f.type === 'asset_sale'

  if (draft) {
    return (
      <div className="c2-fund-row">
        <span className={'c2-fund-dot ' + f.type} />
        <div style={{ gridColumn: '2 / 5' }}>
          <EditRow
            draft={draft}
            setDraft={setDraft}
            descKey="note"
            descPlaceholder="Note (optional)"
            onSave={() => {
              const amountField = isAssetSale
                ? { sell_quantity: Number(draft.amount) || 0 }
                : { amount: Number(draft.amount) || 0 }
              void m.updateFunding(f.id, { label: draft.label, note: draft.note, ...amountField })
              setDraft(null)
            }}
            onCancel={() => setDraft(null)}
            onDelete={() => {
              void m.removeFunding(f.id)
              setDraft(null)
            }}
          />
        </div>
      </div>
    )
  }

  return (
    <EntityRow
      as="div"
      className={'c2-fund-row' + (f.disabled ? ' c3-fund-disabled' : '')}
      type="funding_source"
      id={f.id}
      label={f.label || f.type.replace('_', ' ')}
      item={f}
      toFields={(it) => ({
        label: it.label,
        commitment_name: c.name,
        type: it.type,
        amount: it.amount,
        currency: it.currency,
        available_date: it.available_date,
        cadence: it.cadence,
        disabled: it.disabled ? 'yes' : 'no',
        note: it.note,
      })}
      search={`${f.label} ${c.name} ${f.type} ${f.note}`}
      sublabel={`${f.type.replace('_', ' ')} • ${c.name}`}
    >
      <span className={'c2-fund-dot ' + f.type} />
      <div className="c2-fund-mid">
        <div className="c2-fund-name">
          <span className="c2-fund-name-text">{f.label}</span>
          <button
            className="c2-iconbtn"
            aria-label={'Edit ' + f.label}
            onClick={() =>
              setDraft({
                label: f.label,
                amount: isAssetSale ? (f.sell_quantity ?? 0) : (f.amount ?? 0),
                note: f.note || '',
              })
            }
          >
            <PencilIcon />
          </button>
        </div>
        <div className="c2-fund-type">
          <div className="c2-fund-tagline">
            <span
              className={'c3-fund-tag ' + (isEnvelopeSource(f) ? 'env' : f.type)}
              title={
                isEnvelopeSource(f)
                  ? 'A monthly set-aside that builds up in Budget — payments draw only what it has saved'
                  : 'A specific, dated funding event you set up yourself — not auto-tracked by a synced Budget target'
              }
            >
              {isEnvelopeSource(f) ? 'Budget target' : FUNDING_LABELS[f.type] || f.type}
            </span>
            <span className="c2-fund-when c2-fund-when-inline">{fundingWhen(f, pos)}</span>
          </div>
          {(f.note ||
            f.disabled ||
            (isAssetSale && f.ref_position_id && pos?.instrumentId) ||
            f.type === 'loan') && (
            <div className="c2-fund-note">
              {f.note}
              {f.disabled && (
                <>
                  {f.note ? ' • ' : ''}
                  <b className="c3-fund-lost">removed — not happening</b>
                </>
              )}
              {isAssetSale && f.ref_position_id && pos?.instrumentId && (
                <>
                  {' '}
                  •{' '}
                  <AssetLink
                    instrumentId={pos.instrumentId}
                    ticker={pos.symbol}
                    className="c2-link"
                  >
                    {pos.symbol || 'position'} <ArrowRight size={13} aria-hidden="true" />
                  </AssetLink>
                </>
              )}
              {f.type === 'loan' && f.available_date && !f.repayment_commitment_id && (
                <>
                  {' '}
                  •{' '}
                  <button
                    className="c2-link"
                    onClick={() =>
                      m.onAskLucius(
                        `Help me set up a loan repayment commitment for the "${f.label}" funding source on my "${c.name}" commitment — ${f.value_base ? fBase(f.value_base) + ' ' : ''}available ${f.available_date ? fmtDate(f.available_date) : 'soon'}.`,
                      )
                    }
                  >
                    set up repayment plan <ArrowRight size={13} aria-hidden="true" />
                  </button>
                </>
              )}
              {f.type === 'loan' && f.repayment_commitment_id && (
                <>
                  {' '}
                  •{' '}
                  <button
                    className="c2-link"
                    onClick={() => m.onOpenCommitment(f.repayment_commitment_id as string)}
                  >
                    repayment plan <ArrowRight size={13} aria-hidden="true" />
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
      <span className="c2-fund-when c2-fund-when-standalone">{fundingWhen(f, pos)}</span>
      <span className={'c2-fund-amt money' + (f.disabled ? ' c3-fund-lost-amt' : '')}>
        {/* Was `f.type === 'income'`, which is TRUE for both the one live-synced envelope
            (isEnvelopeSource — genuinely no fixed number; it accrues dynamically in Planning,
            "recurring" is the honest answer) AND every manually-dated set-aside entry, which
            DOES have a real, concrete f.value_base (hydrateFundingPlan always computes one,
            and consecutive months can genuinely differ — e.g. Aug $4,000 vs Oct $3,000 once a
            loan repayment starts eating into free cash flow). The broad condition was hiding
            that real, varying number behind the same vague word for every income-type row,
            the one place on this row where the reader could otherwise tell two months apart. */}
        {isEnvelopeSource(f) ? 'recurring' : f.value_base != null ? fBase(f.value_base) : '—'}
      </span>
    </EntityRow>
  )
}

interface FundingProps {
  commitment: Commitment
  fx: Record<string, number>
  envelope: EnvelopeAccrualState | null
  positions: Record<string, PositionMeta>
  m: DetailMutations
}

export function Funding({ commitment: c, fx, envelope, positions, m }: FundingProps) {
  const navigate = useNavigate()
  const narrow = useNarrow()
  const [typeFilter, setTypeFilter] = useState<FundingType | 'all'>('all')
  const tracker = isTracker(c)
  const { remaining, gap, scaleMax, segs } = computeCoverage(c, fx, envelope)

  const typeCounts: Partial<Record<FundingType, number>> = {}
  for (const f of c.funding_plan || []) typeCounts[f.type] = (typeCounts[f.type] || 0) + 1
  const presentTypes = (
    ['income', 'cash', 'loan', 'asset_sale', 'windfall'] as FundingType[]
  ).filter((t) => typeCounts[t])
  const filteredPlan = (c.funding_plan || []).filter(
    (f) => typeFilter === 'all' || f.type === typeFilter,
  )

  return (
    <div className="c2-exp-sec">
      <div className="c2-exp-sec-h">
        <span className="c2-exp-sec-t">How it's funded</span>
        {narrow ? (
          presentTypes.length > 1 && (
            <span className={'c2-fund-typefilter-btn' + (typeFilter !== 'all' ? ' active' : '')}>
              <FilterIcon />
              <select
                className="c2-fund-typefilter-select"
                aria-label="Filter by funding type"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as FundingType | 'all')}
              >
                <option value="all">All ({c.funding_plan.length})</option>
                {presentTypes.map((t) => (
                  <option key={t} value={t}>
                    {FUNDING_LABELS[t] || t} ({typeCounts[t]})
                  </option>
                ))}
              </select>
            </span>
          )
        ) : (
          <span className="c2-exp-sec-note c2-fund-headnote">
            {!tracker &&
              (gap > 0 ? (
                <span className="c2-exp-sec-note bad">
                  <span className="money">{fBase(gap)}</span> unfunded •{' '}
                </span>
              ) : (
                <span className="c2-exp-sec-note good">Fully funded • </span>
              ))}
            <button
              className="c2-link lucius"
              onClick={() =>
                m.onAskLucius(`Help me add a funding source to my "${c.name}" commitment's plan.`)
              }
            >
              Manage with Lucius <ArrowRight size={13} aria-hidden="true" />
            </button>
          </span>
        )}
      </div>

      {c.funding_plan && c.funding_plan.length > 0 ? (
        <>
          {!tracker && (
            <FundingCoverageBar
              segs={segs}
              gap={gap}
              scaleMax={scaleMax}
              remaining={remaining}
              fundingPlan={c.funding_plan}
              positions={positions}
            />
          )}
          {envelope ? (
            <TargetLink
              t={envelope}
              onOpen={() => void navigate({ to: '/plan', search: { sub: 'budget' } } as never)}
            />
          ) : c.envelope_id ? (
            // A real FK, but the live figures didn't resolve (still loading, or the row wasn't
            // found) — the link is honest to show; the numbers aren't, so they're omitted.
            <div className="c2-target">
              <div className="c2-target-top">
                <span className="c2-synced">
                  <SyncIcon /> Synced target
                </span>
                <button
                  className="c2-link"
                  onClick={() => void navigate({ to: '/plan', search: { sub: 'budget' } } as never)}
                >
                  Manage in Budget <ArrowRight size={13} aria-hidden="true" />
                </button>
              </div>
            </div>
          ) : null}
          {presentTypes.length > 1 && !narrow ? (
            <div className="c2-fund-typefilter">
              <button
                className={'c2-mini-btn' + (typeFilter === 'all' ? ' pri' : '')}
                onClick={() => setTypeFilter('all')}
              >
                All ({c.funding_plan.length})
              </button>
              {presentTypes.map((t) => (
                <button
                  key={t}
                  className={'c2-mini-btn' + (typeFilter === t ? ' pri' : '')}
                  onClick={() => setTypeFilter(t)}
                >
                  {FUNDING_LABELS[t] || t} ({typeCounts[t]})
                </button>
              ))}
            </div>
          ) : null}
          <div className="c2-fund-list">
            {filteredPlan.map((f) => (
              <FundRow
                key={f.id}
                c={c}
                f={f}
                pos={f.ref_position_id ? positions[f.ref_position_id] : undefined}
                m={m}
              />
            ))}
          </div>
        </>
      ) : (
        <div className="c2-fund-empty">
          No funding sources yet — ask Lucius how you'll cover these installments (cash, an asset
          sale, or a loan).
        </div>
      )}
    </div>
  )
}
