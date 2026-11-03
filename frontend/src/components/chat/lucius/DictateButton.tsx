import { useEffect, useRef } from 'react'
import { Mic } from 'lucide-react'
import { useDictation } from './useDictation'
import { useVoiceCall } from './voiceCall'

// Dictate into the message box (owner, 2026-11-03): beside Send, the browser's own speech
// recognition writing your words in as you say them. Hidden where the browser can't, and during
// a voice call (the call already has the microphone, and hears you).

const LABEL = {
  idle: 'Dictate',
  listening: 'Stop dictating',
  blocked: "Dictation can't use the microphone. Allow it for this site, then try again.",
  unavailable: "Dictation isn't available right now. Try again, or type instead.",
} as const

export function DictateButton({
  onText,
  empty,
}: {
  onText: (text: string, final: boolean) => void
  /** The message box is empty: once it empties under dictation (sent, or cleared), it stops,
   *  so the next message starts fresh with a tap of the mic (owner, 2026-11-03). */
  empty: boolean
}) {
  const dictation = useDictation(onText)
  const inCall = useVoiceCall().phase !== 'idle'
  const wasEmpty = useRef(empty)
  const { listening, cancel } = dictation
  useEffect(() => {
    if (listening && empty && !wasEmpty.current) cancel()
    wasEmpty.current = empty
  }, [empty, listening, cancel])
  if (!dictation.supported || inCall) return null
  const label = dictation.listening ? LABEL.listening : LABEL[dictation.problem ?? 'idle']
  return (
    <button
      type="button"
      className="lx-dictate"
      aria-label={label}
      title={label}
      aria-pressed={dictation.listening}
      data-problem={dictation.problem || undefined}
      onMouseDown={(e) => e.preventDefault()}
      onClick={dictation.toggle}
    >
      <Mic size={16} />
    </button>
  )
}
