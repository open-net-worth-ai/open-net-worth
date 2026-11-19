import { TAB_SUBTITLES } from '../../tabs'
import { Button, Cluster, PageHeader } from '../ui'
import { Radar } from '../../ui/icons'
import { FreshnessChip } from './FreshnessChip'
import type { MonitoringConfig } from './monitoringConfig'

/** Intel's page header: when prices and news were last checked (while monitoring is on) and the
 *  manual Scan button. */
export function IntelPageHeader({
  config: cfg,
  scanning,
  onScan,
}: {
  config?: MonitoringConfig
  scanning: boolean
  onScan: () => void
}) {
  return (
    <PageHeader
      className="intel-page-header"
      title="Intelligence"
      subtitle={TAB_SUBTITLES.intel}
      actions={
        <Cluster className="intel-header-actions">
          {cfg?.enabled && (
            <>
              <FreshnessChip
                label="Prices"
                timestamp={cfg.last_price_check_at ?? undefined}
                intervalSeconds={cfg.price_check_interval_seconds}
              />
              <FreshnessChip
                label="News"
                timestamp={cfg.last_scan_at ?? undefined}
                intervalSeconds={cfg.news_scan_interval_seconds}
              />
            </>
          )}
          <Button
            variant="primary"
            size="sm"
            className="intel-scan-button"
            aria-label={scanning ? 'Scanning intelligence' : 'Scan intelligence'}
            iconLeft={<Radar size={14} aria-hidden="true" />}
            onClick={onScan}
            disabled={scanning}
          >
            {scanning ? 'Scanning…' : 'Scan'}
          </Button>
        </Cluster>
      }
    />
  )
}
