// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { act, render } from '@testing-library/react'
import { fireEvent } from '@testing-library/dom'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'
import { LuciusAskButton } from './AskLucius'
import type { MentionRef } from '../../types/mentions'

// A spy component that surfaces the store's pendingMentions for assertion.
function Spy({ onState }: { onState: (m: MentionRef[]) => void }) {
  const { pendingMentions } = useChatComposer()
  onState(pendingMentions)
  return null
}

describe('LuciusAskButton (the Lucius-mark affordance)', () => {
  it('renders a real, labeled <button>', () => {
    const { container } = render(
      <ChatComposerProvider>
        <LuciusAskButton
          type="holding"
          refId="pos_1"
          label="AAPL holding"
          className="hold-ask-btn"
        />
      </ChatComposerProvider>,
    )
    const btn = container.querySelector('button')
    expect(btn).toBeTruthy()
    expect(btn!.tagName).toBe('BUTTON')
    expect(btn!.getAttribute('type')).toBe('button')
    expect(btn!.getAttribute('aria-label')).toBe('Ask Lucius about AAPL holding')
    expect(btn!.querySelector('svg')).not.toBeNull()
  })

  it('dispatches the correct MentionRef into the composer store on click', () => {
    let staged: MentionRef[] = []
    const { container } = render(
      <ChatComposerProvider>
        <Spy onState={(m) => (staged = m)} />
        <LuciusAskButton type="net_worth" refId="" label="Net worth" className="sumcard-ask-btn" />
      </ChatComposerProvider>,
    )
    const btn = container.querySelector('button') as HTMLButtonElement
    act(() => {
      fireEvent.click(btn)
    })
    expect(staged).toEqual([{ type: 'net_worth', ref: '', label: 'Net worth' }])
  })
})
