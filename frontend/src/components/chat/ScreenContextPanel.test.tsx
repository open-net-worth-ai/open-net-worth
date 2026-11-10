// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render, fireEvent } from '@testing-library/react'
import { ScreenContextPanel } from './ScreenContextPanel'
import type { ScreenSnapshot } from '../../state/screenSnapshot'

afterEach(cleanup)

const snap: ScreenSnapshot = {
  id: 'asset-detail',
  title: 'NVIDIA Corporation',
  facts: { price: '170 USD', today: '+3.1%' },
  handles: [
    {
      id: 'news-0',
      type: 'news_story',
      label: 'Reuters',
      descriptor: 'NVDA beats',
      source: 'https://x',
      icon: '◴',
    },
  ],
}

describe('ScreenContextPanel', () => {
  it('shows what is passed inline + the on-demand handles when enabled', () => {
    const { getByText } = render(
      <ScreenContextPanel
        snapshot={snap}
        enabled
        onToggle={() => {}}
        pinnedIds={[]}
        onPin={() => {}}
      />,
    )
    expect(getByText('Screen awareness')).toBeTruthy()
    expect(getByText('Passed inline now')).toBeTruthy()
    expect(getByText('NVIDIA Corporation')).toBeTruthy() // the Screen fact
    expect(getByText('170 USD')).toBeTruthy()
    expect(getByText('Reuters')).toBeTruthy() // a handle
    expect(getByText('attach')).toBeTruthy()
  })

  it('tapping "attach" pins the handle', () => {
    const onPin = vi.fn()
    const { getByText } = render(
      <ScreenContextPanel
        snapshot={snap}
        enabled
        onToggle={() => {}}
        pinnedIds={[]}
        onPin={onPin}
      />,
    )
    fireEvent.click(getByText('attach'))
    expect(onPin).toHaveBeenCalledWith('news-0')
  })

  it('shows attached state for a pinned handle', () => {
    const { getByText } = render(
      <ScreenContextPanel
        snapshot={snap}
        enabled
        onToggle={() => {}}
        pinnedIds={['news-0']}
        onPin={() => {}}
      />,
    )
    expect(getByText('attached')).toBeTruthy()
  })

  it('toggling the switch fires onToggle', () => {
    const onToggle = vi.fn()
    const { getByRole } = render(
      <ScreenContextPanel
        snapshot={snap}
        enabled
        onToggle={onToggle}
        pinnedIds={[]}
        onPin={() => {}}
      />,
    )
    fireEvent.click(getByRole('switch'))
    expect(onToggle).toHaveBeenCalled()
  })

  it('renders nothing when enabled but no snapshot (no confusing empty box)', () => {
    const { container } = render(
      <ScreenContextPanel
        snapshot={null}
        enabled
        onToggle={() => {}}
        pinnedIds={[]}
        onPin={() => {}}
      />,
    )
    expect(container.querySelector('.screen-ctx')).toBeNull()
  })

  it('shows the off-state header when disabled (a place to turn it back on)', () => {
    const { getByText } = render(
      <ScreenContextPanel
        snapshot={null}
        enabled={false}
        onToggle={() => {}}
        pinnedIds={[]}
        onPin={() => {}}
      />,
    )
    expect(getByText('Screen awareness off')).toBeTruthy()
  })
})
