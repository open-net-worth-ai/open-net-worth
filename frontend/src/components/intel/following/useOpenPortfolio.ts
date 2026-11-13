import { useCallback, useEffect, useRef } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import type { FollowKind } from '../../../api/followedPortfolios'
import type { PortfolioRef } from './Discover'

// The open portfolio IS the address (?portfolio=): opening one adds a history entry, so the
// browser's Back (and a mouse's back button) returns to the list, and Forward reopens it. It is
// also remembered for the visit, the way Intel remembers its tab: leaving for Topics & macro and
// coming back lands on the same portfolio, not the list.
const VISIT_KEY = 'intel-following-open'

// In the address, written with characters that are never escaped: "fund-1067983",
// "member-pelosi~nancy", "etf-ARKK" (a member's ref "last|first" keeps "~" for "|" and "_" for a
// space). The old form ("fund_13f:1067983", escaped as %3A) still opens.
const SLUG: Record<FollowKind, string> = {
  fund_13f: 'fund',
  house_member: 'member',
  etf_daily: 'etf',
}
const KIND_OF_SLUG: Record<string, FollowKind> = {
  fund: 'fund_13f',
  member: 'house_member',
  etf: 'etf_daily',
}

/** The address form of a portfolio. */
export const portfolioParam = (p: Pick<PortfolioRef, 'kind' | 'source_ref'>) =>
  `${SLUG[p.kind]}-${p.source_ref.replace(/\|/g, '~').replace(/ /g, '_')}`

export function parsePortfolioParam(raw: string | null | undefined): PortfolioRef | null {
  const text = String(raw ?? '')
  const dash = text.indexOf('-')
  const kind = dash > 0 ? KIND_OF_SLUG[text.slice(0, dash)] : undefined
  if (kind) {
    const ref = text
      .slice(dash + 1)
      .replace(/~/g, '|')
      .replace(/_/g, ' ')
    return ref ? { kind, source_ref: ref, name: '', subtitle: '' } : null
  }
  const colon = text.indexOf(':')
  const old = colon > 0 ? (text.slice(0, colon) as FollowKind) : null
  const ref = text.slice(colon + 1)
  return old && old in SLUG && ref ? { kind: old, source_ref: ref, name: '', subtitle: '' } : null
}

function remembered(): PortfolioRef | null {
  try {
    const saved = sessionStorage.getItem(VISIT_KEY)
    return saved ? (JSON.parse(saved) as PortfolioRef) : null
  } catch {
    return null
  }
}

function remember(p: PortfolioRef | null) {
  try {
    if (p) sessionStorage.setItem(VISIT_KEY, JSON.stringify(p))
    else sessionStorage.removeItem(VISIT_KEY)
  } catch {
    /* per-visit convenience only */
  }
}

// The address carries only kind and id; the name a card or row opened it with is kept here, so
// the page can say what it is loading before its data arrives.
const known = new Map<string, Pick<PortfolioRef, 'name' | 'subtitle'>>()
const withName = (p: PortfolioRef | null): PortfolioRef | null => {
  const k = p && known.get(portfolioParam(p))
  return p && k && !p.name ? { ...p, ...k } : p
}

export function useOpenPortfolio() {
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { portfolio?: string }
  const open = withName(parsePortfolioParam(search.portfolio))
  // Set when THIS screen opened the portfolio (a history entry it added): closing it then steps
  // back to the list instead of adding another entry.
  const pushed = useRef(false)

  const write = useCallback(
    (value: string | undefined, replace: boolean) =>
      void navigate({
        to: '/intel',
        // A different portfolio (or the list) opens on its first tab: ?tab= goes with it.
        search: ((prev: Record<string, unknown>) => ({
          ...prev,
          portfolio: value,
          tab: undefined,
          range: undefined,
        })) as never,
        replace,
      } as never),
    [navigate],
  )

  // Coming back to the tab with no portfolio in the address: the one open earlier this visit goes
  // into the address (no new history entry), and the page follows the address from there.
  useEffect(() => {
    const saved = window.location.search.includes('portfolio=') ? null : remembered()
    if (saved?.name)
      known.set(portfolioParam(saved), { name: saved.name, subtitle: saved.subtitle })
    if (saved) write(portfolioParam(saved), true)
  }, [write])

  // Remember the address's portfolio whenever it changes (Back and Forward included).
  const param = search.portfolio
  useEffect(() => {
    remember(withName(parsePortfolioParam(param)))
  }, [param])

  const setOpen = useCallback(
    (next: PortfolioRef | null) => {
      if (next) {
        if (next.name) known.set(portfolioParam(next), { name: next.name, subtitle: next.subtitle })
        pushed.current = true
        write(portfolioParam(next), false)
      } else if (pushed.current) {
        pushed.current = false
        window.history.back()
      } else {
        write(undefined, false)
      }
    },
    [write],
  )

  // Leaving for another Intel tab takes the param off that tab's address; leaving Intel itself
  // (a stock's page opened from a row) writes nothing: the address is that page's by then.
  useEffect(
    () => () => {
      if (window.location.pathname.startsWith('/intel')) write(undefined, true)
    },
    [write],
  )

  return { open, setOpen }
}
