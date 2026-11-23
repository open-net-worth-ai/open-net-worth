// Private Cloud: confirm it's you without leaving the Terminal. The gateway in front of this agent
// emails a code to the account's owner (good only for opening this Terminal) and checks the code
// typed here; a correct one re-signs this browser's session as just confirmed (gateway_confirm.py
// in onw-cloud). It holds the terminal's place at the terminal's height, so nothing moves.
import { useEffect, useRef, useState } from 'react'
import type React from 'react'
import { Btn, Empty } from '../ds'

const RESEND_SECONDS = 30

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
  const [wait, setWait] = useState(0) // seconds before another code may be sent
  const boxes = useRef<Array<HTMLInputElement | null>>([])
  const focusBox = (i: number) => window.setTimeout(() => boxes.current[i]?.focus())

  useEffect(() => {
    if (sentTo) focusBox(0)
  }, [sentTo])
  useEffect(() => {
    if (wait <= 0) return
    const timer = window.setTimeout(() => setWait((w) => w - 1), 1000)
    return () => window.clearTimeout(timer)
  }, [wait])

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
      if (status === 200) {
        setCode('')
        setWait(RESEND_SECONDS)
        setSentTo(data.sent_to ?? email ?? 'your email')
        focusBox(0)
      } else if (status === 409 && data.fallback) onFallback(data.fallback)
      else if (status === 401) onFallback()
      else setError(data.error ?? 'Could not send a code. Try again.')
    })

  const verify = (value: string) =>
    run(async () => {
      const { status, data } = await post('/_gateway/confirm/verify', { code: value })
      if (status === 200 && data.ok) return onConfirmed()
      if (status === 409 && data.fallback) return onFallback(data.fallback)
      if (status === 401) return onFallback()
      setCode('')
      setError(data.error ?? "That code didn't work. Try again.")
      focusBox(0)
    })

  // Six boxes, one digit each. Typing moves on, Backspace moves back, and a paste or an
  // autofilled code (one-time-code on the first box) fills every box at once.
  const place = (i: number, raw: string) => {
    const digits = raw.replace(/\D/g, '')
    if (!digits && raw) return
    const next = (code.slice(0, i) + digits + code.slice(i + Math.max(digits.length, 1))).slice(
      0,
      6,
    )
    setCode(next)
    setError('')
    focusBox(Math.min(i + Math.max(digits.length, 1), 5))
    if (next.length === 6 && !busy) void verify(next)
  }
  const back = (i: number, event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Backspace' || code[i] || i === 0) return
    event.preventDefault()
    setCode(code.slice(0, i - 1))
    focusBox(i - 1)
  }

  const errorLine = error ? (
    <div className="rd-term-error" role="alert">
      {error}
    </div>
  ) : null

  if (!sentTo) {
    return (
      <div className="rd-term-slot">
        <Empty
          title="Confirm it's you"
          body={`We'll email a code to ${email ?? 'your account'}.`}
          action={
            <>
              <Btn variant="primary" onClick={() => void send()} disabled={busy}>
                {busy ? 'Sending…' : 'Email me a code'}
              </Btn>
              {errorLine}
            </>
          }
        />
      </div>
    )
  }
  return (
    <div className="rd-term-slot">
      <Empty
        title="Enter the code"
        body={`Sent to ${sentTo}.`}
        action={
          <>
            <div className="rd-term-boxes" role="group" aria-label="Code from the email">
              {Array.from({ length: 6 }, (_, i) => (
                <input
                  key={i}
                  ref={(el) => {
                    boxes.current[i] = el
                  }}
                  className="rd-input rd-term-box"
                  aria-label={`Digit ${i + 1}`}
                  inputMode="numeric"
                  autoComplete={i === 0 ? 'one-time-code' : 'off'}
                  maxLength={i === 0 ? 6 : 1}
                  value={code[i] ?? ''}
                  onChange={(event) => place(i, event.target.value)}
                  onKeyDown={(event) => back(i, event)}
                  onFocus={(event) => event.target.select()}
                  disabled={busy}
                />
              ))}
            </div>
            {errorLine}
            <Btn size="xs" variant="ghost" onClick={() => void send()} disabled={busy || wait > 0}>
              {wait > 0 ? `Send a new code in ${wait}s` : 'Send a new code'}
            </Btn>
          </>
        }
      />
    </div>
  )
}
