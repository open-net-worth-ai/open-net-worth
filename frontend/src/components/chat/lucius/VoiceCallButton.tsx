import { useNavigate } from '@tanstack/react-router'
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

/** Our own, not an icon set's: four thick bars stay clear on the message box's small button, where
 *  Lucide's six thin ones got busy, and it reads as talking to Lucius (owner, 2026-11-05). */
function VoiceBars({ large }: { large: boolean }) {
  const size = large ? 22 : 15
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={large ? 2.4 : 3}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M4.5 10v4" />
      <path d="M9.5 6v12" />
      <path d="M14.5 3v18" />
      <path d="M19.5 8v8" />
    </svg>
  )
}

export function VoiceCallButton({
  sessionId,
  tone = 'composer',
}: {
  sessionId: string
  /** `pill`: the dock's collapsed pill, where a call starts without opening the chat. `fab`: a
   *  phone's round button, for the same reason. */
  tone?: 'composer' | 'pill' | 'fab'
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
      className={'lx-call-btn' + (tone === 'composer' ? '' : ` lx-call-btn--${tone}`)}
      aria-label={label}
      title={label}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => (needsSetup ? openSetup() : void startCall(sessionId))}
    >
      <VoiceBars large={tone === 'fab'} />
    </button>
  )
}
