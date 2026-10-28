// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { createMemoryHistory } from '@tanstack/react-router'
import type { RouterHistory } from '@tanstack/history'
import { HistoryStepContext } from './useHistoryStep'
import { MEMORY_STEP, MEMORY_TOPIC_STEP, useMemoryWindow } from './useMemoryWindow'
import { useHistoryStep } from './useHistoryStep'

function Probe() {
  const memory = useMemoryWindow('home')
  const topic = useHistoryStep<{ section: string; key: string }>(MEMORY_TOPIC_STEP)
  return (
    <div>
      <span data-testid="window">{memory.open ? 'open' : 'closed'}</span>
      <span data-testid="topic">{topic.value ? topic.value.key : 'none'}</span>
      <button onClick={memory.show}>show</button>
      <button onClick={() => topic.open({ section: 'money', key: 'housing' })}>topic</button>
      <button onClick={topic.close}>back to list</button>
      <button onClick={memory.close}>done</button>
    </div>
  )
}

function mount(history: RouterHistory) {
  render(
    <HistoryStepContext.Provider value={history}>
      <Probe />
    </HistoryStepContext.Provider>,
  )
}

const text = (id: string) => screen.getByTestId(id).textContent
const click = (name: string) => act(() => screen.getByText(name).click())
const back = (history: RouterHistory) => act(() => history.back())

afterEach(cleanup)

describe('useHistoryStep — Back closes the memory window and its topic page first', () => {
  it('Back after opening the window closes it and stays on the same page', async () => {
    const history = createMemoryHistory({ initialEntries: ['/assets', '/lucius'] })
    mount(history)
    click('show')
    expect(text('window')).toBe('open')
    expect(history.location.pathname).toBe('/lucius')
    back(history)
    await act(async () => {})
    expect(text('window')).toBe('closed')
    expect(history.location.pathname).toBe('/lucius')
  })

  it('Back on a topic page returns to the list, then closes the window', async () => {
    const history = createMemoryHistory({ initialEntries: ['/assets', '/lucius'] })
    mount(history)
    click('show')
    click('topic')
    expect(text('topic')).toBe('housing')
    back(history)
    await act(async () => {})
    expect([text('window'), text('topic')]).toEqual(['open', 'none'])
    back(history)
    await act(async () => {})
    expect([text('window'), history.location.pathname]).toEqual(['closed', '/lucius'])
  })

  it('closing from the UI pops its own entries, leaving no stray step behind', async () => {
    const history = createMemoryHistory({ initialEntries: ['/assets', '/lucius'] })
    mount(history)
    click('show')
    click('topic')
    click('done')
    await act(async () => {})
    expect([text('window'), text('topic')]).toEqual(['closed', 'none'])
    expect(history.location.pathname).toBe('/lucius')
    const state = history.location.state as unknown as Record<string, unknown>
    expect(state[MEMORY_STEP] ?? null).toBeNull()
    back(history)
    await act(async () => {})
    expect(history.location.pathname).toBe('/assets')
  })

  it('without a router it is local state', () => {
    render(<Probe />)
    click('show')
    expect(text('window')).toBe('open')
    click('done')
    expect(text('window')).toBe('closed')
  })
})
