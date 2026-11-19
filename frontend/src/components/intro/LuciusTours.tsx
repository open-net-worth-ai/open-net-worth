// Lucius's guides: no popup, just the spotlight tour over his own controls. The home tour
// runs the first time the empty Lucius home is shown; the screen tip the first time his
// screen-awareness bar appears beside another screen. Each runs once per user.
import { useEffect, useState } from 'react'
import { onGuideRequest } from '../../state/guideRequests'
import type { GuideRequest } from '../../state/guideRequests'
import { PageTour } from './PageTour'
import { findTarget, guidedThisVisit, guidesHeld, inFirstRun, modalOpen } from './tourTarget'
import type { TourStep } from './PageTour'
import { useSeenIntro } from './useSeenIntro'

const LUCIUS_HOME_TOUR: TourStep[] = [
  {
    target: 'lucius-mention',
    title: 'Point at something',
    body: 'Type @ to bring in an account, transaction or holding, then say what to do with it.',
  },
  {
    target: 'lucius-skills',
    title: 'Run a skill',
    body: 'Type / for saved playbooks, like a monthly report.',
  },
  {
    target: 'lucius-documents',
    title: 'Documents',
    body: 'Your files and saved reports in one place. Mark one Private and Lucius never reads it.',
  },
]

const LUCIUS_SCREEN_TIP: TourStep[] = [
  {
    target: 'lucius-screen',
    title: 'Lucius sees this screen',
    body: 'He knows what you are looking at and can act on it for you. Switch it off here any time.',
  },
]

// Pointed at a real transaction row on screen, never at the composer: a Dashboard widget is not
// draggable, so a general "drag anything" claim misled there. Dropping works on the open panel
// and on the collapsed "Ask Lucius" pill alike (both are drop zones; the pill opens itself).
const LUCIUS_DRAG_TIP: TourStep[] = [
  {
    // Never the account's Starting Balance row: it can't be recategorized, split or edited.
    target: '[data-entity-id^="transaction:"][draggable="true"]:not([data-opening])',
    title: 'Hand Lucius any transaction',
    body: 'Drag it onto Lucius. He can explain it, recategorize it, split it or fix it.',
  },
]

// One tour at a time: two mounted at once (the home tour and a screen tip) must not both run.
let tourRunning = false

/** Runs `steps` once per user, the first time its first control is on screen and nothing
 *  else is: no modal up, no other tour running, and no other guide yet this visit (one per
 *  visit — see `guidedThisVisit`). Steps whose control is not visible are skipped (a narrow
 *  layout hides some). `requires` is a further condition, checked with the rest. */
function OnceTour({
  seenKey,
  steps,
  requires,
}: {
  seenKey: string
  steps: TourStep[]
  /** A further condition to wait for, checked with the rest. */
  requires?: () => boolean
}) {
  const { unseen, markSeen } = useSeenIntro(seenKey)
  const [shown, setShown] = useState<TourStep[] | null>(null)
  useEffect(() => {
    if (!unseen || shown) return
    const timer = setInterval(() => {
      if (tourRunning || inFirstRun() || modalOpen() || !findTarget(steps[0]!.target)) return
      if (guidedThisVisit() || guidesHeld()) return
      if (requires && !requires()) return
      clearInterval(timer)
      tourRunning = true
      setShown(steps.filter((s) => findTarget(s.target)))
    }, 700)
    return () => clearInterval(timer)
  }, [unseen, shown, steps, requires])
  useEffect(
    () => () => {
      if (shown) tourRunning = false
    },
    [shown],
  )
  if (!shown) return null
  return (
    <PageTour
      steps={shown}
      onClose={() => {
        tourRunning = false
        setShown(null)
        markSeen()
      }}
    />
  )
}

export function LuciusHomeTour() {
  return <OnceTour seenKey="lucius" steps={LUCIUS_HOME_TOUR} />
}

// The screen tip belongs to Lucius open BESIDE a screen (the side panel). The floating box that
// opens when the Ask Lucius pill is hovered sat on top of the tip's card and closed again on
// mouse-out, leaving the tip pointing at nothing — so it never starts there.
const besidePanelOnly = () => !document.querySelector('.lx-dock:not(.lx-collapsed)')

export function LuciusScreenTip() {
  return <OnceTour seenKey="lucius-screen" steps={LUCIUS_SCREEN_TIP} requires={besidePanelOnly} />
}

// A phone has nowhere to drop: Lucius is his own tab there, not a panel or pill beside the page.
const canDropOnLucius = () => !!document.querySelector('[data-dropzone]')

export function LuciusDragTip() {
  return <OnceTour seenKey="lucius-drag" steps={LUCIUS_DRAG_TIP} requires={canDropOnLucius} />
}

// Shown after Lucius's first-ever change, on the Restore of the message that caused it. Like
// every guide it is the only one in its visit: during onboarding the paperclip guide usually
// took this visit, so this one waits for the next.
const LUCIUS_RESTORE_TIP: TourStep[] = [
  {
    target: 'lucius-restore',
    title: 'Undo anything Lucius changes',
    body: 'Restore puts your data back as it was before this message. The restore can be undone too.',
  },
]

export function LuciusRestoreTip() {
  return <OnceTour seenKey="lucius-restore" steps={LUCIUS_RESTORE_TIP} />
}

const LUCIUS_ATTACH_TIP: TourStep[] = [
  {
    target: 'lucius-attach',
    title: 'Send it here',
    body: 'Attach a statement, screenshot or photo, or drag the file in. Lucius reads it for you.',
  },
]

/** Guides Lucius asks for himself (the show_guide tool): the paperclip at the moment he asks
 *  for a file during onboarding, his controls tour once you say yes to one. Each still shows
 *  once per person, so a request for one already seen changes nothing. */
export function RequestedGuides() {
  const [request, setRequest] = useState<GuideRequest | null>(null)
  useEffect(() => onGuideRequest(setRequest), [])
  if (request === 'attach')
    return <OnceTour key="attach" seenKey="lucius-attach" steps={LUCIUS_ATTACH_TIP} />
  if (request === 'lucius_tour')
    return <OnceTour key="tour" seenKey="lucius" steps={LUCIUS_HOME_TOUR} />
  return null
}
