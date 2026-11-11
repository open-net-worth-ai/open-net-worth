import { Tag } from '../../ui'
import { COPY } from './following.copy'
import { AssetLink } from '../AssetLink'

/** A stock in any of these tables: ticker, company, and "You own" when it is in your book,
 *  so how a famous portfolio touches yours is visible on every row. */
export function NameCell({
  symbol,
  name,
  held,
  linkTicker,
}: {
  symbol: string | null
  name: string
  held: boolean
  /** The ticker opens the stock's own page (a row whose own click does something else). */
  linkTicker?: boolean
}) {
  return (
    <span className="mk-hcell">
      {symbol && linkTicker ? (
        <span className="fol-sym-link" onClick={(e) => e.stopPropagation()}>
          <AssetLink ticker={symbol} label={name}>
            <span className="mk-hsym">{symbol}</span>
          </AssetLink>
        </span>
      ) : symbol ? (
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
