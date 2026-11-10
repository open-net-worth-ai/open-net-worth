// What to do once a connector has a linked account: linking imports nothing, so say so, and offer
// the one step that brings transactions in — the connector's sync Automation (as a draft the user
// reviews, on the template's own schedule) — or the sentence to ask Lucius for a one-off fetch.
import { useRouter } from '@tanstack/react-router'
import { Button, Text } from '../../ui'
import { ALL_TEMPLATES, templateKeyForConnector } from '../automations/automationTemplates'

/** "every 6 hours" / "every hour" — the template's own interval, so the copy can't drift. */
function cadence(templateKey: string | null): string | null {
  const trigger = ALL_TEMPLATES.find((t) => t.key === templateKey)?.trigger
  if (!trigger || trigger.kind !== 'schedule' || trigger.type !== 'interval') return null
  const hours = Math.round((trigger.seconds ?? 0) / 3600)
  if (hours < 1) return null
  return hours === 1 ? 'every hour' : `every ${hours} hours`
}

export function ConnectorNextStep({
  provider,
  providerLabel,
  linkedCount,
  unlinkedCount,
  bare = false,
}: {
  provider: string
  providerLabel: string
  linkedCount: number
  unlinkedCount: number
  /** Inside a setup step that supplies its own frame and title — render the body only. */
  bare?: boolean
}) {
  // Optional: these forms also render outside the router (tests, previews).
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined
  const templateKey = templateKeyForConnector(provider)
  if (linkedCount === 0) return null
  const every = cadence(templateKey)
  const target = linkedCount === 1 ? 'your linked account' : 'your linked accounts'

  const openTemplate = () => {
    if (!templateKey) return
    if (router)
      void router.navigate({ to: '/automations', search: { template: templateKey } as never })
    else window.location.assign(`/automations?template=${encodeURIComponent(templateKey)}`)
  }

  const body = (
    <>
      <Text size="xs" tone="muted">
        Linking doesn&apos;t import anything yet.{' '}
        {templateKey
          ? `Set up an automation and Lucius will add new transactions from ${providerLabel} to ${target}${every ? ` ${every}` : ''}.`
          : `Ask Lucius to add transactions from ${providerLabel} to ${target}.`}
        {unlinkedCount > 0 &&
          ` ${unlinkedCount} ${unlinkedCount === 1 ? "account isn't" : "accounts aren't"} linked, so ${unlinkedCount === 1 ? "it'll" : "they'll"} be skipped.`}
      </Text>
      <div className="conn-next-actions">
        {templateKey && (
          <Button size="sm" variant="primary" onClick={openTemplate}>
            Set up automation
          </Button>
        )}
        <Text size="xs" tone="faint">
          Or ask Lucius now: “Fetch my {providerLabel} transactions.”
        </Text>
      </div>
    </>
  )
  if (bare) return <div className="conn-next-body">{body}</div>
  return (
    <div className="conn-next" role="region" aria-label="Next step">
      <Text size="sm" tone="ink" weight={600}>
        Next: bring in transactions
      </Text>
      {body}
    </div>
  )
}
