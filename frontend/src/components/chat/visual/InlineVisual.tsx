import { Component, type ReactNode } from 'react'
import { MaskedText } from '../../ui/MaskedText'
import { fDate, moneyClass } from '../../../utils/format'
import type { VisualBlock, VisualSpec, VisualTile } from '../../../types/chat'
import { formatVisualValue } from './format'
import { VisualChart } from './VisualChart'
import { VisualDrawing } from './VisualDrawing'
import { VisualMenu } from './VisualMenu'
import { VisualDiverging, VisualProgress, VisualStack } from './VisualBars'

class Guard extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

const isArr = (v: unknown): v is unknown[] => Array.isArray(v)

/** The change since the comparison, signed, with its percentage. Green when it moved the
 *  way Lucius said is good, red when it moved the other way, grey when it didn't say. */
function Delta({ tile }: { tile: VisualTile }) {
  const d = tile.delta
  if (d == null || d === 0) return null
  const tone = !tile.better ? 'flat' : d > 0 === (tile.better === 'up') ? 'good' : 'bad'
  const sign = d > 0 ? '+' : '−'
  const pct =
    tile.delta_pct == null ? '' : ` (${sign}${Math.abs(tile.delta_pct * 100).toFixed(0)}%)`
  return (
    <span className={`ivis-tile-delta ivis-tile-delta--${tone}`}>
      <span className={moneyClass()}>
        {sign}
        {formatVisualValue(Math.abs(d), tile.format, tile.currency)}
      </span>
      {pct}{' '}
    </span>
  )
}

function Stats({ block }: { block: Extract<VisualBlock, { type: 'stats' }> }) {
  if (!isArr(block.tiles)) return null
  return (
    <div className="ivis-stats" data-count={block.tiles.length}>
      {block.tiles.map((t, i) => (
        <div className="ivis-tile" key={i}>
          <div className="ivis-tile-label">{t.label}</div>
          <div className={moneyClass('ivis-tile-value')}>
            {formatVisualValue(t.value, t.format, t.currency)}
          </div>
          {t.compare != null && (
            <div className="ivis-tile-compare">
              <Delta tile={t} />
              vs {t.compare_label ? `${t.compare_label} ` : ''}
              <span className={moneyClass()}>
                {formatVisualValue(t.compare, t.format, t.currency)}
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function Timeline({ block }: { block: Extract<VisualBlock, { type: 'timeline' }> }) {
  if (!isArr(block.items)) return null
  return (
    <ul className="ivis-timeline">
      {block.items.map((it, i) => (
        <li key={i}>
          <span className="ivis-tl-date">{fDate(it.date)}</span>
          <span className="ivis-tl-label">{it.label}</span>
          {it.value != null && (
            <span className={moneyClass('ivis-tl-value')}>
              {formatVisualValue(it.value, block.format, block.currency)}
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

function Chart({ block }: { block: Extract<VisualBlock, { type: 'chart' }> }) {
  if (!isArr(block.labels) || !isArr(block.series) || block.series.length === 0) return null
  switch (block.kind) {
    case 'line':
    case 'bar':
      return <VisualChart block={block} />
    case 'diverging':
      return <VisualDiverging block={block} />
    case 'stack':
      return <VisualStack block={block} />
    case 'progress':
      return <VisualProgress block={block} />
    default:
      return null
  }
}

function renderBlock(block: VisualBlock, i: number) {
  switch (block?.type) {
    case 'stats':
      return <Stats key={i} block={block} />
    case 'chart':
      return <Chart key={i} block={block} />
    case 'timeline':
      return <Timeline key={i} block={block} />
    default:
      return null
  }
}

/** A `show_visual` result in the chat column, flat on the page (no card): its title, the blocks
 *  and/or Lucius's own drawing (`code`), then muted caption lines. The title is always shown
 *  here; the prompt tells Lucius never to draw one inside a drawing. An unknown or malformed
 *  block draws
 *  nothing — it never takes the bubble down with it. */
export function InlineVisual({ visual, draft }: { visual: VisualSpec; draft?: boolean }) {
  const code = typeof visual?.code === 'string' ? visual.code : null
  if (!visual || (!code && !isArr(visual.blocks))) return null
  const blocks = isArr(visual.blocks) ? visual.blocks : []
  const captions = isArr(visual.caption) ? visual.caption : []
  return (
    <section className="ivis" aria-label={visual.title || 'Visual'}>
      {!draft && <VisualMenu title={visual.title} drawingOnly={blocks.length === 0} />}
      {visual.title && <h4 className="ivis-title">{visual.title}</h4>}
      {blocks.map((b, i) => (
        <Guard key={i}>{renderBlock(b, i)}</Guard>
      ))}
      {code && (
        <Guard>
          <VisualDrawing code={code} title={visual.title} draft={draft} />
        </Guard>
      )}
      {captions.map((c, i) => (
        <p className="ivis-caption" key={i}>
          <MaskedText>{String(c)}</MaskedText>
        </p>
      ))}
    </section>
  )
}
