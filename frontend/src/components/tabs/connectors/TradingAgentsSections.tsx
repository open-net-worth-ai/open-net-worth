import { useState, type ReactNode } from 'react'
import { acceptTaDisclaimer, TaError } from '../../../hooks/useTradingAgents'
import type { useModels } from '../../../hooks/useModels'
import type { TaAnalyst, TaConfig, TaStatus } from '../../../types/tradingAgents'
import { TA_ANALYST_LABEL, TA_ANALYSTS } from '../../../types/tradingAgentsConstants'
import { fCurrency } from '../../../utils/format'
import {
  Button,
  Checkbox,
  CheckboxGroup,
  Cluster,
  FormField,
  Grid,
  Meter,
  Stack,
  Text,
  TextField,
} from '../../ui'
import type { MeterTone } from '../../ui'
import { useConnectorWrote } from './formSave'
import { ModelField, NumField } from './TradingAgentsFields'
import type { TaDraft, TaDraftErrors } from './tradingAgentsDraft'
import type { TaPatch } from './useTaDraft'

const USD = (n: number) => fCurrency(n, 'USD')

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="ta-section" aria-label={title}>
      <Stack gap="--space-4">
        <Text as="h3" size="md" weight={600}>
          {title}
        </Text>
        {children}
      </Stack>
    </section>
  )
}

export interface FormProps {
  draft: TaDraft
  errors: TaDraftErrors
  patch: TaPatch
}

export function NoticeSection({ accepted }: { accepted: boolean }) {
  const wrote = useConnectorWrote()
  const [ack, setAck] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function accept() {
    setAccepting(true)
    setError(null)
    try {
      await acceptTaDisclaimer()
      wrote()
    } catch (e) {
      setError(e instanceof TaError ? e.message : 'Could not record the notice.')
    } finally {
      setAccepting(false)
    }
  }

  return (
    <Section title="Research-only notice">
      <div className="ta-notice">
        TradingAgents is research software. A team of AI analysts reads public data about a stock,
        debates it, and a portfolio manager gives a rating from Buy to Sell. The rating is generated
        by AI models, can be wrong, incomplete or out of date, and is not financial advice. Open Net
        Worth never places an order, and neither does TradingAgents. Any decision is yours.
      </div>
      {accepted ? (
        <Text size="sm" tone="good">
          Notice accepted.
        </Text>
      ) : (
        <>
          <Checkbox
            id="ta-ack"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
            label="I understand this is research only, not financial advice."
          />
          <Cluster>
            <Button
              variant="primary"
              size="sm"
              disabled={!ack}
              loading={accepting}
              aria-describedby={ack ? undefined : 'ta-ack-why'}
              onClick={() => void accept()}
            >
              Accept and continue
            </Button>
            {!ack && (
              <Text size="xs" tone="faint">
                <span id="ta-ack-why">Tick the box to continue</span>
              </Text>
            )}
          </Cluster>
          {error && (
            <Text size="xs" tone="bad">
              {error}
            </Text>
          )}
        </>
      )}
    </Section>
  )
}

export function ModelsSection({
  draft,
  patch,
  availability,
  models,
}: {
  draft: TaDraft
  patch: TaPatch
  availability: TaStatus['availability']
  models: Pick<ReturnType<typeof useModels>, 'models' | 'defaultModel' | 'loaded'>
}) {
  const { models: list, defaultModel, loaded } = models
  return (
    <Section title="Models">
      <Text size="sm" tone="faint">
        Every model call goes through your own AI provider in Open Net Worth, so there are no extra
        API keys and every call shows in your usage totals. TradingAgents never talks to an AI
        vendor directly.
      </Text>
      <Grid min={280} gap="--space-5">
        <ModelField
          id="ta-deep"
          label="Deep-thinking model"
          help="Used for the research manager and the portfolio manager, the two decisions that matter most."
          defaultLabel="Your Lucius model"
          value={draft.deepModel}
          onChange={(v) => patch('deepModel', v)}
          models={list}
          defaultModel={defaultModel}
          loaded={loaded}
        />
        <ModelField
          id="ta-quick"
          label="Quick model"
          help="Used for the analysts, the debates, the trader and the risk panel. Most calls go here, so a cheaper model saves the most."
          defaultLabel="Intel model, else deep"
          value={draft.quickModel}
          onChange={(v) => patch('quickModel', v)}
          models={list}
          defaultModel={defaultModel}
          loaded={loaded}
        />
      </Grid>
      {availability.status === 'model_unavailable' && (
        <Text size="sm" tone="warn">
          {availability.message}
        </Text>
      )}
    </Section>
  )
}

