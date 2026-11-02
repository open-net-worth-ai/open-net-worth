// A built-in connector's own writes — turning it on or off and removing it — shared by every
// screen that shows its panel (the Connectors tab, and ConnectorPanel wherever else a
// connector is set up), so the two can never answer the same click differently.
import { authFetch } from '../../../api/client'
import type { ConnectorDetailEntry } from './model'

export type ActionResult = { ok: true; message: string } | { ok: false; error: string }

/** `PATCH /api/connectors/{id}` `{enabled}`. Telegram also reports whether the live bot picked
 *  the change up (`reloaded`); no other connector sends that field. */
export async function setConnectorEnabled(
  entry: ConnectorDetailEntry,
  enabled: boolean,
): Promise<ActionResult> {
  const res = await authFetch(`/api/connectors/${encodeURIComponent(entry.id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok)
    return { ok: false, error: `Couldn't ${enabled ? 'connect' : 'disconnect'} ${entry.name}` }
  const body = (await res.json().catch(() => null)) as { reloaded?: boolean } | null
  if (body?.reloaded === false) {
    return {
      ok: true,
      message:
        `${entry.name} ${enabled ? 'connected' : 'disconnected'} in settings, but the live ` +
        `bot didn't confirm the change — it may still be running the old configuration. ` +
        `Try again, or restart the app.`,
    }
  }
  return { ok: true, message: `${entry.name} ${enabled ? 'connected' : 'disconnected'}` }
}

/** `DELETE /api/connectors/{id}` — the native-connector half of removal. For Telegram, the
 *  backend REFUSES (502) rather than clearing credentials if it couldn't confirm the live bot
 *  actually stopped first — surfacing that real detail (not a generic "Couldn't remove") is the
 *  point: a silent generic failure would hide exactly the fail-open risk the backend ordering
 *  fix exists to prevent. */
export async function removeNativeConnector(
  entry: ConnectorDetailEntry,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const res = await authFetch(`/api/connectors/${encodeURIComponent(entry.id)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
    const detail = body?.detail
    const message = typeof detail === 'string' && detail ? detail : `Couldn't remove ${entry.name}`
    return { ok: false, error: message }
  }
  return { ok: true }
}
