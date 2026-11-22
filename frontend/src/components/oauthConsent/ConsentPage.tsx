// /oauth/consent?request=… — where a hosted AI app (claude.ai, ChatGPT) sends the owner to approve
// a connection to the book (flag mcp_oauth).
//
// Rendered BEFORE the sign-in gate (main.tsx). Two steps: type the password for this request, then
// the consent card with Deny / Allow. The sign-in is for THIS request only (a short cookie on the
// consent routes, never the app's session, never loopback trust), so a link someone else started
// always needs the password typed for it. Read is the default; Read and change and Talk to Lucius
// are the other choices.
// The app's name is the server's label for a known host, never what the app calls itself.
import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useBrand } from '../../brand-context'
import { LockKeyhole } from '../../ui/icons'
import { Button, ErrorState, Loading, PasswordField, RadioGroup } from '../ui'
import { decideConsent, loadConsent, signIn } from './consentApi'
import type { ConsentDetails } from './consentApi'

type Load =
  | { state: 'loading' }
  | { state: 'gone' }
  | { state: 'ready'; details: ConsentDetails }
  | { state: 'leaving' }

const ERRORS = {
  expired: 'This request has expired. Start again from the app.',
  // The request is still good: the call didn't get a normal answer (a dropped connection, a
  // tunnel or server hiccup), so trying again on this page is the fix.
  failed: "Couldn't reach Open Net Worth. Try again.",
} as const

export function ConsentPage() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const request = new URLSearchParams(window.location.search).get('request') ?? ''

  const reload = useCallback(
    () =>
      loadConsent(request)
        .then((details) => setLoad({ state: 'ready', details }))
        .catch(() => setLoad({ state: 'gone' })),
    [request],
  )

  useEffect(() => {
    // The URL names a pending sign-in — never pass it on as a Referer.
    const meta = document.createElement('meta')
    meta.name = 'referrer'
    meta.content = 'no-referrer'
    document.head.appendChild(meta)
    void reload()
    return () => meta.remove()
  }, [reload])

  const leave = (url: string) => {
    setLoad({ state: 'leaving' })
    window.location.assign(url)
  }

  return (
    <main className="oauth-consent">
      <div className="consent-card">
        {load.state === 'ready' && !load.details.signed_in ? (
          <SignInStep request={request} details={load.details} onSignedIn={() => void reload()} />
        ) : null}
        {load.state === 'ready' && load.details.signed_in ? (
          <Decide
            request={request}
            details={load.details}
            onLeave={leave}
            onSignedOut={() =>
              setLoad({ state: 'ready', details: { ...load.details, signed_in: false } })
            }
          />
        ) : null}
        {load.state !== 'ready' ? (
          <>
            <Pairing app={null} />
            {load.state === 'loading' ? <Loading label="Opening…" /> : null}
            {load.state === 'leaving' ? <Loading label="Returning to the app…" /> : null}
            {load.state === 'gone' ? (
              <ErrorState
                title="This request no longer works"
                message="It expired or was already answered. Start again from the app you were connecting."
              />
            ) : null}
          </>
        ) : null}
      </div>
    </main>
  )
}

/** The one visual moment: this instance's mark, a locked link, and the app asking to connect —
 * its own icon, which the server fetches from the app's verified domain, or its initial. */
function Pairing({ app, request }: { app: ConsentDetails | null; request?: string }) {
  const brand = useBrand()
  const [iconFailed, setIconFailed] = useState(false)
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
        {app && request && !iconFailed ? (
          <img
            src={`/api/oauth/consent/icon?request=${encodeURIComponent(request)}`}
            alt=""
            onError={() => setIconFailed(true)}
          />
        ) : app ? (
          app.app_name.charAt(0).toUpperCase()
        ) : null}
      </span>
    </div>
  )
}

