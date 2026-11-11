import { Tag } from '../../ui'
import { COPY } from './following.copy'

/** A stock in any of these tables: ticker, company, and "You own" when it is in your book,
 *  so how a famous portfolio touches yours is visible on every row. */
export function NameCell({
  symbol,
  name,
  held,
}: {
  symbol: string | null
  name: string
  held: boolean
}) {
  return (
    <span className="mk-hcell">
      {symbol ? (
        <span className="mk-hsym">{symbol}</span>
      ) : (
        <span className="fol-noticker">{COPY.noTicker}</span>
      )}
      <span className="mk-hnm">{name}</span>
      {held && (
        <span className="fol-own">
          <Tag tone="accent">{COPY.youOwn}</Tag>
        </span>
      )}
    </span>
  )
}
