import { useEffect, useMemo, useRef, useState } from 'react'
import { Modal, TextField } from './ui'
import { Badge } from './ui/Badge'
import { authFetch } from '../api/client'
import { fetchCachedQuery } from '../state/queryCache'
import { ACCOUNTS_KEY, CATEGORIES_KEY, fetchJson } from '../hooks/serverState'
import type { ComputedRow } from '../types/portfolio'
import { gateAndRankLocalIds, orderGroups } from './cmdkAssemble'

// Every sidebar destination the palette can jump to. Kept here (not imported from
// App) so the palette stays a self-contained leaf — the labels mirror App.TAB_LABELS.
type PaletteTab =
  | 'lucius'
  | 'dashboard'
  | 'inbox'
  | 'investments'
  | 'accounts'
  | 'assets'
  | 'commitments'
  | 'plan'
  | 'intel'
  | 'skills'
  | 'automations'
  | 'build'
  | 'profile'
  | 'connectors'
  | 'settings'

const TAB_DESTINATIONS: { tab: PaletteTab; label: string; hint: string }[] = [
  { tab: 'lucius', label: 'Lucius', hint: 'Chat, history, and everything Lucius can do' },
  { tab: 'dashboard', label: 'Dashboard', hint: 'Net worth, allocation, briefing' },
  { tab: 'investments', label: 'Investments', hint: 'Stocks, ETFs, crypto, commodities' },
  { tab: 'accounts', label: 'Accounts', hint: 'Balances, liabilities, register' },
  { tab: 'assets', label: 'Assets', hint: 'Property, vehicles, valuables' },
  { tab: 'plan', label: 'Planning', hint: 'Budgets, goals, envelopes' },
  { tab: 'commitments', label: 'Commitments', hint: 'Dated obligations and funding' },
  { tab: 'intel', label: 'Intelligence', hint: 'Living signals and watchlist' },
  { tab: 'skills', label: 'Skills', hint: 'Reusable Lucius playbooks' },
  { tab: 'automations', label: 'Automations', hint: 'Scheduled and event-driven conversations' },
  { tab: 'profile', label: 'Profile', hint: 'Personal, income, FIRE' },
  { tab: 'connectors', label: 'Connectors', hint: 'Channels, data sources, tools' },
  { tab: 'settings', label: 'Settings', hint: 'AI model, appearance, data' },
]

interface AccountLite {
  id: string
  name: string
  type: string
  currency: string
}

interface CategoryLite {
  id: string
  label: string
}

interface MarketResult {
  symbol: string
  name: string
  asset_class: 'equity' | 'crypto' | 'commodity'
  exchange: string
  price_source: 'yahoo' | 'coingecko'
  price_source_id: string
}

// A single actionable command. `group` clusters it in the rendered list; `keywords`
// (lowercased haystack) is what the fuzzy matcher scores against; `run` performs it.
interface Command {
  id: string
  group:
    | 'Quick actions'
    | 'Tabs'
    | 'Accounts'
    | 'Holdings'
    | 'Categories'
    | 'Markets'
    | 'Ask Lucius'
  label: string
  hint?: string
  keywords: string
  run: () => void
  // Market-specific rendering extras
  _market?: MarketResult
  // Ask-Lucius marker
  _isAskLucius?: boolean
}

const GROUP_ORDER: Command['group'][] = [
  'Quick actions',
  'Tabs',
  'Accounts',
  'Holdings',
  'Categories',
  'Markets',
  'Ask Lucius',
]

// Sentinel id for the synthetic Ask Lucius row (never a real Command.id — those are all
// prefixed qa-/tab-/acct-/hold-/cat-/mkt-). The active selection is tracked by this stable
// id, not a bare index, so an async `flat` rebuild (e.g. market results landing mid-nav)
// can't silently retarget the highlight onto an unrelated row (I8 review).
const ASK_LUCIUS_ID = '__ask-lucius__'

// Ask Lucius takes the default Enter highlight when looksLikeQuestion or !hasStrongMatch.

