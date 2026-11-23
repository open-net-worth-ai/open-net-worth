// Settings → Terminal. A shell on the machine this app runs on. In Private Cloud that is your
// own server; a self-hosted app offers it only when its owner has switched it on.
import { lazy, Suspense, useEffect, useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useGatewayIdentity } from '../../../../hooks/useGatewayIdentity'
import {
  desktopFreshSignIn,
  freshSignInUrl,
  reauthUrlFrom,
  startGatewayReauth,
} from '../../../../utils/gatewayReauth'
import { TERMINAL_NOTICE } from '../../../shared/privateCloudCopy'
import { Btn, Empty, Panel } from '../ds'
import { TerminalConfirm } from './TerminalConfirm'
import { TerminalHelp } from './TerminalHelp'
import type { SectionProps } from '../ds'

const TerminalView = lazy(() => import('./TerminalView').then((m) => ({ default: m.TerminalView })))

type Status =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'sign-in-again'; reauth?: string }

const UNAVAILABLE: Record<string, string> = {
  disabled: 'The terminal is switched off on this server.',
  unsupported_platform: 'The terminal is not available on this operating system.',
  not_signed_in: 'Sign in to use the terminal.',
}

/** The terminal's availability for this browser; null when the answer is not one to show. */
async function loadStatus(gatewayMode: boolean): Promise<Status | null> {
  try {
    const res = await authFetch('/api/terminal/status')
    if (res.status === 401) {
      const body = (await res
        .clone()
        .json()
        .catch(() => null)) as { code?: string } | null
      return body?.code === 'fresh_session_required'
        ? {
            kind: 'sign-in-again',
            reauth: (await reauthUrlFrom(res)) ?? (gatewayMode ? freshSignInUrl() : undefined),
          }
        : null
    }
    const body = (await res.json()) as { available: boolean; reason: string | null }
    return body.available
      ? { kind: 'ready' }
      : { kind: 'unavailable', reason: UNAVAILABLE[body.reason ?? ''] ?? 'Not available.' }
  } catch {
    return { kind: 'unavailable', reason: 'Could not reach the server. Try again.' }
  }
}

export function TerminalSection({ gatewayMode = false }: SectionProps) {
  const [status, setStatus] = useState<Status>({ kind: 'loading' })
  const [session, setSession] = useState(0) // bumps to open a fresh terminal
  const [open, setOpen] = useState(false)
  const [signInError, setSignInError] = useState('')
  const identity = useGatewayIdentity(gatewayMode)

  useEffect(() => {
    let cancelled = false
    void loadStatus(gatewayMode).then((next) => {
      if (!cancelled && next) setStatus(next)
    })
    return () => {
      cancelled = true
    }
  }, [gatewayMode])

  const signInAgain = (reauth?: string) => {
    const desktop = desktopFreshSignIn()
    if (!desktop) {
      startGatewayReauth(reauth, undefined, undefined, true)
      return
    }
    setSignInError('')
    desktop.catch(() => setSignInError('Could not start the sign-in. Try again.'))
  }

  const recheck = () => void loadStatus(gatewayMode).then((next) => next && setStatus(next))

  // Opening this screen is asking for the terminal: it starts on its own once allowed. A button
  // only comes back after a session ends, to start another.
  useEffect(() => {
    if (status.kind === 'ready' && !open) {
      setSession((n) => n + 1)
      setOpen(true)
    }
  }, [status.kind, open])

  if (status.kind === 'unavailable')
    return (
      <Panel title="Terminal" sub={TERMINAL_NOTICE}>
        <div className="rd-help">{status.reason}</div>
      </Panel>
    )
  return (
    <Panel title="Terminal" sub={TERMINAL_NOTICE}>
      <div className="rd-term-area">
        <TerminalHelp />
        {(status.kind === 'loading' || (status.kind === 'ready' && !open)) && (
          <div className="rd-term-slot" aria-busy="true" />
        )}
        {status.kind === 'sign-in-again' && gatewayMode && (
          <TerminalConfirm
            email={identity?.email ?? null}
            onConfirmed={() => setStatus({ kind: 'ready' })}
            onFallback={(url) => signInAgain(url ?? status.reauth)}
          />
        )}
        {status.kind === 'sign-in-again' && gatewayMode && signInError && (
          <div className="rd-term-error">{signInError}</div>
        )}
        {status.kind === 'sign-in-again' && !gatewayMode && (
          <div className="rd-term-slot">
            <Empty
              title="Sign in again to use the terminal"
              body="For your security it asks for a recent sign-in."
              action={
                <>
                  {status.reauth || !identity?.portal_url ? (
                    <Btn variant="primary" onClick={() => signInAgain(status.reauth)}>
                      Sign in again
                    </Btn>
                  ) : (
                    <a href={identity.portal_url} target="_blank" rel="noopener noreferrer">
                      Open the Private Cloud portal to sign in again
                    </a>
                  )}
                  {signInError && <div className="rd-term-error">{signInError}</div>}
                </>
              }
            />
          </div>
        )}
        {status.kind === 'ready' && open && (
          <Suspense fallback={<div className="rd-term-slot" aria-busy="true" />}>
            <TerminalView
              key={session}
              onEnded={(reason) => {
                if (reason === 'sign-in-again') {
                  setOpen(false)
                  recheck()
                } else setSession((n) => n + 1) // a new shell, in the same box
              }}
            />
          </Suspense>
        )}
      </div>
    </Panel>
  )
}
