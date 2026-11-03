import { Phone } from 'lucide-react'
import { authFetch } from '../../../api/client'
import { useFlag } from '../../../hooks/useFlag'
import { useQueryCache } from '../../../state/queryCache'
import { openVoiceSetup, startCall, useVoiceCall } from './voiceCall'

// The Call button in the composer's Send slot (`voice_calls` flag): typing and calling are
// alternative ways in, so they share one slot — Call while the box is empty, Send once you type.
// It never errors inline (owner, 2026-11-03): not set up → it opens Voice calls setup in a dialog; set up but
// not working → the reason appears in the call notice above the composer (VoiceCallNotice).

interface VoiceReadiness {
  ready: boolean
  worker: { flag_on: boolean; libraries_installed: boolean; running: boolean; error: string }
}

export const VOICE_CONFIG_KEY = 'voice-config'

async function fetchVoiceConfig(): Promise<VoiceReadiness> {
  const r = await authFetch('/api/voice/config')
  if (!r.ok) throw new Error(`voice config ${r.status}`)
  return r.json()
}

export function useVoiceReadiness(): VoiceReadiness | undefined {
  const on = useFlag('voice_calls')
  const { data } = useQueryCache(VOICE_CONFIG_KEY, fetchVoiceConfig, { enabled: on === true })
  return on === true ? data : undefined
}

/** Opens Voice calls setup in a dialog over the current screen (no navigating away). */
export function useOpenVoiceSetup(): () => void {
  return openVoiceSetup
}

export function VoiceCallButton({
  sessionId,
  tone = 'composer',
}: {
  sessionId: string
  /** `pill`: the dock's collapsed pill, where a call starts without opening the chat. */
  tone?: 'composer' | 'pill'
}) {
  const on = useFlag('voice_calls')
  const readiness = useVoiceReadiness()
  const call = useVoiceCall()
  const openSetup = useOpenVoiceSetup()
  // Mid-call the slot is the composer's Send again: typing goes to Lucius, the call goes on.
  if (on !== true || call.phase !== 'idle') return null

  const needsSetup = !!readiness && !readiness.ready
  const label = needsSetup ? 'Set up voice calls' : 'Call Lucius'
  return (
    <button
      type="button"
      className={
        'lx-call-btn' +
        (tone === 'pill' ? ' lx-call-btn--pill' : '') +
        (needsSetup ? ' lx-call-btn--setup' : '')
      }
      aria-label={label}
      title={label}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => (needsSetup ? openSetup() : void startCall(sessionId))}
    >
      <Phone size={16} fill="currentColor" strokeWidth={1.5} />
    </button>
  )
}
