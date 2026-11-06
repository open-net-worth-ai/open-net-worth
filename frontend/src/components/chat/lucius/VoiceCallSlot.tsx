import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useFlag } from '../../../hooks/useFlag'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { VoiceCallHeard } from './VoiceCallHeard'
import { VoiceCallStage } from './VoiceCallStage'
import { useVoiceCall } from './voiceCall'

/** Inside a composer card: while a call is open its strip shows above the composer — or, on a
 *  phone, the full-screen call until it is folded down to the same strip. The composer stays
 *  under it: you can type or send a file mid-call, and Lucius reads the answer aloud (owner,
 *  2026-11-03). With the `voice_calls` flag off this is exactly the composer. */
export function VoiceCallSlot({
  busy,
  visible = true,
  children,
}: {
  busy: boolean
  /** False while the surface is collapsed or off-screen: no call view is mounted there (no
   *  focusable Mute/End behind a hidden card, no second level loop). */
  visible?: boolean
  children: ReactNode
}) {
  const on = useFlag('voice_calls')
  const call = useVoiceCall()
  const mobile = useIsMobile()
  if (on !== true) return <>{children}</>
  const shown = visible && call.phase !== 'idle'
  const fullScreen = shown && mobile && call.view === 'stage'
  return (
    <>
      {shown && !fullScreen && <VoiceCallHeard variant="card" />}
      {shown && !fullScreen && <VoiceCallStage variant="card" busy={busy} expandable={mobile} />}
      {fullScreen && createPortal(<VoiceCallStage variant="screen" busy={busy} />, document.body)}
      {children}
    </>
  )
}
