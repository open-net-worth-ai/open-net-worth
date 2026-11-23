// Private Cloud: confirm it's you without leaving the Terminal. The gateway in front of this agent
// emails a sign-in code to the account's owner and checks the code typed here; a correct one
// re-signs this browser's session as just confirmed (gateway_confirm.py in onw-cloud).
import { useState } from 'react'
import { Btn } from '../ds'

type Answer = { sent_to?: string; ok?: boolean; error?: string; fallback?: string }

async function post(path: string, body?: object): Promise<{ status: number; data: Answer }> {
  const res = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-Onw-Return': window.location.pathname + window.location.search,
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = (await res.json().catch(() => ({}))) as Answer
  return { status: res.status, data }
}

export function TerminalConfirm({
  email,
  onConfirmed,
  onFallback,
}: {
  email: string | null
  onConfirmed: () => void
  onFallback: (url?: string) => void
}) {
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await work()
    } catch {
      setError('Could not reach the server. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const send = () =>
    run(async () => {
      const { status, data } = await post('/_gateway/confirm/send')
      if (status === 200) setSentTo(data.sent_to ?? email ?? 'your email')
      else if (status === 409 && data.fallback) onFallback(data.fallback)
      else if (status === 401) onFallback()
      else setError(data.error ?? 'Could not send a code. Try again.')
    })

  const verify = () =>
    run(async () => {
      const { status, data } = await post('/_gateway/confirm/verify', { code })
      if (status === 200 && data.ok) onConfirmed()
      else if (status === 409 && data.fallback) onFallback(data.fallback)
      else if (status === 401) onFallback()
      else setError(data.error ?? 'That code did not work. Try again.')
    })

  if (!sentTo) {
    return (
      <div className="rd-term-note">
        <div className="rd-help">
          {`For your security, the terminal asks you to confirm it's you every 2 hours. We'll email a code to ${email ?? 'your account'}.`}
        </div>
        <Btn variant="primary" onClick={() => void send()} disabled={busy}>
          {busy ? 'Sending…' : 'Email me a code'}
        </Btn>
        {error && <div className="rd-help">{error}</div>}
      </div>
    )
  }
  return (
    <form
      className="rd-term-note"
      onSubmit={(event) => {
        event.preventDefault()
        void verify()
      }}
    >
      <div className="rd-help">{`Enter the code we sent to ${sentTo}.`}</div>
      <input
        className="rd-input"
        aria-label="Code from the email"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={12}
        value={code}
        onChange={(event) => setCode(event.target.value)}
        autoFocus
      />
      <Btn variant="primary" type="submit" disabled={busy || !code.trim()}>
        {busy ? 'Checking…' : 'Confirm'}
      </Btn>
      <Btn onClick={() => void send()} disabled={busy}>
        Send a new code
      </Btn>
      {error && <div className="rd-help">{error}</div>}
    </form>
  )
}
