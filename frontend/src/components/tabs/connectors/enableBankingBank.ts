// One bank linked through Enable Banking, as `GET /api/enable-banking/status` lists it (by
// name and public key — never its session).
export interface EnableBankingBank {
  key: string
  aspsp: string
  country: string
  valid_until: string | null
  linked_at: string | null
  accounts: number
}

/** A consent whose end date has passed: the bank stops answering until it is renewed. */
export function accessEnded(b: EnableBankingBank, now = Date.now()): boolean {
  return !!b.valid_until && Date.parse(b.valid_until) <= now
}
