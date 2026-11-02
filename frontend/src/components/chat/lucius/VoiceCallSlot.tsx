import type { ReactNode } from 'react'
import { useFlag } from '../../../hooks/useFlag'
import { VoiceCallBar } from './VoiceCallBar'
import { useVoiceCall } from './voiceCall'

/** Inside a composer card: while a call is open the call bar shows and the composer stays
 *  MOUNTED but hidden (one composer per surface — its refs and your draft survive the call).
 *  With the `voice_calls` flag off this is exactly the composer, nothing added. */
export function VoiceCallSlot({ busy, children }: { busy: boolean; children: ReactNode }) {
  const on = useFlag('voice_calls')
  const call = useVoiceCall()
  if (on !== true) return <>{children}</>
  const inCall = call.phase !== 'idle'
  return (
    <>
      {inCall && <VoiceCallBar busy={busy} />}
      <div hidden={inCall}>{children}</div>
    </>
  )
}
