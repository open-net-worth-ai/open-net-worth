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

export interface TrajectoryPoint {
  year: number
  age: number
  nw: number
  target: number
}

export interface TrajectoryChartProps {
  trajectory: TrajectoryPoint[]
  fireNumber: number
  loading?: boolean
  height?: number
}

export function TrajectoryChart({
  trajectory,
  fireNumber,
  loading = false,
  height = 196,
}: TrajectoryChartProps) {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInst = useRef<Chart | null>(null)
  const trajectoryRef = useRef(trajectory)
  trajectoryRef.current = trajectory
  const { resolved: theme } = useTheme()
  const themeAtBuild = useRef(theme)

  useEffect(() => {
    if (!chartRef.current || trajectory.length === 0) return

    // Chart chrome colors are concrete values read at build time — rebuild on theme flips.
    if (chartInst.current && themeAtBuild.current !== theme) {
      chartInst.current.destroy()
      chartInst.current = null
    }
    themeAtBuild.current = theme

    const labels = trajectory.map((p) => `${p.age.toFixed(0)}`)
    const nwValues = trajectory.map((p) => p.nw)
    const crossIdx = trajectory.findIndex((p) => p.nw >= fireNumber)
    const fireTarget = trajectory.map(() => fireNumber)

    // Single visible dot at the crossing point
    const pointRadii = nwValues.map((_, i) => (i === crossIdx && crossIdx > 0 ? 5 : 0))
    const pointColors = nwValues.map((_, i) =>
      i === crossIdx && crossIdx > 0 ? cssToken('--warn') : cssToken('--good'),
    )
    const pointBorderColors = nwValues.map((_, i) =>
      i === crossIdx && crossIdx > 0 ? cssToken('--surface') : cssToken('--good'),
    )

    const datasets: Chart['data']['datasets'] = [
      {
        label: 'Net worth',
        data: nwValues,
        borderColor: cssToken('--good'),
        backgroundColor: 'rgba(52,176,138,0.08)',
        borderWidth: 2,
        pointRadius: pointRadii,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointBorderColors,
        pointBorderWidth: 2,
        fill: true,
        tension: 0.3,
      },
      {
        label: `FIRE target ${fBaseCompact(fireNumber)}`,
        data: fireTarget,
        borderColor: 'rgba(224,160,50,0.7)',
        borderWidth: 1.5,
        borderDash: [5, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
      },
    ]

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
                const p = trajectoryRef.current[first.dataIndex]
                return `Age ${p?.age.toFixed(0) ?? first.label}`
              },
              label: (item) => {
                // Show FIRE target label differently
                if (item.datasetIndex === 1) {
                  return ` FIRE target: ${fBaseCompact(item.raw as number)}`
                }
                return ` Net worth: ${fBaseCompact(item.raw as number)}`
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
  }, [trajectory, fireNumber, theme])

  useEffect(() => {
    return () => {
      chartInst.current?.destroy()
      chartInst.current = null
    }
  }, [])

  return (
    <div className={`traj-chart-wrap${loading ? ' fan-chart-loading' : ''}`}>
      <div style={{ height, position: 'relative' }}>
        <canvas ref={chartRef} />
      </div>
    </div>
  )
}
