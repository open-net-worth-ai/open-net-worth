/**
 * Overview.tsx — All-accounts header: title + actions + Cleared+Uncleared=Working
 * balance card + group totals bar rows + footer stats.
 *
 * Presentational. Port of prototype/accounts/main.jsx Overview + AccountHeader
 * (all-accounts variant).
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button, InfoPopover } from '../../ui'
import {
  ChevronsRight,
  Download,
  MoreHorizontal,
  Plus,
  Tags,
  Upload,
  User,
  Archive,
  Smartphone,
} from '../../../ui/icons'
import type { AccountGroup } from './types'
import { fCur } from './data'
import { ReviewStrip } from './ReviewStrip'

type OverviewMenuIcon = 'payees' | 'categories' | 'import' | 'export' | 'archive' | 'phone'

function OverviewMenuItem({
  icon,
  children,
  className,
  onClick,
}: {
  icon: OverviewMenuIcon
  children: ReactNode
  className?: string
  onClick: () => void
}) {
  const common = { size: 16, strokeWidth: 1.75, 'aria-hidden': true }
  const Icon = {
    payees: User,
    categories: Tags,
    import: Upload,
    export: Download,
    archive: Archive,
    phone: Smartphone,
  }[icon]

  return (
    <button className={className} onClick={onClick}>
      <span className="acct-menu-icon">
        <Icon {...common} />
      </span>
      <span>{children}</span>
    </button>
  )
}

// ── Prop types ────────────────────────────────────────────────────────────────

export interface OverviewTotals {
  cash: number
  fixed_deposit: number
  investment: number
  retirement: number
  receivable: number
  credit: number
  loan: number
  liabilities: number
}

/** Cash-accounts-only breakdown for the balance card (checking/savings/e-wallet — never
 *  credit cards, loans, investments, or the mortgage; see useAccountsLedger's `cashBalance`). */
export interface OverviewCashBalance {
  total: number
  cleared: number
  uncleared: number
}

export interface OverviewStats {
  count: number
  review: number
}

export interface OverviewProps {
  totals: OverviewTotals
  groups: AccountGroup[]
  /** Cash-accounts-only cleared/uncleared/total, in base currency */
  cashBalance: OverviewCashBalance
  stats: OverviewStats
  /** Base currency code, e.g. "SGD" */
  baseCurrency: string
  collapsed: boolean
  onReopen: () => void
  /** Hover on the reopen button floats the accounts rail (AccountsTab's peek). */
  onReopenHover?: () => void
  onReopenLeave?: () => void
  onAddTxn: () => void
  onAdd: () => void
  onManagePayees: () => void
  onManageCategories: () => void
  onImport: () => void
  onExport: () => void
  onReview: () => void
  onStartReview: () => void
  onShowUncleared: () => void
  onArchivedAccounts?: () => void
  archivedCount?: number
  /** Opens Settings → Phone shortcuts (log a spend from the phone the moment you pay). */
  onPhoneShortcuts?: () => void
}

// ── Overview ─────────────────────────────────────────────────────────────────

