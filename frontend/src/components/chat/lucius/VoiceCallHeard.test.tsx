// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

const call = vi.hoisted(() => ({ heard: '' }))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => call }))

import { VoiceCallHeard } from './VoiceCallHeard'

afterEach(() => {
  cleanup()
  call.heard = ''
})

describe('VoiceCallHeard', () => {
  it('draws nothing while nothing is being said', () => {
    const { container } = render(<VoiceCallHeard variant="screen" />)
    expect(container.firstChild).toBeNull()
  })

  it('shows your words in the form it was asked for', () => {
    call.heard = 'how much did I'
    const { container } = render(<VoiceCallHeard variant="card" />)
    expect(container.textContent).toBe('how much did I')
    expect(container.querySelector('.lx-callheard--card')).toBeTruthy()
  })
})
