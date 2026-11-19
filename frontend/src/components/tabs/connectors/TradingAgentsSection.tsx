import { useCallback, useState, type ReactNode } from 'react'
import {
  acceptTaDisclaimer,
  saveTaConfig,
  TaError,
  useTaConfig,
  useTaStatus,
} from '../../../hooks/useTradingAgents'
import { useModels } from '../../../hooks/useModels'
import { TA_ANALYST_LABEL, TA_ANALYSTS } from '../../../types/tradingAgents'
import type { TaAnalyst, TaAvailabilityStatus, TaContextLevel } from '../../../types/tradingAgents'
import { fCurrency } from '../../../utils/format'
import { modelRef } from '../../../utils/tradingAgents'
import {
  Badge,
  Button,
  Checkbox,
  CheckboxGroup,
  Cluster,
  ErrorState,
  FormField,
  Grid,
  List,
  Loading,
  Meter,
  RadioGroup,
  Stack,
  Text,
  TextField,
  useSecretReveal,
} from '../../ui'
import type { BadgeTone, MeterTone } from '../../ui'
import { unrevealed, useConnectorSave, useConnectorWrote } from './formSave'
import { revealConnectorSecret } from './secrets'
import { TradingAgentsInstall } from './TradingAgentsInstall'
import { ModelField, NumField, KeyField } from './TradingAgentsFields'
import { TradingAgentsRecipients } from './TradingAgentsRecipients'
import { draftFromConfig, patchFromDraft, validateDraft } from './tradingAgentsDraft'
import type { TaDraft, TaDraftErrors } from './tradingAgentsDraft'

const USD = (n: number) => fCurrency(n, 'USD')

const AVAILABILITY: Record<TaAvailabilityStatus, { label: string; tone: BadgeTone }> = {
  ready: { label: 'Ready', tone: 'pos' },
  installing: { label: 'Installing', tone: 'warn' },
  model_unavailable: { label: 'Model unavailable', tone: 'warn' },
  not_installed: { label: 'Needs install', tone: 'neutral' },
  connector_paused: { label: 'Paused', tone: 'muted' },
  connector_off: { label: 'Not set up', tone: 'neutral' },
  flag_off: { label: 'Off', tone: 'muted' },
}

const CONTEXT_OPTIONS: { value: TaContextLevel; label: string; description: string }[] = [
  {
    value: 'investments',
    label: 'Investments',
    description:
      'Your investable cash and every investment position (ticker, quantity, average price).',
  },
  {
    value: 'position',
    label: 'This position',
    description: 'Your investable cash and only the position in the stock being analysed.',
  },
  {
    value: 'ticker',
    label: 'Ticker only',
    description: 'Nothing about your holdings. Ticker and date only.',
  },
]

const DATA_SOURCES: [string, string][] = [
  ['Yahoo Finance', 'prices, fundamentals, news'],
  ['SEC EDGAR', 'filings, insider activity; also receives the contact line below'],
  ['StockTwits and Reddit', 'social sentiment'],
  ['Polymarket', 'prediction markets'],
]

