// The register's grid (owner 2026-09-28: "a bit messy when users might have a lot of things").
// Class cards first, then assets most valuable first — on the All view only, a category of
// STACK_AT or more as ONE stacked card (stacks.ts) that opens the category: its own cards, as
// ever (owner: "the grouping should only happen when we show All"); the chips and back are the
// way out. Built on register.tsx's cards; never imported by it.
import type { AssetCategory, AssetLoan, OwnedAsset } from '../../../types/assets'
import type { ClassCardModel } from './assetsData'
import { fmt, pct } from './assetsData'
import { Icons } from './icons'
import { AssetCard, ClassCard, Thumb } from './register'
import { byValue, registerItems, type AssetStack } from './stacks'

function Delta({ value }: { value: number | null }) {
  return (
    <span className={'as-delta money ' + (value == null ? 'flat' : value >= 0 ? 'pos' : 'neg')}>
      {value == null ? '' : pct(value)}
    </span>
  )
}

interface StackCardProps {
  stack: AssetStack
  cats: AssetCategory[]
  onCat(key: string): void
}

/** A category of STACK_AT+ assets as one card: its layers drawn behind it, the photo of its
 *  most valuable pictured thing, its total, its gain and its three most valuable things by
 *  name. Tapping it opens the category. */
export function StackCard({ stack, cats, onCat }: StackCardProps) {
  const c = stack.category
  const top = stack.assets.slice(0, 3).map((a) => a.name)
  const more = stack.assets.length - top.length
  return (
    <div className="as-stack-pile">
      <span className="as-stack-layer" aria-hidden="true" />
      <button
        type="button"
        className="as-card as-stack"
        aria-label={`${c.plural}, ${stack.assets.length} items`}
        onClick={() => onCat(c.key)}
      >
        <div className="as-card-top">
          <Thumb a={stack.cover} cats={cats} />
          <div className="as-card-id">
            <div className="as-card-nm">{c.plural}</div>
            <div className="as-card-sub">{stack.assets.length} items</div>
          </div>
        </div>
        <div className="as-card-row">
          <span className="as-card-val money">{fmt(stack.total)}</span>
          <span className="as-card-side">
            <Delta value={stack.gainPct} />
          </span>
        </div>
        <div className="as-stack-top">
          {top.join(' · ')}
          {more > 0 ? ` +${more} more` : ''}
        </div>
      </button>
    </div>
  )
}

export interface CardGridProps {
  list: OwnedAsset[]
  classes: ClassCardModel[]
  cats: AssetCategory[]
  loans: AssetLoan[]
  cat: string
  onOpen: (id: string) => void
  /** The trailing "Add an asset" card; omitted when the page lists kinds to add below. */
  onAdd?: () => void
  onNav: (tab: string) => void
  /** Open a category — what tapping its stack does, like its chip. */
  onCat?: (key: string) => void
}

// Liquidity order, like a balance sheet: cash, then market, then things. Classes hold a
// fixed slot; things follow most valuable first.
export function CardGrid(props: CardGridProps) {
  const { list, classes, cats, loans, cat, onOpen, onAdd, onNav, onCat } = props
  const assets =
    cat === 'all'
      ? registerItems(list, cats).map((item) =>
          item.kind === 'stack' ? (
            <StackCard
              key={`stack-${item.stack.category.key}`}
              stack={item.stack}
              cats={cats}
              onCat={onCat ?? (() => {})}
            />
          ) : (
            <AssetCard
              key={item.asset.id}
              a={item.asset}
              cats={cats}
              loans={loans}
              onOpen={onOpen}
            />
          ),
        )
      : byValue(list).map((a) => (
          <AssetCard key={a.id} a={a} cats={cats} loans={loans} onOpen={onOpen} />
        ))
  const items = [...classes.map((c) => <ClassCard key={c.key} c={c} onNav={onNav} />), ...assets]
  if (!items.length) return <div className="as-empty">Nothing matches that filter.</div>
  return (
    <div className="as-grid">
      {items}
      {onAdd && (
        <button className="as-add" onClick={onAdd}>
          {Icons.plus!()}Add an asset
        </button>
      )}
    </div>
  )
}
