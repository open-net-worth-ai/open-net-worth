import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { FollowKind } from '../../../api/followedPortfolios'
import type { PortfolioRef } from './Discover'

// The portfolio you had open is remembered for the VISIT, the way Intel remembers its tab:
// leaving for Topics & macro and coming back lands on the same portfolio, not the list.
const VISIT_KEY = 'intel-following-open'
const KINDS: FollowKind[] = ['fund_13f', 'house_member', 'etf_daily']

/** `kind:ref`, the form `?portfolio=` carries. */
export const portfolioParam = (p: Pick<PortfolioRef, 'kind' | 'source_ref'>) =>
  `${p.kind}:${p.source_ref}`

export function parsePortfolioParam(raw: string | null | undefined): PortfolioRef | null {
  const at = (raw ?? '').indexOf(':')
  if (at < 1) return null
  const kind = raw!.slice(0, at) as FollowKind
  const ref = raw!.slice(at + 1)
  return KINDS.includes(kind) && ref ? { kind, source_ref: ref, name: '', subtitle: '' } : null
}

function initial(): PortfolioRef | null {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('portfolio')
    if (fromUrl) return parsePortfolioParam(fromUrl)
    const saved = sessionStorage.getItem(VISIT_KEY)
    return saved ? (JSON.parse(saved) as PortfolioRef) : null
  } catch {
    return null
  }
}

export function useOpenPortfolio() {
  const navigate = useNavigate()
  const [open, setOpenState] = useState<PortfolioRef | null>(initial)

  const setOpen = useCallback((next: PortfolioRef | null) => {
    setOpenState(next)
    try {
      if (next) sessionStorage.setItem(VISIT_KEY, JSON.stringify(next))
      else sessionStorage.removeItem(VISIT_KEY)
    } catch {
      /* per-visit convenience only */
    }
  }, [])

  // Mirror into ?portfolio= while this tab is shown, and take it off the URL when leaving, so
  // another Intel tab's address never names a portfolio.
  useEffect(() => {
    const write = (value: string | undefined) =>
      void navigate({
        to: '/intel',
        search: ((prev: Record<string, unknown>) => ({ ...prev, portfolio: value })) as never,
        replace: true,
      } as never)
    write(open ? portfolioParam(open) : undefined)
    return () => write(undefined)
  }, [open, navigate])

  return { open, setOpen }
}
