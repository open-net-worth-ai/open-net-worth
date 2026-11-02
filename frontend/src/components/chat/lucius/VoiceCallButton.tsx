import { Phone } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { authFetch } from '../../../api/client'
import { useFlag } from '../../../hooks/useFlag'
import { dispatchScreenCommand } from '../../../lucius/bus'
import { useQueryCache } from '../../../state/queryCache'
import { startCall, useVoiceCall } from './voiceCall'

// The Call button in the composer's Send slot (`voice_calls` flag): typing and calling are
// alternative ways in, so they share one slot — Call while the box is empty, Send once you type.
// It never errors inline (owner, 2026-11-03): not set up → it opens Voice calls setup; set up but
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

/** Opens Connectors → Voice calls' setup form — the same two steps as a setup chip. */
export function useOpenVoiceSetup(): () => void {
  const navigate = useNavigate()
  return () => {
    dispatchScreenCommand({ name: 'connectors.open_setup', args: { id: 'voice' } } as never)
    void navigate({ to: '/connectors' })
  }
}

export function VoiceCallButton({ sessionId }: { sessionId: string }) {
  const on = useFlag('voice_calls')
  const readiness = useVoiceReadiness()
  const call = useVoiceCall()
  const openSetup = useOpenVoiceSetup()
  if (on !== true) return null

  const needsSetup = !!readiness && !readiness.ready
  const label = needsSetup ? 'Set up voice calls' : 'Call Lucius'
  return (
    <button
      type="button"
      className={'lx-call-btn' + (needsSetup ? ' lx-call-btn--setup' : '')}
      aria-label={label}
      title={label}
      disabled={call.phase === 'connecting'}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => (needsSetup ? openSetup() : void startCall(sessionId))}
    >
      <Phone size={16} />
    </button>
  )
}
