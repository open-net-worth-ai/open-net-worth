// The setup nudge — a card floating over the page (mounted once by RouterShell, so it follows the
// owner across screens): beside the nav rail on desktop, above the tab bar on a phone. Which case
// shows is decided in setupNudges.ts, when in a visit in useGuideTurn.ts; this file reads the
// facts they need and draws the one card.
import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { listAutomations, type AutomationListResponse } from '../../api/automations'
import { fetchJson } from '../../hooks/serverState/fetchJson'
import { PROFILE_KEY } from '../../hooks/serverState/keys'
import { useSetupStatus } from '../../hooks/serverState/useSetupStatus'
import { useProfile, type ProfileData } from '../../hooks/serverState/useProfile'
import { setCachedQueryData, useQueryCache } from '../../state/queryCache'
import { X } from '../../ui/icons'
import { NudgePreview } from './NudgePreview'
import { useGuideTurn } from './useGuideTurn'
import { useConnectors } from '../tabs/connectors/useConnectors'
import {
  eligibleNudges,
  pickNudge,
  withTouch,
  type Nudge,
  type NudgeAction,
  type NudgeCase,
} from './setupNudges'

const AUTOMATIONS_KEY = '/api/automations'

/** The card to show, or null while any of its inputs is still unknown — an unread profile
 *  could be hiding a dismissal, so nothing shows rather than a card that may be a repeat. */
function useSetupNudge(): { nudge: Nudge | null; touch: (c: NudgeCase) => void } {
  const profile = useProfile()
  const setup = useSetupStatus()
  const connectors = useConnectors()
  const automations = useQueryCache<AutomationListResponse>(AUTOMATIONS_KEY, listAutomations, {
    staleTime: 60_000,
  })
  // Hides the card the moment it is touched, before the profile write lands.
  const [touched, setTouched] = useState<NudgeCase | null>(null)

  const nudge = useMemo(() => {
    if (!profile.data || !setup.data || !connectors.data || !automations.data) return null
    if (profile.data.is_demo || setup.data.is_fresh) return null
    const seen = (profile.data.seen_intros as string[] | undefined) ?? []
    const eligible = eligibleNudges(connectors.connectors, automations.data.automations)
    const doneAt = setup.data.onboarding_completed_at ?? null
    const pick = pickNudge(eligible, seen, new Date(), doneAt)
    return pick && pick.case !== touched ? pick : null
  }, [profile.data, setup.data, connectors.data, connectors.connectors, automations.data, touched])

  const touch = useCallback((c: NudgeCase) => {
    setTouched(c)
    // Read the list fresh: the cached copy may predate a key another device just added.
    void fetchJson<ProfileData>(PROFILE_KEY)
      .then((fresh) =>
        fetchJson<{ profile: ProfileData }>(PROFILE_KEY, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            seen_intros: withTouch((fresh.seen_intros as string[]) ?? [], c, new Date()),
          }),
        }),
      )
      .then((saved) => setCachedQueryData<ProfileData>(PROFILE_KEY, saved.profile))
      .catch(() => {})
  }, [])

  return { nudge, touch }
}

/** The card itself, without its data — what the shell shows, and what a test can render
 *  for any case. The × lives inside the picture so it stays pinned to the picture's corner. */
export function NudgeCardView({
  nudge,
  left,
  mobile,
  onDismiss,
  onGo,
}: {
  nudge: Nudge
  left?: number
  /** Full width above the phone's tab bar, with a shorter picture. */
  mobile?: boolean
  onDismiss: () => void
  onGo: (action: NudgeAction) => void
}) {
  return (
    <section
      className={`setup-nudge${mobile ? ' setup-nudge--mobile' : ''}`}
      style={left == null ? undefined : { left }}
      aria-label={nudge.title}
    >
      <NudgePreview c={nudge.case}>
        <button
          type="button"
          className="setup-nudge-x"
          onClick={onDismiss}
          aria-label="Not now"
          title="Not now"
        >
          <X size={16} />
        </button>
      </NudgePreview>
      <div className="setup-nudge-t">{nudge.title}</div>
      <p className="setup-nudge-b">{nudge.body}</p>
      <div className="setup-nudge-acts">
        <button type="button" className="setup-nudge-btn" onClick={() => onGo(nudge.primary)}>
          {nudge.primary.label}
        </button>
        {nudge.secondary && (
          <button type="button" className="setup-nudge-btn" onClick={() => onGo(nudge.secondary!)}>
            {nudge.secondary.label}
          </button>
        )}
      </div>
    </section>
  )
}

export function SetupNudgeCard({ left, mobile }: { left?: number; mobile?: boolean }) {
  const { nudge, touch } = useSetupNudge()
  const navigate = useNavigate()
  const shown = useGuideTurn(nudge !== null)
  if (!nudge || !shown) return null
  const go = (action: NudgeAction) => {
    touch(nudge.case)
    const url = new URL(action.to, window.location.origin)
    void navigate({
      to: url.pathname,
      search: Object.fromEntries(url.searchParams),
    } as never)
  }
  return (
    <NudgeCardView
      nudge={nudge}
      left={left}
      mobile={mobile}
      onDismiss={() => touch(nudge.case)}
      onGo={go}
    />
  )
}
