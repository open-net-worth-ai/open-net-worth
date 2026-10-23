// ScreenCommandChip — "View on Outlook →" action chip for a PARKED screen command
// (run_screen_command status:"parked"; block re-derived from the persisted tool pair on
// reload). Click RE-DISPATCHES the command onto the bus (sticky — it applies when the
// target screen registers) and navigates, so the chip works even after a reload with no
// SSE in sight. Routes resolve from this STATIC map keyed by the catalog's symbolic
// route — never a model-supplied URL.
import { useNavigate } from '@tanstack/react-router'
import { dispatchScreenCommand } from '../../lucius/bus'
import type { ScreenCommandInvocation } from '../../lucius/bus'
import { ArrowRight } from '../../ui/icons'

// `to` must be the URL slug (router.tsx), NOT the tab id — the legacy alias routes
// (e.g. /transactions) redirect WITHOUT forwarding search params, which would drop
// the ?sub= deep-link and strand the parked command (review finding, 2026-07-11).
const ROUTES: Record<string, { label: string; to: string; search?: Record<string, string> }> = {
  'plan-outlook': { label: 'View on Outlook', to: '/plan', search: { sub: 'outlook' } },
  automations: { label: 'Review automation', to: '/automations' },
}

export function ScreenCommandChip({
  command,
  route,
}: {
  command: ScreenCommandInvocation
  route?: string
}) {
  const navigate = useNavigate()
  const target = ROUTES[route ?? '']
  if (!target) return null
  return (
    <button
      type="button"
      className="screen-cmd-chip"
      onClick={() => {
        dispatchScreenCommand(command)
        void navigate({
          to: target.to,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          search: ((prev: any) => ({ ...prev, ...target.search })) as any,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any)
      }}
    >
      <span className="screen-cmd-chip-glyph" aria-hidden="true">
        ✦
      </span>
      {target.label}
      <span className="screen-cmd-chip-arrow" aria-hidden="true">
        <ArrowRight size={12} />
      </span>
    </button>
  )
}
