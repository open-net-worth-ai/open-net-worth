import type { CallSignal } from './voiceCallStatus'

/** Signal bars, the weaker of the two connections. Rising bars, not a figure in ms: normal
 *  variation in a number looks like a problem. */
export function VoiceCallSignal({ signal }: { signal: CallSignal }) {
  return (
    <span
      className="lx-callstage-signal"
      data-bars={signal.bars}
      role="img"
      aria-label={`Connection quality: ${signal.label}`}
      title={`Connection: ${signal.label}`}
    >
      <span />
      <span />
      <span />
      <span />
    </span>
  )
}
