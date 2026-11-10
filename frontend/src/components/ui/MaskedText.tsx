import { splitFigures } from '../../utils/amountMask'
import { MONEY_CLASS } from '../../utils/format'

/**
 * A sentence whose figures mask under Hide amounts while its words stay readable — for text that
 * arrives whole, like a server-written brief. Where the figure is at hand, prefer <Money>.
 */
export function MaskedText({ children }: { children: string }) {
  return (
    <>
      {splitFigures(children).map((piece, i) =>
        piece.figure ? (
          <span key={i} className={MONEY_CLASS}>
            {piece.text}
          </span>
        ) : (
          piece.text
        ),
      )}
    </>
  )
}
