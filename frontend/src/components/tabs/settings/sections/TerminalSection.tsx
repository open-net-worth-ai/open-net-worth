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
import { Btn, Panel } from '../ds'
import { TerminalConfirm } from './TerminalConfirm'
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

  return (
    <Panel title="Terminal" sub={TERMINAL_NOTICE}>
      {status.kind === 'loading' && <div className="rd-help">Checking…</div>}
      {status.kind === 'unavailable' && <div className="rd-help">{status.reason}</div>}
      {status.kind === 'sign-in-again' && gatewayMode && (
        <TerminalConfirm
          email={identity?.email ?? null}
          onConfirmed={recheck}
          onFallback={(url) => signInAgain(url ?? status.reauth)}
        />
      )}
      {status.kind === 'sign-in-again' && gatewayMode && signInError && (
        <div className="rd-help">{signInError}</div>
      )}
      {status.kind === 'sign-in-again' && !gatewayMode && (
        <div className="rd-term-note">
          <div className="rd-help">
            For your security the terminal asks for a recent sign-in. Sign in again to continue.
          </div>
          {status.reauth || !identity?.portal_url ? (
            <Btn variant="primary" onClick={() => signInAgain(status.reauth)}>
              Sign in again
            </Btn>
          ) : (
            <a href={identity.portal_url} target="_blank" rel="noopener noreferrer">
              Open the Private Cloud portal to sign in again
            </a>
          )}
          {signInError && <div className="rd-help">{signInError}</div>}
        </div>
      )}
      {status.kind === 'ready' && !open && (
        <div>
          <Btn
            variant="primary"
            onClick={() => {
              setSession((n) => n + 1)
              setOpen(true)
            }}
          >
            Open terminal
          </Btn>
        </div>
      )}
      {status.kind === 'ready' && open && (
        <Suspense fallback={<div className="rd-help">Loading…</div>}>
          <TerminalView
            key={session}
            onEnded={(reason) => {
              setOpen(false)
              if (reason === 'sign-in-again') recheck()
            }}
          />
        </Suspense>
      )}
    </Panel>
  )
}
