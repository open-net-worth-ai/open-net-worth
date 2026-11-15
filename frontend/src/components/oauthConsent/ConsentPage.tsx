// /oauth/consent?request=… — where a hosted AI app (claude.ai, ChatGPT) sends the owner to approve
// a connection to the book (flag mcp_oauth).
//
// Rendered BEFORE the sign-in gate (main.tsx): the owner types their password HERE, every time,
// and that typed password is what approves — never a session, never loopback trust. Read-only is
// the default; changes are a box the owner ticks. The app's name is the server's label for a
// known host, never what the app calls itself.
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useBrand } from '../../brand-context'
import {
  Button,
  Checkbox,
  Container,
  ErrorState,
  Loading,
  PageHeader,
  Panel,
  PasswordField,
  Stack,
  Text,
} from '../ui'
import { decideConsent, loadConsent } from './consentApi'
import type { ConsentDetails } from './consentApi'

type Load =
  | { state: 'loading' }
  | { state: 'gone' }
  | { state: 'ready'; details: ConsentDetails }
  | { state: 'leaving' }

const ERRORS = {
  password: 'That password is not right.',
  throttled: 'Too many wrong passwords. Wait 15 minutes and try again.',
  expired: 'This request has expired. Start again from the app.',
  failed: 'Something went wrong. Start again from the app.',
} as const

export function ConsentPage() {
  const brand = useBrand()
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const request = new URLSearchParams(window.location.search).get('request') ?? ''

  useEffect(() => {
    // The URL names a pending sign-in — never pass it on as a Referer.
    const meta = document.createElement('meta')
    meta.name = 'referrer'
    meta.content = 'no-referrer'
    document.head.appendChild(meta)
    let live = true
    loadConsent(request)
      .then((details) => live && setLoad({ state: 'ready', details }))
      .catch(() => live && setLoad({ state: 'gone' }))
    return () => {
      live = false
      meta.remove()
    }
  }, [request])

  return (
    <main className="oauth-consent">
      <Container size="narrow">
        <Stack gap="--space-6">
          <Text size="sm" weight={700} tone="faint">
            {brand.app_name.toUpperCase()}
          </Text>
          {load.state === 'loading' ? <Loading label="Opening…" /> : null}
          {load.state === 'gone' ? (
            <ErrorState
              title="This request no longer works"
              message="It expired or was already answered. Start again from the app you were connecting."
            />
          ) : null}
          {load.state === 'leaving' ? <Loading label="Returning to the app…" /> : null}
          {load.state === 'ready' ? (
            <Decide
              request={request}
              details={load.details}
              onLeave={(url) => {
                setLoad({ state: 'leaving' })
                window.location.assign(url)
              }}
            />
          ) : null}
        </Stack>
      </Container>
    </main>
  )
}

function Decide({
  request,
  details,
  onLeave,
}: {
  request: string
  details: ConsentDetails
  onLeave: (url: string) => void
}) {
  const [password, setPassword] = useState('')
  const [write, setWrite] = useState(false)
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canWrite = details.requested_scopes.includes('write')

  async function decide(decision: 'allow' | 'deny') {
    setBusy(decision)
    setError(null)
    const result = await decideConsent(
      decision === 'allow'
        ? { request, decision, password, allow_write: canWrite && write }
        : { request, decision },
    )
    setBusy(null)
    if (result.ok) onLeave(result.redirect)
    else setError(ERRORS[result.reason])
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void decide('allow')
  }

  return (
    <form onSubmit={submit}>
      <Stack gap="--space-5">
        <PageHeader
          title={`Connect ${details.app_name} to your book?`}
          subtitle={`${details.app_name} (${details.app_host}) wants to read your accounts, transactions, budget and investments.`}
        />
        <Panel title="Only continue if you started this">
          <Text size="sm">
            Approve only if you just added Open Net Worth in {details.app_name} yourself, from your
            own account. If someone sent you this link, choose Deny. After you approve, you go back
            to {details.redirect_host}.
          </Text>
        </Panel>
        {canWrite ? (
          <Checkbox
            label="Also let it make changes"
            description="Add, edit and delete records, run automations and send messages for you. Every change is recorded and can be undone. Leave this off for read-only access."
            checked={write}
            onChange={(e) => setWrite(e.target.checked)}
          />
        ) : null}
        <label>
          <Text size="sm" weight={600}>
            Your Open Net Worth password
          </Text>
          <PasswordField
            autoComplete="current-password"
            value={password}
            invalid={error === ERRORS.password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error ? <ErrorState title="Not connected" message={error} /> : null}
        <Stack gap="--space-3">
          <Button
            type="submit"
            variant="primary"
            loading={busy === 'allow'}
            disabled={!password || busy !== null}
          >
            {write && canWrite ? 'Allow reading and changes' : 'Allow read-only access'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            loading={busy === 'deny'}
            disabled={busy !== null}
            onClick={() => void decide('deny')}
          >
            Deny
          </Button>
        </Stack>
        <Text size="sm" tone="faint">
          You can disconnect it any time in Settings → MCP. Changing your password disconnects every
          app.
        </Text>
      </Stack>
    </form>
  )
}
