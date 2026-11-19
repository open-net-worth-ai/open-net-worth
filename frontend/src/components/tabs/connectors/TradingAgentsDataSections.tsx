import type { TaConfig, TaContextLevel } from '../../../types/tradingAgents'
import { Badge, FormField, Grid, List, RadioGroup, Text, TextField } from '../../ui'
import type { useSecretReveal } from '../../ui'
import { KeyField } from './TradingAgentsFields'
import { TradingAgentsRecipients } from './TradingAgentsRecipients'
import { Section } from './TradingAgentsSections'
import type { FormProps } from './TradingAgentsSections'
import type { TaDraft } from './tradingAgentsDraft'

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

type Reveal = ReturnType<typeof useSecretReveal>

export function DataSourcesSection({
  draft,
  errors,
  patch,
  cfg,
  saving,
  setDraft,
  avReveal,
  fredReveal,
}: FormProps & {
  cfg: TaConfig
  saving: boolean
  setDraft: (update: (d: TaDraft | null) => TaDraft | null) => void
  avReveal: Reveal
  fredReveal: Reveal
}) {
  return (
    <Section title="Data sources">
      <Text size="sm" tone="faint">
        TradingAgents reads public data. These services receive the ticker and the date, never your
        amounts.
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
        error={errors.sec_edgar_user_agent}
      >
        <TextField
          id="ta-sec"
          value={draft.secUserAgent}
          invalid={!!errors.sec_edgar_user_agent}
          onChange={(e) => patch('secUserAgent', e.target.value)}
        />
      </FormField>
    </Section>
  )
}

export function ContextSection({
  draft,
  patch,
  providers,
  keyVendors,
}: Pick<FormProps, 'draft' | 'patch'> & { providers: string[]; keyVendors: string[] }) {
  return (
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
        Cash is the cash in your cash accounts plus the cash inside your investment accounts, before
        money set aside in budget envelopes. Amounts are converted to the stock&apos;s currency at
        today&apos;s rates. Retirement accounts, unvested equity, owned assets and untracked
        brokerage totals are not included.
      </Text>
      <Text size="xs" tone="faint">
        Your thesis is never sent. Open Net Worth compares the rating to it afterwards, on this
        computer.
      </Text>
    </Section>
  )
}

export function PermissionsSection() {
  return (
    <Section title="Permissions">
      <Text size="sm" tone="faint">
        Which tools Lucius may use, and whether starting a run asks you first, is set under Tool
        permissions on this connector. Starting a run asks every time by default.
      </Text>
    </Section>
  )
}
