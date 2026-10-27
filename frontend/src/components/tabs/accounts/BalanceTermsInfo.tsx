/**
 * BalanceTermsInfo.tsx — the one "i" beside a balance card's title. It explains all three figures
 * at once; an "i" under each 16px label was three hard-to-hit targets saying a line each
 * (owner, 2026-10-26).
 */

import { InfoPopover } from '../../ui'

export function BalanceTermsInfo({ total, meaning }: { total: string; meaning: string }) {
  return (
    <InfoPopover
      width={280}
      label="What these figures mean"
      text={
        <span className="acct-bal-terms">
          <span>
            <b>Cleared</b> — posted; the amount won't change.
          </span>
          <span>
            <b>Uncleared</b> — pending holds that can still change.
          </span>
          <span>
            <b>{total}</b> — {meaning}
          </span>
        </span>
      }
    />
  )
}