function Section({ title, children }: { title: string; children: ReactNode }) {
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

function meterTone(pct: number): MeterTone {
  return pct >= 100 ? 'bad' : pct >= 80 ? 'warn' : 'good'
}

export function TradingAgentsSection() {
  const config = useTaConfig()
  const status = useTaStatus()
  const { models, defaultModel, loaded } = useModels(true)
  const wrote = useConnectorWrote()
  const cfg = config.data
  const [draft, setDraft] = useState<TaDraft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<TaDraftErrors>({})
  const [ack, setAck] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [noticeErr, setNoticeErr] = useState<string | null>(null)

  const baseline = cfg ? draftFromConfig(cfg) : null
  const baselineJson = JSON.stringify(baseline)
  // The inputs follow the server only while nothing is being edited: `synced` is the baseline
  // they were last seeded from, so an untouched form takes a newer one and an edited form keeps
  // its edits.
  const [synced, setSynced] = useState<string | null>(null)
  const dirty = draft !== null && JSON.stringify(draft) !== synced
  if (baseline && baselineJson !== synced && !dirty) {
    setSynced(baselineJson)
    setDraft(baseline)
  }

  const patch = useCallback(<K extends keyof TaDraft>(key: K, value: TaDraft[K]) => {
    setDraft((d) => (d ? { ...d, [key]: value } : d))
    setSaveErr(null)
  }, [])

  const avReveal = useSecretReveal({
    saved: !!cfg?.has_alpha_vantage_api_key,
    value: draft?.alphaVantageKey ?? '',
    onChange: (v) =>
      setDraft((d) => (d ? { ...d, alphaVantageKey: v, clearAlphaVantage: false } : d)),
    reveal: useCallback(() => revealConnectorSecret('trading_agents', 'alpha_vantage_api_key'), []),
  })
  const fredReveal = useSecretReveal({
    saved: !!cfg?.has_fred_api_key,
    value: draft?.fredKey ?? '',
    onChange: (v) => setDraft((d) => (d ? { ...d, fredKey: v, clearFred: false } : d)),
    reveal: useCallback(() => revealConnectorSecret('trading_agents', 'fred_api_key'), []),
  })

  async function save(): Promise<boolean> {
    if (!draft || !cfg) return false
    const errors = validateDraft(draft)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      setSaveErr('Fix the highlighted fields.')
      return false
    }
    setSaving(true)
    setSaveErr(null)
    try {
      const saved = await saveTaConfig(patchFromDraft(draft, cfg))
      setDraft(draftFromConfig(saved))
      setSynced(JSON.stringify(draftFromConfig(saved)))
      await config.refresh()
      return true
    } catch (e) {
      if (e instanceof TaError && e.field) {
        setFieldErrors({ [e.field]: e.message })
        setSaveErr('Fix the highlighted fields.')
      } else {
        setSaveErr(e instanceof TaError ? e.message : 'Could not save, could not reach the server.')
      }
      return false
    } finally {
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: !!cfg && !!draft,
    values: draft && {
      ...draft,
      alphaVantageKey: unrevealed(draft.alphaVantageKey, avReveal),
      fredKey: unrevealed(draft.fredKey, fredReveal),
    },
    baseline,
    saving,
    error: saveErr,
    save,
  })

  async function accept() {
    setAccepting(true)
    setNoticeErr(null)
    try {
      await acceptTaDisclaimer()
      wrote()
    } catch (e) {
      setNoticeErr(e instanceof TaError ? e.message : 'Could not record the notice.')
    } finally {
      setAccepting(false)
    }
  }

  if (config.error || status.error) {
    const retry = () => {
      void config.refresh()
      void status.refresh()
    }
    return (
      <ErrorState
        title="Couldn't load TradingAgents"
        message="Check the connection and try again."
        onRetry={retry}
      />
    )
  }
  const st = status.data
  if (!cfg || !draft || !st) return <Loading label="Loading TradingAgents settings…" />

  const availability = AVAILABILITY[st.availability.status]
  const spendPct = cfg.monthly_cap_usd > 0 ? (st.month_spend_usd / cfg.monthly_cap_usd) * 100 : null
  const keyVendors = [
    cfg.has_alpha_vantage_api_key && 'Alpha Vantage',
    cfg.has_fred_api_key && 'FRED',
  ].filter((v): v is string => !!v)
  const chosen = st.models
    ? [
        modelRef(st.models.deep, st.models.transports.deep),
        modelRef(st.models.quick, st.models.transports.quick),
      ]
    : []
  const providers = [...new Set(chosen.flatMap((m) => (m ? [m.provider] : [])))]

  return (
    <Stack gap="--space-7">
      <Cluster justify="between" gap="--space-4">
        <Badge tone={availability.tone}>{availability.label}</Badge>
        <Text size="xs" tone="faint">
          {st.availability.message}
        </Text>
      </Cluster>

      <Section title="Research-only notice">
        <div className="ta-notice">
          TradingAgents is research software. A team of AI analysts reads public data about a stock,
          debates it, and a portfolio manager gives a rating from Buy to Sell. The rating is
          generated by AI models, can be wrong, incomplete or out of date, and is not financial
          advice. Open Net Worth never places an order, and neither does TradingAgents. Any decision
          is yours.
        </div>
        {st.disclaimer_accepted ? (
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
            {noticeErr && (
              <Text size="xs" tone="bad">
                {noticeErr}
              </Text>
            )}
          </>
        )}
      </Section>

      <Section title="Install">
        <TradingAgentsInstall install={st.install} />
      </Section>

      <Section title="Models">
        <Text size="sm" tone="faint">
          Every model call goes through your own AI provider in Open Net Worth, so there are no
          extra API keys and every call shows in your usage totals. TradingAgents never talks to an
          AI vendor directly.
        </Text>
        <Grid min={280} gap="--space-5">
          <ModelField
            id="ta-deep"
            label="Deep-thinking model"
            help="Used for the research manager and the portfolio manager, the two decisions that matter most."
            defaultLabel="Your Lucius model"
            value={draft.deepModel}
            onChange={(v) => patch('deepModel', v)}
            models={models}
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
            models={models}
            defaultModel={defaultModel}
            loaded={loaded}
          />
        </Grid>
        {st.availability.status === 'model_unavailable' && (
          <Text size="sm" tone="warn">
            {st.availability.message}
          </Text>
        )}
      </Section>

      <Section title="Analysts and depth">
        <FormField label="Analysts" error={fieldErrors.analysts}>
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
            error={fieldErrors.max_debate_rounds}
            onChange={(v) => patch('debateRounds', v)}
          />
          <NumField
            id="ta-risk"
            label="Risk rounds (1–3)"
            value={draft.riskRounds}
            error={fieldErrors.max_risk_rounds}
            onChange={(v) => patch('riskRounds', v)}
          />
          <NumField
            id="ta-tools"
            label="Tool rounds per analyst (2–20)"
            help="Lower is cheaper."
            value={draft.toolRounds}
            error={fieldErrors.max_tool_rounds}
            onChange={(v) => patch('toolRounds', v)}
          />
          <NumField
            id="ta-calls"
            label="Max model calls per run (20–600)"
            help="A hard stop per run. Protects your plan's quota on a subscription."
            value={draft.maxCalls}
            error={fieldErrors.max_llm_calls}
            onChange={(v) => patch('maxCalls', v)}
          />
          <NumField
            id="ta-conc"
            label="Parallel model calls (1–8)"
            help="Leave blank for automatic (4 on API models, 2 on a subscription)."
            value={draft.concurrency}
            error={fieldErrors.bridge_concurrency}
            onChange={(v) => patch('concurrency', v)}
          />
          <FormField label="Report language" htmlFor="ta-lang" error={fieldErrors.output_language}>
            <TextField
              id="ta-lang"
              value={draft.language}
              invalid={!!fieldErrors.output_language}
              onChange={(e) => patch('language', e.target.value)}
            />
          </FormField>
        </Grid>
      </Section>

      <Section title="Budget">
        <Text size="sm" tone="faint">
          Caps are enforced: a run stops when it reaches its cap, and a new run is refused if it
          would pass your monthly cap.
        </Text>
        <Grid min={220} gap="--space-5">
          <NumField
            id="ta-per-run"
            label="Per-run cap (USD)"
            step={0.1}
            value={draft.perRunCap}
            error={fieldErrors.per_run_cap_usd}
            onChange={(v) => patch('perRunCap', v)}
          />
          <NumField
            id="ta-monthly"
            label="Monthly cap (USD)"
            help="0 = no metered runs"
            step={1}
            value={draft.monthlyCap}
            error={fieldErrors.monthly_cap_usd}
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
            valueText={`${USD(st.month_spend_usd)} of ${USD(cfg.monthly_cap_usd)}`}
          />
        )}
        <Text size="xs" tone="faint">
          {USD(st.month_spend_usd)} of {USD(cfg.monthly_cap_usd)} this month · {st.queue_length}{' '}
          queued
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

      <Section title="Data sources">
        <Text size="sm" tone="faint">
          TradingAgents reads public data. These services receive the ticker and the date, never
          your amounts.
        </Text>
        <List>
          {DATA_SOURCES.map(([name, what]) => (
            <li key={name}>
              <strong>{name}</strong> ({what})
            </li>
          ))}
          <li>
            Optional with a key: <strong>Alpha Vantage</strong> (extra fundamentals and news),{' '}
            <strong>FRED</strong> (economic data).
          </li>
        </List>
        <Grid min={280} gap="--space-5">
          <KeyField
            id="ta-av"
            label="Alpha Vantage API key (optional)"
            saved={cfg.has_alpha_vantage_api_key}
            value={draft.alphaVantageKey}
            clear={draft.clearAlphaVantage}
            reveal={avReveal}
            saving={saving}
            onChange={(v) => patch('alphaVantageKey', v)}
            onClear={() =>
              setDraft(
                (d) => d && { ...d, alphaVantageKey: '', clearAlphaVantage: !d.clearAlphaVantage },
              )
            }
          />
          <KeyField
            id="ta-fred"
            label="FRED API key (optional)"
            saved={cfg.has_fred_api_key}
            value={draft.fredKey}
            clear={draft.clearFred}
            reveal={fredReveal}
            saving={saving}
            onChange={(v) => patch('fredKey', v)}
            onClear={() => setDraft((d) => d && { ...d, fredKey: '', clearFred: !d.clearFred })}
          />
        </Grid>
        <FormField
          label="SEC EDGAR contact (user agent)"
          htmlFor="ta-sec"
          help="Sent to SEC EDGAR with every request, as the SEC asks. If you put your name or email here, SEC EDGAR receives it. Never filled from your profile."
          error={fieldErrors.sec_edgar_user_agent}
        >
          <TextField
            id="ta-sec"
            value={draft.secUserAgent}
            invalid={!!fieldErrors.sec_edgar_user_agent}
            onChange={(e) => patch('secUserAgent', e.target.value)}
          />
        </FormField>
      </Section>

      <Section title="Portfolio context">
        <Text size="sm" tone="faint">
          A run can be given your real investing amounts so the trader and risk team can size advice
          against what you hold, for example: you hold 120 shares, that is 18% of your investments,
          and you have 9,400 to deploy. You choose how much it sees; you see the exact list before
          every run.
        </Text>
        <FormField label="Default for each run">
          <RadioGroup
            name="ta-context-level"
            ariaLabel="Default portfolio context"
            value={draft.contextLevel}
            onChange={(v) => patch('contextLevel', v as TaContextLevel)}
            options={CONTEXT_OPTIONS.map((o) => ({
              ...o,
              badge: o.value === 'investments' ? <Badge tone="neutral">Default</Badge> : undefined,
            }))}
          />
        </FormField>
        <TradingAgentsRecipients providers={providers} keys={keyVendors} />
        <Text size="xs" tone="faint">
          Cash is the cash in your cash accounts plus the cash inside your investment accounts,
          before money set aside in budget envelopes. Amounts are converted to the stock&apos;s
          currency at today&apos;s rates. Retirement accounts, unvested equity, owned assets and
          untracked brokerage totals are not included.
        </Text>
        <Text size="xs" tone="faint">
          Your thesis is never sent. Open Net Worth compares the rating to it afterwards, on this
          computer.
        </Text>
      </Section>

      <Section title="Permissions">
        <Text size="sm" tone="faint">
          Which tools Lucius may use, and whether starting a run asks you first, is set under Tool
          permissions on this connector. Starting a run asks every time by default.
        </Text>
      </Section>
    </Stack>
  )
}
