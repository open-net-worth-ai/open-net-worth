// A Dashboard panel = one declared server query + a view (the Grafana model).
//
// The query is a `GET /api/query` source (`panel_*`, backend/services/dashboard_panels.py) that
// answers with every figure and verdict the widget shows. The view formats and draws; it never
// adds, subtracts, divides, rounds, sorts or reads the clock — `noBrowserMath.test.ts` fails
// the build if one does. ADR 2026-11-22-a-dashboard-widget-is-a-server-query-and-a-view.

import type { ComponentType } from 'react'
import { useDataVersion } from '../../../../hooks/useDataVersion'
import { useFlag } from '../../../../hooks/useFlag'
import {
  querySource,
  type DataSource,
  type QueryParams,
} from '../../../../hooks/serverState/dataQuery'
import { useQueryCache } from '../../../../state/queryCache'
import type { WidgetContentProps, WidgetDefinition, WidgetInstance } from '../types'

export const SERVER_PANELS_FLAG = 'dashboard_server_panels'

export interface PanelQuery {
  source: DataSource
  params: QueryParams
}

export interface PanelViewProps<T> extends WidgetContentProps {
  /** The server's answer; undefined until it first arrives. */
  data: T | undefined
  /** Nothing to show yet. */
  loading: boolean
  /** The query failed with nothing earlier to show. */
  error: boolean
}

export interface PanelDefinition<T> {
  /** The ONE query this widget runs, from its settings. */
  query: (instance: WidgetInstance, def: WidgetDefinition) => PanelQuery
  /** The write domains that make its answer stale (`useDataVersion`). */
  signals: readonly string[]
  View: ComponentType<PanelViewProps<T>>
}

/** One cache entry per query identity: the source and its parameters. */
export function panelKey({ source, params }: PanelQuery): string {
  return `/api/query#${source}?${JSON.stringify(params)}`
}

function usePanelQuery<T>(query: PanelQuery, signals: readonly string[]) {
  const version = useDataVersion([...signals])
  return useQueryCache<T>(panelKey(query), () => querySource<T>(query.source, query.params), {
    version,
  })
}

/** The widget component for a panel: run its declared query, hand the answer to its view. */
export function panelWidget<T>(panel: PanelDefinition<T>): ComponentType<WidgetContentProps> {
  function Panel(props: WidgetContentProps) {
    const result = usePanelQuery<T>(panel.query(props.instance, props.def), panel.signals)
    return (
      <panel.View
        {...props}
        data={result.data}
        loading={result.data === undefined && !result.error}
        error={!!result.error && result.data === undefined}
      />
    )
  }
  return Panel
}

/**
 * THE switch point for `dashboard_server_panels`: each widget with a panel renders the panel
 * when the flag is on, and its old component otherwise (also while the flag list loads).
 */
export function withServerPanels(
  components: Record<string, ComponentType<WidgetContentProps>>,
  panels: Record<string, ComponentType<WidgetContentProps>>,
): Record<string, ComponentType<WidgetContentProps>> {
  const out = { ...components }
  for (const [defId, Panel] of Object.entries(panels)) {
    const Old = components[defId]
    if (!Old) continue
    const Switch = (props: WidgetContentProps) => {
      const on = useFlag(SERVER_PANELS_FLAG)
      return on ? <Panel {...props} /> : <Old {...props} />
    }
    Switch.displayName = `ServerPanelSwitch(${defId})`
    out[defId] = Switch
  }
  return out
}
