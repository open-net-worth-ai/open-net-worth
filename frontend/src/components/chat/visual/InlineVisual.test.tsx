// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { InlineVisual } from './InlineVisual'
import { formatVisualValue } from './format'
import type { VisualBlock, VisualSpec } from '../../../types/chat'

// The frame's font files are fetched in the browser; these tests only render the frame element.
vi.mock('./drawingFonts', () => ({
  FRAME_FONT: 'Plus Jakarta Sans',
  loadFrameFonts: () => Promise.resolve(''),
}))

// Chart.js needs a real canvas; the wrapper is covered by the Overview tests.
const chartSpy = vi.fn()
vi.mock('../../tabs/overview/charts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../tabs/overview/charts')>()
  return {
    ...actual,
    OvChart: (props: { config: unknown }) => {
      chartSpy(props.config)
      return <canvas data-testid="ov-chart" />
    },
  }
})

const FIXTURE: VisualSpec = {
  id: 'vis_000000000001',
  title: 'Spending, Nov vs Oct',
  as_of: '2026-11-17T09:30:00Z',
  caption: ['What-if — monthly saving 3,000 (your plan: 2,000)'],
  blocks: [
    {
      type: 'stats',
      tiles: [
        {
          label: 'This month',
          value: 4180,
          compare: 3640,
          compare_label: 'Oct',
          format: 'money',
          currency: 'SGD',
        },
      ],
    },
    {
      type: 'chart',
      kind: 'bar',
      format: 'money',
      currency: 'SGD',
      labels: ['Dining', 'Travel'],
      series: [
        { name: 'Nov', values: [310, 420] },
        { name: 'Oct', values: [280, null] },
      ],
      targets: null,
    },
    {
      type: 'timeline',
      format: 'money',
      currency: 'SGD',
      items: [{ date: '2026-12-01', label: 'ACME vest', value: 5200 }],
    },
  ],
}

const chart = (kind: string, extra: object = {}): VisualBlock =>
  ({
    type: 'chart',
    kind,
    format: 'money',
    currency: 'SGD',
    labels: ['A', 'B'],
    series: [{ name: 's', values: [100, -50] }],
    targets: null,
    ...extra,
  }) as VisualBlock

afterEach(() => {
  cleanup()
  chartSpy.mockClear()
  document.body.classList.remove('hide-amounts')
})

