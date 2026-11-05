import { useState } from 'react'
import type { BudgetAppInfo } from '../automations/datasync/budgetApps'
import type { Preview, PreviewAccount } from './runs'

// The state of a review that settles a type for each new account. The choices come from the
// server (`type_options`); a suggestion counts only once it has been looked at, because a card
// left as cash quietly inflates Ready to Assign.

export interface AccountTypes {
  /** The type shown for an account: chosen, else suggested, else none. */
  valueOf: (a: PreviewAccount) => string
  isSettled: (a: PreviewAccount) => boolean
  suggestionOf: (a: PreviewAccount) => string | null
  /** New accounts nobody has looked at yet. */
  unsettled: number
  /** How many of them have a suggestion to accept. */
  acceptable: number
  choose: (a: PreviewAccount, type: string) => void
  accept: () => void
  /** The account id -> type an import or a re-check sends, given these links. */
  forRequest: (links: Record<string, string>) => Record<string, string>
  /** A choice the shown preview doesn't reflect yet, as a key that changes with the choices. */
  ahead: string | null
}

const isLinked = (a: PreviewAccount) => !!a.link

export function useAccountTypes(first: Preview | null | undefined, shown: Preview | null) {
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const suggested = Object.fromEntries((first?.accounts ?? []).map((a) => [a.id, a.type]))
  const open = (shown?.accounts ?? []).filter((a) => a.id && !isLinked(a))
  const suggestionOf = (a: PreviewAccount) => {
    const type = a.id ? suggested[a.id] : undefined
    return type && a.type_options?.includes(type) ? type : null
  }
  const left = open.filter((a) => !(a.id! in chosen))
  const offshoots = open.filter((a) => a.id! in chosen && chosen[a.id!] !== a.type)
  const types: AccountTypes = {
    valueOf: (a) => (a.id && chosen[a.id]) || suggestionOf(a) || '',
    isSettled: (a) => !!a.id && a.id in chosen,
    suggestionOf,
    unsettled: left.length,
    acceptable: left.filter(suggestionOf).length,
    choose: (a, type) => setChosen((c) => ({ ...c, [a.id!]: type })),
    accept: () =>
      setChosen((c) => ({
        ...Object.fromEntries(
          left.flatMap((a) => (suggestionOf(a) ? [[a.id!, suggestionOf(a)!]] : [])),
        ),
        ...c,
      })),
    forRequest: (links) =>
      Object.fromEntries(
        (shown?.accounts ?? []).flatMap((a) => {
          const here = a.chosen ? !!a.id && a.id in links : isLinked(a) || (!!a.id && a.id in links)
          const type = a.id && !here ? (chosen[a.id] ?? suggestionOf(a)) : null
          return type ? [[a.id!, type]] : []
        }),
      ),
    ahead: offshoots.length > 0 ? JSON.stringify(chosen) : null,
  }
  return types
}

export const importBlockKey = (app: BudgetAppInfo, planId: string) =>
  `${app.apiPrefix}/migration?plan_id=${encodeURIComponent(planId)}`
