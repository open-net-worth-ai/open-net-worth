// Settings → Developer → Experimental. GENERATED from GET /api/feature-flags: one row per
// declared flag, never hand-written UI per flag (docs/FEATURE_FLAGS.md). A flag that is off
// runs exactly the old code, so switching one back off is always safe.
import { authFetch } from '../../../api/client'
import { useFeatureFlags } from '../../../hooks/useFlag'
import type { FeatureFlag } from '../../../hooks/useFlag'
import { Badge, Btn, Empty, Panel, Toggle, row, useSectionBusy } from './ds'
import type { SectionProps } from './ds'

function title(name: string): string {
  const words = name.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

const onOff = (v: boolean) => (v ? 'on' : 'off')

function stateNote(flag: FeatureFlag, locked: boolean): string {
  if (locked) return `Fixed at its default (${onOff(flag.default)}) on the public demo.`
  if (flag.source === 'env')
    return `Set to ${onOff(flag.enabled)} by ${flag.env_var} on the server — the switch here has no effect while that is set.`
  if (flag.source === 'test') return 'Forced by a test.'
  if (flag.stored !== null) return `Changed — the default is ${onOff(flag.default)}.`
  return `Default (${onOff(flag.default)}).`
}

export function ExperimentalPanel({ toast }: SectionProps) {
  const { data, error, initialLoading, refresh } = useFeatureFlags()
  useSectionBusy(initialLoading && !data)
  const locked = data?.locked ?? false
  const flags = data?.flags ?? []

  async function save(flag: FeatureFlag, enabled: boolean | null) {
    try {
      const r = await authFetch(`/api/feature-flags/${encodeURIComponent(flag.name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void refresh()
      toast(
        enabled === null
          ? `${title(flag.name)} reset to its default`
          : `${title(flag.name)} turned ${onOff(enabled)}`,
      )
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <Panel
      title="Experimental"
      sub="Features still being tried out. Off means the app works exactly as it did before."
    >
      {error && !data ? (
        <Empty
          title="Could not load experimental features"
          body="The server did not answer."
          action={
            <Btn size="xs" onClick={() => void refresh()}>
              Retry
            </Btn>
          }
        />
      ) : initialLoading && !data ? (
        <span className="rd-help">Loading…</span>
      ) : flags.length === 0 ? (
        <Empty
          title="No experimental features right now"
          body="When a new feature ships behind a switch, it appears here at its default and you decide when to turn it on."
        />
      ) : (
        flags.map((flag) => (
          <div key={flag.name} data-flag={flag.name}>
            {row(
              <>
                {title(flag.name)}{' '}
                {flag.kind === 'kill_switch' ? <Badge tone="warn">Kill switch</Badge> : null}{' '}
                {flag.stored !== null && !locked ? <Badge tone="accent">Changed</Badge> : null}
              </>,
              <>
                {flag.description}
                <br />
                {stateNote(flag, locked)}
              </>,
              <>
                {flag.stored !== null && !locked ? (
                  <Btn size="xs" variant="ghost" onClick={() => void save(flag, null)}>
                    Reset
                  </Btn>
                ) : null}
                <Toggle
                  on={flag.enabled}
                  disabled={locked || flag.source === 'env' || flag.source === 'test'}
                  onChange={(v) => void save(flag, v)}
                  label={title(flag.name)}
                />
              </>,
            )}
          </div>
        ))
      )}
    </Panel>
  )
}
