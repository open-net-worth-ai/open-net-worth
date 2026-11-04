import type { DelegationReview } from '../../../types/trading'
import { Checkbox, ErrorState, Panel, Stack, Text } from '../../ui'
import { tradingMoney, tradingTime } from './presentation'

export function WorkspaceReview({
  review,
  authorized,
  busy,
  onAuthorize,
}: {
  review: DelegationReview
  authorized: boolean
  busy: boolean
  onAuthorize: (value: boolean) => Promise<void>
}) {
  return (
    <>
      <Text tone="muted">
        Unsaved draft. Review the exact delegation and authority before starting.
      </Text>
      <Panel title={review.workspace.name}>
        <Stack gap="--space-4">
          <Text as="p" className="trading-mandate-text">
            {review.workspace.instructions}
          </Text>
          <Text>
            Executor: {review.workspace.agent_label} · {review.workspace.agent_kind}
          </Text>
          <Text>
            Environment: {review.environment.mode} · {tradingTime(review.environment.start_at)} –{' '}
            {review.environment.end_at ? tradingTime(review.environment.end_at) : 'Until stopped'}
          </Text>
          {review.environment.accounts.map((account, index) => (
            <Text key={index}>
              {account.name} · {account.venue} · {tradingMoney(account.capital, account.currency)}
            </Text>
          ))}
          <Text>Markets: {review.workspace.mandate.allowed_asset_classes.join(', ')}</Text>
          <Text>
            Maximum order:{' '}
            {review.workspace.mandate.max_order_notional == null
              ? 'Available authorized capital'
              : tradingMoney(
                  review.workspace.mandate.max_order_notional,
                  review.environment.base_currency,
                )}
          </Text>
          <Text>
            Agent budget: {tradingMoney(review.workspace.mandate.max_model_cost_usd, 'USD')} ·
            Separate from trading capital
          </Text>
          <Text>
            Data: {review.workspace.mandate.market_data_source} · {review.environment.data_version}
          </Text>
          <Text tone="muted">
            End policy: stop new work, request cancellation of open orders, retain holdings. Already
            submitted orders may still execute.
          </Text>
        </Stack>
      </Panel>
      {!!review.issues?.length && <ErrorState message={review.issues.join(' ')} />}
      {review.environment.mode === 'live' && (
        <Checkbox
          checked={authorized}
          disabled={busy}
          onChange={(e) => void onAuthorize(e.target.checked)}
          label="I authorize live trading of the displayed accounts and allocation within this mandate."
        />
      )}
    </>
  )
}
