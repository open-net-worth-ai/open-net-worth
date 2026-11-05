// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OTHER_VOICE } from './voiceOptions'
import { VoicePicker } from './VoicePicker'

const FLASH = 'inworld/inworld-tts-2-flash'

function Form({ start = '', onChange }: { start?: string; onChange?: (id: string) => void }) {
  const [voiceId, setVoiceId] = useState(start)
  return (
    <VoicePicker
      model={FLASH}
      voiceId={voiceId}
      onChange={(id) => {
        setVoiceId(id)
        onChange?.(id)
      }}
    />
  )
}

const menu = () => screen.getByLabelText('Voice') as HTMLSelectElement
const idBox = () => screen.queryByLabelText('Voice id') as HTMLInputElement | null

describe('VoicePicker', () => {
  afterEach(cleanup)

  it('saves a named voice by its id, with no box to fill in', () => {
    const onChange = vi.fn()
    render(<Form onChange={onChange} />)
    expect(menu().value).toBe('')
    fireEvent.change(menu(), { target: { value: 'Edward' } })
    expect(onChange).toHaveBeenLastCalledWith('Edward')
    expect(menu().value).toBe('Edward')
    expect(idBox()).toBeNull()
  })

  it('opens the box for another voice, and stays open while it is blank', () => {
    const onChange = vi.fn()
    render(<Form start="Edward" onChange={onChange} />)
    fireEvent.change(menu(), { target: { value: OTHER_VOICE } })
    expect(onChange).toHaveBeenLastCalledWith('')
    expect(menu().value).toBe(OTHER_VOICE)
    expect(idBox()?.value).toBe('')
    fireEvent.change(idBox()!, { target: { value: 'Someone' } })
    expect(onChange).toHaveBeenLastCalledWith('Someone')
    expect(screen.getByRole('link', { name: "Inworld's voices" })).toBeTruthy()
  })

  it('shows a saved id we do not name in the box, not as the default', () => {
    render(<Form start="Someone" />)
    expect(menu().value).toBe(OTHER_VOICE)
    expect(idBox()?.value).toBe('Someone')
  })

  it('closes the box when a listed voice is picked again', () => {
    render(<Form start="Someone" />)
    fireEvent.change(menu(), { target: { value: '' } })
    expect(menu().value).toBe('')
    expect(idBox()).toBeNull()
  })
})
