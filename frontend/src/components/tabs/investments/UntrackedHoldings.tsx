// A Brokerage total account's holdings not entered yet (flag investment_value_source, ADR
// 2026-11-10 amendment 1). Invested, in no known class — so the server keeps it out of the
// book's weights, concentration, today's move and the rebalance trades, and reports it as
// `untracked_excluded`. These pieces SAY what was left out; they compute nothing.

import type { UntrackedExcluded } from '../../../types/aggregates'
import { ASSET_CLASS_COLOR } from '../../../utils/constants'
import { fBase } from '../../../utils/format'
import { Money } from '../../ui/Money'
import { untrackedWhere } from './untracked'

/** The allocation's "Not broken down" slice: its share of everything invested. The class bars
 *  above it stay shares of the broken-down holdings, which is what targets are set against. */
export function UntrackedSlice({ u }: { u: UntrackedExcluded }) {
  const pct = Math.round(u.pct_of_invested)
  return (
    <div className="mk-atl-untracked" data-testid="untracked-slice">
      <div className="mk-atl-top">
        <span className="mk-atl-dot" style={{ background: ASSET_CLASS_COLOR.untracked_holdings }} />
        <span className="mk-atl-nm">Not broken down</span>
        <Money className="mk-atl-cur">{`${pct}%`}</Money>
      </div>
      <div className="mk-atl-track">
        <div
          className="mk-atl-fill mk-atl-fill--untracked"
          style={{ width: Math.min(pct, 100) + '%' }}
        />
      </div>
      <div className="mk-atl-untracked-sub">
        <Money>{fBase(u.total)}</Money> at {untrackedWhere(u)} · counted as invested, left out of
        the shares above until {u.accounts.length === 1 ? 'its' : 'their'} holdings are added
      </div>
    </div>
  )
}

/** One line under a card: "$4,980.00 at Old 401(k) isn't broken into holdings — add holdings
 *  to rebalance it." */
export function UntrackedNote({ u, action }: { u: UntrackedExcluded; action: string }) {
  return (
    <div className="mk-untracked-note" data-testid="untracked-note">
      <Money>{fBase(u.total)}</Money> at {untrackedWhere(u)} isn&apos;t broken into holdings — add
      holdings to {action} it.
    </div>
  )
}
