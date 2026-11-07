// Settings → Security → Password: set, change, reset or remove the instance password.
//
// Which form shows is the SERVER's answer for this very request (`/api/auth/settings`), never a
// client guess: `current_password_required` is false only for the desktop app's own session,
// which may set a new password without the old one (a forgotten password is reset from here).
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { tooManyAttemptsMessage } from '../../../../api/rateLimit'
import { PasswordField } from '../../../ui/PasswordField'
import { Btn, Field, Panel } from '../ds'

export interface PasswordState {
  password_set: boolean
  current_password_required: boolean
  trust_loopback: boolean
}

// Matches the first-run step and the server's floor.
const MIN_PASSWORD_LENGTH = 8
// The server's `detail` for a wrong current password (backend/api/auth.py).
const WRONG_CURRENT_DETAIL = 'current password incorrect'

export function PasswordPanel({
  state,
  refresh,
  toast,
}: {
  state: PasswordState | undefined
  refresh: () => Promise<unknown> | void
  toast: (message: string) => void
}) {
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmingRemove, setConfirmingRemove] = useState(false)

  // Until settings load, assume the strictest panel (Current + New + Confirm) rather than
  // flashing a no-current-password form that the server may then refuse.
  const isSet = state?.password_set ?? true
  const needsCurrent = state?.current_password_required ?? true
  // Outside the desktop app the server refuses removal while this computer is asked for the
  // password: an unconfigured instance opens loopback, which would override that choice.
  const removalBlocked = needsCurrent && state?.trust_loopback === false
  const verb = !isSet ? 'set' : needsCurrent ? 'change' : 'reset'
  const primaryLabel = `${verb[0]!.toUpperCase()}${verb.slice(1)} password`

  function clearFields() {
    setOldPw('')
    setNewPw('')
    setConfirmPw('')
  }

  // Both writes share the same wrong-current-password and transport handling.
  async function submit(path: string, body: Record<string, unknown>, failure: string) {
    setBusy(true)
    try {
      const res = await authFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          old_password: needsCurrent ? oldPw || undefined : undefined,
          ...body,
        }),
      })
      if (!res.ok) {
        if (res.status === 429) {
          toast(tooManyAttemptsMessage(res))
          return false
        }
        const detail = (await res.json().catch(() => null))?.detail
        // 403, not 401: a 401 would be read by authFetch as an expired session (sign-in screen).
        if (res.status === 403 && detail === WRONG_CURRENT_DETAIL) {
          toast('Current password is wrong')
          return false
        }
        toast(typeof detail === 'string' ? detail : `${failure} (HTTP ${res.status})`)
        return false
      }
      clearFields()
      void refresh()
      return true
    } catch {
      toast('Could not reach the server. Try again.')
      return false
    } finally {
      setBusy(false)
    }
  }

  async function savePassword() {
    // Passwords are kept exactly as typed, spaces included — but all spaces is no password.
    if (!newPw.trim()) {
      toast('Enter a new password')
      return
    }
    if (newPw.length < MIN_PASSWORD_LENGTH) {
      toast(`New password must be at least ${MIN_PASSWORD_LENGTH} characters`)
      return
    }
    if (newPw !== confirmPw) {
      toast("New password and confirmation don't match")
      return
    }
    const ok = await submit(
      '/api/auth/change-password',
      { new_password: newPw },
      `Could not ${verb} the password`,
    )
    if (ok) toast(isSet ? 'Password changed' : 'Password set')
  }

  async function removePassword() {
    if (needsCurrent && !oldPw) {
      toast('Enter your current password to remove it')
      return
    }
    const ok = await submit('/api/auth/remove-password', {}, 'Could not remove the password')
    if (!ok) return
    setConfirmingRemove(false)
    toast('Password removed')
  }

  return (
    <Panel
      title={isSet ? 'Password' : 'Set a password'}
      sub="Signs you in to this instance — it is the only account, and it is stored on your own server"
    >
      <div className="rd-formgrid">
        {needsCurrent && (
          <Field label="Current password">
            <PasswordField
              className="rd-input"
              autoComplete="current-password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
            />
          </Field>
        )}
        {/* Removing needs only the current password (where it is required), never a new one. */}
        {!confirmingRemove && (
          <>
            <Field
              label="New password"
              help={
                <>
                  At least {MIN_PASSWORD_LENGTH} characters.
                  {isSet && !needsCurrent && (
                    <> You're on this computer's own book, so the current password isn't needed.</>
                  )}
                  {isSet && needsCurrent && (
                    <>
                      {' '}
                      Forgot it? In the desktop app, set a new one here. On a server, run{' '}
                      <code>onw reset-password</code> on that machine.
                    </>
                  )}
                </>
              }
            >
              <PasswordField
                className="rd-input"
                autoComplete="new-password"
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
              />
            </Field>
            <Field label="Confirm password">
              <PasswordField
                className="rd-input"
                autoComplete="new-password"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
              />
            </Field>
          </>
        )}
      </div>
      {confirmingRemove && (
        <div className="rd-help">
          {removalBlocked ? (
            <>
              Turn on "Don't ask for the password on this computer" first, or keep a password:
              removing it would open this book to every local request.
            </>
          ) : (
            <>
              Remove the password? Other devices will be signed out and can't open this book until
              you set a password again. This computer will open it without one.
            </>
          )}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {confirmingRemove ? (
          <>
            <Btn
              variant="danger-solid"
              disabled={busy || removalBlocked}
              onClick={() => void removePassword()}
            >
              Remove password
            </Btn>
            <Btn disabled={busy} onClick={() => setConfirmingRemove(false)}>
              Cancel
            </Btn>
          </>
        ) : (
          <>
            <Btn variant="primary" disabled={busy} onClick={() => void savePassword()}>
              {primaryLabel}
            </Btn>
            {isSet && (
              <Btn disabled={busy} onClick={() => setConfirmingRemove(true)}>
                Remove password
              </Btn>
            )}
          </>
        )}
      </div>
    </Panel>
  )
}
