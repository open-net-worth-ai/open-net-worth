// The context and hook alone — no components — so a caller that only needs to OPEN the
// surfaces (ChatPanel, a tab) never pulls the review cards and the drawer into its import
// graph. The provider that renders them lives in TransactionSurfaces.tsx.

import { createContext, useContext } from 'react'
import type { ReviewScope } from './TransactionReviewMode'
import type { Txn } from './types'

export interface TransactionSurfaces {
  /** The review cards. `automation`: rows an automation run created (Lucius's door);
   *  `all`: everything flagged for review (the Accounts tab's door). `accountId` narrows
   *  either to one account's rows — Start review on an account page. */
  openReview: (scope: ReviewScope, accountId?: string | null) => void
  /** The transaction drawer, read-only or straight into editing. */
  openTransaction: (txn: Txn, mode?: 'view' | 'edit') => void
  /** The drawer for a row the caller knows only by id (a receipt chip, a deep link): the
   *  drawer opens AT ONCE, its sections loading by that id, and fills in when the row
   *  arrives — a tap that showed nothing for a whole round trip read as a hang on a phone
   *  (owner, 2026-09-22). `hint` is the caller's label for the row, its title meanwhile. */
  openTransactionById: (id: string, hint?: string) => void
  reviewOpen: boolean
}

export const TransactionSurfacesContext = createContext<TransactionSurfaces | null>(null)

const INERT: TransactionSurfaces = {
  openReview: () => {},
  openTransaction: () => {},
  openTransactionById: () => {},
  reviewOpen: false,
}

/** The shell always provides; a tab rendered on its own (a test) gets an inert pair, so
 *  mounting it never throws — opening then does nothing, which such a test never asks for. */
export function useTransactionSurfaces(): TransactionSurfaces {
  return useContext(TransactionSurfacesContext) ?? INERT
}
