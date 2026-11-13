// One bank linked through Enable Banking, as `GET /api/enable-banking/status` lists it (by
// name and public key — never its session).
export interface EnableBankingBank {
  key: string
  aspsp: string
  country: string
  valid_until: string | null
  linked_at: string | null
  accounts: number
  /** Why the consent no longer answers, until renewed: linked under the app used before this
   *  one, or ended at Enable Banking (revoked, expired, gone). */
  access_ended?: 'app_changed' | 'ended' | null
}

/** A consent that no longer answers — flagged by the server, or past its end date: the bank
 *  stops syncing until it is renewed. */
export function accessEnded(b: EnableBankingBank, now = Date.now()): boolean {
  return !!b.access_ended || (!!b.valid_until && Date.parse(b.valid_until) <= now)
}
