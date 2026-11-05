import { useNavigate } from '@tanstack/react-router'
import { Phone } from 'lucide-react'
import { authFetch } from '../../../api/client'
import { useFlag } from '../../../hooks/useFlag'
import { dispatchScreenCommand } from '../../../lucius/bus'
import { CONNECTORS_OPEN_SETUP_SPEC } from '../../../lucius/screenCommands'
import { useQueryCache } from '../../../state/queryCache'
import { dismissProblem, startCall, useVoiceCall } from './voiceCall'

// The Call button (`voice_calls` flag). In the composer's Send slot once calls are set up: typing
// and calling are alternative ways in, so they share one slot — Call while the box is empty, Send
// once you type. Until then the slot stays Send, and the dock's pill offers the setup.
// It never errors inline (owner, 2026-11-03): not set up → it opens Voice calls in Connectors; set up but
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

/** Whether the composer's Send slot may hold Call: a button that draws nothing (flag off) or only
 *  offers setup left a new owner with no Send at all, or a faded phone in its place (owner,
 *  2026-11-05). */
export function useCallInSendSlot(): boolean {
  const readiness = useVoiceReadiness()
  const call = useVoiceCall()
  return readiness?.ready === true && call.phase === 'idle'
}

/** Takes the owner to Voice calls in Connectors. The tab may not be mounted yet: the command waits
 *  for it. */
export function useOpenVoiceSetup(): () => void {
  const navigate = useNavigate()
  return () => {
    dismissProblem()
    dispatchScreenCommand({ name: CONNECTORS_OPEN_SETUP_SPEC.name, args: { id: 'voice' } })
    void navigate({ to: '/connectors' } as never)
  }
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
      className={'lx-call-btn' + (tone === 'pill' ? ' lx-call-btn--pill' : '')}
      aria-label={label}
      title={label}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => (needsSetup ? openSetup() : void startCall(sessionId))}
    >
      <Phone size={16} fill="currentColor" strokeWidth={1.5} />
    </button>
  )
}
