import { useState } from 'react'
import { authFetch } from '../../../api/client'
import { Button, Cluster, Text } from '../../ui'

// Tries the saved connection end to end (address, proxy, certificate, sign-in, then the chosen
// budget downloading) and says which step stopped it. Only for what is saved: unsaved changes are
// not tested, and the line says so.

export function ActualCheck({ unsaved }: { unsaved: boolean }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  async function check() {
    setBusy(true)
    setResult(null)
    try {
      const r = await authFetch('/api/actual-budget/check', { method: 'POST' })
      const body = (await r.json().catch(() => null)) as { ok?: boolean; text?: string } | null
      setResult(
        r.ok && body?.text
          ? { ok: !!body.ok, text: body.text }
          : { ok: false, text: 'The test couldn’t run. Try again in a moment.' },
      )
    } catch {
      setResult({ ok: false, text: 'Could not reach this app’s server.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Cluster gap="--space-2" align="center">
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => void check()}>
        {busy ? 'Testing…' : 'Test connection'}
      </Button>
      {result ? (
        <span role="status">
          <Text size="xs" tone={result.ok ? 'good' : 'bad'}>
            {result.text}
          </Text>
        </span>
      ) : (
        unsaved && (
          <Text size="xs" tone="faint">
            Tests what is saved: save your changes first.
          </Text>
        )
      )}
    </Cluster>
  )
}
