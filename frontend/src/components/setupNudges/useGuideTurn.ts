// Whether the setup nudge may be on screen now. It is a guide like a screen intro, a tour or a
// tip, so it keeps their one rule (`intro/tourTarget.ts`): ONE guide per visit, never over a
// dialog, never while a flow is mid-way or a visit came by a link. It also lets each screen's
// own intro or tour go first — they explain the screen you just opened; the nudge can wait.
import { useEffect, useReducer, useState } from 'react'
import { useRouterState } from '@tanstack/react-router'
import {
  guidedThisVisit,
  guidesHeld,
  inFirstRun,
  markGuided,
  modalOpen,
  useTourShowing,
} from '../intro/tourTarget'

/** How long a screen has to start its own intro or tour before the nudge may claim the visit. */
export const NUDGE_SETTLE_MS = 4000

// The claim outlives the card: on a phone it unmounts on the Lucius tab, and a remount that
// forgot it had already taken this visit's turn would wait behind its own claim for good.
let claimedThisVisit = false
/** Tests only: a new visit. */
export function resetNudgeClaimForTests(): void {
  claimedThisVisit = false
}

export function useGuideTurn(want: boolean): boolean {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const touring = useTourShowing()
  const [claimed, setClaimed] = useState(() => claimedThisVisit)
  // Until claimed, every new screen starts the wait again; then the visit is the nudge's.
  useEffect(() => {
    if (!want || claimed || touring) return
    const arrived = Date.now()
    const timer = setInterval(() => {
      if (Date.now() - arrived < NUDGE_SETTLE_MS) return
      if (inFirstRun() || modalOpen() || guidesHeld() || guidedThisVisit()) return
      markGuided()
      claimedThisVisit = true
      setClaimed(true)
    }, 700)
    return () => clearInterval(timer)
  }, [want, claimed, touring, pathname])
  // Once on screen it steps out of a dialog's way, so look again on a timer, as the intros do.
  const [, recheck] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    if (!want || !claimed) return
    const timer = setInterval(recheck, 700)
    return () => clearInterval(timer)
  }, [want, claimed])
  return want && claimed && !touring && !inFirstRun() && !modalOpen()
}
