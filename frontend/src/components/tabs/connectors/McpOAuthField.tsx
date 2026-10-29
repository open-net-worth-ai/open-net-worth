// The OAuth Sign-in button + status area (RFC `2026-08-14-mcp-oauth` §4.2/§4.3/§4.6) — split out
// of `McpConnectionFields.tsx` purely for size (CLAUDE.md's ~400-line file cap). All the state
// lives on `flow.oauth` (`McpAddFlow.tsx`'s `useMcpAddFlow`), which drives the popup + poll and
// survives the modal staying mounted through the whole sequence (RFC §4.3: "Modal or separate
// window").
//
// The footer's "Connect" button is the one trigger for sign-in — this field only shows STATUS,
// never a button of its own to start one. Renders nothing at all while idle with nothing to
// report.
//
//   pending   — popup open, polling → "Waiting for sign-in…" + Cancel, plus a link to the
//               authorize URL for a window the browser opened without a handle (Arc, Dia).
//   error     — `begin()`/poll failed, the popup was blocked, or it was closed early → the
//               backend's own message (§4.6), plus the two reason-specific affordances the
//               table calls for: a Client ID field (`no_registration`) or the redirect URIs to
//               register by hand (`dcr_rejected`).
//   signed_in — a fresh sign-in landed this session, or a saved server's summary says connected
//               and it does not need reauth → the connected-account summary, with a way to
//               redo it.
// A `needs_reauth` saved summary is deliberately NOT folded into `signed_in` — the hook's own
// seed already keeps `status` at `idle` for that case, so it renders as the plain button, with
// the stale summary shown alongside as a fact ("was … until it needed reauth"), never as if
// nothing is wrong.

import type { CSSProperties, JSX } from 'react'
import { Btn } from './primitives'
import { Icons } from './icons'
import type { McpAddFlow } from './McpAddFlow'

const ROW: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }

export function McpOAuthField({ flow }: { flow: McpAddFlow }): JSX.Element | null {
  const { oauth } = flow
  const staleSummary = oauth.summary?.connected && oauth.summary.needs_reauth ? oauth.summary : null
  const hasSomethingToShow =
    oauth.status === 'pending' ||
    oauth.status === 'signed_in' ||
    !!staleSummary ||
    !!oauth.error ||
    oauth.needsClientId ||
    oauth.redirectUris.length > 0
  if (!hasSomethingToShow) return null

  return (
    <div className="rd-field full">
      <span className="rd-label">OAuth</span>

      {oauth.status === 'pending' ? (
        // `role="status"` (the toast's own convention, `primitives.tsx`) — a screen-reader user
        // whose focus isn't already on this field otherwise gets no announcement at all when the
        // popup closes and sign-in succeeds or fails to complete.
        <div style={ROW} role="status">
          <span className="cn-spinner" />
          <span style={{ fontSize: 13 }}>Waiting for sign-in…</span>
          {/* Distinct wording from the modal's own Cancel/Discard (spec: "a Cancel that drops
              the staged flow") — this one abandons the sign-in, not the whole add flow. */}
          <Btn size="xs" onClick={oauth.cancel}>
            Cancel sign-in
          </Btn>
          {oauth.authorizeUrl ? (
            <span className="rd-help" style={{ flexBasis: '100%' }}>
              No sign-in window?{' '}
              <a href={oauth.authorizeUrl} target="_blank" rel="noreferrer">
                Open the sign-in page
              </a>
            </span>
          ) : null}
        </div>
      ) : oauth.status === 'signed_in' ? (
        <div style={ROW} role="status">
          <span style={{ color: 'var(--good)', display: 'inline-flex' }}>
            <Icons.check />
          </span>
          <span style={{ fontSize: 13 }}>
            Signed in
            {oauth.summary?.issuer_host ? ` — ${oauth.summary.issuer_host}` : ''}
            {oauth.summary?.scope ? ` • ${oauth.summary.scope}` : ''}
          </span>
          <Btn size="xs" onClick={oauth.signIn}>
            Sign in again
          </Btn>
        </div>
      ) : staleSummary || oauth.error || oauth.needsClientId || oauth.redirectUris.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
          {staleSummary ? (
            <span className="rd-help">
              Was connected to {staleSummary.issuer_host || 'this server'} — sign-in expired.
            </span>
          ) : null}

          {oauth.status === 'error' && oauth.error ? (
            <span className="rd-help rd-help--bad" role="alert">
              {oauth.error}
              {/* RFC §4.6's `no_metadata` row is explicit: the message PLUS the catalog
                  entry's own docs link — there is no probe result to pull one from here (the
                  failure happens before Test connect ever runs), so this is the one place
                  `flow.meta.homepageUrl` is seeded straight from the catalog at `start()`. */}
              {oauth.reason === 'no_metadata' && flow.meta.homepageUrl ? (
                <>
                  {' '}
                  <a href={flow.meta.homepageUrl} target="_blank" rel="noreferrer">
                    {flow.meta.homepageUrl}
                  </a>
                </>
              ) : null}
            </span>
          ) : null}

          {oauth.needsClientId ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
              <span className="rd-label" id="mcp-oauth-clientid-label">
                Client ID
              </span>
              <input
                className="rd-input"
                aria-labelledby="mcp-oauth-clientid-label"
                value={oauth.clientId}
                onChange={(e) => oauth.setClientId(e.target.value)}
                placeholder="Paste the client_id from a registration you made at the vendor"
              />
              <span className="rd-help">
                This server needs an app registration made by hand at the vendor. Paste its client
                id, then try again.
              </span>
            </div>
          ) : null}

          {oauth.redirectUris.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
              <span className="rd-help">
                Register this redirect URI with the vendor, then try again:
              </span>
              {oauth.redirectUris.map((uri) => (
                <code
                  key={uri}
                  className="rd-code"
                  style={{ padding: '7px 10px', wordBreak: 'break-all' }}
                >
                  {uri}
                </code>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
