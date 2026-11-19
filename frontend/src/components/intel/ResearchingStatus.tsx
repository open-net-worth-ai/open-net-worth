import { LuciusMark } from '../../ui/LuciusMark'

// "ready" means the genuinely time-sensitive content (key_issues/portfolio_impact — "Lucius's
// take") has landed — AssetDetailView hides the refreshing banner there instead of waiting for
// the whole backend refresh (including background company-profile polish) to finish, so this
// label is a fallback that should rarely if ever actually render.
export type RefreshStage = 'searching' | 'analysing' | 'ready' | 'retrying'

// Real backend milestones, written by monitoring.asset_brief._do_refresh as each stage
// actually completes (a "searching -> analysing -> ready" caption backed by the literal store
// writes, not a fixed timer). Exported so other call sites can validate a stage value without
// duplicating the label set.
//
// "retrying" (owner ask 2026-08-31): stamped by analyst.trigger_on_demand_brief_refresh when a
// key_issues/portfolio_narrative pass fails but still has attempts left — a call that needs a
// retry can take several minutes total, so this tells a user watching the page real work is
// still happening instead of the badge silently reverting to a generic fallback.
// eslint-disable-next-line react-refresh/only-export-components -- shared label lookup, not a component
export const STAGE_LABELS: Record<RefreshStage, string> = {
  searching: 'Searching the web for news',
  analysing: 'Analysing key developments',
  ready: 'Wrapping up',
  retrying: 'Retrying — this one needs another pass',
}

const DEFAULT_LABEL = 'Refreshing'

interface Props {
  /** The asset detail response's `refresh_stage` — undefined/null before the first poll
   *  lands (or on a backend that hasn't set it yet), in which case a neutral, non-cycling
   *  caption is shown instead of guessing a phase that may not match reality. */
  stage?: RefreshStage | null
}

/** "Lucius is researching…" caption shown in the news skeleton's header / refreshing
 *  banner — driven by the backend's real refresh_stage instead of a cosmetic cycling loop. */
export function ResearchingStatus({ stage }: Props) {
  const label = stage ? STAGE_LABELS[stage] : DEFAULT_LABEL
  return (
    // key={stage} re-mounts the span on each real transition so the fade-in animation replays.
    <span key={stage ?? 'unknown'} className="asd-research-status" role="status" aria-live="polite">
      {label} <LuciusMark size="1em" aria-hidden="true" />
    </span>
  )
}
