// What to do once a connector has a linked account: linking books nothing, so say so, and offer
// the one step that brings transactions in — the connector's sync Automation (as a draft the user
// reviews) — or the sentence to ask Lucius for a one-off fetch.
import { useRouter } from '@tanstack/react-router'
import { Button, Text } from '../../ui'
import { templateKeyForConnector } from '../automations/automationTemplates'

export function ConnectorNextStep({
  provider,
  providerLabel,
  linkedCount,
  unlinkedCount,
}: {
  provider: string
  providerLabel: string
  linkedCount: number
  unlinkedCount: number
}) {
  // Optional: these forms also render outside the router (tests, previews).
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined
  const templateKey = templateKeyForConnector(provider)
  if (linkedCount === 0) return null

  const openTemplate = () => {
    if (!templateKey) return
    if (router)
      void router.navigate({ to: '/automations', search: { template: templateKey } as never })
    else window.location.assign(`/automations?template=${encodeURIComponent(templateKey)}`)
  }

  return (
    <div className="conn-next" role="region" aria-label="Next step">
      <Text size="sm" tone="ink" weight={600}>
        Next: bring in transactions
      </Text>
      <Text size="xs" tone="muted">
        Linking books nothing on its own. Lucius fetches the last 14 days from {providerLabel} and
        books them into the linked {linkedCount === 1 ? 'account' : 'accounts'}, skipping anything
        already there.
        {unlinkedCount > 0 &&
          ` ${unlinkedCount} unlinked ${unlinkedCount === 1 ? 'account is' : 'accounts are'} left out — he'll ask before touching ${unlinkedCount === 1 ? 'it' : 'them'}.`}
      </Text>
      <div className="conn-next-actions">
        {templateKey && (
          <Button size="sm" variant="primary" onClick={openTemplate}>
            Sync automatically…
          </Button>
        )}
        <Text size="xs" tone="faint">
          Or ask Lucius now: “Fetch my {providerLabel} transactions and book them.”
        </Text>
      </div>
    </div>
  )
}
