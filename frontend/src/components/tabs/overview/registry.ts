// Overview v2 — the ONE merge point for every widget group's content
// components. Each group module owns exactly two files of its own
// (`widgets/<group>.tsx` + `widgets/<group>.css`, the latter imported from
// overview.css) so parallel lanes never edit the same file — see ADR
// 2026-07-22-overview-tab-redesign-faithful-port.md, decision 8.

import type { ComponentType } from 'react'
import type { WidgetContentProps } from './types'
import { netWorthWidgetComponents } from './widgets/netWorth'
import { overviewCashflowCoreWidgetComponents } from './widgets/overviewCashflowCore'
import { cashflowPlanningWidgetComponents } from './widgets/cashflowPlanning'
import { investingWidgetComponents } from './widgets/investing'
import { trackingBudgetingWidgetComponents } from './widgets/trackingBudgeting'
import { reportsWidgetComponents } from './widgets/reports'
import { contentWidgetComponents } from './widgets/content'
import { luxCoffeeWidgetComponents } from './widgets/luxCoffee'
import { spendingPaceWidgetComponents } from './widgets/spendingPace'
import { transactionsWidgetComponents } from './widgets/transactions'
import { withServerPanels } from './panels/panel'
import { PANEL_COMPONENTS } from './panels'

const BASE_COMPONENTS: Record<string, ComponentType<WidgetContentProps>> = {
  ...netWorthWidgetComponents,
  ...overviewCashflowCoreWidgetComponents,
  ...cashflowPlanningWidgetComponents,
  ...investingWidgetComponents,
  ...trackingBudgetingWidgetComponents,
  ...reportsWidgetComponents,
  ...contentWidgetComponents,
  ...luxCoffeeWidgetComponents,
  ...spendingPaceWidgetComponents,
  ...transactionsWidgetComponents,
}

// `dashboard_server_panels` picks, per widget, the server panel or the old component.
export const WIDGET_COMPONENTS = withServerPanels(BASE_COMPONENTS, PANEL_COMPONENTS)
