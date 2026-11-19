/**
 * The account card, its footer and its notices for an investment or retirement account valued
 * by Brokerage total or Live holdings (ADR 2026-11-10). Every figure is read from the server —
 * the account's holdings, its balance and what a linked connector reported.
 */
import type { ReactNode } from 'react'
import { Button, InfoPopover } from '../../ui'
import { fCur } from './format'
import { getBaseCurrency } from '../../../utils/format'
import type { Account } from './types'
import {
  cardFigures,
  isSyncing,
  isWaiting,
  shortDate,
  smallRemainder,
  valueDate,
} from './valuation'
import type { Valuation } from './valuation'

function money(n: number, ccy: string) {
  return fCur(n, ccy, { cents: true })
}

function institution(account: Account) {
  return account.institution || 'Your brokerage'
}

function footerText(account: Account, v: Valuation): { text: string; warn: boolean } {
  const when = shortDate(valueDate(account))
  if (!isSyncing(v)) {
    if (v.sync?.total != null && v.sync.status !== 'kept')
      return { text: `Last total from ${v.provider} · ${when}`, warn: true }
    return { text: `Value you entered${when ? ` · ${when}` : ''}`, warn: false }
  }
  const status = v.sync?.status
  if (status === 'waiting') return { text: `Waiting for ${v.provider}’s first total`, warn: false }
  if (status === 'kept') return { text: `Value you kept${when ? ` · ${when}` : ''}`, warn: false }
  const warn = status !== 'fresh'
  // No total applied yet (the first one was held back, or couldn't be read): nothing synced.
  if (v.sync?.total == null) return { text: `Value you entered${when ? ` · ${when}` : ''}`, warn }
  return { text: `Value synced via ${v.provider}${when ? ` · last changed ${when}` : ''}`, warn }
}

export function AccountValueFooter({ account }: { account: Account }) {
  const v = account.valuation as Valuation
  const f = footerText(account, v)
  return (
    <span className="acct-recon-stat acct-src-stat" role="status">
      <span className={'acct-src-dot' + (f.warn ? ' acct-src-dot--warn' : '')} aria-hidden="true" />
      {f.text}
    </span>
  )
}

interface CardProps {
  account: Account
  onAddHolding: () => void
  onSwitch: () => void
}

/** The Account value card. A Brokerage total account with no holdings shows its total alone;
 *  once it holds something, the equation tracked + untracked = total. */
export function AccountValueCard({ account, onAddHolding, onSwitch }: CardProps) {
  const v = account.valuation as Valuation
  const byTotal = v.source === 'total'
  const f = cardFigures(account)
  const waiting = isWaiting(v)
  const ccy = f.ccy

  let side: ReactNode = null
  if (byTotal && !waiting && smallRemainder(account)) {
    side = (
      <div className="acct-src-side">
        <span>
          Only <b className="mask">{money(f.untracked, ccy)}</b> is untracked. Switch to live
          holdings?
        </span>
        <Button size="sm" variant="secondary" onClick={onSwitch}>
          Switch
        </Button>
      </div>
    )
  } else if (byTotal && !waiting) {
    side = (
      <div className="acct-src-side">
        {f.tracked === 0 && <span>Add holdings for live prices, gains and allocation</span>}
        <Button size="sm" variant="secondary" onClick={onAddHolding}>
          Add holding
        </Button>
      </div>
    )
  }

  return (
    <>
      <div className="acct-bal-k">
        Account value
        {byTotal && (
          <InfoPopover
            width={300}
            label="What these figures mean"
            text={
              <span className="acct-bal-terms">
                <span>
                  <b>Total value</b> — the account’s total,{' '}
                  {isSyncing(v) ? `synced via ${v.provider}` : 'as you entered it'}.
                </span>
                {f.tracked > 0 && (
                  <>
                    <span>
                      <b>Tracked holdings</b> — the holdings you’ve added, at market prices.
                    </span>
                    <span>
                      <b>Cash and untracked holdings</b> — the rest of the total.
                    </span>
                    {isSyncing(v) && (
                      <span>
                        Tracked holdings move with the market between syncs, so the total can differ
                        slightly from {v.provider}’s figure until the next sync.
                      </span>
                    )}
                  </>
                )}
              </span>
            }
          />
        )}
      </div>
      <div className="acct-src-top">
        {waiting ? (
          <div className="acct-bal-fig work acct-src-one acct-src-wait">—</div>
        ) : f.tracked > 0 || !byTotal ? (
          <div className="acct-bal-breakdown">
            <div className="acct-bal-part">
              <div className="acct-bal-fig mask">{money(f.tracked, ccy)}</div>
              <div className="acct-bal-lbl">{byTotal ? 'Tracked holdings' : 'Holdings'}</div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">+</span>
              <div className="acct-bal-part">
                <div className={'acct-bal-fig mask' + (f.untracked < 0 ? ' neg' : '')}>
                  {money(f.untracked, ccy)}
                </div>
                <div className="acct-bal-lbl">
                  {byTotal ? 'Cash and untracked holdings' : 'Cash'}
                </div>
              </div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">=</span>
              <div className="acct-bal-part">
                <div className="acct-bal-fig work mask">{money(f.total, ccy)}</div>
                <div className="acct-bal-lbl">Total value</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="acct-src-one">
            <div className="acct-bal-fig work mask">{money(f.total, ccy)}</div>
            <div className="acct-bal-lbl">Total value</div>
          </div>
        )}
        {side}
      </div>
    </>
  )
}

