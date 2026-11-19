import { LuciusMark } from '../../ui/LuciusMark'
import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import { InfoPopover, Panel, Skeleton, Text } from '../ui'
import type { FundamentalsResponse, FundamentalsRow } from '../../types/assetDetail'

interface Props {
  instrumentId: string
  /** Kept for call-site compatibility; display strings come from the API. */
  currency?: string
}

const DASH = '—'

function hasSectorScale(row: FundamentalsRow): boolean {
  return (
    row.sector_low != null &&
    row.sector_high != null &&
    row.sector_high > row.sector_low &&
    row.position_pct != null
  )
}

function peerCountHint(rows: FundamentalsRow[]): number | null {
  for (const r of rows) {
    if (r.peer_count != null && r.peer_count > 0) return r.peer_count
  }
  return null
}

function ScaleColsHead() {
  return (
    <div className="asd-scale-cols-head" aria-hidden="true">
      <span className="asd-scale-cols-spacer" />
      <span className="asd-scale-cols-track-spacer" />
      <div className="asd-scale-nums asd-scale-nums--head">
        <span>Low</span>
        <span>Mean</span>
        <span>High</span>
        <span>Ticker</span>
      </div>
    </div>
  )
}

function FundRow({ row }: { row: FundamentalsRow }) {
  const scaled = hasSectorScale(row)
  return (
    <div className="asd-scale-row">
      <div className="asd-scale-row-label">
        <span>{row.label}</span>
        {row.help ? <InfoPopover text={row.help} width={280} /> : null}
      </div>
      <div
        className="asd-scale-track"
        role="img"
        aria-label={
          scaled
            ? `${row.label} at ${row.position_pct}% of sector range`
            : `${row.label} ${row.ticker_display}`
        }
      >
        {scaled ? (
          <>
            <span
              className={
                row.higher_is_better === false
                  ? 'asd-scale-track-line asd-scale-track-line--invert'
                  : 'asd-scale-track-line'
              }
            />
            <span className="asd-scale-marker" style={{ left: `${row.position_pct}%` }} />
          </>
        ) : (
          <span className="asd-scale-track-empty">{DASH}</span>
        )}
      </div>
      <div className="asd-scale-nums">
        <span className="asd-scale-num">
          <span className="asd-scale-num-label">Low</span>
          {scaled ? row.sector_low_display || DASH : DASH}
        </span>
        <span className="asd-scale-num">
          <span className="asd-scale-num-label">Mean</span>
          {scaled ? row.sector_mean_display || DASH : DASH}
        </span>
        <span className="asd-scale-num">
          <span className="asd-scale-num-label">High</span>
          {scaled ? row.sector_high_display || DASH : DASH}
        </span>
        <span className="asd-scale-num asd-scale-num--ticker">
          <span className="asd-scale-num-label">Ticker</span>
          {row.ticker_display || DASH}
        </span>
      </div>
    </div>
  )
}

function FundSection({
  title,
  rows,
  sector,
  spacer,
}: {
  title: string
  rows: FundamentalsRow[]
  sector: string | null
  spacer?: boolean
}) {
  if (!rows.length) return null
  const peers = peerCountHint(rows)
  const subtitle =
    sector && peers != null
      ? `${sector} sector · ${peers} peers`
      : sector
        ? `${sector} sector`
        : null

  return (
    <>
      <div className={`asd-section-head${spacer ? ' asd-fund-head-spacer' : ''}`}>
        <span className="asd-section-title">{title}</span>
        {subtitle && <span className="asd-fund-sector-sub">{subtitle}</span>}
      </div>
      <div className="asd-scale-block">
        <div className="asd-scale-axis" aria-hidden="true">
          <span>Sector low</span>
          <span>Below mean</span>
          <span>Mean</span>
          <span>Above mean</span>
          <span>Sector high</span>
        </div>
        <ScaleColsHead />
        {rows.map((row) => (
          <FundRow key={row.id} row={row} />
        ))}
      </div>
    </>
  )
}

/** Analysis-tab FA snapshot — valuation + growth/health with sector scales + AI blurb. */
export function FundamentalsSnapshot({ instrumentId }: Props) {
  const [data, setData] = useState<FundamentalsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [narrating, setNarrating] = useState(false)

  useEffect(() => {
    setData(null)
    setLoading(true)
  }, [instrumentId])

  useEffect(() => {
    let alive = true
    setLoading(true)
    // First paint: rows without waiting on LLM (narrate=false), then top-up narrative.
    authFetch(`/api/intel/assets/${instrumentId}/fundamentals?narrate=false`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: FundamentalsResponse | null) => {
        if (!alive) return
        setData(d)
        setLoading(false)
        const hasRows = !!(d?.valuation?.length || d?.growth?.length)
        if (hasRows && !d?.narrative) {
          setNarrating(true)
          authFetch(`/api/intel/assets/${instrumentId}/fundamentals?narrate=true`)
            .then((r) => (r.ok ? r.json() : null))
            .then((full: FundamentalsResponse | null) => {
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
  }, [instrumentId])

  if (loading && !data) {
    return (
      <Panel>
        <div className="asd-section-head">
          <span className="asd-section-title">Fundamental Analysis</span>
        </div>
        <div className="asd-scale-block">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} height={22} />
          ))}
        </div>
      </Panel>
    )
  }

  const valuation = data?.valuation ?? []
  const growth = data?.growth ?? []
  if (!valuation.length && !growth.length) return null

  const sector = data?.sector ?? null

  return (
    <Panel>
      <FundSection title="Fundamental Analysis" rows={valuation} sector={sector} />
      <FundSection
        title="Growth & Health"
        rows={growth}
        sector={sector}
        spacer={valuation.length > 0}
      />

      <div className="asd-fund-narrative">
        {(data?.narrative || narrating) && (
          <div className="asd-pi-wrap">
            <div className="asd-pi-head">
              <p className="asd-pi-label">
                Fundamental Analysis <LuciusMark size="1em" aria-hidden="true" />
              </p>
              {narrating && (
                <span
                  className="asd-pi-updating"
                  role="status"
                  aria-label="Fundamental Analysis narrative is generating"
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
        <Text size="xs" tone="faint" className="asd-fund-caption">
          Lean from multiples vs sector peers · via Yahoo · not personal advice.
        </Text>
      </div>
    </Panel>
  )
}
