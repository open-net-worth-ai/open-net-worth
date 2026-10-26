/**
 * Sidebar.tsx — Accounts tab sidebar: net-worth header + grouped accounts +
 * venue nesting + resize handle + collapse button + affordances.
 *
 * Presentational — receives data and callbacks as props from AccountsTab.
 * Port of prototype/accounts/app.jsx Sidebar + AccountRow.
 */

import { useState } from 'react'
import type { PointerEventHandler } from 'react'
import { fBaseCompact, getBaseCurrency } from '../../../utils/format'
import { EntityRow } from '../../../lucius/EntityRow'
import type { Account, AccountGroup } from './types'
import { accountValue, fBase, fCur } from './data'
import { TYPE_ORDER, typeLabel } from './categories'
import { ChevronRight } from '../../../ui/icons'
import { countNeedsReconciling, needReconcilingLabel, needsReconciling } from './reconcileDue'

// ── Local icon helpers (SVG, pixel-centered) ─────────────────────────────────

function IconPlus({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

function IconChevrons({ dir = 'left', size = 13 }: { dir?: 'left' | 'right'; size?: number }) {
  const d = dir === 'left' ? 'M17 6l-6 6 6 6M11 6l-6 6 6 6' : 'M7 6l6 6-6 6M13 6l6 6-6 6'
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  )
}

// ── Prop types ───────────────────────────────────────────────────────────────

export interface SidebarTotals {
  cash: number
  fixed_deposit: number
  investment: number
  retirement: number
  receivable: number
  credit: number
  loan: number
  liabilities: number
}

export interface SidebarProps {
  accounts: Account[]
  groups: AccountGroup[]
  /** Raw sidebar-group totals (base currency) — used only for `liab` (Credit + Loans) here;
   *  `assets` in the net-worth split bar is derived from `netWorth` instead, never re-summed
   *  from these groups (owned assets aren't a group). */
  totals: SidebarTotals
  selectedId: string | null
  netWorth: number
  sheet?: boolean
  /** Rendered as the floating hover peek over a collapsed rail: no resize handle, and the
   *  corner button pins the rail open instead of collapsing it. */
  peek?: boolean
  collapsed: boolean
  onToggleCollapse?: () => void
  onPointerDown?: PointerEventHandler<HTMLDivElement>
  onSelect: (id: string | null) => void
  onAdd: () => void
  onManagePayees: () => void
}

// ── AccountRow ───────────────────────────────────────────────────────────────

interface AccountRowProps {
  a: Account
  sel: boolean
  onSelect: (id: string) => void
}

function AccountRow({ a, sel, onSelect }: AccountRowProps) {
  const isSec = a.role === 'securities'
  const neg = a.balance < 0
  const base = getBaseCurrency()
  // A securities account reads in BASE, because `accountValue` sums two base figures
  // (positions + settlement cash); a plain account reads in its own currency.
  const value = accountValue(a)
  const due = needsReconciling(a)
  const balanceStr = isSec
    ? fBase(value, { cents: true })
    : `${fCur(a.balance, a.currency, { cents: true })} ${a.currency}`

  return (
    <EntityRow
      as="div"
      type="account"
      id={a.id}
      label={a.name}
      search={[a.name, a.institution, typeLabel(a.type)].filter(Boolean).join(' ')}
      item={a}
      toFields={(it) => ({
        name: it.name,
        kind: it.kind,
        type: typeLabel(it.type),
        balance: balanceStr,
        custodian: it.institution || null,
        currency: it.currency !== base ? it.currency : null,
      })}
      className={'acct-row acct-row--leaf' + (sel ? ' sel' : '')}
      onClick={() => onSelect(a.id)}
    >
      <span className="acct-row-id">
        <span className="acct-row-name">{a.name}</span>
        {/* No type: the group heading above already says it. */}
        {(a.lastFour || due) && (
          <span className="acct-row-sub">
            {a.lastFour && '•••' + a.lastFour}
            {a.lastFour && due && ' · '}
            {due && <span className="acct-due">Needs reconciling</span>}
          </span>
        )}
      </span>
      <span className="acct-row-v">
        {isSec ? (
          <span className={'acct-row-bal hold mask' + (value < 0 ? ' neg' : '')}>
            <span className="marker">◈</span>
            {fBaseCompact(value)}
          </span>
        ) : (
          <span className={'acct-row-bal mask' + (neg ? ' neg' : '')}>
            {fCur(a.balance, a.currency, { cents: true })}
          </span>
        )}
        {/* "value", not "holdings": the figure is positions PLUS settlement cash. */}
        <span className="acct-row-cur">{isSec ? 'value' : a.currency}</span>
      </span>
    </EntityRow>
  )
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export function Sidebar({
  accounts,
  groups,
  totals,
  selectedId,
  netWorth,
  sheet = false,
  peek = false,
  collapsed,
  onToggleCollapse,
  onPointerDown,
  onSelect,
  onAdd,
  onManagePayees,
}: SidebarProps) {
  const [openVenues, setOpenVenues] = useState<Record<string, boolean>>({})

  // `assets` is deliberately DEFINED from `netWorth` (assets − debts = net worth, rearranged),
  // never independently summed from `totals` — that re-sum is exactly the account-groups-only
  // total the Accounts-tab balance card shows, and it silently excludes owned assets (Assets
  // tab), since an owned asset isn't an account and contributes to no group. Anchoring on the
  // authoritative net worth instead makes "Assets − Debts = Net worth" true by construction,
  // with no separate owned-assets fetch to keep in sync.
  const liab = Math.abs(totals.liabilities)
  const assets = netWorth + liab
  const denom = assets + liab || 1

  // Build a lookup of accounts per group key for venue grouping
  const groupMap: Record<string, Account[]> = {}
  for (const g of groups) groupMap[g.key] = g.accounts

  // "All accounts" is the sum of the rows beneath it — NOT net worth, which also counts owned
  // assets (Assets tab) that are never accounts.
  const accountsTotal = TYPE_ORDER.reduce(
    (s, g) => s + (groupMap[g] ?? []).reduce((t, a) => t + accountValue(a), 0),
    0,
  )
  const listed = TYPE_ORDER.flatMap((g) => groupMap[g] ?? [])
  const accountsCount = listed.length
  const dueCount = countNeedsReconciling(listed)

  return (
    <aside
      className={
        'acct-side' +
        (sheet ? ' is-sheet' : '') +
        (peek ? ' is-peek' : '') +
        (collapsed ? ' is-collapsed' : '')
      }
    >
      {!sheet && (
        <>
          {!peek && (
            <div
              className="acct-side-resize"
              onPointerDown={onPointerDown}
              title="Drag to resize"
            />
          )}
          <button
            className="acct-side-collapse"
            onClick={onToggleCollapse}
            title={peek ? 'Keep accounts open' : 'Collapse panel'}
            aria-label={peek ? 'Keep accounts open' : 'Collapse panel'}
          >
            <IconChevrons dir={peek ? 'right' : 'left'} />
          </button>
        </>
      )}

      {/* net-worth header — also the singleton net_worth mention/drag target */}
      <EntityRow
        as="div"
        type="net_worth"
        id=""
        label="Net worth"
        search="net worth assets liabilities"
        item={{ netWorth, assets, liab }}
        toFields={(it) => ({
          net_worth: fBase(it.netWorth, { cents: true }),
          assets: fBase(it.assets, { cents: true }),
          liabilities: fBase(it.liab, { cents: true }),
        })}
        className="acct-side-nw"
      >
        <div className="acct-side-nw-k">Net worth</div>
        <div className={'acct-side-nw-v mask l' + (netWorth < 0 ? ' neg' : '')}>
          {fBase(netWorth, { cents: true })}
        </div>
        <div className="acct-side-nw-bar">
          <i style={{ width: (assets / denom) * 100 + '%', background: 'var(--good)' }} />
          <i style={{ width: (liab / denom) * 100 + '%', background: 'var(--bad)' }} />
        </div>
        <div className="acct-side-nw-legend">
          <span>
            <i style={{ background: 'var(--good)' }} />
            Assets <b className="mask">{fBaseCompact(assets)}</b>
          </span>
          <span>
            <i style={{ background: 'var(--bad)' }} />
            Debts <b className="mask">{fBaseCompact(liab)}</b>
          </span>
        </div>
      </EntityRow>

      {/* scrollable account list — the Lucius "nav" screen-context region */}
      <div className="acct-side-scroll" data-ctx-region="nav" data-ctx-region-label="Accounts">
        {/* "All accounts" row */}
        <div
          className={'acct-all' + (selectedId === null ? ' sel' : '')}
          onClick={() => onSelect(null)}
          title="The accounts below added together, debts included. Net worth also counts what you own on the Assets page, so the two differ."
        >
          <span className="acct-all-name">
            <span className="acct-all-id">
              All accounts
              <span className="acct-all-sub">
                {accountsCount} {accountsCount === 1 ? 'account' : 'accounts'}
              </span>
              {/* Its own line: beside the count it ellipsized in a default-width sidebar. */}
              {dueCount > 0 && (
                <span className="acct-all-sub acct-due">{needReconcilingLabel(dueCount)}</span>
              )}
            </span>
          </span>
          <span className={'acct-all-v mask' + (accountsTotal < 0 ? ' neg' : '')}>
            {fBaseCompact(accountsTotal)}
          </span>
        </div>

        {TYPE_ORDER.map((g) => {
          const items = groupMap[g] ?? []
          if (!items.length) return null

          const total = items.reduce((s, a) => s + accountValue(a), 0)

          // Venue grouping: ≥2 accounts sharing the same institution
          const byVenue: Record<string, Account[]> = {}
          const flat: Account[] = []
          items.forEach((a) => {
            if (a.institution) {
              ;(byVenue[a.institution] ??= []).push(a)
            } else {
              flat.push(a)
            }
          })
          const venues: [string, Account[]][] = []
          Object.entries(byVenue).forEach(([p, arr]) => {
            if (arr.length >= 2) venues.push([p, arr])
            else flat.push(...arr)
          })

          return (
            <div className="acct-grp" key={g}>
              <div className="acct-grp-h">
                <span>{groups.find((gr) => gr.key === g)?.label ?? g}</span>
                <span className={'t mask' + (total < 0 ? ' neg' : '')}>{fBaseCompact(total)}</span>
              </div>

              {flat.map((a) => (
                <AccountRow key={a.id} a={a} sel={selectedId === a.id} onSelect={onSelect} />
              ))}

              {venues.map(([p, arr]) => {
                const sub = arr.reduce((s, a) => s + accountValue(a), 0)
                const venueDue = countNeedsReconciling(arr)
                const open = !!openVenues[p]
                return (
                  <div key={p}>
                    <div
                      className="acct-row acct-row--venue"
                      onClick={() => setOpenVenues((v) => ({ ...v, [p]: !v[p] }))}
                    >
                      <span className="acct-row-id">
                        <span className="acct-row-name">
                          <span className="acct-venue-nm">{p}</span>
                          <span className="acct-venue-sub">• {arr.length}</span>
                        </span>
                        {venueDue > 0 && (
                          <span className="acct-row-sub acct-due">
                            {needReconcilingLabel(venueDue)}
                          </span>
                        )}
                      </span>
                      <span className="acct-row-v">
                        <span className="acct-row-bal mask">
                          {fBaseCompact(sub)}
                          <span className={'acct-venue-caret' + (open ? ' open' : '')}>
                            <ChevronRight size="1em" aria-hidden="true" />
                          </span>
                        </span>
                      </span>
                    </div>
                    {open && (
                      <div className="acct-venue-kids">
                        {arr.map((a) => (
                          <AccountRow
                            key={a.id}
                            a={a}
                            sel={selectedId === a.id}
                            onSelect={onSelect}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}

        {accounts.length === 0 && <div className="acct-side-empty">No accounts yet</div>}

        <button className="acct-add" onClick={onAdd}>
          <IconPlus /> Add account
        </button>
        <button className="acct-managepayees" onClick={onManagePayees}>
          Manage payees
        </button>
      </div>
    </aside>
  )
}
