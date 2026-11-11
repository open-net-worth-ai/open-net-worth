import { Tag } from '../../ui'
import { COPY } from './following.copy'
import { useAssetNav } from '../../../state/assetNav'
import { Tooltip } from '../../ui/Tooltip'
import { ArrowUpRight } from '../../../ui/icons'

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
  const { openAsset } = useAssetNav()
  return (
    <span className="mk-hcell fol-name">
      {symbol ? (
        <span className="mk-hsym">{symbol}</span>
      ) : (
        <span className="fol-noticker">{COPY.noTicker}</span>
      )}
      <span className="mk-hnm">{name}</span>
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
      {held && (
        <span className="fol-own">
          <Tag tone="accent">{COPY.youOwn}</Tag>
        </span>
      )}
    </span>
  )
}