// First words that suggest a natural-language question rather than a symbol/name lookup.
const QUESTION_STARTERS = new Set([
  'how',
  'what',
  'why',
  'when',
  'where',
  'who',
  'should',
  'can',
  'did',
  'do',
  'is',
  'are',
  'show',
  'list',
  'find',
  'compare',
  'explain',
])

export interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  onNavigate: (tab: PaletteTab) => void
  onOpenChat: (query?: string, opts?: { fresh?: boolean }) => void
  // Portfolio rows already loaded by App (holdings + account rows). We read only
  // labels/tickers here — never amounts — so privacy mode is respected by design.
  rows: ComputedRow[]
  // Deep-link the Accounts register to a category for the current month (mirrors
  // the Planning Activity → Accounts deep-link via App's cashFilter).
  onCategoryFilter: (category: string, month: string) => void
  // Open a holding's per-asset detail (App's openAssetDetail — ADR 0007). Used by
  // the Holdings results so a ticker jumps straight to its detail, not the tab.
  onOpenAsset: (row: {
    instrument_id?: string
    label?: string
    ticker?: string
    assetClass?: string
    priceSource?: string
    priceSourceId?: string
  }) => void
}

function looksLikeQuestion(query: string): boolean {
  const trimmed = query.trim()
  if (trimmed.endsWith('?')) return true
  const words = trimmed.split(/\s+/)
  if (words.length >= 4) return true
  const first = words[0]?.toLowerCase() ?? ''
  return QUESTION_STARTERS.has(first)
}

// Asset-class glyph for the Markets tier icon column.
function assetClassGlyph(assetClass: string): string {
  if (assetClass === 'crypto') return '₿'
  if (assetClass === 'commodity') return '◆'
  return '◈' // equity / ETF / default
}

// Badge label for the Markets tier.
function assetClassLabel(assetClass: string): string {
  if (assetClass === 'crypto') return 'Crypto'
  if (assetClass === 'commodity') return 'Commodity'
  return 'Equity'
}

const currentMonth = (): string => new Date().toISOString().slice(0, 7)

