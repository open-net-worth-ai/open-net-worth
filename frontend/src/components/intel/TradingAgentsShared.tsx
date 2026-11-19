import type { TaContext } from '../../types/tradingAgents'
import { fCurrency, fQty, fUnitPrice } from '../../utils/format'
import { fxLabel } from '../../utils/tradingAgents'
import { Text } from '../ui'

/** What was (or will be) shared with the AI provider: the recipient, the exact amounts and
 *  the disclosure notes. Presentational: the caller supplies the context and the provider
 *  names resolved from the run's two models. */
export function TradingAgentsShared({
  context,
  providers,
}: {
  context: TaContext
  providers: string[]
}) {
  const { portfolio, disclosure } = context
  const to = providers.length > 0 ? providers.join(' and ') : 'your AI provider'
  const notes = [
    disclosure.fx_converted.length > 0 &&
      `Converted at today's rates: ${disclosure.fx_converted.map(fxLabel).join(' · ')}.`,
    disclosure.average_price_omitted.length > 0 &&
      `Average price left out for ${disclosure.average_price_omitted.join(', ')} (no cost basis or no rate).`,
    disclosure.partial_cost.length > 0 &&
      `Average price for ${disclosure.partial_cost.join(', ')} covers only the lots with a known cost.`,
    disclosure.cash_omitted && `Cash left out: ${disclosure.cash_omitted}.`,
    disclosure.cash_floored && `Cash shown as zero: ${disclosure.cash_floored}.`,
  ].filter((n): n is string => !!n)

  return (
    <div className="ta-shared">
      {portfolio ? (
        <>
          <Text size="sm">
            {to} {providers.length > 1 ? 'receive' : 'receives'} the ticker and date, and:
          </Text>
          <table className="ta-shared__table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col" className="ta-shared__num">
                  Quantity
                </th>
                <th scope="col" className="ta-shared__num">
                  Avg price
                </th>
              </tr>
            </thead>
            <tbody>
              <tr title={disclosure.cash_basis ?? undefined}>
                <td>Investable cash</td>
                <td className="ta-shared__num" colSpan={2}>
                  {portfolio.cash === null
                    ? 'Unavailable'
                    : fCurrency(portfolio.cash, portfolio.currency)}
                </td>
              </tr>
              {portfolio.positions.map((p) => (
                <tr
                  key={p.ticker}
                  className={p.ticker === context.ticker ? 'ta-shared__self' : undefined}
                >
                  <td>{p.ticker}</td>
                  <td className="ta-shared__num">{fQty(p.quantity)}</td>
                  <td className="ta-shared__num">
                    {p.average_price === null
                      ? 'n/a'
                      : fUnitPrice(p.average_price, portfolio.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <Text size="sm">
          {to} {providers.length > 1 ? 'receive' : 'receives'} the ticker and date only. Nothing
          about your holdings.
        </Text>
      )}
      {notes.map((note) => (
        <Text key={note} size="xs" tone="faint">
          {note}
        </Text>
      ))}
      <Text size="xs" tone="faint">
        Data vendors receive the ticker and date only, never your amounts. Never shared:{' '}
        {disclosure.never_sent.join(', ')}.
      </Text>
    </div>
  )
}
