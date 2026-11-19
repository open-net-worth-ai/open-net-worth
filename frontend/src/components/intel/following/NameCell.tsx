import { Tag } from '../../ui'
import { COPY } from './following.copy'
import { useAssetNav } from '../../../state/assetNav'
import { Tooltip } from '../../ui/Tooltip'
import { ArrowUpRight } from '../../../ui/icons'
import type { OptionContract } from '../../../api/followedPortfolios'
import { contractLabel, contractWords, exchangeLabel } from './following.format'

/** A stock in any of these tables: the company in full, and under it its ticker · exchange and
 *  "You own" when it is in your book, so how a famous portfolio touches yours shows on every row. */
export function NameCell({
  symbol,
  name,
  held,
  linkTicker,
  contracts,
  exchange,
}: {
  symbol: string | null
  name: string
  held: boolean
  /** The ticker opens the stock's own page (a row whose own click does something else). */
  linkTicker?: boolean
  /** Option contracts on this stock: each a chip, its full terms in its tooltip. */
  contracts?: OptionContract[]
  /** Where the ticker trades, from its own quote (none when that could not be confirmed). */
  exchange?: string
}) {
  const market = exchangeLabel(exchange)
  const { openAsset } = useAssetNav()
  return (
    <span className="mk-hcell fol-name">
      {/* The company in full (it wraps rather than truncates), the way to its page beside it. */}
      <span className="fol-name-line">
        <span className="fol-name-full">{name}</span>
        {/* The stock's own page, right beside its name (the row's own click shows the move). */}
        {symbol && linkTicker && (
          <span className="fol-open-inline" onClick={(e) => e.stopPropagation()}>
            <Tooltip label={COPY.openStock(symbol)}>
              <button
                type="button"
                className="fol-open-btn"
                aria-label={COPY.openStock(symbol)}
                onClick={() => openAsset({ ticker: symbol, label: name })}
              >
                <ArrowUpRight size={13} aria-hidden="true" />
              </button>
            </Tooltip>
          </span>
        )}
      </span>
      {/* Under it: the ticker and where it trades, then its option contracts and "You own". */}
      <span className="fol-name-sub">
        {symbol ? (
          <span className="mk-hsym">
            {symbol}
            {market && <span className="fol-exch"> · {market}</span>}
          </span>
        ) : (
          <span className="fol-noticker">{COPY.noTicker}</span>
        )}
        {contracts?.map((c) => (
          <span key={`${c.right}${c.strike}${c.expiry}`} className="fol-own">
            <Tooltip label={contractWords(c)}>
              <span tabIndex={0}>
                <Tag tone="muted">{contractLabel(c)}</Tag>
              </span>
            </Tooltip>
          </span>
        ))}
        {held && (
          <span className="fol-own">
            <Tag tone="accent">{COPY.youOwn}</Tag>
          </span>
        )}
      </span>
    </span>
  )
}
