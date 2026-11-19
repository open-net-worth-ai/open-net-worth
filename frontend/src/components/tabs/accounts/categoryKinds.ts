// A category's kinds and the names the app shows for them — shared by the Add category dialog
// and the dashboard's report filter, so both say the same thing in the same order.

export type CatKind = 'income' | 'expense' | 'transfer' | 'investment'

export const KIND_ORDER: CatKind[] = ['expense', 'income', 'transfer', 'investment']
export const KIND_LABEL: Record<CatKind, string> = {
  expense: 'Expense',
  income: 'Income',
  transfer: 'Transfer',
  investment: 'Investment',
}
