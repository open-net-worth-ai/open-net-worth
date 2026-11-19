import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  startTaRun,
  TaError,
  useTaContextPreview,
  useTaEstimate,
} from '../../hooks/useTradingAgents'
import { TA_ANALYST_LABEL, TA_ANALYSTS, TA_CONTEXT_LABEL } from '../../types/tradingAgentsConstants'
import type { TaAnalyst, TaContextLevel, TaEstimate, TaStatus } from '../../types/tradingAgents'
import { fCurrency } from '../../utils/format'
import { runPlanLabel, runProviders } from '../../utils/tradingAgents'
import {
  Button,
  CheckboxGroup,
  Cluster,
  FormField,
  Loading,
  Meter,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
} from '../ui'
import { TradingAgentsShared } from './TradingAgentsShared'

const USD = (n: number) => fCurrency(n, 'USD')

const LEVEL_HELP: Record<TaContextLevel, string> = {
  investments:
    'Your investable cash and every investment position (ticker, quantity, average price).',
  position: 'Your investable cash and only the position in the stock being analysed.',
  ticker: 'Nothing about your holdings. Ticker and date only.',
}

function EstimateText({ estimate, plan }: { estimate: TaEstimate; plan: string | null }) {
  const calls = `about ${estimate.calls} model calls`
  const range = `${USD(estimate.estimate_usd)} (range ${USD(estimate.low_usd)} – ${USD(estimate.high_usd)})`
  const main = estimate.plan_included
    ? `Uses your ${plan ?? 'subscription'} · ${calls} · no dollars`
    : plan
      ? `About ${range} metered, plus calls on your ${plan} · ${calls}`
      : `About ${range} · ${calls}`
  const basis = estimate.plan_included
    ? "Counts against your plan's quota. Runs take longer on a plan."
    : estimate.basis === 'history'
      ? `Based on your last ${estimate.sample_size} runs.`
      : 'Rough estimate: no run history yet.'
  return (
    <>
      <Text size="sm">{main}</Text>
      <Text size="xs" tone="faint">
        {basis}
      </Text>
      {!estimate.priced && (
        <Text size="sm" tone="warn">
          {estimate.unpriced_models.length > 0
            ? `No price known for ${estimate.unpriced_models.join(', ')} — a run on it is refused.`
            : 'A model has no known price, so this understates.'}
        </Text>
      )}
    </>
  )
}

/** Pick analysts and how much of the book a run may see, read what it would cost and exactly
 *  what would be shared, and start it. The server enforces every cap; a refusal shows its own
 *  sentence under the button. */
