import { fBareAmount } from '../../../../utils/format'
import { Stack, Text } from '../../../ui'
import type { BudgetAppInfo } from './budgetApps'

// Ready to Assign here beside the app's own To Budget for this month, worked out the way the app
// works it out (checked against Actual's engine), with the part they count differently: money in
// card payment envelopes, which Actual has no place for.

export interface ToBudgetFigures {
  month: string
  theirs: number
  here: number
  difference: number
  card_payment_envelopes: number
}

export function ToBudgetCompare({
  app,
  figures,
}: {
  app: BudgetAppInfo
  figures?: ToBudgetFigures | null
}) {
  if (!figures) return null
  const { here, theirs, difference, card_payment_envelopes: cards } = figures
  const rest = Math.round((difference + cards) * 100) / 100
  return (
    <section className="a3-block" aria-label={`Ready to Assign and ${app.name}'s To Budget`}>
      <div className="a3-block-h">{`Ready to Assign and ${app.name}'s To Budget`}</div>
      <Stack gap="--space-1">
        <Text size="sm">{`Here: ${fBareAmount(here)} · ${app.name}: ${fBareAmount(theirs)}`}</Text>
        {Math.abs(difference) < 0.005 ? (
          <Text size="xs" tone="faint">
            They agree.
          </Text>
        ) : (
          <>
            <Text size="xs" tone="faint">
              {`They differ by ${fBareAmount(Math.abs(difference))}. ${app.name} has no card payment envelopes: here, money for card payments sits in them (${fBareAmount(cards)}) instead of in Ready to Assign.`}
            </Text>
            {Math.abs(rest) >= 0.005 && (
              <Text size="xs" tone="faint">
                {`The other ${fBareAmount(Math.abs(rest))} is how the two treat overspending and held money: here an overspent envelope holds its hole until the month ends, and spending with no category holds the same way.`}
              </Text>
            )}
          </>
        )}
        <Text size="xs" tone="faint">{`As of the last sync's read of ${app.name}.`}</Text>
      </Stack>
    </section>
  )
}
