import type { ReactNode } from 'react'
import type { ToolPreview } from '../../../../types/chat'
import { TA_ANALYST_LABEL } from '../../../../types/tradingAgentsConstants'
import type { TaAnalyst, TaDisclosure, TaPortfolio } from '../../../../types/tradingAgents'
import { fCurrency, fQty, fUnitPrice } from '../../../../utils/format'
import { pluralize } from '../../../../utils/pluralize'
import { fxLabel, modelFromDisplay, modelText } from '../../../../utils/tradingAgents'
import type { TaModelRef } from '../../../../utils/tradingAgents'
import {
  ApprovalCode,
  ApprovalDetails,
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
  ApprovalNote,
  ApprovalTable,
} from '../ApprovalCard'
import { isRecord } from '../cardTypes'

// The card for `start_trading_agents_analysis` (preview kind `trading_agents_run`): what runs,
// what it costs, and EXACTLY which of the owner's amounts go to which AI provider. Every field
// is read through guards, so a payload that drifts shows "No preview available" and the raw
// arguments, never a blank or a throw. `estimated_cost` and `this_month` are the backend's own
// prose: shown, never parsed.

interface RunPreview {
  ticker: string
  tradeDate: string
  analysts: TaAnalyst[]
  deepModel: string
  quickModel: string
  estimatedCost: string
  perRunCap: string
  thisMonth: string
  portfolio: TaPortfolio | null
  disclosure: TaDisclosure
  dataVendors: string
}

const text = (v: unknown): string => (typeof v === 'string' ? v : '')

function asPortfolio(v: unknown): TaPortfolio | null {
  if (!isRecord(v) || !Array.isArray(v.positions)) return null
  return {
    cash: typeof v.cash === 'number' ? v.cash : null,
    currency: text(v.currency),
    positions: v.positions.filter(isRecord).map((p) => ({
      ticker: text(p.ticker),
      quantity: Number(p.quantity),
      average_price: typeof p.average_price === 'number' ? p.average_price : null,
    })),
  }
}

function asDisclosure(v: unknown): TaDisclosure | null {
  if (!isRecord(v)) return null
  const list = (x: unknown): string[] =>
    Array.isArray(x) ? x.filter((i) => typeof i === 'string') : []
  return {
    level: v.level as TaDisclosure['level'],
    includes: list(v.includes),
    never_sent: list(v.never_sent),
    currency: typeof v.currency === 'string' ? v.currency : null,
    currency_source: null,
    cash_basis: typeof v.cash_basis === 'string' ? v.cash_basis : null,
    fx_converted: Array.isArray(v.fx_converted)
      ? (v.fx_converted.filter(isRecord) as unknown as TaDisclosure['fx_converted'])
      : [],
    average_price_omitted: list(v.average_price_omitted),
    partial_cost: list(v.partial_cost),
    cash_omitted: typeof v.cash_omitted === 'string' ? v.cash_omitted : null,
    cash_floored: typeof v.cash_floored === 'string' ? v.cash_floored : null,
  }
}