/** Holdings are priced from market data and the brokerage's total from its own last update, so
 *  they never match exactly. Only a gap this large (likely a missing holding) is worth a word. */
const LARGE_GAP = 0.1

interface NoticeProps {
  account: Account
  onOpenConnector: () => void
  onEdit: () => void
  onReviewHoldings: () => void
  onConfirmHeld: (accept: boolean) => void
}

export function AccountValueNotice(props: NoticeProps) {
  const found = noticeFor(props)
  if (!found) return null
  return (
    <div className="acct-alert" role="status">
      <span className="mark">●</span>
      <span className="grow">{found.body}</span>
      {found.actions && <span className="acct-alert-actions">{found.actions}</span>}
    </div>
  )
}

function noticeFor({
  account,
  onOpenConnector,
  onEdit,
  onReviewHoldings,
  onConfirmHeld,
}: NoticeProps): { body: ReactNode; actions?: ReactNode } | null {
  const v = account.valuation as Valuation
  const s = v.sync
  const ccy = account.currency
  const name = institution(account)
  const f = cardFigures(account)
  const keep = money(v.totalNative ?? f.total, v.totalNative != null ? ccy : f.ccy)
  const when = shortDate(valueDate(account))
  const open = (
    <Button size="sm" variant="secondary" onClick={onOpenConnector}>
      Open {v.provider}
    </Button>
  )
  if (v.source === 'total' && s && v.linkStatus !== 'connected') {
    const paused = v.linkStatus === 'paused'
    return {
      body: (
        <>
          {v.provider} is {paused ? 'paused' : 'removed'}, so this account isn’t updating. It keeps{' '}
          <b className="mask">{keep}</b>. Reconcile to update it yourself.
        </>
      ),
      actions: open,
    }
  }
  if (v.source === 'total' && s?.status === 'held_back' && s.held_total != null) {
    const held = money(s.held_total, ccy)
    return {
      body: (
        <>
          {name} reported <b className="mask">{held}</b>. That looks wrong, so it wasn’t used. Is it
          real?
        </>
      ),
      actions: (
        <>
          <Button size="sm" variant="primary" onClick={() => onConfirmHeld(false)}>
            Keep {keep}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onConfirmHeld(true)}>
            Use {held}
          </Button>
        </>
      ),
    }
  }
  if (v.source === 'total' && s?.status === 'reconnect') {
    return {
      body: (
        <>
          Your sign-in to {name} has expired. Reconnect to keep this account up to date. Until then
          it keeps <b className="mask">{keep}</b>
          {when ? ` from ${when}` : ''}.
        </>
      ),
      actions: (
        <Button size="sm" variant="secondary" onClick={onOpenConnector}>
          Reconnect
        </Button>
      ),
    }
  }
  if (v.source === 'total' && s?.status === 'currency_mismatch') {
    return {
      body: (
        <>
          {name} reports this account in {s.reported_currency || 'another currency'}, but it’s set
          up in {ccy}. Nothing was booked, so the value hasn’t changed.
        </>
      ),
      actions: (
        <Button size="sm" variant="secondary" onClick={onEdit}>
          Edit account
        </Button>
      ),
    }
  }
  if (v.source === 'total' && s && ['error', 'no_balance', 'not_found'].includes(s.status)) {
    const why =
      s.status === 'not_found'
        ? `${v.provider} no longer lists this account`
        : s.status === 'no_balance'
          ? `${v.provider} didn’t report a total for this account`
          : `${v.provider} couldn’t be read`
    return {
      body: (
        <>
          {why}, so it keeps <b className="mask">{keep}</b>. It will try again.
        </>
      ),
      actions: open,
    }
  }
  if (v.source === 'total' && s?.status === 'stale') {
    return {
      body: (
        <>
          {name}’s total hasn’t changed since {when}, so this value may be out of date.
        </>
      ),
      actions: open,
    }
  }
  if (
    v.source === 'holdings' &&
    s?.status === 'compare' &&
    s.reported_total != null &&
    v.siblings.length === 0 &&
    ccy === getBaseCurrency() // the reported figure is native; the holdings are in base
  ) {
    const mine = account.holdingsBase + account.balanceBase
    if (Math.abs(s.reported_total - mine) > Math.abs(s.reported_total) * LARGE_GAP) {
      return {
        body: (
          <>
            {name} reports <b className="mask">{money(s.reported_total, ccy)}</b>, but your holdings
            and cash add up to <b className="mask">{money(mine, f.ccy)}</b>. You may be{' '}
            {s.reported_total > mine ? 'missing a holding' : 'counting a holding twice'}.
          </>
        ),
        actions: (
          <Button size="sm" variant="secondary" onClick={onReviewHoldings}>
            Review holdings
          </Button>
        ),
      }
    }
  }
  if (v.source === 'total' && f.tracked > 0 && f.untracked < 0) {
    return {
      body: (
        <>
          Your holdings add up to <b className="mask">{money(f.tracked, f.ccy)}</b>,{' '}
          <b className="mask">{money(-f.untracked, f.ccy)}</b> more than the total of{' '}
          <b className="mask">{money(f.total, f.ccy)}</b>. Check for a typo, or the total may be out
          of date.
        </>
      ),
      actions: (
        <Button size="sm" variant="secondary" onClick={onReviewHoldings}>
          Review holdings
        </Button>
      ),
    }
  }
  return null
}
