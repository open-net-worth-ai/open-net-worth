// Every Dashboard widget that is a server panel: its defId → its declared query and view.
// A widget joins by adding one entry here; with `dashboard_server_panels` off it keeps its
// old component (`withServerPanels` in ./panel).

import type { ComponentType } from 'react'
import type { WidgetContentProps } from '../types'
import { panelWidget } from './panel'
import { firePanel } from './fire'
import { fxPanel } from './fx'
import { crossoverPanel, spendingPacePanel } from './planning'
import { savingsRatePanel, spendCategoriesPanel } from './reports'
import { upcomingPanel } from './upcoming'

export const PANEL_COMPONENTS: Record<string, ComponentType<WidgetContentProps>> = {
  upcoming: panelWidget(upcomingPanel),
  fx: panelWidget(fxPanel),
  'rep-spendcat': panelWidget(spendCategoriesPanel),
  'rep-savings': panelWidget(savingsRatePanel),
  crossover: panelWidget(crossoverPanel),
  'plan-fire': panelWidget(firePanel),
  'spending-pace': panelWidget(spendingPacePanel),
}
