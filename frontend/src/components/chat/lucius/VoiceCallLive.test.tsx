// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

const call = vi.hoisted(() => ({
  lastSpoken: '',
  heard: '',
  heardDone: false,
  spoken: '',
  spokenDone: false,
  phase: 'live',
  sessionId: 's1' as string | null,
}))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => call }))

import { VoiceCallCaption, VoiceCallHeardMessage, VoiceCallSpokenMessage } from './VoiceCallLive'

afterEach(() => {
  cleanup()
  Object.assign(call, { heard: '', heardDone: false, spoken: '', lastSpoken: '', sessionId: 's1' })
})

describe('your words in the chat', () => {
  it('stream as a message of yours in the conversation the call is on', () => {
    call.heard = 'how much did I'
    const { container } = render(<VoiceCallHeardMessage sessionId="s1" />)
    expect(container.querySelector('.msg-user')?.textContent).toBe('how much did I')
  })

  it('stay out of any other conversation', () => {
    call.heard = 'how much did I'
    const { container } = render(<VoiceCallHeardMessage sessionId="other" />)
    expect(container.firstChild).toBeNull()
  })

  it('step aside once the real message has landed', () => {
    call.heard = 'How much did I spend?'
    const { container } = render(
      <VoiceCallHeardMessage
        sessionId="s1"
        lastUser={{ id: 'u2', text: 'how much did I spend' }}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('stay after you finish, with no gap, until the real message lands', () => {
    call.heard = 'how much did I spend'
    const earlier = { id: 'u1', text: 'hello' }
    const { container, rerender } = render(
      <VoiceCallHeardMessage sessionId="s1" lastUser={earlier} />,
    )
    call.heardDone = true
    rerender(<VoiceCallHeardMessage sessionId="s1" lastUser={earlier} />)
    expect(container.textContent).toBe('how much did I spend')
    rerender(
      <VoiceCallHeardMessage sessionId="s1" lastUser={{ id: 'u2', text: 'how much I spent' }} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('stay when the last message was something else', () => {
    call.heard = 'and my rent'
    const { container } = render(
      <VoiceCallHeardMessage
        sessionId="s1"
        lastUser={{ id: 'u1', text: 'how much did I spend' }}
      />,
    )
    expect(container.textContent).toBe('and my rent')
  })
})

describe("the voice's own lines in the chat", () => {
  it('stream as a message of its own while they are said', () => {
    call.lastSpoken = 'One sec, let me check'
    const { container } = render(
      <VoiceCallSpokenMessage
        sessionId="s1"
        lastAssistant={{ id: 'a1', text: 'Earlier answer.' }}
      />,
    )
    expect(container.querySelector('.msg-assistant')?.textContent).toBe('One sec, let me check')
  })

  it("leave Lucius's own answer to the message already in the chat", () => {
    call.lastSpoken = 'Your balance is about'
    const { container } = render(
      <VoiceCallSpokenMessage
        sessionId="s1"
        lastAssistant={{ id: 'a2', text: 'Your **balance** is about two thousand.' }}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('step aside once the saved line is in the chat, and stay out of other conversations', () => {
    call.lastSpoken = "You're welcome."
    const saved = render(
      <VoiceCallSpokenMessage
        sessionId="s1"
        lastAssistant={{ id: 'a3', text: "You're welcome." }}
      />,
    )
    expect(saved.container.firstChild).toBeNull()
    saved.unmount()
    const other = render(<VoiceCallSpokenMessage sessionId="other" />)
    expect(other.container.firstChild).toBeNull()
  })
})

describe('the full screen caption', () => {
  it('draws nothing while nobody is talking', () => {
    const { container } = render(<VoiceCallCaption />)
    expect(container.firstChild).toBeNull()
  })

  it('hands over to Lucius once you have finished', () => {
    call.heard = 'how much did I spend'
    call.heardDone = true
    const { container, rerender } = render(<VoiceCallCaption />)
    const caption = () => container.querySelector('.lx-callcaption')
    expect(caption()?.textContent).toBe('how much did I spend')
    call.spoken = 'About two'
    rerender(<VoiceCallCaption />)
    expect(caption()?.textContent).toBe('About two')
    expect(caption()?.getAttribute('data-who')).toBe('lucius')
  })

  it('blurs its top line only once the words run past the box', () => {
    const height = (self: HTMLElement) => (self.tagName === 'SPAN' ? spanHeight : 72)
    let spanHeight = 72
    const span = vi
      .spyOn(HTMLElement.prototype, 'offsetHeight', 'get')
      .mockImplementation(function (this: HTMLElement) {
        return height(this)
      })
    const box = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(72)
    call.spoken = 'three lines of words'
    const { container, rerender } = render(<VoiceCallCaption />)
    const caption = () => container.querySelector('.lx-callcaption')
    expect(caption()?.hasAttribute('data-long')).toBe(false)
    spanHeight = 96
    call.spoken = 'three lines of words and a fourth'
    rerender(<VoiceCallCaption />)
    expect(caption()?.hasAttribute('data-long')).toBe(true)
    span.mockRestore()
    box.mockRestore()
  })

  it('follows Lucius, and you when you talk over him', () => {
    call.spoken = 'Your balance'
    const { container, rerender } = render(<VoiceCallCaption />)
    const caption = () => container.querySelector('.lx-callcaption')
    expect(caption()?.getAttribute('data-who')).toBe('lucius')
    expect(caption()?.textContent).toBe('Your balance')
    call.heard = 'wait'
    rerender(<VoiceCallCaption />)
    expect(caption()?.getAttribute('data-who')).toBe('you')
    expect(caption()?.textContent).toBe('wait')
  })
})
