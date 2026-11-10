// @vitest-environment jsdom
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CodeBlock } from './CodeBlock'

afterEach(() => {
  vi.useRealTimers()
})

it('copies the whole block, exactly as shown, and says so', async () => {
  vi.useFakeTimers()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.assign(navigator, { clipboard: { writeText } })
  render(
    <CodeBlock>
      <code>{'git clone https://example.test/repo.git\ncd repo\nnpm install'}</code>
    </CodeBlock>,
  )
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }))
  })
  expect(writeText).toHaveBeenCalledWith(
    'git clone https://example.test/repo.git\ncd repo\nnpm install',
  )
  expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy()
  act(() => {
    vi.advanceTimersByTime(1600)
  })
  expect(screen.getByRole('button', { name: 'Copy code' })).toBeTruthy()
})
