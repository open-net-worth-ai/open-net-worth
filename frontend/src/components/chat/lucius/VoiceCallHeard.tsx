import { useVoiceCall } from './voiceCall'

// Your words as the call hears them, so you know it's listening before any answer comes (owner,
// 2026-11-06). Not announced to a screen reader: it changes with every word, and the real message
// follows in the chat. `screen` sits above the mic; `card` above the strip, under the messages.
export function VoiceCallHeard({ variant }: { variant: 'screen' | 'card' }) {
  const { heard } = useVoiceCall()
  if (!heard) return null
  return (
    <div className={`lx-callheard lx-callheard--${variant}`} aria-hidden="true">
      <span>{heard}</span>
    </div>
  )
}
