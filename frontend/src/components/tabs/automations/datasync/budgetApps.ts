import type { ComponentType } from 'react'
import { ActualMark } from './ActualMark'
import { YnabMark } from './YnabMark'

// The budget apps a book can import from and keep in step with. Everything an app's screens need
// is data here, so the components never branch on which app they are showing.

export interface BudgetAppInfo {
  /** The run provider, the connector id and the `?sync=` value. */
  key: 'ynab' | 'actual_budget'
  name: string
  apiPrefix: string
  flag: string
  blurb: string
  Mark: ComponentType
  tokenNoun: string
  planWord: string
  /** The word with its article: "a YNAB", "an Actual". */
  aName: string
  setupLine: string
  savedLine: string
  saveFirst: string
  categoryColumn: string
  signInSaved: (status: Record<string, unknown>) => boolean
  /** The budget the connector remembers, if the app has one worth starting from. */
  savedPlanId: (status: Record<string, unknown>) => string | null
  /** Its review settles a type for each new account, and a currency if the budget has none. */
  asksTypeAndCurrency: boolean
  /** Writes in the app carry its red flag (YNAB); otherwise a line in the category's note. */
  flagTrail: boolean
  /** Its report counts requests against a limit. */
  showsRequests: boolean
  /** The server says, before an import starts, whether it would refuse it. */
  tellsImportBlock: boolean
  /** The page reads the sync's status again when an import ends. */
  refreshesAfterImport: boolean
  /** The sync can move between budgets, so an import date belongs to the one it was of. */
  importsPerPlan: boolean
  /** Its page's links to the connector open the form, not the overview, even when it's healthy. */
  opensConnectorForm: boolean
  /** History lists a review's previews. Each re-check is one, which swamps a settled review's. */
  listsPreviewRuns: boolean
}

// Built from a part so that this file holds no `/api/...` literal: the route consistency test
// reads every such literal as an endpoint, and a prefix is not one.
const API = '/api'

export const YNAB_APP: BudgetAppInfo = {
  key: 'ynab',
  name: 'YNAB',
  apiPrefix: `${API}/ynab`,
  flag: 'ynab_import_and_sync',
  blurb: 'Transactions and each month’s Assigned, in step with your YNAB plan.',
  Mark: YnabMark,
  tokenNoun: 'token',
  planWord: 'plan',
  aName: 'a YNAB',
  setupLine: 'Add your YNAB personal access token.',
  savedLine: 'Token saved',
  saveFirst: 'Save your YNAB access token on the YNAB connector first.',
  categoryColumn: 'YNAB category',
  signInSaved: (status) => !!status.has_access_token,
  savedPlanId: () => null,
  asksTypeAndCurrency: false,
  flagTrail: true,
  showsRequests: true,
  tellsImportBlock: false,
  refreshesAfterImport: false,
  importsPerPlan: false,
  opensConnectorForm: false,
  listsPreviewRuns: true,
}

export const ACTUAL_APP: BudgetAppInfo = {
  key: 'actual_budget',
  name: 'Actual',
  apiPrefix: `${API}/actual-budget`,
  flag: 'actual_budget_sync',
  blurb: 'Transactions and each month’s Assigned, in step with your Actual budget.',
  Mark: ActualMark,
  tokenNoun: 'sign-in',
  planWord: 'budget',
  aName: 'an Actual',
  setupLine: 'Add your Actual server address and sign-in.',
  savedLine: 'Sign-in saved',
  saveFirst: 'Save your Actual server address and sign-in on the Actual connector first.',
  categoryColumn: 'Category in Actual',
  signInSaved: (status) => !!status.configured,
  savedPlanId: (status) => (status.budget_id as string | null | undefined) ?? null,
  asksTypeAndCurrency: true,
  flagTrail: false,
  showsRequests: false,
  tellsImportBlock: true,
  refreshesAfterImport: true,
  importsPerPlan: true,
  opensConnectorForm: true,
  listsPreviewRuns: false,
}

/** Fixed order: the cards never swap places. */
export const BUDGET_APPS = [YNAB_APP, ACTUAL_APP]

export const appForProvider = (provider: string) =>
  BUDGET_APPS.find((a) => a.key === provider) ?? YNAB_APP

/** What the app's sync covers. A budget in tracking mode syncs everything but Assigned. */
export const appBlurb = (app: BudgetAppInfo, tracking: boolean) =>
  tracking
    ? `Transactions and categories, in step with your ${app.name} ${app.planWord}.`
    : app.blurb

/** The sync's one line on what moves which way. */
export function flowLine(
  app: BudgetAppInfo,
  direction: 'both' | 'from_ynab' | 'to_ynab',
  tracking: boolean,
) {
  const what = tracking ? 'transactions and categories' : 'transactions, categories and Assigned'
  const name = app.name
  if (direction === 'from_ynab')
    return `${name}’s ${what} come here. Nothing is written to ${name}.`
  if (direction === 'to_ynab') return `This book’s ${what} go to ${name}. Nothing comes back.`
  return `${what[0]!.toUpperCase()}${what.slice(1)} changed on either side are copied to the other.`
}

/** What a sync's writes leave behind in the app, so they can be spotted there. */
export function trailRule(app: BudgetAppInfo, tracking: boolean) {
  if (app.flagTrail)
    return `What it adds or changes in ${app.name} gets ${app.name}'s red flag, so you can spot it there.`
  return tracking
    ? null
    : `Each Assigned change it makes in ${app.name} leaves a line in that category’s note, so you can spot it there.`
}