export function CommandPalette({
  open,
  onClose,
  onNavigate,
  onOpenChat,
  rows,
  onCategoryFilter,
  onOpenAsset,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  // The active item's stable id (a Command.id, ASK_LUCIUS_ID, or null for "no explicit
  // selection yet" — resolved to a render index by the `activeIdx` memo below).
  const [activeId, setActiveId] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<AccountLite[]>([])
  const [categories, setCategories] = useState<CategoryLite[]>([])
  const [marketResults, setMarketResults] = useState<MarketResult[]>([])
  const [marketLoading, setMarketLoading] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const abortController = useRef<AbortController | null>(null)
  const marketCache = useRef<Map<string, MarketResult[]>>(new Map())
  // The (normalized) query that the current `marketResults` state was actually fetched for —
  // lets the `filtered` memo (read at render time, so this must be state, not a ref) detect
  // the ~250ms debounce window where `query` has already moved on but `marketResults` still
  // holds the previous query's rows.
  const [marketResultsQuery, setMarketResultsQuery] = useState('')
  // Tracks whether the user has moved the selection with the arrow keys for the
  // current query, so late-arriving market results (which grow askLuciusIdx) don't
  // stomp their position. Cleared whenever the query text itself changes.
  const userNavigated = useRef(false)
  const routedQuery = useRef<string | null>(null)
  // Mirrors `query` into a ref so the async market-search callback below can check
  // staleness against the LIVE value — reading the `query` state var directly there
  // would close over the render that scheduled the timeout, which never changes, so
  // that comparison was always true-vs-itself and could never detect a superseded
  // request (e.g. a typo like "pltrr" corrected to "pltr" before the "pltrr" fetch
  // resolves) — letting the older, irrelevant response clobber the newer one.
  const latestQueryRef = useRef(query)
  useEffect(() => {
    latestQueryRef.current = query
  }, [query])

  // Reset query + selection each time the palette opens (the Modal remounts its
  // card, so a fresh open always starts clean). Initial focus is handled by the
  // Modal honoring the input's `data-autofocus` marker — plain `autoFocus` loses
  // to the Modal's focus-trap, which would otherwise land on the × button.
  // Deferred off the effect body to avoid the cascading-render lint (mirrors the
  // useBudget pattern).
  useEffect(() => {
    if (!open) return
    void Promise.resolve().then(() => {
      setQuery('')
      setActiveId(null)
      setMarketResults([])
      setMarketLoading(false)
      setMarketResultsQuery('')
    })
  }, [open])

  // Lazy-load accounts + categories the first time the palette opens (best-effort;
  // holdings come from the already-loaded `rows`, so no extra fetch for those).
  useEffect(() => {
    if (!open) return
    if (accounts.length === 0) {
      fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
        .then((data) => Array.isArray(data) && setAccounts(data))
        .catch(() => {})
    }
    if (categories.length === 0) {
      fetchCachedQuery<CategoryLite[]>(CATEGORIES_KEY, () =>
        fetchJson<CategoryLite[]>(CATEGORIES_KEY),
      )
        .then((data) => Array.isArray(data) && setCategories(data))
        .catch(() => {})
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced market search: fires ~250ms after the query settles (>= 2 chars).
  useEffect(() => {
    const q = query.trim()

    if (q.length < 2) {
      // Clear prior results and cancel any pending search.
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      abortController.current?.abort()
      setMarketResultsQuery(q.toLowerCase())
      setMarketResults([])
      setMarketLoading(false)
      return
    }

    // Return cached results immediately (no loading flash on repeat keystrokes).
    const normalized = q.toLowerCase()
    if (marketCache.current.has(normalized)) {
      setMarketResultsQuery(normalized)
      setMarketResults(marketCache.current.get(normalized)!)
      setMarketLoading(false)
      return
    }

    setMarketLoading(true)
    if (debounceTimer.current) clearTimeout(debounceTimer.current)

    debounceTimer.current = setTimeout(async () => {
      // Abort any prior in-flight request.
      abortController.current?.abort()
      const ctrl = new AbortController()
      abortController.current = ctrl

      try {
        const res = await authFetch(`/api/intel/assets/search?q=${encodeURIComponent(q)}&limit=8`, {
          signal: ctrl.signal,
        })
        // Guard: ignore stale response if query has already changed.
        if (latestQueryRef.current.trim().toLowerCase() !== normalized) return
        if (!res.ok) {
          setMarketResultsQuery(normalized)
          setMarketResults([])
          setMarketLoading(false)
          return
        }
        const data = (await res.json()) as MarketResult[]
        const results = Array.isArray(data) ? data : []
        marketCache.current.set(normalized, results)
        setMarketResultsQuery(normalized)
        setMarketResults(results)
        setMarketLoading(false)
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') return
        if (latestQueryRef.current.trim().toLowerCase() !== normalized) return
        setMarketResultsQuery(normalized)
        setMarketResults([])
        setMarketLoading(false)
      }
    }, 250)

    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps

  const go = (tab: PaletteTab) => {
    onNavigate(tab)
    onClose()
  }

  // Build the full command set (re-derived when source data changes, not per keystroke).
  const commands = useMemo<Command[]>(() => {
    const out: Command[] = []

    // Quick actions — wired to existing affordances where trivial; else navigate.
    out.push({
      id: 'qa-chat',
      group: 'Quick actions',
      label: 'Open Lucius chat',
      hint: 'Ask the assistant',
      keywords: 'open lucius chat assistant ai ask',
      run: () => {
        onOpenChat()
        onClose()
      },
    })
    out.push({
      id: 'qa-add-txn',
      group: 'Quick actions',
      label: 'Add transaction',
      hint: 'Opens Accounts',
      keywords: 'add transaction expense income spend log accounts',
      run: () => go('accounts'),
    })
    out.push({
      id: 'qa-new-goal',
      group: 'Quick actions',
      label: 'New goal / target',
      hint: 'Opens Planning',
      keywords: 'new goal target savings plan',
      run: () => go('plan'),
    })

    // Tabs.
    for (const d of TAB_DESTINATIONS) {
      out.push({
        id: `tab-${d.tab}`,
        group: 'Tabs',
        label: d.label,
        hint: d.hint,
        keywords: `${d.label} ${d.hint} tab go to`,
        run: () => go(d.tab),
      })
    }

    // Accounts → jump to the Accounts tab. (No balances surfaced — privacy-safe.)
    for (const a of accounts) {
      out.push({
        id: `acct-${a.id}`,
        group: 'Accounts',
        label: a.name,
        hint: `${a.type.replace(/_/g, ' ')} • ${a.currency}`,
        keywords: `${a.name} ${a.type} ${a.currency} account`,
        run: () => go('accounts'),
      })
    }

    // Holdings / instruments → open the per-asset detail (ADR 0007), falling back
    // to the Investments tab when the row has no instrument_id to open. Source from
    // already-loaded rows; a market row carries a ticker (account rows don't).
    const seen = new Set<string>()
    for (const r of rows) {
      const isMarket = !!r.ticker || ['equity', 'crypto', 'commodity'].includes(r.asset_class)
      if (!isMarket) continue
      const sym = r.symbol || r.ticker || ''
      const key = `${r.label}|${sym}`
      if (seen.has(key)) continue
      seen.add(key)
      const instrumentId = r.instrument_id
      const label = r.label
      const ticker = r.ticker
      out.push({
        id: `hold-${r.position_id || key}`,
        group: 'Holdings',
        label: r.label || sym,
        hint: [sym, r.asset_class].filter(Boolean).join(' • '),
        keywords: `${r.label} ${sym} ${r.ticker} ${r.asset_class} holding instrument`,
        run: () => {
          if (instrumentId) {
            onOpenAsset({ instrument_id: instrumentId, label, ticker })
            onClose()
          } else {
            go('investments')
          }
        },
      })
    }

    // Categories → deep-link the Accounts register filtered to that category
    // (reuses App's cashFilter mechanism), for the current month.
    for (const c of categories) {
      out.push({
        id: `cat-${c.id}`,
        group: 'Categories',
        label: c.label || 'Uncategorized',
        hint: 'View transactions',
        keywords: `${c.label} category spending`,
        run: () => {
          onCategoryFilter(c.id, currentMonth())
          onClose()
        },
      })
    }

    return out
  }, [accounts, categories, rows, onClose, onOpenChat, onNavigate, onCategoryFilter, onOpenAsset]) // eslint-disable-line react-hooks/exhaustive-deps

  // Filter + rank against the query (flat, ranked). With no query, keep source order.
  // Scatter (Tier-2) matches are dropped whenever a Tier-1 contiguous hit exists anywhere —
  // among these commands OR among the current Markets results (scored locally for this
  // gating decision only) — see cmdkAssemble.ts.
  const filtered = useMemo(() => {
    const byId = new Map(commands.map((cmd) => [cmd.id, cmd] as const))
    // Trust marketResults for the scatter-gating decision only when they were actually
    // fetched for the CURRENT query — during the ~250ms debounce window right after a
    // keystroke, marketResults can still hold the PREVIOUS query's rows (nit from I8 review).
    const marketResultsFresh = marketResultsQuery === query.trim().toLowerCase()
    const marketRows = marketResultsFresh
      ? marketResults.map((m) => ({
          id: m.symbol,
          group: 'Markets',
          keywords: `${m.name} ${m.symbol} ${m.asset_class}`,
        }))
      : []
    const ids = gateAndRankLocalIds(
      query,
      commands.map((cmd) => ({ id: cmd.id, group: cmd.group, keywords: cmd.keywords })),
      marketRows,
    )
    return ids.map((id) => byId.get(id)!)
  }, [commands, query, marketResults, marketResultsQuery])

  // Build the full navigable flat list including Markets + Ask Lucius.
  // Markets bypass local fuzzyScore for FILTERING (server-ranked) but are scored locally for
  // GROUP PLACEMENT (cmdkAssemble.orderGroups) so an exact ticker hit can surface Markets
  // first. Ask Lucius is always last.
  const { flat, groups, askLuciusIdx } = useMemo(() => {
    const trimmedQuery = query.trim()

    // Build Markets commands (server-ranked). A symbol you already hold is NOT excluded here
    // — it still shows in Markets alongside its Holdings row; Holdings always sorts above
    // Markets (cmdkAssemble.orderGroups) so the two never compete for the top slot.
    const marketCmds: Command[] = []
    if (trimmedQuery.length >= 2 && marketResults.length > 0) {
      for (const m of marketResults) {
        const mkt = m
        marketCmds.push({
          // NOT `mkt-${m.symbol}` — the backend can legitimately return several DISTINCT
          // results sharing one ticker (e.g. multiple CoinGecko tokenized-stock wrapper
          // products all trading as "OKLO"). Backend dedup already keys on
          // (price_source, price_source_id) — the one pair that actually identifies a
          // listing — so mirror it here. A symbol-only id collapsed those into one Map
          // entry (last-write-wins) that orderGroups then referenced N times, rendering
          // the same row 2-3x over (owner report 2026-08-10, "oklo" search).
          id: `mkt-${m.price_source}-${m.price_source_id}`,
          group: 'Markets',
          label: m.name,
          hint: m.symbol,
          keywords: `${m.name} ${m.symbol} ${m.asset_class}`,
          _market: mkt,
          run: () => {
            onOpenAsset({
              ticker: mkt.symbol,
              label: mkt.name,
              assetClass: mkt.asset_class,
              priceSource: mkt.price_source,
              priceSourceId: mkt.price_source_id,
            })
            onClose()
          },
        })
      }
    }

    const byId = new Map<string, Command>()
    for (const cmd of filtered) byId.set(cmd.id, cmd)
    for (const cmd of marketCmds) byId.set(cmd.id, cmd)

    const orderedGroups = orderGroups(
      query,
      filtered.map((cmd) => ({ id: cmd.id, group: cmd.group, keywords: cmd.keywords })),
      marketCmds.map((cmd) => ({ id: cmd.id, group: cmd.group, keywords: cmd.keywords })),
      GROUP_ORDER,
    )

    const groupList = orderedGroups.map((g) => ({
      group: g.group,
      items: g.ids.map((id) => byId.get(id)!),
    }))
    const flatCmds = groupList.flatMap((g) => g.items)

    // Ask Lucius — always last, only when query is non-empty.
    let askIdx = -1
    if (trimmedQuery) {
      askIdx = flatCmds.length
    }

    return { flat: flatCmds, groups: groupList, askLuciusIdx: askIdx }
  }, [filtered, marketResults, query, onOpenAsset, onClose])

  // Total navigable length includes the Ask Lucius row when present.
  const totalLength = askLuciusIdx >= 0 ? flat.length + 1 : flat.length

  // Determine the default active item when the query changes.
  // Routes to Ask Lucius when the query looks like a question or lacks a strong match.
  useEffect(() => {
    const queryChanged = routedQuery.current !== query
    if (queryChanged) userNavigated.current = false
    routedQuery.current = query

    if (!query.trim()) {
      setActiveId(null)
      return
    }
    if (askLuciusIdx < 0) {
      if (queryChanged) setActiveId(flat[0]?.id ?? null)
      return
    }
    // Market results arriving mid-navigation rebuild + reorder `flat` (I8 review fix). If the
    // user's previously-active item survived the rebuild (still present by id), keep it —
    // don't recompute the default once the user has manually navigated this query. If it did
    // NOT survive, fall through and re-pick a fresh default instead of leaving `activeId`
    // pointing at an item that's gone (which would silently retarget the highlight onto
    // whatever now resolves to that stale id — or, worse, land nowhere).
    if (!queryChanged && userNavigated.current) {
      const stillPresent =
        activeId === ASK_LUCIUS_ID || (activeId != null && flat.some((c) => c.id === activeId))
      if (stillPresent) return
      userNavigated.current = false
    }

    const topCmd = flat[0]
    // A "strong match" = the top in-app result is a close fuzzy/substring hit, OR there are
    // market results (server-ranked, inherently relevant to the typed query). Either way
    // Ask Lucius must NOT steal the default highlight — it only owns the default for
    // question-shaped queries or when nothing real matched. (Letting it default for an
    // asset lookup like "mr" pinned the highlight to the bottom row and scrolled the list
    // past the Markets results.)
    // A "strong match" = a contiguous-substring hit on the top result (which is exactly what
    // `keywords.includes(query)` tests — the new scorer ranks any contiguous match above all
    // scattered ones, so a non-substring top result is only ever scattered noise), or any
    // market results present. No score-threshold branch is needed: a Tier-1 match always
    // satisfies includes(), and a Tier-2 (scattered) score is never "strong".
    const hasStrongMatch =
      marketResults.length > 0 ||
      (topCmd != null && topCmd.keywords.toLowerCase().includes(query.trim().toLowerCase()))
    const shouldDefaultToLucius = looksLikeQuestion(query) || !hasStrongMatch
    setActiveId(shouldDefaultToLucius ? ASK_LUCIUS_ID : (topCmd?.id ?? null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, askLuciusIdx, marketResults.length])

  // Resolve the active item's stable id back to a render index every render — this is what
  // makes the highlight immune to `flat` reordering: if the id moved, this just finds its
  // new position; if it's gone, it falls back to the top row (the effect above is what
  // decides whether that fallback should instead be Ask Lucius).
  const activeIdx = useMemo(() => {
    if (activeId === ASK_LUCIUS_ID) return askLuciusIdx >= 0 ? askLuciusIdx : 0
    if (activeId != null) {
      const idx = flat.findIndex((cmd) => cmd.id === activeId)
      if (idx >= 0) return idx
    }
    return 0
  }, [activeId, flat, askLuciusIdx])

  // Reset the results list to the top whenever the query changes, so a new search always
  // shows results from the top rather than wherever the previous active row left the scroll.
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0
  }, [query])

  // Scroll the active row into view ONLY when the user is navigating with the keyboard.
  // The auto-default highlight must not scroll — a default Ask Lucius row at the bottom
  // would otherwise drag the freshly-arrived market results out of view.
  useEffect(() => {
    if (!open || !userNavigated.current) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-index="${activeIdx}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIdx, open])

  const isAskLuciusActive = askLuciusIdx >= 0 && activeIdx === askLuciusIdx

  const runActive = () => {
    if (isAskLuciusActive) {
      onClose()
      onOpenChat(query.trim(), { fresh: true })
      return
    }
    flat[activeIdx]?.run()
  }

  // Resolves a render index back to its stable id (a Command.id, or the Ask Lucius
  // sentinel) — the inverse of the `activeIdx` memo, used by arrow-key navigation so the
  // stored selection is always an id, never a bare index.
  const idAtIndex = (idx: number): string | null =>
    askLuciusIdx >= 0 && idx === askLuciusIdx ? ASK_LUCIUS_ID : (flat[idx]?.id ?? null)

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      userNavigated.current = true
      setActiveId(idAtIndex(totalLength ? (activeIdx + 1) % totalLength : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      userNavigated.current = true
      setActiveId(idAtIndex(totalLength ? (activeIdx - 1 + totalLength) % totalLength : 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      runActive()
    }
    // Escape is handled by the Modal primitive.
  }

  const trimmedQuery = query.trim()

  // Loading row for Markets (shown when loading and no cached results yet).
  const showMarketLoading = marketLoading && trimmedQuery.length >= 2 && marketResults.length === 0

  return (
    <Modal open={open} onClose={onClose} size="lg">
      <div className="cmdk" onKeyDown={onKeyDown}>
        <div className="cmdk-searchbar">
          <TextField
            autoFocus
            data-autofocus
            className="cmdk-input"
            placeholder="Search or ask Lucius…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
            }}
            aria-label="Search or ask Lucius"
          />
          <button
            type="button"
            className="cmdk-cancel"
            aria-label="Cancel search"
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
        <div className="cmdk-results" ref={listRef} role="listbox">
          {totalLength === 0 && !showMarketLoading ? (
            <div className="cmdk-empty">No matches for "{query}"</div>
          ) : (
            <>
              {groups.map(({ group, items }) => (
                <div className="cmdk-group" key={group}>
                  <div className="cmdk-group-label">{group}</div>
                  {group === 'Markets'
                    ? items.map((cmd) => {
                        const idx = flat.indexOf(cmd)
                        const mkt = cmd._market!
                        return (
                          <div
                            key={cmd.id}
                            data-cmd-index={idx}
                            role="option"
                            aria-selected={idx === activeIdx}
                            className={`cmdk-item${idx === activeIdx ? ' active' : ''}`}
                            onMouseMove={() => idx !== activeIdx && setActiveId(cmd.id)}
                            onClick={() => cmd.run()}
                          >
                            <span className="cmdk-item-icon" aria-hidden="true">
                              {assetClassGlyph(mkt.asset_class)}
                            </span>
                            <span className="cmdk-item-label">{cmd.label}</span>
                            <span className="cmdk-item-meta">
                              <Badge tone="muted">{assetClassLabel(mkt.asset_class)}</Badge>
                              <span className="cmdk-item-symbol">{mkt.symbol.toUpperCase()}</span>
                              {mkt.exchange && (
                                <span className="cmdk-item-exchange">{mkt.exchange}</span>
                              )}
                            </span>
                          </div>
                        )
                      })
                    : items.map((cmd) => {
                        const idx = flat.indexOf(cmd)
                        return (
                          <div
                            key={cmd.id}
                            data-cmd-index={idx}
                            role="option"
                            aria-selected={idx === activeIdx}
                            className={`cmdk-item${idx === activeIdx ? ' active' : ''}`}
                            onMouseMove={() => idx !== activeIdx && setActiveId(cmd.id)}
                            onClick={() => cmd.run()}
                          >
                            <span className="cmdk-item-label">{cmd.label}</span>
                            {cmd.hint && <span className="cmdk-item-hint">{cmd.hint}</span>}
                          </div>
                        )
                      })}
                </div>
              ))}

              {/* Markets loading placeholder — not navigable */}
              {showMarketLoading && (
                <div className="cmdk-group">
                  <div className="cmdk-group-label">Markets</div>
                  <div className="cmdk-item cmdk-item--loading" aria-disabled="true">
                    <span className="cmdk-item-label">Searching the market…</span>
                  </div>
                </div>
              )}

              {/* Ask Lucius — always last when query is non-empty */}
              {askLuciusIdx >= 0 && (
                <div
                  data-cmd-index={askLuciusIdx}
                  role="option"
                  aria-selected={isAskLuciusActive}
                  className={`cmdk-item cmdk-item--ask${isAskLuciusActive ? ' active' : ''}`}
                  onMouseMove={() => !isAskLuciusActive && setActiveId(ASK_LUCIUS_ID)}
                  onClick={() => {
                    onClose()
                    onOpenChat(trimmedQuery, { fresh: true })
                  }}
                >
                  <span className="cmdk-item-icon" aria-hidden="true">
                    ✦
                  </span>
                  <span className="cmdk-item-label">
                    <span className="cmdk-item-ask-prefix">Ask Lucius: </span>
                    <span className="cmdk-item-ask-query">
                      &ldquo;
                      {trimmedQuery.length > 60 ? trimmedQuery.slice(0, 60) + '…' : trimmedQuery}
                      &rdquo;
                    </span>
                  </span>
                </div>
              )}
            </>
          )}
        </div>
        <div className="cmdk-footer">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> to navigate
          </span>
          <span>
            <kbd>↵</kbd> {isAskLuciusActive ? 'Ask Lucius' : 'to open'}
          </span>
          <span>
            <kbd>esc</kbd> to close
          </span>
          {/* Teach the shortcut. The palette is mostly reached by clicking Search, so this is
              the one moment a user is guaranteed to be looking at it. */}
          <span className="cmdk-footer-hint">
            <kbd>⌘K</kbd> to open anytime
          </span>
        </div>
      </div>
    </Modal>
  )
}
