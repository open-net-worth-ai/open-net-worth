// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'

const levels = vi.hoisted(() => ({ current: { lucius: 0, you: 0, luciusTalking: false } }))
vi.mock('./voiceCall', () => ({ readLevels: () => levels.current }))

import { VoiceCallGlow } from './VoiceCallGlow'

let frames: FrameRequestCallback[] = []
let animate: ReturnType<typeof vi.fn>
let reduced = false

function run(count = 40) {
  act(() => {
    for (let i = 0; i < count; i++) {
      const due = frames
      frames = []
      due.forEach((f) => f(performance.now()))
    }
  })
}

function hear(side: 'lucius' | 'you' | 'nobody') {
  levels.current = {
    lucius: side === 'lucius' ? 0.5 : 0,
    you: side === 'you' ? 0.5 : 0,
    luciusTalking: side === 'lucius',
  }
  run()
}

const glow = () => document.querySelector('.lx-callglow') as HTMLElement

beforeEach(() => {
  vi.useFakeTimers()
  frames = []
  reduced = false
  levels.current = { lucius: 0, you: 0, luciusTalking: false }
  vi.stubGlobal('requestAnimationFrame', (f: FrameRequestCallback) => frames.push(f))
  vi.stubGlobal('cancelAnimationFrame', () => {})
  vi.stubGlobal('matchMedia', () => ({ matches: reduced }))
  animate = vi.fn()
  HTMLElement.prototype.animate = animate as unknown as HTMLElement['animate']
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate
})

describe('VoiceCallGlow', () => {
  it('moves with whoever is talking', () => {
    render(<VoiceCallGlow live />)
    hear('you')
    expect(glow().dataset.speaker).toBe('you')
    expect(Number(glow().style.getPropertyValue('--lx-orb-you'))).toBeGreaterThan(0.5)
    hear('nobody')
    hear('lucius')
    expect(glow().dataset.speaker).toBe('lucius')
    expect(Number(glow().style.getPropertyValue('--lx-orb-lucius'))).toBeGreaterThan(0.5)
  })

  it('sends the wave out once when he starts answering, not at every pause', () => {
    render(<VoiceCallGlow live />)
    hear('lucius')
    expect(animate).toHaveBeenCalledTimes(1)
    hear('nobody')
    vi.advanceTimersByTime(1000)
    hear('lucius')
    expect(animate).toHaveBeenCalledTimes(1)
  })

  it('sends it again for his next answer, after you spoke', () => {
    render(<VoiceCallGlow live />)
    hear('lucius')
    hear('nobody')
    hear('you')
    hear('nobody')
    hear('lucius')
    expect(animate).toHaveBeenCalledTimes(2)
  })

  it('sends it again after a long silence', () => {
    render(<VoiceCallGlow live />)
    hear('lucius')
    hear('nobody')
    vi.advanceTimersByTime(3000)
    hear('lucius')
    expect(animate).toHaveBeenCalledTimes(2)
  })

  it('marks when the light is his, so talking over him leaves the wash off', () => {
    render(<VoiceCallGlow live />)
    levels.current = { lucius: 0.3, you: 0.5, luciusTalking: true }
    run()
    expect(glow().dataset.luciusTalking).toBe('')
    expect(Number(glow().style.getPropertyValue('--lx-orb-lucius'))).toBeGreaterThan(0)
    levels.current = { lucius: 0, you: 0.5, luciusTalking: false }
    run()
    expect(glow().dataset.luciusTalking).toBeUndefined()
  })

  it('stays dark until the call is live', () => {
    levels.current = { lucius: 0.5, you: 0.5, luciusTalking: true }
    render(<VoiceCallGlow live={false} />)
    run()
    expect(glow().dataset.speaker).toBeUndefined()
    expect(animate).not.toHaveBeenCalled()
  })

  it('does not animate for someone who asked for less motion', () => {
    reduced = true
    render(<VoiceCallGlow live />)
    hear('lucius')
    expect(animate).not.toHaveBeenCalled()
    expect(glow().dataset.speaker).toBe('lucius') // the steady glow still says whose turn it is
  })
})