function parse(preview: ToolPreview): RunPreview | null {
  const d = preview.data
  const disclosure = asDisclosure(d?.disclosure)
  if (!d || !disclosure || !text(d.ticker)) return null
  return {
    ticker: text(d.ticker),
    tradeDate: text(d.trade_date),
    analysts: Array.isArray(d.analysts) ? (d.analysts as TaAnalyst[]) : [],
    deepModel: text(d.deep_model),
    quickModel: text(d.quick_model),
    estimatedCost: text(d.estimated_cost),
    perRunCap: text(d.per_run_cap),
    thisMonth: text(d.this_month),
    portfolio: asPortfolio(d.shared_with_the_ai_provider),
    disclosure,
    dataVendors: text(d.data_vendors),
  }
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function analystNames(analysts: TaAnalyst[]): string {
  const names = analysts.map((a) => TA_ANALYST_LABEL[a] ?? a)
  return names.length === 1 ? names[0]! : names.join(' and ')
}

function lead(run: RunPreview): string {
  const n = run.analysts.length
  const who = n <= 2 && n > 0 ? analystNames(run.analysts) : `${n} ${pluralize('analyst', n)}`
  return `Research as of ${run.tradeDate} with ${who}`
}

function ModelFacts({ run }: { run: RunPreview }) {
  const row = (label: string, raw: string) => {
    const parts = modelFromDisplay(raw)
    return (
      <ApprovalFact key={label} label={label} title={raw}>
        {parts ? modelText(parts) : raw}
      </ApprovalFact>
    )
  }
  if (run.deepModel === run.quickModel) return <>{row('Model', run.deepModel)}</>
  return (
    <>
      {row('Deep model', run.deepModel)}
      {row('Quick model', run.quickModel)}
    </>
  )
}

function recipients(run: RunPreview): string[] {
  const seen = new Set<string>()
  for (const raw of [run.deepModel, run.quickModel]) {
    const parts: TaModelRef | null = modelFromDisplay(raw)
    seen.add(parts ? parts.provider : raw)
  }
  return [...seen]
}

function sharedSummary(portfolio: TaPortfolio, ticker: string, level: string): string {
  const cash =
    portfolio.cash === null
      ? 'cash unavailable'
      : `cash ${fCurrency(portfolio.cash, portfolio.currency)}`
  const n = portfolio.positions.length
  if (level === 'position') {
    return n === 0
      ? `Shared: ${cash} + no position (you don't hold ${ticker})`
      : `Shared: ${cash} + your ${ticker} position`
  }
  return n === 0
    ? `Shared: ${cash}, no positions`
    : `Shared: ${cash} + ${n} ${pluralize('position', n)}`
}

function DisclosureNotes({ disclosure }: { disclosure: TaDisclosure }) {
  const notes: ReactNode[] = [
    disclosure.fx_converted.length > 0 &&
      `Converted at today's rates: ${disclosure.fx_converted.map(fxLabel).join(' · ')}`,
    disclosure.average_price_omitted.length > 0 &&
      `Average price left out for ${disclosure.average_price_omitted.join(', ')} (no cost basis or no rate)`,
    disclosure.partial_cost.length > 0 &&
      `Average price for ${disclosure.partial_cost.join(', ')} covers only the lots with a known cost`,
    disclosure.cash_omitted && `Cash left out: ${disclosure.cash_omitted}`,
  ]
  return (
    <>
      {notes.filter(Boolean).map((note, i) => (
        <ApprovalMeta key={i} items={[note]} />
      ))}
    </>
  )
}

function SharedDetails({ run, portfolio }: { run: RunPreview; portfolio: TaPortfolio }) {
  const { disclosure } = run
  const money = (n: number) => fCurrency(n, portfolio.currency)
  return (
    <ApprovalDetails summary={sharedSummary(portfolio, run.ticker, disclosure.level)}>
      <ApprovalFacts>
        <ApprovalFact label="Cash" title={disclosure.cash_basis ?? undefined}>
          {portfolio.cash === null ? 'Unavailable' : money(portfolio.cash)}
        </ApprovalFact>
      </ApprovalFacts>
      {portfolio.positions.length > 0 && (
        <ApprovalTable
          head={['Ticker', 'Quantity', 'Avg price']}
          numeric={[1, 2]}
          rows={portfolio.positions.map((p) => ({
            key: p.ticker,
            cells: [
              p.ticker === run.ticker ? (
                <span className="ac-tone-strong">{p.ticker}</span>
              ) : (
                p.ticker
              ),
              fQty(p.quantity),
              p.average_price === null ? 'n/a' : fUnitPrice(p.average_price, portfolio.currency),
            ],
          }))}
        />
      )}
      <DisclosureNotes disclosure={disclosure} />
    </ApprovalDetails>
  )
}

function RawArguments({ value }: { value: unknown }) {
  return (
    <ApprovalDetails summary="Raw arguments">
      <ApprovalCode>{JSON.stringify(value ?? {}, null, 2)}</ApprovalCode>
    </ApprovalDetails>
  )
}

export function TradingAgentsRunPreview({ preview }: { preview: ToolPreview }) {
  const run = parse(preview)
  if (!run) {
    return (
      <>
        <ApprovalNote>No preview available</ApprovalNote>
        <RawArguments value={preview.raw_arguments} />
      </>
    )
  }
  const names = recipients(run).join(' and ')
  const neverSent = run.disclosure.never_sent
  return (
    <>
      <ApprovalLead>{lead(run)}</ApprovalLead>
      <ApprovalMeta items={['Research only', 'never places an order']} />
      <ApprovalFacts>
        <ApprovalFact
          label="Estimated cost"
          tone={/no known price/.test(run.estimatedCost) ? 'warn' : 'strong'}
        >
          {capitalise(run.estimatedCost)}
        </ApprovalFact>
        <ApprovalFact label="Run limit">{run.perRunCap}</ApprovalFact>
        <ApprovalFact label="This month">{run.thisMonth}</ApprovalFact>
        <ModelFacts run={run} />
        <ApprovalFact label="Shared with">
          {run.portfolio
            ? names
            : `${names} receive${recipients(run).length > 1 ? '' : 's'} the ticker and date`}
        </ApprovalFact>
        <ApprovalFact label="Data sources">{run.dataVendors}</ApprovalFact>
      </ApprovalFacts>
      {run.portfolio ? (
        <>
          <ApprovalNote tone="warn">Your real cash and position sizes go to {names}.</ApprovalNote>
          <SharedDetails run={run} portfolio={run.portfolio} />
        </>
      ) : (
        <ApprovalNote tone="muted">
          Nothing about your holdings is shared. Ticker and date only.
        </ApprovalNote>
      )}
      {neverSent.length > 0 && (
        <ApprovalDetails summary={`Never shared · ${neverSent.slice(0, 2).join(', ')}…`}>
          <ApprovalMeta items={[neverSent.join(', ')]} />
        </ApprovalDetails>
      )}
      <RawArguments value={preview.raw_arguments} />
    </>
  )
}
