import { Component, type ReactNode } from 'react'
import { MaskedText } from '../../ui/MaskedText'
import { fDate, moneyClass } from '../../../utils/format'
import type { VisualBlock, VisualSpec } from '../../../types/chat'
import { formatVisualValue } from './format'
import { VisualChart } from './VisualChart'
import { VisualDrawing } from './VisualDrawing'
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

/** A `show_visual` result in the chat column: optional title, the blocks stacked (or Lucius's
 *  own drawing, `code`), muted caption
 *  lines, then follow-up chips that send their text as the next message. An unknown block or a
 *  malformed one draws nothing — it never takes the bubble down with it. */
export function InlineVisual({
  visual,
  onSendMessage,
}: {
  visual: VisualSpec
  onSendMessage: (text: string) => void
}) {
  const code = typeof visual?.code === 'string' ? visual.code : null
  if (!visual || (!code && !isArr(visual.blocks))) return null
  const blocks = isArr(visual.blocks) ? visual.blocks : []
  const captions = isArr(visual.caption) ? visual.caption : []
  const followUps = isArr(visual.follow_ups) ? visual.follow_ups : []
  return (
    <section className="ivis" aria-label={visual.title || 'Visual'}>
      {visual.title && <h4 className="ivis-title">{visual.title}</h4>}
      {blocks.map((b, i) => (
        <Guard key={i}>{renderBlock(b, i)}</Guard>
      ))}
      {code && (
        <Guard>
          <VisualDrawing code={code} title={visual.title} />
        </Guard>
      )}
      {captions.map((c, i) => (
        <p className="ivis-caption" key={i}>
          <MaskedText>{String(c)}</MaskedText>
        </p>
      ))}
      {followUps.length > 0 && (
        <div className="ivis-followups">
          {followUps.map((f, i) => (
            <button type="button" className="ivis-chip" key={i} onClick={() => onSendMessage(f)}>
              {f}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
