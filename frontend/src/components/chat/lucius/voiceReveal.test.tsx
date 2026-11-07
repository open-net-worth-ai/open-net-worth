// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { Message } from '../../../types/chat'

const call = vi.hoisted(() => ({
  phase: 'live',
  sessionId: 's1' as string | null,
  spoken: '',
  spokenDone: false,
}))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => ({ ...call }) }))

import { spokenPrefix, useVoiceReveal } from './voiceReveal'

const ANSWER = 'Your **balance** is about two thousand dollars, up a little today.'

function Bubble({ message }: { message: Message }) {
  const shown = useVoiceReveal(message, 's1')
  return <p data-testid="text">{shown.text}</p>
}
const msg = (over: Partial<Message> = {}) =>
  ({ id: 'a1', role: 'assistant', text: ANSWER, ...over }) as Message
const shown = () => screen.getByTestId('text').textContent

beforeEach(() => {
  vi.useFakeTimers()
  Object.assign(call, { phase: 'live', sessionId: 's1', spoken: '', spokenDone: false })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('spokenPrefix', () => {
  it('finds what has been said, ignoring what the voice never reads', () => {
    expect(spokenPrefix(ANSWER, 'Your balance is')).toBe('Your **balance** is')
  })

  it('works for a language written without spaces', () => {
    expect(spokenPrefix('您的余额大约是两千元。', '您的余额')).toBe('您的余额')
  })

  it('keeps Vietnamese marks', () => {
    expect(spokenPrefix('Ngân sách của bạn còn ổn.', 'Ngân sách')).toBe('Ngân sách')
  })

  it('knows when the voice is saying something else', () => {
    expect(spokenPrefix(ANSWER, 'One sec, let me check.')).toBeNull()
    expect(spokenPrefix(ANSWER, '')).toBeNull()
  })
})

describe('an answer during a call', () => {
  it('waits for the voice, then follows it word by word, then shows whole', () => {
    const { rerender } = render(<Bubble message={msg()} />)
    expect(shown()).toBe('')
    call.spoken = 'Your balance'
    rerender(<Bubble message={msg()} />)
    expect(shown()).toBe('Your **balance')
    call.spoken = 'Your balance is about two'
    rerender(<Bubble message={msg()} />)
    expect(shown()).toBe('Your **balance** is about two')
    call.spokenDone = true
    rerender(<Bubble message={msg()} />)
    rerender(<Bubble message={msg()} />)
    expect(shown()).toBe(ANSWER)
  })

  it('shows whole if the voice never starts', () => {
    render(<Bubble message={msg()} />)
    expect(shown()).toBe('')
    act(() => {
      vi.advanceTimersByTime(4100)
    })
    expect(shown()).toBe(ANSWER)
  })

  it('does not start that wait until the answer has finished writing', () => {
    const { rerender } = render(<Bubble message={msg({ streaming: true })} />)
    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(shown()).toBe('')
    rerender(<Bubble message={msg()} />)
    act(() => {
      vi.advanceTimersByTime(4100)
    })
    expect(shown()).toBe(ANSWER)
  })

  it('shows whole if the voice stalls or is cut off', () => {
    const { rerender } = render(<Bubble message={msg()} />)
    call.spoken = 'Your balance'
    rerender(<Bubble message={msg()} />)
    act(() => {
      vi.advanceTimersByTime(6100)
    })
    expect(shown()).toBe(ANSWER)
  })

  it('shows whole if the voice moves on to something else', () => {
    const { rerender } = render(<Bubble message={msg()} />)
    call.spoken = 'Your balance'
    rerender(<Bubble message={msg()} />)
    call.spoken = 'Anything else?'
    rerender(<Bubble message={msg()} />)
    rerender(<Bubble message={msg()} />)
    expect(shown()).toBe(ANSWER)
  })

  it('shows whole the moment the call ends', () => {
    const { rerender } = render(<Bubble message={msg()} />)
    call.phase = 'ended'
    rerender(<Bubble message={msg()} />)
    rerender(<Bubble message={msg()} />)
    expect(shown()).toBe(ANSWER)
  })

  it('never holds back an error or an approval', () => {
    render(<Bubble message={msg({ awaitingApproval: true } as Partial<Message>)} />)
    expect(shown()).toBe(ANSWER)
  })

  it('leaves alone an answer from before the call, and other conversations', () => {
    call.phase = 'idle'
    render(<Bubble message={msg()} />)
    expect(shown()).toBe(ANSWER)
    cleanup()
    Object.assign(call, { phase: 'live', sessionId: 'other' })
    render(<Bubble message={msg()} />)
    expect(shown()).toBe(ANSWER)
  })
})