export function Overview({
  cashBalance,
  stats,
  baseCurrency,
  collapsed,
  onReopen,
  onReopenHover,
  onReopenLeave,
  onAdd,
  onManagePayees,
  onManageCategories,
  onImport,
  onExport,
  onReview,
  onStartReview,
  onShowUncleared,
  onArchivedAccounts,
  archivedCount = 0,
  onPhoneShortcuts,
}: OverviewProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const h = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  })

  // Cash on hand is the literal sum of the two cells beside it — never net worth, and never
  // the wider all-account total either (that swamped this card in the mortgage and became
  // unreadable: always a large negative, nothing a user could act on). Cash accounts only —
  // see useAccountsLedger's `cashBalance`. Written as the sum, not `cashBalance.total` (a
  // same-valued but differently-named field the hook also returns), so a stray future
  // divergence between the two can't resurrect a "doesn't add up" card.
  const cashOnHand = cashBalance.cleared + cashBalance.uncleared

  return (
    <>
      <div className="acct-head">
        <div className="acct-head-l">
          {collapsed && (
            <button
              className="acct-reopen"
              onClick={onReopen}
              onMouseEnter={onReopenHover}
              onMouseLeave={onReopenLeave}
              title="Show accounts"
            >
              <ChevronsRight size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          )}
          <div>
            <div className="acct-head-title" data-ctx-title="Accounts">
              Accounts
            </div>
          </div>
        </div>
        <div className="acct-head-actions">
          <Button
            className="acct-head-collapsible"
            size="sm"
            variant="ghost"
            onClick={onManagePayees}
          >
            Manage payees
          </Button>
          {/* Beside Manage payees, its natural sibling — both are ledger-entity admin, and both
              are used well outside the screen that happens to host them. */}
          <Button
            className="acct-head-collapsible"
            size="sm"
            variant="ghost"
            onClick={onManageCategories}
          >
            Manage categories
          </Button>
          <Button className="acct-head-collapsible" size="sm" variant="ghost" onClick={onImport}>
            Import
          </Button>
          <Button size="sm" variant="secondary" onClick={onAdd} data-tour="setup-accounts">
            <span className="acct-btn-ico">
              <Plus size={14} strokeWidth={2} aria-hidden="true" /> Add account
            </span>
          </Button>
          <div className="acct-menu-wrap" ref={menuRef}>
            <button
              className="btn-ghost-icon"
              onClick={() => setMenuOpen((o) => !o)}
              aria-label="More actions"
            >
              <MoreHorizontal size={18} strokeWidth={1.75} aria-hidden="true" />
            </button>
            {menuOpen && (
              <div className="acct-menu">
                {/* Folded in from the header row on narrow widths (see accounts.css) */}
                <OverviewMenuItem
                  icon="payees"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onManagePayees()
                  }}
                >
                  Manage payees
                </OverviewMenuItem>
                <OverviewMenuItem
                  icon="categories"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onManageCategories()
                  }}
                >
                  Manage categories
                </OverviewMenuItem>
                <OverviewMenuItem
                  icon="import"
                  className="acct-menu-collapsible"
                  onClick={() => {
                    setMenuOpen(false)
                    onImport()
                  }}
                >
                  Import
                </OverviewMenuItem>
                <OverviewMenuItem
                  icon="export"
                  onClick={() => {
                    setMenuOpen(false)
                    onExport()
                  }}
                >
                  Export transactions
                </OverviewMenuItem>
                <OverviewMenuItem
                  icon="archive"
                  onClick={() => {
                    setMenuOpen(false)
                    onArchivedAccounts?.()
                  }}
                >
                  Archived accounts{archivedCount ? ` (${archivedCount})` : ''}
                </OverviewMenuItem>
                {onPhoneShortcuts ? (
                  <OverviewMenuItem
                    icon="phone"
                    onClick={() => {
                      setMenuOpen(false)
                      onPhoneShortcuts()
                    }}
                  >
                    Phone shortcuts
                  </OverviewMenuItem>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Balance card — cash accounts only (checking/savings/e-wallet); see cashBalance's own
          doc comment for why credit cards, loans and investments are deliberately excluded. */}
      <div className="acct-bal">
        <div className="acct-bal-card">
          <div className="acct-bal-k">Cash</div>
          <div className="acct-bal-breakdown">
            <div className="acct-bal-part">
              <div className={'acct-bal-fig mask' + (cashBalance.cleared > 0 ? ' pos' : '')}>
                {fCur(cashBalance.cleared, baseCurrency, { cents: true })}
              </div>
              <div className="acct-bal-lbl">
                Cleared <InfoPopover text="Posted — finalized; the amount won't change." />
              </div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">+</span>
              <div className="acct-bal-part">
                <div className={'acct-bal-fig mask' + (cashBalance.uncleared > 0 ? ' pos' : '')}>
                  {fCur(cashBalance.uncleared, baseCurrency, { cents: true })}
                </div>
                <div className="acct-bal-lbl">
                  <button
                    type="button"
                    className="acct-bal-lbl-btn"
                    onClick={onShowUncleared}
                    title="Show uncleared transactions"
                  >
                    Uncleared
                  </button>{' '}
                  <InfoPopover text="Pending — a temporary hold; the amount can still change." />
                </div>
              </div>
            </div>
            <div className="acct-bal-grp">
              <span className="acct-bal-op">=</span>
              <div className="acct-bal-part">
                <div className={'acct-bal-fig work mask' + (cashOnHand > 0 ? ' pos' : '')}>
                  {fCur(cashOnHand, baseCurrency, { cents: true })}
                </div>
                <div className="acct-bal-lbl">
                  Cash on hand{' '}
                  <InfoPopover width={260} text="Bank and e-wallet accounts, cleared + uncleared" />
                </div>
              </div>
            </div>
          </div>

          {/* Always rendered, even at 0 txns, so the card's height doesn't shift. */}
          <div className="acct-recon-line">
            <span className="acct-recon-grow" />
            <span className="acct-recon-group acct-recon-group--review">
              <ReviewStrip count={stats.review} onReview={onReview} onStartReview={onStartReview} />
              <span className="acct-recon-stat muted">{stats.count} txns</span>
            </span>
          </div>
        </div>
      </div>
    </>
  )
}
