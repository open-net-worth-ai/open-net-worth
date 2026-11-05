import { useEffect, useRef } from 'react'
import { authFetch } from '../../../api/client'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { onScreenRequest, type ScreenRequest } from '../../../lucius/bus'
import { captureAndSerialize } from '../../../lucius/screenContract'
import { decideScreenReply } from '../../../lucius/screenRead'
import { useVisibleIdsValue } from '../../../lucius/visibleIds'
import { useLuciusChat } from '../../../state/luciusChat'
import { useScreenSnapshot } from '../../../state/screenSnapshot'
import { useVoiceCall } from './voiceCall'

// The shell's own label for the page: Lucius opens in place of a screen on routes other than
// /lucius too (the home route, a route with nothing else to show), so the URL can't say.
function isLuciusPage(): boolean {
  return document.querySelector('[data-ctx-screen]')?.getAttribute('data-ctx-screen') === 'Lucius'
}

/** Answers read_screen: when Lucius asks what is on screen, an app showing that conversation
 *  captures the page the same way a typed message does. Mounted once, renders nothing. */
export function ScreenReadResponder() {
  const { activeId } = useLuciusChat()
  const { enabled, pinnedHandleIds } = useScreenSnapshot()
  const { visibleIds } = useVisibleIdsValue()
  const call = useVoiceCall()
  const mobile = useIsMobile()

  const latest = useRef({ activeId, enabled, pinnedHandleIds, visibleIds, call, mobile })
  useEffect(() => {
    latest.current = { activeId, enabled, pinnedHandleIds, visibleIds, call, mobile }
  })

  useEffect(() => {
    const answer = (request: ScreenRequest) => {
      const now = latest.current
      const onCall = now.call.phase !== 'idle' && now.call.sessionId === request.session_id
      if (!onCall && now.activeId !== request.session_id) return
      const reply = decideScreenReply({
        sharing: now.enabled,
        onCall,
        focused: document.hasFocus(),
        visible: document.visibilityState === 'visible',
        covered:
          isLuciusPage() || (now.mobile && now.call.phase !== 'idle' && now.call.view === 'stage'),
      })
      if (!reply) return
      const screen =
        reply.status === 'ok'
          ? captureAndSerialize('lucius-read', now.visibleIds, now.pinnedHandleIds)
          : null
      void authFetch(`/api/screen-reads/${encodeURIComponent(request.request_id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...reply, screen }),
      }).catch(() => {
        /* the tool reports no answer as no screen; there is nothing to retry */
      })
    }
    return onScreenRequest(answer)
  }, [])

  return null
}
