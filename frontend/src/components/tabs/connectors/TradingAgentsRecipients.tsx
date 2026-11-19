import type { ReactNode } from 'react'
import { TA_NEVER_SENT } from '../../../types/tradingAgentsConstants'
import { Grid, Text } from '../../ui'

function Recipient({ who, children }: { who: string; children: ReactNode }) {
  return (
    <dl className="ta-recipient">
      <dt className="ta-recipient__who">{who}</dt>
      <dd className="ta-recipient__what">{children}</dd>
    </dl>
  )
}

/** Who receives what, in four blocks. `providers` are the names resolved from the two chosen
 *  models; `keys` are the optional data vendors the owner has added a key for. */
export function TradingAgentsRecipients({
  providers,
  keys,
}: {
  providers: string[]
  keys: string[]
}) {
  const vendors = ['Yahoo Finance', 'SEC EDGAR', 'StockTwits', 'Reddit', 'Polymarket', ...keys]
  return (
    <Grid min={260} gap="--space-5">
      <Recipient who="Your AI provider">
        <Text as="div" size="xs" tone="muted">
          {providers.length > 0 ? providers.join(' and ') : 'The provider of the models you choose'}
          .
        </Text>
        <Text as="div" size="xs" tone="muted">
          <strong>Receives:</strong> the ticker and date, and at the level above your investable
          cash and positions in the stock&apos;s currency, plus everything TradingAgents writes
          during the run. A subscription CLI runs on this computer and sends it under your plan.
        </Text>
      </Recipient>
      <Recipient who="Data vendors">
        <Text as="div" size="xs" tone="muted">
          {vendors.join(', ')}.
        </Text>
        <Text as="div" size="xs" tone="muted">
          <strong>Receive:</strong> the ticker and date only. <strong>Never</strong> your amounts.
        </Text>
      </Recipient>
      <Recipient who="Open Net Worth (this computer)">
        <Text as="div" size="xs" tone="muted">
          Keeps a record of what was shared on every run (shown as What was shared) in your local
          data and backups.
        </Text>
      </Recipient>
      <Recipient who="Nobody">
        <Text as="div" size="xs" tone="muted">
          Never leaves, at any level: {TA_NEVER_SENT.join(', ')}.
        </Text>
      </Recipient>
    </Grid>
  )
}
