import { useEffect, useRef } from 'react'
import { Mic } from 'lucide-react'
import { useDictation } from './useDictation'
import { useVoiceCall } from './voiceCall'

// Dictate into the message box (owner, 2026-11-03): beside Send, your words written in as you say
// them — by macOS's own dictation in the Mac desktop app, by the browser's speech recognition
// elsewhere (`useDictation`). Hidden where neither can, and during a voice call (the call already
// has the microphone, and hears you).

const LABEL = {
  idle: 'Dictate',
  listening: 'Stop dictating',
  blocked:
    "Dictation can't use the microphone in this browser. Allow it for this site, or open the app in Chrome or Safari.",
  'no-service': "This browser can't turn speech into text. Dictate in Chrome, Edge or Safari.",
  'no-mic': 'Dictation found no microphone. Connect one, then try again.',
  'no-speech': "Dictation didn't hear anything. Tap the mic and speak again.",
  'no-start': "Dictation didn't start. Tap the mic again.",
  other: 'Dictation stopped. Tap the mic to try again.',
  system:
    "macOS dictation didn't start. Turn on Dictation in System Settings → Keyboard, then try again.",
} as const

export function DictateButton({
  onText,
  focusBox,
  empty,
}: {
  onText: (text: string, final: boolean) => void
  /** The caret into the message box: the Mac's own dictation types wherever it is. */
  focusBox: () => void
  /** The message box is empty: once it empties under dictation (sent, or cleared), it stops,
   *  so the next message starts fresh with a tap of the mic (owner, 2026-11-03). */
  empty: boolean
}) {
  const dictation = useDictation(onText, focusBox)
  const inCall = useVoiceCall().phase !== 'idle'
  const wasEmpty = useRef(empty)
  const { listening, cancel, stop } = dictation
  useEffect(() => {
    if (listening && empty && !wasEmpty.current) cancel()
    wasEmpty.current = empty
  }, [empty, listening, cancel])
  // A call takes the microphone and hears you itself: dictation still running would type what
  // you say to Lucius into the box (owner, 2026-11-03).
  useEffect(() => {
    if (inCall && listening) stop()
  }, [inCall, listening, stop])
  if (!dictation.supported || inCall) return null
  const problemText = (problem: NonNullable<typeof dictation.problem>) =>
    problem === 'other' && dictation.problemCode
      ? `Dictation stopped (${dictation.problemCode}). Tap the mic to try again.`
      : LABEL[problem]
  const label = dictation.listening
    ? LABEL.listening
    : dictation.problem
      ? problemText(dictation.problem)
      : LABEL.idle
  return (
    <span className="lx-dictate-wrap">
      {dictation.noticed && dictation.problem && (
        <span className="lx-dictate-tip" role="status">
          {problemText(dictation.problem)}
        </span>
      )}
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
    </span>
  )
}