function SignInStep({
  request,
  details,
  onSignedIn,
}: {
  request: string
  details: ConsentDetails
  onSignedIn: () => void
}) {
  const brand = useBrand()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    const result = await signIn(request, password)
    setBusy(false)
    if (result.ok) {
      setPassword('')
      onSignedIn()
    } else setError(result.message)
  }

  return (
    <form className="consent-form" onSubmit={submit}>
      <Pairing app={details} request={request} />
      <header className="consent-head">
        <h1>Sign in to connect {details.app_name}</h1>
        <p>
          <strong>{details.app_host}</strong> is asking to use {brand.app_name}. Sign in first, then
          choose what it can do.
        </p>
      </header>
      {/* The same hidden username as the main sign-in, so a password manager fills the login it
          saved for this instance. */}
      <input
        type="text"
        name="username"
        autoComplete="username"
        defaultValue="opennetworth"
        readOnly
        tabIndex={-1}
        aria-hidden="true"
        className="sr-only"
      />
      <div className="consent-password">
        <label htmlFor="consent-password">Password</label>
        <PasswordField
          id="consent-password"
          name="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          invalid={error !== null}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error ? <p className="consent-error">{error}</p> : null}
      </div>
      <Button type="submit" variant="primary" loading={busy} disabled={!password || busy}>
        Sign in
      </Button>
    </form>
  )
}

type Access = 'read' | 'write' | 'talk'

function accessOptions(canWrite: boolean) {
  const options = [{ value: 'read', label: 'Read', description: 'See everything, change nothing.' }]
  if (canWrite)
    options.push({
      value: 'write',
      label: 'Read and change',
      description: 'See and change everything. Changes can be undone.',
    })
  options.push({
    value: 'talk',
    label: 'Talk to Lucius',
    description: 'Only talk to Lucius. He follows your permissions.',
  })
  return options
}

function Decide({
  request,
  details,
  onLeave,
  onSignedOut,
}: {
  request: string
  details: ConsentDetails
  onLeave: (url: string) => void
  onSignedOut: () => void
}) {
  const brand = useBrand()
  const [access, setAccess] = useState<Access>('read')
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canWrite = details.requested_scopes.includes('write')
  const changes = canWrite && access === 'write'
  const talk = access === 'talk'
  const app = details.app_name

  async function decide(decision: 'allow' | 'deny') {
    setBusy(decision)
    setError(null)
    const result = await decideConsent(
      decision === 'allow'
        ? { request, decision, allow_write: changes, ...(talk ? { talk_only: true } : {}) }
        : { request, decision },
    )
    setBusy(null)
    if (result.ok) onLeave(result.redirect)
    else if (result.reason === 'signin') onSignedOut()
    else setError(ERRORS[result.reason])
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void decide('allow')
  }

  return (
    <form className="consent-form" onSubmit={submit}>
      <Pairing app={details} request={request} />
      <header className="consent-head">
        <h1>
          Connect {app} to {brand.app_name}?
        </h1>
        <p>
          Requested by <strong>{details.app_host}</strong>
        </p>
      </header>

      <section className="consent-scope" aria-label={`What ${app} can do`}>
        <p className="consent-scope__title">What can {app} do?</p>
        <RadioGroup
          name="consent-access"
          ariaLabel={`What ${app} can do`}
          value={access}
          onChange={(next) => setAccess(next as Access)}
          options={accessOptions(canWrite)}
        />
      </section>

      <p className="consent-warn">
        Only continue if you just added {brand.app_name} in {app} yourself. If someone sent you this
        link, choose Deny.
      </p>

      {error ? <p className="consent-error">{error}</p> : null}

      <div className="consent-actions">
        <Button
          type="button"
          variant="secondary"
          loading={busy === 'deny'}
          disabled={busy !== null}
          onClick={() => void decide('deny')}
        >
          Deny
        </Button>
        <Button type="submit" variant="primary" loading={busy === 'allow'} disabled={busy !== null}>
          {talk
            ? 'Allow talking to Lucius'
            : changes
              ? 'Allow read and write access'
              : 'Allow read access'}
        </Button>
      </div>

      <p className="consent-foot">
        You'll return to {details.redirect_host}. Disconnect any time in Settings → MCP; changing
        your password disconnects every app.
      </p>
    </form>
  )
}
