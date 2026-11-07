// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'

const call = vi.hoisted(() => ({
  spokenLines: [] as string[],
  connectedAt: null as number | null,
  heard: '',
  heardDone: false,
  heardShown: false,
  luciusSpeaking: false,
  spoken: '',
  spokenDone: false,
  phase: 'live',
  sessionId: 's1' as string | null,
}))
vi.mock('./voiceCall', () => ({ useVoiceCall: () => call }))

import {
  LastAnswerReveal,
  VoiceCallCaption,
  VoiceCallHeardMessage,
  VoiceCallSpokenMessage,
} from './VoiceCallLive'
import { unheardAnswer, unsavedLines } from './voiceReveal'
import { chatSessions } from '../../../state/chatSessions'

afterEach(() => {
  cleanup()
  Object.assign(call, {
    heard: '',
    heardDone: false,
    heardShown: false,
    luciusSpeaking: false,
    spoken: '',
    spokenLines: [],
    sessionId: 's1',
  })
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

const asked = { role: 'user' as const, text: 'What is my balance?' }
const answer = (text: string) => ({ role: 'assistant' as const, text })

describe("the voice's own lines in the chat", () => {
  it('stream as a message of its own while they are said', () => {
    call.spokenLines = ['One sec, let me check']
    const { container } = render(
      <VoiceCallSpokenMessage sessionId="s1" messages={[answer('Earlier answer.'), asked]} />,
    )
    expect(container.querySelector('.msg-assistant')?.textContent).toBe('One sec, let me check')
  })

  it('stay as long as Lucius takes, each in order, instead of going away', () => {
    call.spokenLines = ['One sec, let me check', 'Still on it']
    const { container } = render(<VoiceCallSpokenMessage sessionId="s1" messages={[asked]} />)
    expect([...container.querySelectorAll('.msg-assistant')].map((m) => m.textContent)).toEqual([
      'One sec, let me check',
      'Still on it',
    ])
  })

  it("leave Lucius's own answer to the message already in the chat", () => {
    call.spokenLines = ['Your balance is about']
    const { container } = render(
      <VoiceCallSpokenMessage
        sessionId="s1"
        messages={[asked, answer('Your **balance** is about two thousand.')]}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('step aside once the saved line is in the chat, and stay out of other conversations', () => {
    call.spokenLines = ["You're welcome."]
    const saved = render(
      <VoiceCallSpokenMessage sessionId="s1" messages={[answer("You're welcome."), asked]} />,
    )
    expect(saved.container.firstChild).toBeNull()
    saved.unmount()
    const other = render(<VoiceCallSpokenMessage sessionId="other" messages={[asked]} />)
    expect(other.container.firstChild).toBeNull()
  })

  it('sit above an answer still to be heard, not below it', () => {
    call.spokenLines = ['One sec, let me check']
    const messages = [asked, answer('Your balance is about two thousand.')]
    const end = render(<VoiceCallSpokenMessage sessionId="s1" messages={messages} />)
    expect(end.container.firstChild).toBeNull()
    end.unmount()
    const shown = render(
      <LastAnswerReveal
        message={{ ...messages[1], id: 'a1' } as never}
        messages={messages}
        sessionId="s1"
        live
      >
        {() => <div className="the-answer" />}
      </LastAnswerReveal>,
    )
    const first = shown.container.querySelector('.msg-assistant')
    expect(first?.textContent).toBe('One sec, let me check')
    expect(first?.compareDocumentPosition(shown.container.querySelector('.the-answer')!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })
})

describe('unsavedLines', () => {
  const said = 'Your balance is about two thousand.'

  it('puts a line after the answer heard below it, and one before above', () => {
    expect(unsavedLines(['One sec.', said, 'Anything else?'], ['Old.', said])).toEqual({
      above: ['One sec.'],
      below: ['Anything else?'],
    })
  })

  it('holds everything above an answer not heard yet, even one still empty', () => {
    expect(unsavedLines(['One sec.', 'Still on it.'], [said])).toEqual({
      above: ['One sec.', 'Still on it.'],
      below: [],
    })
    expect(unsavedLines(['One sec.'], [''])).toEqual({ above: ['One sec.'], below: [] })
  })

  it('never puts a greeting above an answer from before the call', () => {
    expect(unsavedLines(['Hi there.'], [])).toEqual({ above: [], below: ['Hi there.'] })
  })

  it('drops what the chat has, and a line not begun yet', () => {
    expect(unsavedLines(['One sec.', ''], ['One sec.', said])).toEqual({ above: [], below: [] })
  })

  it("doesn't take a second line for the first one saved", () => {
    expect(unsavedLines(['One sec.', said, 'One sec.'], ['One sec.', said])).toEqual({
      above: [],
      below: ['One sec.'],
    })
  })

  it('never brings back a saved line, however long the call has been', () => {
    const earlier = Array.from({ length: 30 }, (_, i) => `Line number ${i}`)
    expect(unsavedLines(earlier, earlier)).toEqual({ above: [], below: [] })
  })

  it('knows an answer by its opening when what was spoken is a cleaned copy', () => {
    const written = 'Your **balance** is about two thousand.\n\n| a | b |\n| 1 | 2 |'
    expect(unsavedLines([`${said} Rows left out here.`], [written])).toEqual({
      above: [],
      below: [],
    })
  })
})

describe('the full screen caption', () => {
  it("doesn't bring your words back once his line has come and gone", () => {
    call.heard = 'what is my balance'
    call.heardDone = true
    call.spoken = 'One sec'
    const { container, rerender } = render(<VoiceCallCaption />)
    expect(container.textContent).toBe('One sec')
    call.spoken = ''
    rerender(<VoiceCallCaption />)
    expect(container.textContent).toBe('')
  })

  it('draws nothing while nobody is talking', () => {
    const { container } = render(<VoiceCallCaption />)
    expect(container.firstChild).toBeNull()
  })

  it('hands over to Lucius once his line begins', () => {
    call.heard = 'how much did I spend'
    call.heardDone = true
    call.heardShown = true
    const { container, rerender } = render(<VoiceCallCaption />)
    const caption = () => container.querySelector('.lx-callcaption')
    expect(caption()?.textContent).toBe('how much did I spend')
    call.heardShown = false
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

  it('shows the answer when the voice talks and no words have come', () => {
    chatSessions.apply('s1', (slice) => ({
      ...slice,
      messages: [{ id: 'a1', role: 'assistant', text: 'About two thousand.', ts: 0 } as never],
    }))
    Object.assign(call, { luciusSpeaking: true, spokenLines: [] })
    const { container } = render(<VoiceCallCaption />)
    expect(container.textContent).toBe('About two thousand.')
    expect(container.querySelector('[data-who]')?.getAttribute('data-who')).toBe('lucius')
    cleanup()
    Object.assign(call, { luciusSpeaking: false })
    expect(render(<VoiceCallCaption />).container.firstChild).toBeNull()
    chatSessions.apply('s1', (slice) => ({ ...slice, messages: [] }))
  })

  it('waits for an answer still being written before showing it', () => {
    chatSessions.apply('s1', (slice) => ({
      ...slice,
      messages: [
        { id: 'a1', role: 'assistant', text: 'About two', ts: 0, streaming: true } as never,
      ],
    }))
    Object.assign(call, { luciusSpeaking: true, spokenLines: [] })
    expect(render(<VoiceCallCaption />).container.firstChild).toBeNull()
    chatSessions.apply('s1', (slice) => ({ ...slice, messages: [] }))
  })

  it('does not show the same answer for the next line said without words', () => {
    chatSessions.apply('s1', (slice) => ({
      ...slice,
      messages: [{ id: 'a1', role: 'assistant', text: 'About two thousand.', ts: 0 } as never],
    }))
    Object.assign(call, { luciusSpeaking: true, spokenLines: [] })
    const { container, rerender } = render(<VoiceCallCaption />)
    expect(container.textContent).toBe('About two thousand.')
    Object.assign(call, { luciusSpeaking: false })
    rerender(<VoiceCallCaption />)
    Object.assign(call, { luciusSpeaking: true })
    rerender(<VoiceCallCaption />)
    expect(container.textContent).toBe('')
    chatSessions.apply('s1', (slice) => ({ ...slice, messages: [] }))
  })

  it('shows nothing of yours the store has hidden, even with no line of his', () => {
    call.heard = 'how much did I spend'
    call.heardDone = true
    const { container } = render(<VoiceCallCaption />)
    expect(container.firstChild).toBeNull()
  })

  it('follows Lucius, and you when you talk over him', () => {
    call.spoken = 'Your balance'
    const { container, rerender } = render(<VoiceCallCaption />)
    const caption = () => container.querySelector('.lx-callcaption')
    expect(caption()?.getAttribute('data-who')).toBe('lucius')
    expect(caption()?.textContent).toBe('Your balance')
    call.heard = 'wait'
    call.heardShown = true
    rerender(<VoiceCallCaption />)
    expect(caption()?.getAttribute('data-who')).toBe('you')
    expect(caption()?.textContent).toBe('wait')
  })
})

describe('an answer spoken with no words arriving', () => {
  it('is his newest answer, until any of it has been seen', () => {
    expect(unheardAnswer([], ['First.', 'Second.'])).toBe('Second.')
    expect(unheardAnswer(['Second'], ['First.', 'Second.'])).toBe('')
    expect(unheardAnswer(['One sec'], ['First.', 'Second.'])).toBe('Second.')
  })

  it('is nothing with no answer to show', () => {
    expect(unheardAnswer([], [])).toBe('')
    expect(unheardAnswer([], ['First.', ''])).toBe('')
  })
})
