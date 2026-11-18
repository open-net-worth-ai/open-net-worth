// /oauth/consent?request=… — where a hosted AI app (claude.ai, ChatGPT) sends the owner to approve
// a connection to the book (flag mcp_oauth).
//
// Rendered BEFORE the sign-in gate (main.tsx): the owner types their password HERE, every time,
// and that typed password is what approves — never a session, never loopback trust. Read-only is
// the default; changes are a switch the owner turns on. The app's name is the server's label for a
// known host, never what the app calls itself.
import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useBrand } from '../../brand-context'
import { Check, LockKeyhole, X } from '../../ui/icons'
import { Button, ErrorState, Loading, PasswordField, Switch } from '../ui'
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
      <div className="consent-card">
        {load.state === 'ready' ? (
          <Decide
            request={request}
            details={load.details}
            onLeave={(url) => {
              setLoad({ state: 'leaving' })
              window.location.assign(url)
            }}
          />
        ) : (
          <>
            <Pairing appName={null} />
            {load.state === 'loading' ? <Loading label="Opening…" /> : null}
            {load.state === 'leaving' ? <Loading label="Returning to the app…" /> : null}
            {load.state === 'gone' ? (
              <ErrorState
                title="This request no longer works"
                message="It expired or was already answered. Start again from the app you were connecting."
              />
            ) : null}
          </>
        )}
      </div>
    </main>
  )
}

/** The one visual moment: this instance's mark, a locked link, and the app asking to connect. */
function Pairing({ appName }: { appName: string | null }) {
  const brand = useBrand()
  return (
    <div className="consent-pair" aria-hidden="true">
      <span className="consent-pair__tile">
        <img
          src={brand.logo_url || '/logo-mark.svg?v=facet-solid'}
          data-builtin-logo={!brand.logo_url || undefined}
          alt=""
        />
      </span>
      <span className="consent-pair__link">
        <span className="consent-pair__lock">
          <LockKeyhole size={13} strokeWidth={2} />
        </span>
      </span>
      <span className="consent-pair__tile consent-pair__tile--app">
        {appName ? appName.charAt(0).toUpperCase() : ''}
      </span>
    </div>
  )
}

function Ability({ allowed, children }: { allowed: boolean; children: ReactNode }) {
  return (
    <li className={allowed ? 'consent-can' : 'consent-cannot'}>
      {allowed ? <Check size={15} strokeWidth={2.25} /> : <X size={15} strokeWidth={2.25} />}
      <span>{children}</span>
    </li>
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
  const brand = useBrand()
  const [password, setPassword] = useState('')
  const [write, setWrite] = useState(false)
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canWrite = details.requested_scopes.includes('write')
  const changes = canWrite && write
  const app = details.app_name

  async function decide(decision: 'allow' | 'deny') {
    setBusy(decision)
    setError(null)
    const result = await decideConsent(
      decision === 'allow'
        ? { request, decision, password, allow_write: changes }
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
    <form className="consent-form" onSubmit={submit}>
      <Pairing appName={app} />
      <header className="consent-head">
        <h1>Connect {app} to your book?</h1>
        <p>
          <strong>{details.app_host}</strong> is asking to use {brand.app_name}.
        </p>
      </header>

      <section className="consent-scope" aria-label={`What ${app} can do`}>
        <p className="consent-scope__title">{app} will be able to</p>
        <ul>
          <Ability allowed>See your accounts, transactions, budget and investments</Ability>
          <Ability allowed={changes}>Add, edit and delete records</Ability>
          <Ability allowed={changes}>Run automations and send messages for you</Ability>
        </ul>
        {canWrite ? (
          <div className="consent-scope__switch">
            <div>
              <label htmlFor="consent-write">Also let it make changes</label>
              <p>Every change is recorded and can be undone. Leave off for read-only access.</p>
            </div>
            <Switch
              id="consent-write"
              checked={write}
              onChange={(e) => setWrite(e.target.checked)}
            />
          </div>
        ) : null}
      </section>

      <p className="consent-warn">
        Only continue if you just added {brand.app_name} in {app} yourself. If someone sent you this
        link, choose Deny.
      </p>

      <div className="consent-password">
        <label htmlFor="consent-password">Your {brand.app_name} password</label>
        <PasswordField
          id="consent-password"
          autoComplete="current-password"
          value={password}
          invalid={error === ERRORS.password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error ? <p className="consent-error">{error}</p> : null}
      </div>

      <div className="consent-actions">
        <Button
          type="submit"
          variant="primary"
          loading={busy === 'allow'}
          disabled={!password || busy !== null}
        >
          {changes ? 'Allow reading and changes' : 'Allow read-only access'}
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
      </div>

      <p className="consent-foot">
        You'll return to {details.redirect_host}. Disconnect any time in Settings → MCP; changing
        your password disconnects every app.
      </p>
    </form>
  )
}
