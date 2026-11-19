import { LuciusMark } from '../../ui/LuciusMark'
import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import { Badge, InfoPopover, Panel, SegmentedControl, Skeleton, Text } from '../ui'
import type { BadgeTone } from '../ui'
import { TechnicalsMiniChart, type TechOverlays } from './TechnicalsMiniChart'

export interface TechnRow {
  id: string
  label: string
  help: string
  score: number
  raw: number
  raw_display: string
  scale_sell?: number | null
  scale_neutral?: number | null
  scale_buy?: number | null
  scale_sell_display?: string | null
  scale_neutral_display?: string | null
  scale_buy_display?: string | null
}

type Lean = 'leans_buy' | 'leans_sell' | 'mixed'

interface TechnResponse {
  currency: string
  timeframe: string
  rows: TechnRow[]
  overlays: TechOverlays
  points: { date: string; price: number }[]
  narrative: string | null
  bundle_hash: string
  lean?: Lean
}

type TechTf = '1M' | '6M' | '1Y' | '5Y'
const TF_OPTS: { value: TechTf; label: string }[] = [
  { value: '1M', label: '1M' },
  { value: '6M', label: '6M' },
  { value: '1Y', label: '1Y' },
  { value: '5Y', label: '5Y' },
]

interface Props {
  instrumentId: string
}

function scoreToPct(score: number): number {
  return Math.max(0, Math.min(100, ((score + 1) / 2) * 100))
}

function leanTone(lean: Lean): BadgeTone {
  if (lean === 'leans_buy') return 'pos'
  if (lean === 'leans_sell') return 'neg'
  return 'warn'
}

function leanText(lean: Lean): string {
  if (lean === 'leans_buy') return 'Leans buy'
  if (lean === 'leans_sell') return 'Leans sell'
  return 'Mixed'
}

/** Analysis Technicals widget — compact chart, six bipolar rows, hash-gated AI blurb. */
export function TechnicalsWidget({ instrumentId }: Props) {
  const [tf, setTf] = useState<TechTf>('1Y')
  const [data, setData] = useState<TechnResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [narrating, setNarrating] = useState(false)

  useEffect(() => {
    setData(null)
    setLoading(true)
  }, [instrumentId])

  useEffect(() => {
    let alive = true
    setLoading(true)
    authFetch(`/api/intel/assets/${instrumentId}/technicals?timeframe=${tf}&narrate=false`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: TechnResponse | null) => {
        if (!alive) return
        setData(d)
        setLoading(false)
        if (d?.rows?.length && !d.narrative) {
          setNarrating(true)
          authFetch(`/api/intel/assets/${instrumentId}/technicals?timeframe=${tf}&narrate=true`)
            .then((r) => (r.ok ? r.json() : null))
            .then((full: TechnResponse | null) => {
              if (!alive || !full) return
              setData(full)
            })
            .finally(() => {
              if (alive) setNarrating(false)
            })
        }
      })
      .catch(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [instrumentId, tf])

  if (loading && !data) {
    return (
      <Panel>
        <div className="asd-section-head">
          <span className="asd-section-title">Technical Analysis</span>
        </div>
        <Skeleton height={160} />
        <div className="asd-skel-stack" style={{ marginTop: 12 }}>
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} height={22} />
          ))}
        </div>
      </Panel>
    )
  }

  const rows = data?.rows ?? []
  if (!rows.length && !data?.points?.length) return null

  const lean = data?.lean

  return (
    <Panel>
      <div className="asd-section-head asd-tech-head">
        <span className="asd-section-title">Technical Analysis</span>
        <SegmentedControl
          value={tf}
          onChange={(v) => setTf(v as TechTf)}
          options={TF_OPTS}
          ariaLabel="Technical Analysis chart timeframe"
        />
      </div>

      {!!data?.points?.length && (
        <TechnicalsMiniChart points={data.points} overlays={data.overlays} timeframe={tf} />
      )}

      {rows.length > 0 && (
        <div className="asd-scale-block">
          {lean && (
            <div className="asd-tech-lean">
              <Badge tone={leanTone(lean)}>{leanText(lean)}</Badge>
            </div>
          )}
          <div className="asd-scale-axis" aria-hidden="true">
            <span>Strong sell</span>
            <span>Sell</span>
            <span>Neutral</span>
            <span>Buy</span>
            <span>Strong buy</span>
          </div>
          <div className="asd-scale-cols-head" aria-hidden="true">
            <span className="asd-scale-cols-spacer" />
            <span className="asd-scale-cols-track-spacer" />
            <div className="asd-scale-nums asd-scale-nums--head">
              <span>Sell</span>
              <span>Neutral</span>
              <span>Buy</span>
              <span>Now</span>
            </div>
          </div>
          {rows.map((row) => (
            <div key={row.id} className="asd-scale-row">
              <div className="asd-scale-row-label">
                <span>{row.label}</span>
                <InfoPopover text={row.help} width={280} />
              </div>
              <div
                className="asd-scale-track"
                role="img"
                aria-label={`${row.label} score ${row.score}`}
              >
                <span className="asd-scale-track-line" />
                <span className="asd-scale-marker" style={{ left: `${scoreToPct(row.score)}%` }} />
              </div>
              <div className="asd-scale-nums">
                <span className="asd-scale-num">
                  <span className="asd-scale-num-label">Sell</span>
                  {row.scale_sell_display ?? '—'}
                </span>
                <span className="asd-scale-num">
                  <span className="asd-scale-num-label">Neutral</span>
                  {row.scale_neutral_display ?? '—'}
                </span>
                <span className="asd-scale-num">
                  <span className="asd-scale-num-label">Buy</span>
                  {row.scale_buy_display ?? '—'}
                </span>
                <span className="asd-scale-num asd-scale-num--ticker">
                  <span className="asd-scale-num-label">Now</span>
                  {row.raw_display}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="asd-tech-narrative">
        {(data?.narrative || narrating) && (
          <div className="asd-pi-wrap">
            <div className="asd-pi-head">
              <p className="asd-pi-label">
                Technical Analysis <LuciusMark size="1em" aria-hidden="true" />
              </p>
              {narrating && (
                <span
                  className="asd-pi-updating"
                  role="status"
                  aria-label="Technical Analysis narrative is generating"
                >
                  <span className="ui-btn__spinner" aria-hidden="true" />
                  Generating
                </span>
              )}
            </div>
            {data?.narrative ? (
              <p className="asd-pi-body">{data.narrative}</p>
            ) : (
              <div className="asd-pi-body-skeleton" role="status" aria-label="Generating">
                <Skeleton height={13} width="92%" />
                <Skeleton height={13} width="78%" />
              </div>
            )}
          </div>
        )}
        <Text size="xs" tone="faint" className="asd-tech-caption">
          Lean from indicator scores · daily series · via Yahoo/CoinGecko · not personal advice.
        </Text>
      </div>
    </Panel>
  )
}
