// Settings → Intelligence. Every Intel setting in one place: monitoring cadence, where each kind
// of update is sent, the AI analysis on watched tickers (and how far its opinion goes), the pulse
// briefs' detail, and the advanced alert thresholds. Reads and writes the watchlist store's
// monitoring config through `useMonitoringConfig`, the same cache the Intel tab polls.
import { useFlag } from '../../../../../hooks/useFlag'
import { useMonitoringConfig } from '../../../../intel/monitoringConfig'
import type { MonitoringConfig } from '../../../../intel/monitoringConfig'
import { useSectionBusy } from '../../ds'
import type { SectionProps } from '../../ds'
import { AdvancedPanel } from './AdvancedPanel'
import { AiAnalysisPanel } from './AiAnalysisPanel'
import { AlertsPanel } from './AlertsPanel'
import { DeliveryPanel } from './DeliveryPanel'
import { MonitoringCadencePanel } from './MonitoringCadencePanel'
import { PulseSummaryPanel } from './PulseSummaryPanel'

export function IntelSection({ toast }: SectionProps) {
  const { cfg, channels, loading, patch } = useMonitoringConfig()
  const delivery = useFlag('intel_delivery_decider')
  useSectionBusy(loading)
  if (!cfg) return null

  const save = (changes: Partial<MonitoringConfig>, message: string) => {
    void patch(changes).then((ok) => toast(ok ? message : 'Could not save — try again'))
  }

  return (
    <>
      <MonitoringCadencePanel cfg={cfg} save={save} />
      <AlertsPanel cfg={cfg} save={save} channels={channels} />
      {delivery === true && <DeliveryPanel cfg={cfg} save={save} />}
      <AiAnalysisPanel cfg={cfg} save={save} />
      <PulseSummaryPanel cfg={cfg} save={save} />
      <AdvancedPanel cfg={cfg} save={save} />
    </>
  )
}
