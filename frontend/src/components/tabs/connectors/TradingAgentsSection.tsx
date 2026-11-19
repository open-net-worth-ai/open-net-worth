import { useTaConfig, useTaStatus } from '../../../hooks/useTradingAgents'
import { useModels } from '../../../hooks/useModels'
import type { TaAvailabilityStatus, TaConfig, TaStatus } from '../../../types/tradingAgents'
import { modelRef } from '../../../utils/tradingAgents'
import { Badge, Cluster, ErrorState, Loading, Stack, Text } from '../../ui'
import type { BadgeTone } from '../../ui'
import { TradingAgentsInstall } from './TradingAgentsInstall'
import {
  AnalystsSection,
  BudgetSection,
  ModelsSection,
  NoticeSection,
  Section,
} from './TradingAgentsSections'
import { ContextSection, DataSourcesSection, PermissionsSection } from './TradingAgentsDataSections'
import { useTaDraft } from './useTaDraft'

const AVAILABILITY: Record<TaAvailabilityStatus, { label: string; tone: BadgeTone }> = {
  ready: { label: 'Ready', tone: 'pos' },
  installing: { label: 'Installing', tone: 'warn' },
  model_unavailable: { label: 'Model unavailable', tone: 'warn' },
  not_installed: { label: 'Needs install', tone: 'neutral' },
  needs_reinstall: { label: 'Needs reinstall', tone: 'warn' },
  connector_paused: { label: 'Paused', tone: 'muted' },
  connector_off: { label: 'Not set up', tone: 'neutral' },
  flag_off: { label: 'Off', tone: 'muted' },
}

/** The AI providers behind the two chosen models, for the "who receives what" block. */
function chosenProviders(models: TaStatus['models']): string[] {
  if (!models) return []
  const refs = [
    modelRef(models.deep, models.transports.deep),
    modelRef(models.quick, models.transports.quick),
  ]
  return [...new Set(refs.flatMap((m) => (m ? [m.provider] : [])))]
}

function savedKeyVendors(cfg: TaConfig): string[] {
  return [cfg.has_alpha_vantage_api_key && 'Alpha Vantage', cfg.has_fred_api_key && 'FRED'].filter(
    (v): v is string => !!v,
  )
}

export function TradingAgentsSection() {
  const config = useTaConfig()
  const status = useTaStatus()
  const models = useModels(true)
  const cfg = config.data
  const form = useTaDraft(cfg, config.refresh)
  const st = status.data
  const { draft } = form

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
  if (!cfg || !draft || !st) return <Loading label="Loading TradingAgents settings…" />

  const availability = AVAILABILITY[st.availability.status]
  const shared = { draft, errors: form.fieldErrors, patch: form.patch }
  return (
    <Stack gap="--space-7">
      <Cluster justify="between" gap="--space-4">
        <Badge tone={availability.tone}>{availability.label}</Badge>
        <Text size="xs" tone="faint">
          {st.availability.message}
        </Text>
      </Cluster>
      <NoticeSection accepted={st.disclaimer_accepted} />
      <Section title="Install">
        <TradingAgentsInstall
          install={st.install}
          reinstallMessage={
            st.availability.status === 'needs_reinstall' ? st.availability.message : null
          }
        />
      </Section>
      <ModelsSection
        draft={draft}
        patch={form.patch}
        availability={st.availability}
        models={models}
      />
      <AnalystsSection {...shared} />
      <BudgetSection {...shared} cfg={cfg} status={st} saveErr={form.saveErr} />
      <DataSourcesSection
        {...shared}
        cfg={cfg}
        saving={form.saving}
        setDraft={form.setDraft}
        avReveal={form.avReveal}
        fredReveal={form.fredReveal}
      />
      <ContextSection
        draft={draft}
        patch={form.patch}
        providers={chosenProviders(st.models)}
        keyVendors={savedKeyVendors(cfg)}
      />
      <PermissionsSection />
    </Stack>
  )
}