export function AnalystsSection({ draft, errors, patch }: FormProps) {
  return (
    <Section title="Analysts and depth">
      <FormField label="Analysts" error={errors.analysts}>
        <CheckboxGroup
          ariaLabel="Analysts"
          columns={2}
          value={draft.analysts}
          onChange={(next) => patch('analysts', next as TaAnalyst[])}
          options={TA_ANALYSTS.map((a) => ({ value: a, label: TA_ANALYST_LABEL[a] }))}
        />
      </FormField>
      <Grid min={220} gap="--space-5">
        <NumField
          id="ta-debate"
          label="Debate rounds (1–3)"
          help="More rounds mean a longer debate and a higher cost."
          value={draft.debateRounds}
          error={errors.max_debate_rounds}
          onChange={(v) => patch('debateRounds', v)}
        />
        <NumField
          id="ta-risk"
          label="Risk rounds (1–3)"
          value={draft.riskRounds}
          error={errors.max_risk_rounds}
          onChange={(v) => patch('riskRounds', v)}
        />
        <NumField
          id="ta-tools"
          label="Tool rounds per analyst (2–20)"
          help="Lower is cheaper."
          value={draft.toolRounds}
          error={errors.max_tool_rounds}
          onChange={(v) => patch('toolRounds', v)}
        />
        <NumField
          id="ta-calls"
          label="Max model calls per run (20–600)"
          help="A hard stop per run. Protects your plan's quota on a subscription."
          value={draft.maxCalls}
          error={errors.max_llm_calls}
          onChange={(v) => patch('maxCalls', v)}
        />
        <NumField
          id="ta-conc"
          label="Parallel model calls (1–8)"
          help="Leave blank for automatic (4 on API models, 2 on a subscription)."
          value={draft.concurrency}
          error={errors.bridge_concurrency}
          onChange={(v) => patch('concurrency', v)}
        />
        <FormField label="Report language" htmlFor="ta-lang" error={errors.output_language}>
          <TextField
            id="ta-lang"
            value={draft.language}
            invalid={!!errors.output_language}
            onChange={(e) => patch('language', e.target.value)}
          />
        </FormField>
      </Grid>
    </Section>
  )
}

function meterTone(pct: number): MeterTone {
  return pct >= 100 ? 'bad' : pct >= 80 ? 'warn' : 'good'
}

export function BudgetSection({
  draft,
  errors,
  patch,
  cfg,
  status,
  saveErr,
}: FormProps & { cfg: TaConfig; status: TaStatus; saveErr: string | null }) {
  const spendPct =
    cfg.monthly_cap_usd > 0 ? (status.month_spend_usd / cfg.monthly_cap_usd) * 100 : null
  return (
    <Section title="Budget">
      <Text size="sm" tone="faint">
        Caps are enforced: a run stops when it reaches its cap, and a new run is refused if it would
        pass your monthly cap.
      </Text>
      <Grid min={220} gap="--space-5">
        <NumField
          id="ta-per-run"
          label="Per-run cap (USD)"
          step={0.1}
          value={draft.perRunCap}
          error={errors.per_run_cap_usd}
          onChange={(v) => patch('perRunCap', v)}
        />
        <NumField
          id="ta-monthly"
          label="Monthly cap (USD)"
          help="0 = no metered runs"
          step={1}
          value={draft.monthlyCap}
          error={errors.monthly_cap_usd}
          onChange={(v) => patch('monthlyCap', v)}
        />
      </Grid>
      {spendPct === null ? (
        <Text size="xs" tone="faint">
          Metered runs are off.
        </Text>
      ) : (
        <Meter
          size="md"
          value={spendPct}
          tone={meterTone(spendPct)}
          ariaLabel="Spend this month"
          valueText={`${USD(status.month_spend_usd)} of ${USD(cfg.monthly_cap_usd)}`}
        />
      )}
      <Text size="xs" tone="faint">
        {USD(status.month_spend_usd)} of {USD(cfg.monthly_cap_usd)} this month ·{' '}
        {status.queue_length} queued
      </Text>
      <Text size="xs" tone="faint">
        Runs on a subscription model (Claude Code, Codex, Antigravity) are not counted in dollars
        and are not blocked by the monthly cap. Their call limit still applies.
      </Text>
      <Text size="xs" tone="faint">
        TradingAgents spend is tracked in US dollars, whatever your base currency.
      </Text>
      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Section>
  )
}