describe('InlineVisual', () => {
  it('renders title, tiles, chart, timeline and caption from the fixture', () => {
    const { container } = render(<InlineVisual visual={FIXTURE} />)
    expect(screen.getByText('Spending, Nov vs Oct')).toBeTruthy()
    expect(screen.getByText('S$4,180')).toBeTruthy()
    expect(container.querySelector('.ivis-tile-compare')?.textContent).toBe('vs Oct S$3,640')
    expect(screen.getByTestId('ov-chart')).toBeTruthy()
    const cfg = chartSpy.mock.calls[0]![0] as {
      type: string
      data: { datasets: { data: unknown[] }[] }
    }
    expect(cfg.type).toBe('bar')
    expect(cfg.data.datasets[1]!.data).toEqual([280, null])
    expect(screen.getByText('ACME vest')).toBeTruthy()
    expect(screen.getByText('S$5,200')).toBeTruthy()
    expect(screen.getByText(/What-if/)).toBeTruthy()
  })

  it('renders a line chart through the same wrapper', () => {
    render(<InlineVisual visual={{ ...FIXTURE, blocks: [chart('line')] }} />)
    expect((chartSpy.mock.calls[0]![0] as { type: string }).type).toBe('line')
  })

  it('renders diverging, stack and progress as HTML', () => {
    const { container } = render(
      <InlineVisual
        visual={{
          ...FIXTURE,
          blocks: [
            chart('diverging'),
            chart('stack', { series: [{ name: 's', values: [75, 25] }] }),
            chart('progress', { targets: [200, 100] }),
          ],
        }}
      />,
    )
    expect(container.querySelectorAll('.ivis-div-bar.neg').length).toBe(1)
    const parts = container.querySelectorAll<HTMLElement>('.ivis-stack-bar span')
    expect(parts[0]!.style.width).toBe('75%')
    expect(container.querySelector<HTMLElement>('.ivis-prog-fill')!.style.width).toBe('50%')
  })

  it('ignores an unknown block and a malformed one', () => {
    const bad = {
      ...FIXTURE,
      blocks: [
        { type: 'hologram' },
        { type: 'stats' },
        chart('stack', { series: [] }),
        FIXTURE.blocks[2]!,
      ],
    } as unknown as VisualSpec
    render(<InlineVisual visual={bad} />)
    expect(screen.getByText('ACME vest')).toBeTruthy()
    expect(render(<InlineVisual visual={{} as VisualSpec} />).container.innerHTML).toBe('')
  })

  it('shows the change on a tile, coloured by which way is good', () => {
    const tile = (better: 'up' | 'down' | null) =>
      ({
        ...FIXTURE,
        blocks: [
          {
            type: 'stats',
            tiles: [
              {
                label: 'Spent',
                value: 4180,
                compare: 3640,
                compare_label: 'Oct',
                format: 'money',
                currency: 'SGD',
                delta: 540,
                delta_pct: 0.148352,
                better,
              },
            ],
          },
        ],
      }) as VisualSpec
    const bad = render(<InlineVisual visual={tile('down')} />).container
    const delta = bad.querySelector('.ivis-tile-delta')!
    expect(delta.textContent).toBe('+S$540 (+15%) ')
    expect(delta.classList.contains('ivis-tile-delta--bad')).toBe(true)
    const good = render(<InlineVisual visual={tile('up')} />).container
    expect(good.querySelector('.ivis-tile-delta--good')).toBeTruthy()
    const flat = render(<InlineVisual visual={tile(null)} />).container
    expect(flat.querySelector('.ivis-tile-delta--flat')).toBeTruthy()
  })

  it('shows the title above blocks but not above a drawing, which has its own heading', () => {
    const withBlocks = render(<InlineVisual visual={FIXTURE} />).container
    expect(withBlocks.querySelector('.ivis-title')!.textContent).toBe('Spending, Nov vs Oct')
    const drawing = { id: 'vis_2', title: 'Spending', code: '<svg></svg>' } as VisualSpec
    const alone = render(<InlineVisual visual={drawing} />).container
    expect(alone.querySelector('.ivis-title')).toBeNull()
    expect(alone.querySelector('iframe.ivis-frame')!.getAttribute('title')).toBe('Spending')
  })

  it('carries the money mask on tiles, timeline and caption figures, and masks canvas text', () => {
    const visual = { ...FIXTURE, caption: ['Plan saves S$3,000 a month'] }
    const { container } = render(<InlineVisual visual={visual} />)
    expect(container.querySelector('.ivis-tile-value')!.classList.contains('money')).toBe(true)
    expect(container.querySelector('.ivis-tl-value')!.classList.contains('money')).toBe(true)
    expect(container.querySelector('.ivis-caption .money')!.textContent).toBe('S$3,000')
    document.body.classList.add('hide-amounts')
    expect(formatVisualValue(310, 'money', 'SGD', true)).toBe('***')
    expect(formatVisualValue(310, 'money', 'SGD')).toBe('S$310')
  })
})

describe('formatVisualValue', () => {
  it('formats each kind', () => {
    expect(formatVisualValue(0.12, 'percent', null)).toBe('12%')
    expect(formatVisualValue(12, 'percent_points', null)).toBe('12%')
    expect(formatVisualValue(1234.5, 'number', null)).toBe('1,234.5')
    expect(formatVisualValue(4180.5, 'money', 'SGD')).toBe('S$4,181')
    expect(formatVisualValue(12.5, 'money', 'SGD')).toBe('S$12.50')
  })
})