export function TradingAgentsRunForm({
  ticker,
  status,
  initialAnalysts,
  initialLevel,
  onStarted,
  onCancel,
}: {
  ticker: string
  status: TaStatus
  initialAnalysts?: TaAnalyst[]
  initialLevel?: TaContextLevel
  onStarted: () => void
  /** Set when this form replaced a result: returns to it. */
  onCancel?: () => void
}) {
  const navigate = useNavigate()
  const [analysts, setAnalysts] = useState<TaAnalyst[]>(initialAnalysts ?? status.analysts)
  const [level, setLevel] = useState<TaContextLevel>(initialLevel ?? status.context_level)
  const [showShared, setShowShared] = useState(false)
  const [starting, setStarting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const estimate = useTaEstimate(ticker, analysts, true)
  const preview = useTaContextPreview(ticker, level, showShared)

  const plan = status.models ? runPlanLabel({ transports: status.models.transports }) : null
  const { caps, month_spend_usd: spend } = status
  const metered = caps.monthly_cap_usd > 0
  const pct = metered ? (spend / caps.monthly_cap_usd) * 100 : 0
  const planOnly = estimate.data?.plan_included === true
  const blockedByCap = !metered && !planOnly
  const previewBroken = showShared && !!preview.error
  const providers = status.models
    ? runProviders({
        deep: status.models.deep,
        quick: status.models.quick,
        transports: status.models.transports,
      })
    : []

  async function start() {
    setStarting(true)
    setRefusal(null)
    try {
      await startTaRun(ticker, analysts, level)
      onStarted()
    } catch (e) {
      setRefusal(e instanceof TaError ? e.message : 'Could not start the analysis.')
    } finally {
      setStarting(false)
    }
  }

  const openSetup = () =>
    void navigate({ to: '/connectors', search: { connector: 'trading_agents' } } as never)

  const heldNote =
    level === 'position' && preview.data?.portfolio && preview.data.portfolio.positions.length === 0
      ? `You don't hold ${ticker}, so only your cash is shared.`
      : LEVEL_HELP[level]

  return (
    <Stack gap="--space-5">
      <Text size="sm" tone="faint">
        Analysts debate this stock and a portfolio manager gives a five-step rating. Research, not
        financial advice. It never places an order.
      </Text>
      <FormField
        label="Analysts"
        error={analysts.length === 0 ? 'Pick at least one analyst.' : undefined}
      >
        <CheckboxGroup
          ariaLabel="Analysts"
          columns={2}
          value={analysts}
          onChange={(next) => setAnalysts(next as TaAnalyst[])}
          options={TA_ANALYSTS.map((a) => ({ value: a, label: TA_ANALYST_LABEL[a] }))}
        />
      </FormField>
      <FormField label="What it may know about your holdings">
        <Stack gap="--space-3">
          <SegmentedControl
            ariaLabel="Portfolio context"
            value={level}
            onChange={setLevel}
            options={(['investments', 'position', 'ticker'] as const).map((value) => ({
              value,
              label: TA_CONTEXT_LABEL[value],
            }))}
          />
          <Text size="xs" tone="faint">
            {heldNote}
          </Text>
        </Stack>
      </FormField>
      <details
        className="ta-disclose"
        onToggle={(e) => setShowShared((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary>What will be shared</summary>
        <div className="ta-disclose__body">
          {preview.data ? (
            <TradingAgentsShared context={preview.data} providers={providers} />
          ) : previewBroken ? (
            <Cluster gap="--space-4">
              <Text size="xs" tone="bad">
                Couldn&apos;t load what would be shared, so the run can&apos;t start yet.
              </Text>
              <Button variant="ghost" size="sm" onClick={() => void preview.refresh()}>
                Retry
              </Button>
            </Cluster>
          ) : (
            <Loading label="Reading your holdings…" />
          )}
        </div>
      </details>
      <Stack gap="--space-3">
        {estimate.data ? (
          <EstimateText estimate={estimate.data} plan={plan} />
        ) : estimate.error ? (
          <Text size="xs" tone="faint">
            Estimate unavailable.
          </Text>
        ) : (
          <Skeleton height={13} width="70%" />
        )}
        <Text size="xs" tone="faint">
          Stops at {USD(caps.per_run_cap_usd)} or {caps.max_llm_calls} calls per run.
        </Text>
        {metered ? (
          <Meter
            size="sm"
            value={pct}
            tone={pct >= 100 ? 'bad' : pct >= 80 ? 'warn' : 'good'}
            ariaLabel="Monthly spend"
            valueText={`${USD(spend)} of ${USD(caps.monthly_cap_usd)}`}
          />
        ) : null}
        <Text size="xs" tone="faint">
          {metered
            ? `${USD(spend)} of ${USD(caps.monthly_cap_usd)} this month`
            : 'Metered runs are off (monthly cap is $0)'}
        </Text>
      </Stack>
      <Cluster justify="end" gap="--space-4">
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button
          variant="primary"
          loading={starting}
          disabled={analysts.length === 0 || blockedByCap || previewBroken}
          onClick={() => void start()}
        >
          Run multi-agent analysis
        </Button>
      </Cluster>
      {refusal && (
        <Cluster gap="--space-4">
          <Text size="xs" tone="bad">
            {refusal}
          </Text>
          <Button variant="ghost" size="sm" onClick={openSetup}>
            Open setup
          </Button>
        </Cluster>
      )}
    </Stack>
  )
}
