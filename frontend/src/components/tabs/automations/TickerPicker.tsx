// The price trigger's ticker: any symbol on the market, not only ones in your book. Your own
// instruments are suggested first; past them, the market search the command palette uses.
// Picking a market result resolves it (find-or-create) so its quote currency is known.
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { authFetch } from '../../../api/client'
import type { AutomationOption } from '../../../api/automations'

export interface MarketResult {
  symbol: string
  name: string
  asset_class: string
  exchange: string
  price_source: string
  price_source_id: string
}

interface Suggestion {
  key: string
  label: string
  detail?: string
  market?: MarketResult
}

export interface PickedInstrument {
  id: string
  label: string
  currency?: string
}

/** The command palette's market search, debounced: shared with the Famous Portfolios ticker
 *  fix (search only; nothing is created until something is picked here). */
export function useMarketSearch(query: string, enabled: boolean) {
  const [results, setResults] = useState<MarketResult[]>([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    const q = query.trim()
    if (!enabled || !q) return
    const ctrl = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const res = await authFetch(`/api/intel/assets/search?q=${encodeURIComponent(q)}&limit=6`, {
          signal: ctrl.signal,
        })
        const data = res.ok ? ((await res.json()) as MarketResult[]) : []
        setResults(Array.isArray(data) ? data : [])
      } catch (err) {
        if ((err as { name?: string }).name !== 'AbortError') setResults([])
      } finally {
        if (!ctrl.signal.aborted) setLoading(false)
      }
    }, 250)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [query, enabled])
  return { results: query.trim() ? results : [], loading: query.trim() ? loading : false }
}

async function resolveMarket(result: MarketResult): Promise<PickedInstrument | null> {
  const res = await authFetch('/api/intel/assets/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      q: result.symbol,
      asset_class: result.asset_class,
      price_source: result.price_source,
      price_source_id: result.price_source_id,
      name: result.name,
    }),
  })
  if (!res.ok) return null
  const body = (await res.json()) as { instrument_id: string; ticker?: string; currency?: string }
  return {
    id: body.instrument_id,
    label: body.ticker || result.symbol,
    currency: body.currency || undefined,
  }
}

function suggestionsFor(
  query: string,
  instruments: AutomationOption[],
  market: MarketResult[],
): Suggestion[] {
  const q = query.trim().toLocaleLowerCase()
  const own = instruments
    .filter((option) => option.label.toLocaleLowerCase().includes(q))
    .sort((a, b) => {
      const aStarts = a.label.toLocaleLowerCase().startsWith(q)
      const bStarts = b.label.toLocaleLowerCase().startsWith(q)
      return aStarts !== bStarts ? (aStarts ? -1 : 1) : a.label.localeCompare(b.label)
    })
    .slice(0, 5)
    .map((option) => ({ key: `own-${option.id}`, label: option.label, detail: option.currency }))
  const ownLabels = new Set(own.map((s) => s.label.toLocaleLowerCase()))
  const fromMarket = market
    .filter((result) => !ownLabels.has(result.symbol.toLocaleLowerCase()))
    .map((result) => ({
      key: `mkt-${result.price_source}-${result.price_source_id}`,
      label: result.symbol,
      detail: [result.name, result.exchange].filter(Boolean).join(' · '),
      market: result,
    }))
  return [...own, ...fromMarket]
}

