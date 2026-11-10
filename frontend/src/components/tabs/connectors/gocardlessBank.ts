// One bank of the GoCardless connector, as /api/gocardless/status lists it.
export interface GoCardlessBank {
  key: string
  institution_id: string
  institution_name: string
  country: string
  status: string
  consent_expires_at: string | null
  linked_at: string | null
  accounts: number
  /** A new consent for a bank that is still linked — it takes over once linked. */
  renewing: boolean
}

// GoCardless requisition statuses that end without a link — only a new link recovers them.
export const ENDED: Record<string, string> = {
  EX: 'Access expired',
  RJ: 'The bank refused access',
  SU: 'GoCardless suspended this link',
  ER: 'The bank link failed',
}

/** Consent started but not finished at the bank (and not ended) — Check links resolves it. */
export const isPending = (b: GoCardlessBank) => b.status !== 'LN' && !ENDED[b.status]
