import { useState } from 'react'
import { FormField, Select, type SelectProps, TextField } from '../../ui'
import { OTHER_VOICE, speakerChoice, speakerOptions, voiceLibrary } from './voiceOptions'
import { ChevronDown } from '../../../ui/icons'

/** A select with the model picker's own chevron beside it, so every field here reads as one kind
 *  of dropdown — the browser's chevron next to the picker's looked like two (owner, 2026-11-03). */
export function FieldSelect(props: SelectProps) {
  return (
    <div className="vc-select-wrap">
      <Select {...props} className="vc-select" />
      <ChevronDown size={14} className="vc-select-chev" aria-hidden="true" />
    </div>
  )
}

/** Who speaks on this model. The menu names what LiveKit's docs name; the box takes the rest of a
 *  provider's library. Render it with `key={model}`: a new model starts from its own menu. */
export function VoicePicker({
  model,
  voiceId,
  onChange,
}: {
  model: string
  voiceId: string
  onChange: (voiceId: string) => void
}) {
  // Choosing "another voice" clears the id, which alone would read as the default again.
  const [typing, setTyping] = useState(false)
  const choice = speakerChoice(model, voiceId, typing)
  const library = voiceLibrary(model)

  const pick = (value: string) => {
    setTyping(value === OTHER_VOICE)
    onChange(value === OTHER_VOICE ? '' : value)
  }

  return (
    <>
      <FormField label="Voice" htmlFor="conn-voice-voice">
        <FieldSelect
          id="conn-voice-voice"
          value={choice}
          options={speakerOptions(model)}
          onChange={(e) => pick(e.target.value)}
        />
      </FormField>
      {choice === OTHER_VOICE && (
        <FormField
          label="Voice id"
          htmlFor="conn-voice-voice-id"
          help={
            library ? (
              <>
                From{' '}
                <a href={library.url} target="_blank" rel="noreferrer">
                  {library.name}'s voices
                </a>
                . Blank uses the default voice.
              </>
            ) : (
              'A voice id from the provider. Blank uses the default voice.'
            )
          }
        >
          <TextField
            id="conn-voice-voice-id"
            value={voiceId}
            onChange={(e) => onChange(e.target.value)}
          />
        </FormField>
      )}
    </>
  )
}
