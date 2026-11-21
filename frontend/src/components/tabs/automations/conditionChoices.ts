// The owner's categories (with colours) and tags, for "Category is" / "Tags has" conditions —
// read from the same shared cache the rest of the app uses, so any editor offering conditions
// (a Rule, or a Lucius Automation's transaction trigger) has them without threading props.
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import { useTransactionTags } from '../accounts/tagOptions'

export interface CategoryChoice {
  id: string
  label: string
  color?: string
}

const CATEGORIES_KEY = '/api/categories'

async function fetchCategories(): Promise<CategoryChoice[]> {
  const response = await authFetch(CATEGORIES_KEY)
  if (!response.ok) throw new Error(`Categories could not be loaded (${response.status})`)
  return (await response.json()) as CategoryChoice[]
}

/** Categories (never the ledger's "split" marker) and tags; empty while they load. */
export function useConditionChoices(): { categories: CategoryChoice[]; tags: string[] } {
  const categories = useQueryCache(CATEGORIES_KEY, fetchCategories).data ?? []
  const tags = useTransactionTags()
  return { categories: categories.filter((c) => c.id !== 'split'), tags }
}