export function TickerPicker({
  id,
  value,
  instruments,
  needed,
  onChange,
  onOpenChange,
}: {
  id: string
  value: string
  instruments: AutomationOption[]
  needed?: boolean
  /** True while the suggestions are open — the user is still choosing. */
  onOpenChange?: (open: boolean) => void
  /** The ticker typed or picked; `picked` is set when a market result was resolved. */
  onChange: (ticker: string, picked?: PickedInstrument) => void
}) {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    onOpenChange?.(open)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps -- report changes of `open` only
  const [active, setActive] = useState(0)
  const [failed, setFailed] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const { results, loading } = useMarketSearch(value, open)
  const suggestions = open ? suggestionsFor(value, instruments, results) : []
  const listId = `${id}-suggestions`

  const choose = async (suggestion: Suggestion) => {
    setOpen(false)
    setActive(0)
    setFailed(false)
    if (!suggestion.market) {
      onChange(suggestion.label)
      return
    }
    onChange(suggestion.label)
    const picked = await resolveMarket(suggestion.market).catch(() => null)
    if (picked) onChange(picked.label, picked)
    else setFailed(true)
  }

  return (
    <div
      className="a3-combo"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <input
        ref={inputRef}
        id={id}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-controls={listId}
        aria-expanded={suggestions.length > 0}
        aria-activedescendant={
          suggestions[active] ? `${listId}-${suggestions[active].key}` : undefined
        }
        className={`a3-mini-select${needed ? ' needed' : ''}`}
        value={value}
        placeholder="Search any ticker — AAPL, BTC, D05.SI"
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          onChange(event.target.value)
          setFailed(false)
          setActive(0)
          setOpen(true)
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setOpen(true)
            setActive((index) => Math.min(index + 1, Math.max(suggestions.length - 1, 0)))
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => Math.max(index - 1, 0))
          } else if (event.key === 'Enter' && suggestions[active]) {
            event.preventDefault()
            void choose(suggestions[active])
          } else if (event.key === 'Escape') {
            setOpen(false)
          }
        }}
      />
      <button
        type="button"
        className="a3-combo-toggle"
        aria-label="Show ticker suggestions"
        aria-expanded={open}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          setOpen((current) => !current)
          inputRef.current?.focus()
        }}
      >
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {open && (suggestions.length > 0 || loading) && (
        <div id={listId} className="a3-combo-pop" role="listbox" aria-label="Ticker suggestions">
          {suggestions.map((suggestion, index) => (
            <button
              key={suggestion.key}
              id={`${listId}-${suggestion.key}`}
              type="button"
              role="option"
              aria-selected={index === active}
              className={`a3-combo-option${index === active ? ' active' : ''}`}
              onMouseEnter={() => setActive(index)}
              onMouseDown={(event) => {
                event.preventDefault()
                void choose(suggestion)
              }}
            >
              <span className="a3-combo-option-label">{suggestion.label}</span>
              {suggestion.detail && (
                <span className="a3-combo-option-detail">{suggestion.detail}</span>
              )}
            </button>
          ))}
          {loading && suggestions.length === 0 && (
            <div className="a3-combo-status">Searching the market…</div>
          )}
        </div>
      )}
      {failed && <div className="a3-combo-status">Couldn’t look that ticker up — try again.</div>}
    </div>
  )
}

interface Quote {
  name?: string
  exchange?: string
  currency?: string
  price?: number
  previous?: number
}

async function fetchQuote(ticker: string, signal: AbortSignal): Promise<Quote> {
  const res = await authFetch(
    `/api/prices?tickers=${encodeURIComponent(ticker)}&include_names=true`,
    { signal },
  )
  if (!res.ok) return {}
  const data = (await res.json()) as Record<string, Record<string, unknown> | undefined>
  const pick = <T,>(key: string) => data[key]?.[ticker] as T | undefined
  const extended = pick<{ price?: number }>('extended')
  return {
    name: pick<string>('names'),
    exchange: pick<string>('exchanges'),
    currency: pick<string>('currencies')?.toUpperCase(),
    price: extended?.price ?? pick<number>('prices'),
    previous: pick<number>('prev_prices'),
  }
}

/** Under the ticker: what it actually is — name, exchange, currency and today's price — so a
 *  near-miss (an OTC listing, the wrong exchange) is caught before the trigger is saved. */
export function TickerConfirm({
  ticker,
  onCurrency,
  onSearchAgain,
}: {
  ticker: string
  onCurrency: (ticker: string, currency: string) => void
  onSearchAgain: () => void
}) {
  const symbol = ticker.trim()
  const [quote, setQuote] = useState<{ for: string; quote: Quote } | null>(null)
  useEffect(() => {
    if (!symbol) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      fetchQuote(symbol, ctrl.signal)
        .then((next) => {
          setQuote({ for: symbol, quote: next })
          if (next.currency) onCurrency(symbol, next.currency)
        })
        .catch(() => {
          if (!ctrl.signal.aborted) setQuote({ for: symbol, quote: {} })
        })
    }, 400)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [symbol]) // eslint-disable-line react-hooks/exhaustive-deps -- onCurrency is a fresh closure each render

  if (!symbol) return null
  if (!quote || quote.for !== symbol) {
    return <div className="a3-ticker-card a3-muted">Looking up {symbol}…</div>
  }
  const q = quote.quote
  if (!q.name && q.price === undefined) {
    return (
      <div className="a3-ticker-card warn">
        Couldn’t find {symbol} on the market — check the ticker.{' '}
        <button type="button" className="a3-linkbtn" onClick={onSearchAgain}>
          Search again
        </button>
      </div>
    )
  }
  const change =
    q.price !== undefined && q.previous ? ((q.price - q.previous) / q.previous) * 100 : undefined
  const money = (n: number) =>
    `${q.currency ? `${q.currency} ` : ''}${n.toLocaleString(undefined, { maximumFractionDigits: 4 })}`
  return (
    <div className="a3-ticker-card">
      <div className="a3-ticker-name">{q.name ?? symbol}</div>
      <div className="a3-muted">
        {[symbol, q.exchange, q.currency].filter(Boolean).join(' · ')}
        {q.price !== undefined
          ? ` · last ${money(q.price)}${change !== undefined ? ` (${change >= 0 ? '+' : ''}${change.toFixed(1)}%)` : ''}`
          : ' · no live price right now'}
      </div>
      <button type="button" className="a3-linkbtn" onClick={onSearchAgain}>
        Not this one? Search again
      </button>
    </div>
  )
}
