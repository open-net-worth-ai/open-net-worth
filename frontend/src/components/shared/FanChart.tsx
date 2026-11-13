import { useEffect, useRef } from 'react'
import {
  Chart,
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Filler,
} from 'chart.js'
import { fBaseCompact } from '../../utils/format'
import { cssToken } from '../../utils/chartTheme'
import { useTheme } from '../../theme'

Chart.register(
  LineElement,
  LineController,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Filler,
)

export interface FanChartBand {
  year_index: number
  age?: number
  p10: number
  p50: number
  p90: number
}

export interface FanChartProps {
  bands: FanChartBand[]
  secondBands?: FanChartBand[]
  retirementAge?: number | null
  baseCurrency: string
  loading?: boolean
  height?: number
  showDryMarker?: boolean
}

export function FanChart({
  bands,
  secondBands,
  retirementAge,
  loading = false,
  height = 220,
  showDryMarker = false,
}: FanChartProps) {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInst = useRef<Chart | null>(null)
  const bandsRef = useRef<FanChartBand[]>(bands)
  bandsRef.current = bands
  const { resolved: theme } = useTheme()
  const themeAtBuild = useRef(theme)

  useEffect(() => {
    if (!chartRef.current || bands.length === 0) return

    // Chart chrome colors are read as concrete values at build time, so a theme
    // flip must rebuild the chart rather than reuse-and-update it.
    if (chartInst.current && themeAtBuild.current !== theme) {
      chartInst.current.destroy()
      chartInst.current = null
    }
    themeAtBuild.current = theme

    const labels = bands.map((b) => (b.age != null ? `${b.age.toFixed(0)}` : `Y${b.year_index}`))
    const p10 = bands.map((b) => b.p10)
    const p50 = bands.map((b) => b.p50)
    const p90 = bands.map((b) => b.p90)

    const datasets: Chart['data']['datasets'] = [
      {
        label: 'p90 (optimistic)',
        data: p90,
        borderColor: 'rgba(52,176,138,0.35)',
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
        tension: 0.3,
      },
      {
        label: 'p10 (pessimistic)',
        data: p10,
        borderColor: 'rgba(52,176,138,0.18)',
        backgroundColor: 'rgba(52,176,138,0.18)',
        borderWidth: 1,
        pointRadius: 0,
        fill: '-1',
        tension: 0.3,
      },
      {
        label: 'Median (p50)',
        data: p50,
        borderColor: cssToken('--good'),
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        tension: 0.3,
      },
    ]

    if (secondBands && secondBands.length > 0) {
      const b90 = secondBands.map((b) => b.p90)
      const b10 = secondBands.map((b) => b.p10)
      const b50 = secondBands.map((b) => b.p50)
      datasets.push(
        {
          label: 'B — p90',
          data: b90,
          borderColor: 'rgba(52,176,138,0.18)',
          borderWidth: 1,
          pointRadius: 0,
          fill: false,
          tension: 0.3,
          borderDash: [4, 3],
        },
        {
          label: 'B — p10',
          data: b10,
          borderColor: 'rgba(52,176,138,0.10)',
          backgroundColor: 'rgba(52,176,138,0.10)',
          borderWidth: 1,
          pointRadius: 0,
          fill: '-1',
          tension: 0.3,
          borderDash: [4, 3],
        },
        {
          label: 'Scenario B (p50)',
          data: b50,
          borderColor: 'rgba(52,176,138,0.50)',
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
          tension: 0.3,
          borderDash: [4, 3],
        },
      )
    }

    const existing = chartInst.current
    if (existing) {
      existing.data.labels = labels
      existing.data.datasets = datasets
      existing.update()
      return
    }

    chartInst.current = new Chart(chartRef.current, {
      type: 'line',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: cssToken('--surface2'),
            borderColor: cssToken('--line'),
            titleColor: cssToken('--ink'),
            bodyColor: cssToken('--muted'),
            borderWidth: 1,
            callbacks: {
              title: (items) => {
                const first = items[0]
                if (!first) return ''
                const b = bandsRef.current[first.dataIndex]
                return b?.age != null ? `Age ${first.label}` : `Year ${first.label}`
              },
              label: (item) => {
                const lbl = item.dataset.label ?? ''
                if (lbl.startsWith('B —')) return null as unknown as string
                return ` ${lbl}: ${fBaseCompact(item.raw as number)}`
              },
            },
          },
        },
        scales: {
          x: {
            grid: { color: cssToken('--line') },
            ticks: { color: cssToken('--faint'), font: { size: 11 }, maxTicksLimit: 8 },
          },
          y: {
            grid: { color: cssToken('--line') },
            ticks: {
              color: cssToken('--faint'),
              font: { size: 11 },
              maxTicksLimit: 6,
              callback: (v) => fBaseCompact(v as number),
            },
          },
        },
      },
    })
  }, [bands, secondBands, theme])

  useEffect(() => {
    return () => {
      chartInst.current?.destroy()
      chartInst.current = null
    }
  }, [])

  // Retirement age vertical overlay (no annotation plugin required)
  const retirementLinePct = (() => {
    if (!retirementAge || bands.length < 2) return null
    const idx = bands.findIndex((b) => b.age != null && b.age >= retirementAge)
    if (idx < 0) return null
    return (idx / (bands.length - 1)) * 100
  })()

  // Dry-out overlay: first band after midpoint where p50 hits zero
  const dryInfo = (() => {
    if (!showDryMarker || bands.length < 2) return null
    const mid = Math.floor(bands.length / 3)
    const idx = bands.findIndex((b, i) => i > mid && b.p50 <= 0)
    if (idx < 0) return null
    return {
      pct: (idx / (bands.length - 1)) * 100,
      age: bands[idx]?.age,
    }
  })()

  return (
    <div className={`fan-chart-wrap${loading ? ' fan-chart-loading' : ''}`}>
      <div style={{ height, position: 'relative' }}>
        <canvas ref={chartRef} />
        {retirementLinePct != null && (
          <div className="fan-chart-retire-line" style={{ left: `${retirementLinePct}%` }} />
        )}
        {dryInfo != null && (
          <>
            <div className="fan-chart-dry-line" style={{ left: `${dryInfo.pct}%` }} />
            <div
              className="fan-chart-dry-label"
              style={{ left: `${Math.min(dryInfo.pct + 0.5, 92)}%` }}
            >
              {dryInfo.age != null
                ? `typical path runs dry ~age ${dryInfo.age.toFixed(1)}`
                : 'typical path runs dry'}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
