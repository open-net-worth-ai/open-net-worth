// Add — or EDIT — an external MCP server (spec §7, and §3's kebab item) — ported from the
// prototype's `mcpOpen` branch.
//
// THE INVERSION. The old form asked for Server ID, Name, Transport and connection details up
// front — before the server had been reached — with a raw slug as a required field beside Name,
// and its only route for a README snippet was a separate paste MODE that skipped verification
// and force-created a disabled, unreviewed server. This flow reaches the server first, then
// names and permissions what it found:
//
//   1  connection info      — no Name, no Server ID; both derived in step 3
//   2  Test connect         — REQUIRED. An unreached server is NEVER saved.
//   3  confirm              — Name pre-filled from `serverInfo.name`; Server ID collapsed
//   4  permission           — the §4 ladder, built from step 2's discovered tools
//
// Steps 3 and 4 share one screen (`review`), exactly as the prototype does — you are confirming
// and permissioning the same discovered thing.
//
// EDIT MODE (`editing`) is the SAME four steps on a saved server, with four differences, each
// forced by something real rather than chosen:
//
//   * Server ID is fixed and no longer offers `customize` — it namespaces every tool as
//     `mcp__<id>__<tool>` and keys every permission row, and the store refuses to patch it;
//   * Transport is fixed for the same reason (the store refuses that too), which is also why
//     the paste pill is hidden: pasting a snippet switches transport;
//   * step 4 seeds from the server's CURRENT levels instead of resetting everything to `ask`;
//   * Test connect stays REQUIRED — an unreached server is never saved on an edit either, so
//     you cannot break a working server by typing a URL that answers nothing.
//
// The flow state is a HOOK so the modal's pinned footer can drive it: spec §9 makes the footer
// `flex: none` with only the body scrolling, so the step's buttons cannot live inside the body.

import { useEffect, useRef, useState, type JSX } from 'react'
import { Btn } from './primitives'
import { Icons } from './icons'
import { PermissionLadderSwitch } from './PermissionLadderSwitch'
import { VendorTrust } from './VendorTrust'
import { McpConnectionFields } from './McpConnectionFields'
import { MODAL_BODY_MIN_HEIGHT } from './DirectoryBrowser'
import { MCP_INSTALL_PRESETS, type DirectoryRow } from './catalog'
import {
  EMPTY_MCP_FORM,
  displayName,
  formFromEditTarget,
  guessServerName,
  isValidMcpUrl,
  slugify,
  uniqueServerId,
  type McpAuth,
  type McpConnectionForm,
  type McpEditTarget,
} from './mcpConfig'
import {
  createMcpServer,
  deleteMcpServer,
  fetchProbeProgress,
  listMcpServers,
  newProbeToken,
  probeMcpServer,
  testMcpServer,
  toWireConfig,
  updateMcpServer,
  type DiscoveredTool,
  type OAuthBeginBody,
  type OAuthSummary,
  type SavedAuth,
} from './mcpApi'
import { startOAuthSignIn, type OAuthSignInHandle } from './oauthSignIn'
import {
  UNKNOWN_VENDOR,
  buildToolGroups,
  changedPermissions,
  mergeTrust,
  stampGroup,
  type PermissionLevel,
  type PermissionMap,
  type ToolGroup,
  type VendorMeta,
} from './model'

export type McpStep = 'form' | 'connecting' | 'error' | 'review'

/** How often the connecting step asks the backend what the child has printed. */
export const PROBE_POLL_MS = 1000

/**
 * How long a connect has to run before we volunteer the reason it might. Late enough that a
 * normal connect (a warm `npx` finishes in ~3.6s, a remote endpoint faster) never shows it —
 * it would read as an excuse — and early enough to land well before anyone starts wondering
 * whether the thing is broken.
 */
export const PROBE_SLOW_HINT_MS = 10_000

/**
 * How long the output has to stand still before we SAY it has. A command that is downloading
 * and a command that has hung look identical in a log block that just sits there — and they
 * call for opposite responses. Long enough to sit out a quiet stretch between npm phases.
 */
export const PROBE_QUIET_HINT_S = 15

/** What the running probe has told us so far — an elapsed clock plus the child's own output. */
export interface McpProbeActivity {
  /** Seconds since Test connect was pressed, ticking once a second. */
  elapsed: number
  /** The tail of the child's stderr, oldest first. Sanitized + capped by the backend. */
  lines: string[]
  /** `elapsed` when the output last CHANGED — what makes a stall visible rather than implied. */
  lastOutputAt: number
}

const NO_ACTIVITY: McpProbeActivity = { elapsed: 0, lines: [], lastOutputAt: 0 }

/**
 * Live state of the OAuth Sign-in button + status area (RFC `2026-08-14-mcp-oauth` §4.2/§4.3),
 * read by `McpConnectionFields.tsx` and driven from here so it survives the modal staying
 * mounted through the whole popup+poll sequence.
 */
export interface McpOAuthState {
  /** `idle` — nothing tried yet, or the URL changed since the last sign-in (add flow only).
   *  `pending` — popup open, polling. `signed_in` — this is what flips `canConnect`.
   *  `error` — `begin()` failed, the poll came back `error`, the popup was blocked, or it was
   *  closed before finishing. */
  status: 'idle' | 'pending' | 'signed_in' | 'error'
  /** `begin()`'s reason code, only when the failure came from `begin()` — drives the two extra
   *  affordances below. `null` for every other failure (already a plain `error` message). */
  reason: string | null
  /** The message to show — see `mcpApi.ts`'s `OAuthBeginResult` doc comment for why this is
   *  the backend's own text rather than a second copy of the RFC §4.6 table. */
  error: string | null
  /** `dcr_rejected` only — the redirect URIs this instance asked the vendor to register. */
  redirectUris: string[]
  /** `no_registration` only — the spec's "prompt the user" escape hatch. */
  needsClientId: boolean
  clientId: string
  setClientId: (v: string) => void
  /** The connected-account summary — seeded from a saved server on an edit, refreshed by every
   *  successful Test connect. `null` before any server has ever signed in. */
  summary: OAuthSummary | null
  /** The vendor's authorize URL while `pending` (browser only) — the fallback link for a
   *  sign-in window the browser opened without handing us a handle to it (Arc, Dia). */
  authorizeUrl: string | null
  signIn: () => void
  /** Stops polling, closes the popup, and drops the staged flow client-side — the state itself
   *  is simply left to expire on its own TTL (RFC §4.3; see `oauthSignIn.ts`'s doc comment on
   *  why there is no backend "abandon" call). Only meaningful while `status === 'pending'`. */
  cancel: () => void
}

export interface McpAddFlow {
  step: McpStep
  /** `edit` reopens the flow on a saved server; `add` is the four-step add. */
  mode: 'add' | 'edit'
  /** The saved server being edited — what it is called, which secrets it holds. */
  editing: McpEditTarget | null
  form: McpConnectionForm
  setForm: (update: (f: McpConnectionForm) => McpConnectionForm) => void
  tools: string[]
  /** The ladder's headings, from each tool's backend `read_only` flag (labelling only). */
  toolGroups: ToolGroup[]
  meta: VendorMeta
  error: string
  /** What the failed probe's 401 asked for, when it disagrees with the chosen Authentication —
   *  the error step offers a one-click switch. */
  needsAuth: McpAuth | null
  /** Switch Authentication to what the server asked for and go back to the form. */
  switchAuth: (auth: McpAuth) => void
  pasteOpen: boolean
  setPasteOpen: (open: boolean) => void
  perms: PermissionMap
  setTool: (tool: string, level: PermissionLevel) => void
  setGroup: (tools: string[], level: PermissionLevel) => void
  saving: boolean
  saveError: string
  /** Live state of the in-flight probe — only meaningful on the `connecting` step. */
  activity: McpProbeActivity
  /** True once step 1 holds enough to attempt a connection. */
  canConnect: boolean
  /** True once the Server ID can no longer be customized — always true on an edit, and true on
   *  an OAuth ADD the instant sign-in mints its dormant row: that row's id is already fixed
   *  server-side, so offering "customize" there would let the field lie about what `finish()`
   *  actually saves (`oauthServerIdRef`, not whatever was typed). */
  idFixed: boolean
  /** The OAuth Sign-in button's own state — meaningful only while `form.auth === 'oauth'`. */
  oauth: McpOAuthState
  start: (entry?: DirectoryRow) => void
  runConnect: () => void
  back: () => void
  finish: () => void
  /** Save without connecting: the server is stored switched off and untested, to be tested
   *  later from its settings. Add only; `canSave` says whether the form holds enough. */
  saveDormant: () => void
  canSave: boolean
  /** Deletes the dormant, live-credentialed OAuth server row THIS session's sign-in minted, if
   *  the flow is being left without it ever having been claimed by a successful `finish()`. A
   *  no-op on an edit (`editing`'s row predates this session and must survive every dismiss path
   *  unchanged) and a no-op once `finish()` already claimed the row (see its own doc comment).
   *  Call this from EVERY path that can leave the add flow short of Add/Save — Cancel, Discard,
   *  Back to directory, the modal's header X, Escape, an outside click. See `oauth_flow.complete`
   *  (backend) for why the row exists before "Add server" is ever clicked: the vendor's redirect
   *  makes early persistence a structural necessity, unlike every other transport in this flow. */
  discardAbandonedOAuthServer: () => void
}

export function useMcpAddFlow({
  takenIds,
  editing = null,
  onSaved,
}: {
  /** Already-installed server ids, so step 3's derived id is collision-suffixed. */
  takenIds: string[]
  /** A saved server to reopen the flow ON. Read once, at mount — the host renders this hook's
   *  modal only while it is open, so there is no stale-target effect to keep in sync. */
  editing?: McpEditTarget | null
  /** `dormant`: saved switched off without a connection test (the form's Save). */
  onSaved: (name: string, toolCount: number, opts?: { dormant?: boolean }) => void
}): McpAddFlow {
  const [step, setStep] = useState<McpStep>('form')
  const [form, setFormState] = useState<McpConnectionForm>(() =>
    editing ? formFromEditTarget(editing) : EMPTY_MCP_FORM,
  )
  const [discoveredTools, setDiscoveredTools] = useState<DiscoveredTool[]>([])
  const tools = discoveredTools.map((t) => t.name)
  const [meta, setMeta] = useState<VendorMeta>(() =>
    editing
      ? {
          publisher: editing.publisher ?? null,
          verification: editing.verification,
          curated: editing.curated,
          endpoint: '',
        }
      : UNKNOWN_VENDOR,
  )
  const [error, setError] = useState('')
  const [needsAuth, setNeedsAuth] = useState<McpAuth | null>(null)
  const [pasteOpen, setPasteOpen] = useState(false)
  // The saved levels, so an edit shows what the server actually has (spec §3) and writes back
  // only what the user changed.
  const [perms, setPerms] = useState<PermissionMap>(() => editing?.perms ?? {})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [activity, setActivity] = useState<McpProbeActivity>(NO_ACTIVITY)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // The token of the probe whose completion is allowed to touch state — set when a probe
  // starts, cleared by `back()`. A probe is a synchronous backend call we cannot cancel (it
  // reaps its own child regardless of what the browser does), so its promise keeps running
  // after the user goes Back or starts a second one; this ref is what lets the eventual
  // `.then()` tell "am I still the one the user is looking at" and no-op if not, rather than
  // clobbering whatever the user did in the meantime (a fresh probe, or the form again).
  const activeTokenRef = useRef<string | null>(null)
  // The directory card's name, when the flow began from one. A server's self-reported name is
  // often a package slug (Coinbase's CLI says `coinbase`), so the card we list it under wins.
  const catalogNameRef = useRef<string | null>(null)

  // ── OAuth sign-in (RFC `2026-08-14-mcp-oauth` §4.2/§4.3) ──────────────────────
  // `connected` alone is not enough to seed `signed_in`: a refresh can fail and mark
  // `needs_reauth` while `access_token` (and so `connected`) still holds the STALE value — the
  // status area shows that stale summary alongside the plain Sign-in button, not as if the
  // connection were still good (`McpOAuthField.tsx`'s `staleSummary` branch).
  const [oauthStatus, setOauthStatus] = useState<McpOAuthState['status']>(() =>
    editing?.oauth?.connected && !editing.oauth.needs_reauth ? 'signed_in' : 'idle',
  )
  const [oauthReason, setOauthReason] = useState<string | null>(null)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const [oauthRedirectUris, setOauthRedirectUris] = useState<string[]>([])
  const [oauthNeedsClientId, setOauthNeedsClientId] = useState(false)
  const [oauthClientId, setOauthClientId] = useState('')
  const [oauthSummary, setOauthSummary] = useState<OAuthSummary | null>(editing?.oauth ?? null)
  const [oauthAuthorizeUrl, setOauthAuthorizeUrl] = useState<string | null>(null)
  // The server id a completed sign-in is bound to, for the brand-new ADD case only — an EDIT
  // always targets `editing.serverId` directly. Set once the callback's dormant row is found
  // (see `resolveNewOAuthServerId`); a ref because it drives `runConnect`/`finish` without
  // needing to be a render dependency itself (`oauthStatus` already is).
  const oauthServerIdRef = useRef<string | null>(null)
  const oauthHandleRef = useRef<OAuthSignInHandle | null>(null)

  // Review finding (final gate before merge): the vendor callback persists `oauthServerIdRef`'s
  // row server-side — with a REAL access/refresh token already in it — the instant sign-in
  // completes, entirely before the user ever clicks "Add server". Every other transport in this
  // flow persists nothing until then; abandoning an OAuth sign-in must not silently leave that
  // row (and its live credential) behind forever. `editing` is read once at mount and never
  // changes for the life of this hook instance (its own doc comment), so the `if (editing)`
  // guard below is safe to evaluate on every call.
  const discardAbandonedOAuthServer = () => {
    if (editing) return // a Reconnect's row predates this session — must survive Cancel
    const id = oauthServerIdRef.current
    if (!id) return
    oauthServerIdRef.current = null // claim it locally first — a slow delete must not re-fire
    void deleteMcpServer(id) // best-effort: already swallows its own failure, never throws
  }

  const resetOAuthState = () => {
    oauthHandleRef.current?.cancel()
    oauthHandleRef.current = null
    discardAbandonedOAuthServer()
    setOauthStatus('idle')
    setOauthReason(null)
    setOauthError(null)
    setOauthRedirectUris([])
    setOauthNeedsClientId(false)
    setOauthSummary(null)
    setOauthAuthorizeUrl(null)
  }

  /**
   * `setForm`, wrapped to reset a stale OAuth sign-in the instant the URL itself changes.
   *
   * A signed-in dormant server (the add flow's OAuth branch) was minted for the URL that was
   * typed AT sign-in time — retyping the URL afterwards would leave `oauthServerIdRef` pointing
   * at a server for an endpoint nobody is looking at anymore, so a changed URL needs a fresh
   * sign-in. Not on an EDIT: there `begin({server_id})` always targets the SAVED url regardless
   * of an in-progress (unsaved) edit to the field, so an unsaved retype has nothing to
   * invalidate yet — Save, then Reconnect, is how that endpoint change takes effect either way
   * (the store clears `oauth` on `_endpoint_changed`, same as any other auth type).
   *
   * Done HERE, synchronously in the same action that changes the url — not in a `useEffect`
   * reacting to `form.url` afterwards, which would fire the reset a render late and run
   * `resetOAuthState`'s several `setState` calls from inside an effect body (the exact pattern
   * `react-hooks/set-state-in-effect` flags). Safe to read the current-render `form` directly:
   * every caller in `McpConnectionFields.tsx` invokes this once per synchronous DOM event,
   * never twice before a re-render.
   */
  const setForm = (update: (f: McpConnectionForm) => McpConnectionForm) => {
    const next = update(form)
    if (!editing && next.url !== form.url) resetOAuthState()
    setFormState(next)
  }

  const signIn = () => {
    setOauthStatus('pending')
    setOauthError(null)
    setOauthReason(null)
    setOauthRedirectUris([])
    setOauthNeedsClientId(false)
    setOauthAuthorizeUrl(null)
    oauthHandleRef.current?.cancel()
    const clientId = oauthClientId.trim() || undefined
    const body: OAuthBeginBody = editing
      ? { server_id: editing.serverId, client_id: clientId }
      : { url: form.url.trim(), client_id: clientId }
    oauthHandleRef.current = startOAuthSignIn(
      body,
      (outcome) => {
        oauthHandleRef.current = null
        setOauthAuthorizeUrl(null)
        if (!outcome.ok) {
          setOauthStatus('error')
          setOauthReason(outcome.reason)
          setOauthError(outcome.error)
          setOauthNeedsClientId(outcome.needsClientId)
          setOauthRedirectUris(outcome.redirectUris)
          return
        }
        if (editing) {
          oauthServerIdRef.current = editing.serverId
          setOauthStatus('signed_in')
          runOAuthTestConnect(editing.serverId)
          return
        }
        // A brand-new add: the callback already persisted a dormant server for this url the
        // instant it completed (`oauth_flow.complete`) — but it never learns that id any other
        // way, so the one read left is finding it in the live list.
        const urlAtSignIn = form.url
        void (async () => {
          const servers = await listMcpServers()
          const known = new Set(takenIds)
          const candidates = servers.filter(
            (s) => s.auth_type === 'oauth' && s.url === urlAtSignIn.trim(),
          )
          const found = candidates.find((s) => !known.has(s.id)) ?? candidates[0]
          if (!found) {
            setOauthStatus('error')
            setOauthError("Signed in, but couldn't find the new server — try again.")
            return
          }
          oauthServerIdRef.current = found.id
          setOauthSummary(found.oauth)
          setOauthStatus('signed_in')
          runOAuthTestConnect(found.id)
        })()
      },
      (url) => setOauthAuthorizeUrl(url),
    )
  }

  const cancelSignIn = () => {
    oauthHandleRef.current?.cancel()
    oauthHandleRef.current = null
    setOauthStatus('idle')
    setOauthError(null)
    setOauthReason(null)
    setOauthAuthorizeUrl(null)
  }

  const stopPolling = () => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }
  // A probe outlives the modal — it is a synchronous backend call that reaps its own child
  // whatever the browser does — so closing mid-connect must stop the POLLING, and nothing
  // else. Leaving the interval behind would keep fetching against an unmounted component.
  // The OAuth controller is the one thing here that genuinely needs cancelling on unmount: an
  // open popup + a live poll interval must not keep running (or eventually call `onOutcome`)
  // against a component nobody can see anymore.
  useEffect(() => {
    return () => {
      stopPolling()
      oauthHandleRef.current?.cancel()
    }
  }, [])

  const savedAuth: SavedAuth | undefined = editing
    ? { auth_type: editing.authType, auth_header: editing.authHeader }
    : undefined

  const start = (entry?: DirectoryRow) => {
    // Disown whatever probe the LAST time through this flow left running. The host modal
    // (`DirectoryModal`) has its own "Back to directory" control that hides this flow's view
    // (`setMcpOpen(false)`) without ever calling `back()` — it doesn't own a probe token to
    // disown — so a probe abandoned that way is still "active" as far as this hook knows.
    // `start()` is the one call every fresh entry into the flow makes (a catalog tile, "Custom
    // MCP server", edit-then-reopen), so it is where that stale claim has to end: otherwise the
    // earlier probe's eventual answer could still land — mid a BRAND NEW attempt — and jump the
    // user from a form they are filling in straight to review with someone else's discovered
    // tools.
    stopPolling()
    activeTokenRef.current = null
    resetOAuthState()
    setOauthClientId('')
    const preset = entry ? MCP_INSTALL_PRESETS[entry.id] : undefined
    // The generic slot names no server — the server's own report has to do there.
    catalogNameRef.current = entry && entry.id !== 'mcp-custom' ? entry.name : null
    setFormState({
      ...EMPTY_MCP_FORM,
      transport: preset?.transport ?? 'remote',
      url: preset?.url ?? '',
      command: preset?.command ?? '',
      args: preset?.args ?? '',
      env: preset?.env ?? '',
      // A preset states the credential its server actually takes. No preset (a bare "Custom
      // MCP server") means no information at all, so `none` is the honest default — the
      // probe's own real response, not a guess, is what actually reveals an auth need.
      auth: preset?.auth ?? 'none',
    })
    // A catalog entry supplies the publisher we vouch for; a custom server claims no author,
    // and must never borrow ours (spec §5) — hence `unknown`, not `unverified`.
    setMeta({
      publisher: entry?.publisher ?? null,
      verification: entry?.verification ?? 'unknown',
      curated: !!entry,
      endpoint: '',
      // RFC §4.6's `no_metadata` failure needs somewhere to send the user instead — the
      // catalog is the only source for this at sign-in time, since a `no_metadata` failure
      // means the probe/Test connect step this would otherwise come from never runs.
      homepageUrl: entry?.homepageUrl,
    })
    setDiscoveredTools([])
    setPerms({})
    setError('')
    setSaveError('')
    setPasteOpen(false)
    // ALWAYS step 1, even for a catalog entry with a preset: Test connect is the gate, and a
    // preset that still needs an API key would fail a silent auto-probe with a confusing error.
    setStep('form')
  }

  const hasKey = !!form.key.trim() || !!editing?.hasToken
  const urlValid = !!editing || isValidMcpUrl(form.url)
  const canConnect =
    form.auth === 'oauth'
      ? oauthStatus !== 'pending' && urlValid
      : form.transport === 'remote'
        ? urlValid && (form.auth !== 'key' || hasKey)
        : !!form.command.trim()

  const idFixed = !!editing || (form.auth === 'oauth' && oauthStatus === 'signed_in')

  /**
   * OAuth's own probe, chained straight in after sign-in succeeds (RFC §4.2: "the add flow
   * then runs its normal probe against the now-credentialed config"). The id-less probe
   * (`probeMcpServer`) has no field to carry a
   * server-held OAuth token, so this reaches the id-based `POST /api/mcp-clients/{id}/test`
   * instead — the server row already exists by this point either way: an EDIT's row always did,
   * and an ADD's row was minted DORMANT the instant sign-in completed (see `signIn` above).
   * Feeds the SAME `connecting`/`error`/`review` steps as the ordinary probe, just from a
   * differently-shaped result (`PublicMcpServer`, not `McpProbeResult`).
   */
  const runOAuthTestConnect = (serverId: string) => {
    setStep('connecting')
    const token = newProbeToken()
    activeTokenRef.current = token
    const startedAt = Date.now()
    setActivity(NO_ACTIVITY)
    stopPolling()
    // No progress channel exists for this path (only the id-less probe accepts one) — just the
    // elapsed clock, so the connecting step's timer still moves instead of sitting at 0:00.
    pollRef.current = setInterval(() => {
      setActivity((a) => ({ ...a, elapsed: Math.floor((Date.now() - startedAt) / 1000) }))
    }, PROBE_POLL_MS)
    void (async () => {
      const result = await testMcpServer(serverId)
      if (activeTokenRef.current !== token) return
      stopPolling()
      if (!result || result.last_status !== 'ok') {
        setError(result?.last_error || 'Could not reach the server.')
        setStep('error')
        return
      }
      setOauthSummary(result.oauth)
      const discovered = result.discovered_tools.map((t) => t.name)
      setDiscoveredTools(result.discovered_tools)
      setPerms((saved) =>
        Object.fromEntries(
          discovered.map((t) => [t, saved[t] ?? ('needs_approval' as PermissionLevel)]),
        ),
      )
      setFormState((f) =>
        editing
          ? { ...f, name: f.name.trim() || result.name }
          : { ...f, name: catalogNameRef.current ?? displayName(result.name), serverId: result.id },
      )
      setMeta((m) =>
        mergeTrust(m, {
          publisher: result.publisher,
          verification: result.verification,
          homepage_url: result.homepage_url,
          repo_url: result.repo_url,
          endpoint: result.endpoint,
        }),
      )
      setStep('review')
    })()
  }

  const runConnect = () => {
    setError('')
    setNeedsAuth(null)
    if (form.auth === 'oauth') {
      if (oauthStatus !== 'signed_in') {
        signIn()
        return
      }
      const serverId = editing ? editing.serverId : oauthServerIdRef.current
      if (!serverId) {
        // Defensive only — reached only if `signed_in` landed without a resolved id.
        setError('Sign in above, then test the connection.')
        setStep('error')
        return
      }
      runOAuthTestConnect(serverId)
      return
    }
    setStep('connecting')
    // The connecting step used to be a spinner and a sentence, for a wait that can genuinely
    // run for minutes on a first install: the package downloads BEFORE the server it should
    // spawn exists. The token names a progress channel the backend publishes the child's own
    // output into; the elapsed clock beside it is what keeps the display honest when the
    // child says nothing at all — "silent for 90s" is a different fact from "still fetching".
    const token = newProbeToken()
    // Claim ownership BEFORE the first await: `back()` (or a second `runConnect()`) can only
    // ever run on the main thread between our synchronous statements here and the eventual
    // `.then()`, never inside this call, so there is no window where two probes both think
    // they own the ref.
    activeTokenRef.current = token
    const startedAt = Date.now()
    setActivity(NO_ACTIVITY)
    stopPolling()
    pollRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000)
      setActivity((a) => ({ ...a, elapsed }))
      void fetchProbeProgress(token).then((snap) => {
        // A failed poll keeps the lines we already have: losing the tail on one network blip
        // would look like the child fell silent, which is exactly the distinction at stake.
        if (!snap) return
        if (activeTokenRef.current !== token) return // superseded — the interval itself is
        // cleared by whoever superseded us, but a request already in flight can still land.
        setActivity((a) => {
          const changed =
            snap.lines.length !== a.lines.length || snap.lines.at(-1) !== a.lines.at(-1)
          return { ...a, lines: snap.lines, lastOutputAt: changed ? a.elapsed : a.lastOutputAt }
        })
      })
    }, PROBE_POLL_MS)
    void (async () => {
      const result = await probeMcpServer(toWireConfig(form, savedAuth), token)
      // STALE — the user went Back, or started a second probe, while this one was still
      // running server-side. Neither `stopPolling()` (which would tear down the CURRENT
      // probe's interval, not this abandoned one's) nor any state update below may run: this
      // result belongs to a screen nobody is looking at anymore.
      if (activeTokenRef.current !== token) return
      stopPolling()
      if (!result.ok) {
        setError(result.error || 'Could not reach the server.')
        const asked = result.needs_auth ?? null
        setNeedsAuth(asked && asked !== form.auth ? asked : null)
        setStep('error')
        return
      }
      const discovered = result.tools.map((t) => t.name)
      // `suggested_name` is already `serverInfo.name` with the backend's own endpoint-derived
      // fallback; `guessServerName` only covers a server that reported nothing at all.
      const name =
        catalogNameRef.current ?? displayName(result.suggested_name.trim() || guessServerName(form))
      setDiscoveredTools(result.tools)
      // Every NEWLY discovered tool starts at Needs approval — no tiering, no exceptions
      // (spec §4). On an edit the tools already permissioned keep the level they have: a
      // re-test is not a reason to silently re-open a tool the user blocked.
      setPerms((saved) =>
        Object.fromEntries(
          discovered.map((t) => [t, saved[t] ?? ('needs_approval' as PermissionLevel)]),
        ),
      )
      setFormState((f) =>
        // A saved server's name and id are the user's, not the server's to re-assert on every
        // re-test; only fill a name in if the edit left it empty.
        editing
          ? { ...f, name: f.name.trim() || name }
          : { ...f, name, serverId: result.suggested_id || uniqueServerId(name, takenIds) },
      )
      setMeta((m) => mergeTrust(m, result.trust))
      setStep('review')
    })()
  }

  const finish = () => {
    setSaveError('')
    setSaving(true)
    void (async () => {
      const config = toWireConfig(form, savedAuth)
      // The OAuth add branch's row already exists — the callback minted it DORMANT the instant
      // sign-in completed (RFC §4.2) — so finishing it is a PATCH (name + arm `enabled`), never
      // a `createMcpServer` POST, which would 409 against that same id.
      const oauthCreatedId = !editing && form.auth === 'oauth' ? oauthServerIdRef.current : null
      const res = editing
        ? await updateMcpServer({
            id: editing.serverId,
            name: form.name,
            config,
            // An untouched box keeps every saved value; a rewritten one replaces the whole set.
            replaceEnv: !!form.env.trim(),
            tool_permissions: changedPermissions(editing.perms, perms),
          })
        : oauthCreatedId
          ? await updateMcpServer({
              id: oauthCreatedId,
              name: form.name,
              config,
              replaceEnv: false,
              tool_permissions: perms,
              enable: true,
            })
          : await createMcpServer({
              id: form.serverId,
              name: form.name,
              config,
              tool_permissions: perms,
            })
      setSaving(false)
      if (!res.ok) {
        setSaveError(res.error)
        return
      }
      // The row is now CLAIMED — a close that follows `onSaved` (every caller's own success
      // path routes there) must not read this as an abandoned sign-in and delete what was just
      // saved. `oauthCreatedId` was already snapshotted above; only null the live ref.
      if (oauthCreatedId) oauthServerIdRef.current = null
      onSaved(form.name || 'MCP server', tools.length)
    })()
  }

  // Save without connecting (owner, 2026-10-31): a server whose test can't pass yet — real keys
  // still to come, a build still to finish — no longer has to be retyped later. Stored switched
  // off, so nothing is spawned or reached; enabling it from its settings runs the test then.
  const canSave =
    !editing && (form.transport === 'remote' ? isValidMcpUrl(form.url) : !!form.command.trim())
  const saveDormant = () => {
    if (!canSave || saving) return
    setSaving(true)
    void (async () => {
      const name =
        form.name.trim() ||
        catalogNameRef.current ||
        displayName(guessServerName(form)) ||
        'MCP server'
      // An OAuth sign-in already minted the row, dormant; saving names it rather than adding a
      // second one under a new id.
      const oauthCreatedId = form.auth === 'oauth' ? oauthServerIdRef.current : null
      const res = oauthCreatedId
        ? await updateMcpServer({
            id: oauthCreatedId,
            name,
            config: toWireConfig(form, savedAuth),
            replaceEnv: false,
            tool_permissions: {},
          })
        : await createMcpServer({
            id: uniqueServerId(name, takenIds),
            name,
            config: toWireConfig(form, savedAuth),
            tool_permissions: {},
            enabled: false,
          })
      setSaving(false)
      if (!res.ok) {
        setError(res.error)
        setStep('error')
        return
      }
      if (oauthCreatedId) oauthServerIdRef.current = null
      onSaved(name, 0, { dormant: true })
    })()
  }

  return {
    step,
    mode: editing ? 'edit' : 'add',
    editing,
    form,
    setForm,
    tools,
    toolGroups: buildToolGroups(discoveredTools),
    meta,
    error,
    needsAuth,
    switchAuth: (auth) => {
      setFormState((f) => ({ ...f, auth }))
      setNeedsAuth(null)
      setError('')
      setStep('form')
    },
    pasteOpen,
    setPasteOpen,
    perms,
    setTool: (tool, level) => setPerms((p) => ({ ...p, [tool]: level })),
    setGroup: (groupTools, level) => setPerms((p) => stampGroup(p, groupTools, level)),
    saving,
    saveError,
    activity,
    canConnect,
    idFixed,
    oauth: {
      status: oauthStatus,
      reason: oauthReason,
      error: oauthError,
      redirectUris: oauthRedirectUris,
      needsClientId: oauthNeedsClientId,
      clientId: oauthClientId,
      setClientId: setOauthClientId,
      summary: oauthSummary,
      authorizeUrl: oauthAuthorizeUrl,
      signIn,
      cancel: cancelSignIn,
    },
    start,
    runConnect,
    back: () => {
      stopPolling()
      // Disown the in-flight probe (if any): it keeps running server-side — it is a
      // synchronous backend call that reaps its own child regardless — but its eventual
      // result must land as a no-op now that the user is back on the form.
      activeTokenRef.current = null
      setStep('form')
    },
    finish,
    saveDormant,
    canSave,
    discardAbandonedOAuthServer,
  }
}

// ── the body ────────────────────────────────────────────────────────────────

/** `m:ss`, so a wait reads as a duration rather than a growing number of seconds. */
function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function connectingBaseline(flow: McpAddFlow): {
  lines: string[]
  quietFor: number
  stalled: boolean
  slow: boolean
} {
  const { elapsed, lines, lastOutputAt } = flow.activity
  const slow = elapsed * 1000 >= PROBE_SLOW_HINT_MS
  // A downloading command and a hung one look identical in a log block that just sits there.
  const quietFor = lines.length > 0 ? elapsed - lastOutputAt : 0
  const stalled = quietFor >= PROBE_QUIET_HINT_S
  return { lines, quietFor, stalled, slow }
}

/**
 * The connecting step: what is being tested, how long it has taken, and what the command
 * itself is saying.
 *
 * WHY IT IS MORE THAN A SPINNER. A first `npx -y <package>` install downloads the package
 * before the MCP server it should spawn exists — a real vendor's is 264 MB and took 61s cold
 * against 3.6s warm — so the honest states here are "still fetching" and "stalled", and a
 * spinner alone says neither. Three things separate them: the elapsed clock (which moves even
 * when the child is silent), the child's own output (which stops when it stalls), and, once
 * the wait is long enough to be worth explaining, the reason a first connect is slow.
 *
 * THE BASELINE ITSELF (before any of those three has anything to say) used to be just the
 * header line above a blank box — honest, but for the first several seconds of a cold `npx`
 * install it read as broken rather than "nothing to report yet". The sub-line below the header
 * is an always-visible, calmer preview of the `slow` explanation further down — so the one case
 * this whole feature was built for (a silent first-time install) never looks frozen even in
 * the seconds before the elapsed clock earns the right to say so explicitly.
 *
 * THE HERO'S POSITION IS FIXED, not centered in the available height — tried centering the
 * bare (nothing-has-happened-yet) state and dropping it the instant real detail landed (2026-
 * 08-18), and the result was worse than what it replaced: the whole hero visibly JUMPED from
 * mid-box to the top the moment a log line, `stalled`, or `slow` appeared (owner review, same
 * day, on the live app — "why the fuck sometime it's shifted to top like this"). Anchored near
 * the top with generous padding instead: identical position from the first paint through every
 * later state, so nothing about it ever moves except the spinner itself and the content that
 * grows below it.
 */
function McpConnectingState({ flow }: { flow: McpAddFlow }): JSX.Element {
  const local = flow.form.transport === 'local'
  // Keyed off the TRANSPORT, not "whichever field is non-empty": the form keeps both, so a
  // user who typed a URL and then switched to Local was told we were testing the URL.
  const target = (local ? flow.form.command : flow.form.url) || 'server'
  const { elapsed } = flow.activity
  const { lines, quietFor, stalled, slow } = connectingBaseline(flow)
  return (
    <div className="cn-connecting">
      <div className="cn-connecting-hero">
        <span className="cn-spinner-halo">
          <span className="cn-spinner xl" />
        </span>
        <div className="cn-connecting-status">
          Testing connection to {target} and listing tools…
        </div>
        {/* `role=timer` so a screen reader treats the clock as a live region rather than
            announcing every tick as new content. */}
        <span role="timer" aria-label={`Elapsed ${clock(elapsed)}`} className="cn-connecting-clock">
          {clock(elapsed)}
        </span>
      </div>

      {!slow && (
        // A calmer, ALWAYS-visible preview of the `slow` explanation below — so a cold install
        // never reads as frozen for the several seconds before `slow` earns the right to say so
        // more urgently. Swapped for that explanation, not stacked with it, once slow fires.
        <span className="rd-help cn-connecting-note">
          {local
            ? 'A first connect can take a few minutes while the package installs — every ' +
              'connect after that is fast.'
            : 'This usually takes just a few seconds.'}
        </span>
      )}

      {lines.length > 0 && (
        // The child's own words, verbatim but bounded — the backend keeps a rolling tail and
        // sanitizes every line, so nothing here can forge a fence or run away in length.
        <div className="cn-probe-log" aria-live="polite" aria-label="Command output">
          {lines.map((line, i) => (
            <div key={`${i}-${line}`} className="cn-probe-log-line">
              {line}
            </div>
          ))}
        </div>
      )}

      {stalled && (
        // Stated, not implied: without this the log block looks the same whether the command
        // is mid-download or wedged, and only one of those is worth waiting out.
        <span className="rd-help cn-connecting-note">No new output for {clock(quietFor)}.</span>
      )}

      {slow && (
        <span className="rd-help cn-connecting-note">
          {/* Only a LOCAL connect pays the install cost, so only a local connect gets told
              about it — a remote endpoint that is slow is just slow, and explaining npm to
              someone waiting on an HTTPS handshake is noise dressed as help. */}
          {local
            ? 'Still working. The first connect to a local server downloads the package before ' +
              'the server starts, which can take a few minutes on a large one — later connects ' +
              'take seconds.'
            : 'Still working. The endpoint has not answered yet.'}
          {local && lines.length === 0 ? ' This command has not printed anything yet.' : null}
        </span>
      )}
    </div>
  )
}

export function McpAddFlowBody({ flow }: { flow: McpAddFlow }): JSX.Element {
  const { form, setForm } = flow
  // Only the connecting step claims the body's leftover height (`flex: 1`) — `.cn-connecting`
  // inside it top-anchors its hero and lets later content (log, stalled/slow text) grow
  // downward into that room (see `McpConnectingState`'s own doc comment for why it is anchored
  // rather than centered). The other three steps keep their ordinary top-aligned, content-sized
  // box: stretching a short form or a long review list to fill a now-fixed-height `.rd-modal.xl`
  // would just add dead space of its own, or center a scrolling list in a way nothing here asks
  // for.
  const connecting = flow.step === 'connecting'
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
        minHeight: MODAL_BODY_MIN_HEIGHT,
        ...(connecting ? { flex: 1 } : {}),
      }}
    >
      {flow.step === 'form' && <McpConnectionFields flow={flow} />}

      {flow.step === 'connecting' && <McpConnectingState flow={flow} />}

      {flow.step === 'error' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div
            className="rd-help"
            role="alert"
            style={{
              fontSize: 13,
              color: 'var(--bad)',
              display: 'flex',
              alignItems: 'flex-start',
              gap: 8,
              background: 'color-mix(in srgb, var(--bad) 9%, var(--surface))',
              border: '1px solid color-mix(in srgb, var(--bad) 30%, var(--line))',
              borderRadius: 'var(--radius-lg)',
              padding: '11px 13px',
            }}
          >
            <Icons.warn /> {flow.error || 'Could not reach the server.'}
          </div>
          {flow.needsAuth ? (
            <div className="cn-authhint" role="status">
              <span>
                {flow.needsAuth === 'oauth'
                  ? 'This server needs you to sign in. Switch Authentication to OAuth, then connect again.'
                  : 'This server needs a key. Switch Authentication to API key and paste it.'}
              </span>
              <Btn size="xs" variant="primary" onClick={() => flow.switchAuth(flow.needsAuth!)}>
                {flow.needsAuth === 'oauth' ? 'Switch to OAuth' : 'Use an API key'}
              </Btn>
            </div>
          ) : null}
          <span className="rd-help">
            {flow.canSave
              ? 'Fix the URL, command, or credentials and test again — or Save to keep it, switched off, and test it later from its settings.'
              : 'Fix the URL, command, or credentials above and test again.'}
          </span>
        </div>
      )}

      {flow.step === 'review' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div
            style={{
              fontSize: 12.5,
              color: 'var(--good)',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icons.check /> Reachable —{' '}
            {flow.tools.length === 1 ? '1 tool' : `${flow.tools.length} tools`} found
          </div>

          <div className="rd-field full">
            <span className="rd-label" id="mcp-name-label">
              Name
            </span>
            <input
              className="rd-input"
              aria-labelledby="mcp-name-label"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
            {flow.idFixed ? (
              // FIXED — either an existing server (id is the tool namespace
              // `mcp__<id>__<tool>` and the key of every permission row, so renaming it would
              // orphan the lot — the store refuses to patch it) or a brand-new OAuth add whose
              // dormant row already has a server-assigned id the instant sign-in completed
              // (`flow.oauth`'s doc comment) — offering "customize" there would let this field
              // show an id `finish()` does not actually save under.
              <span className="rd-help">
                Server ID{' '}
                <code className="rd-code" style={{ padding: '2px 6px', display: 'inline' }}>
                  {form.serverId}
                </code>{' '}
                • fixed — it namespaces this server&apos;s tools and the permissions you set for
                them.
              </span>
            ) : form.serverIdCustom ? (
              <div style={{ marginTop: 6 }}>
                <span className="rd-label" id="mcp-id-label">
                  Server ID
                </span>
                <input
                  className="rd-input"
                  style={{ marginTop: 6 }}
                  aria-labelledby="mcp-id-label"
                  value={form.serverId}
                  onChange={(e) => setForm((f) => ({ ...f, serverId: slugify(e.target.value) }))}
                />
              </div>
            ) : (
              // Collapsed and automatic — never required, never on equal footing with Name.
              <span className="rd-help">
                Server ID{' '}
                <code className="rd-code" style={{ padding: '2px 6px', display: 'inline' }}>
                  {form.serverId}
                </code>{' '}
                •{' '}
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault()
                    setForm((f) => ({ ...f, serverIdCustom: true }))
                  }}
                  style={{ color: 'var(--accent)' }}
                >
                  customize
                </a>
              </span>
            )}
          </div>

          {/* Who published the thing you are about to trust, BEFORE you commit (spec §5's
              second of three placements). The publisher and endpoint are the probe's own
              report — the server's `initialize` self-report for one, the backend's verbatim
              rendering of the config for the other — with the curated catalog winning where
              it has an answer. `developer` is left UNDEFINED for an unknown publisher: a
              "Unknown publisher" sentinel string would make the panel render the
              known-publisher note, which is the opposite of what §5 asks for. */}
          <VendorTrust
            developer={flow.meta.publisher ?? undefined}
            verification={flow.meta.verification}
            url={
              flow.meta.endpoint ||
              (form.transport === 'remote'
                ? form.url
                : [form.command, form.args].filter(Boolean).join(' '))
            }
          />

          <div>
            <div className="cn-sublabel" style={{ marginBottom: 4 }}>
              Tool permissions
            </div>
            <div className="rd-help" style={{ marginBottom: 8, fontSize: 12.5 }}>
              Choose when Lucius is allowed to use these tools.
            </div>
            <PermissionLadderSwitch
              groups={flow.toolGroups}
              perms={flow.perms}
              onSetTool={flow.setTool}
              onSetGroup={flow.setGroup}
            />
          </div>

          {flow.saveError ? (
            <div className="rd-help" role="alert" style={{ color: 'var(--bad)', fontSize: 12.5 }}>
              {flow.saveError}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

/** The step's footer buttons. Rendered by the modal so the footer stays pinned (spec §9). */
export function McpAddFlowFooter({
  flow,
  onCancel,
}: {
  flow: McpAddFlow
  onCancel: () => void
}): JSX.Element {
  if (flow.step === 'connecting') return <Btn disabled>Testing…</Btn>
  // Save keeps what was typed without a passing test (stored switched off) — on the form, and on
  // the failed test too, where real keys still to come is the usual reason it failed.
  const save = flow.canSave ? (
    <Btn disabled={flow.saving} onClick={flow.saveDormant}>
      {flow.saving ? 'Saving…' : 'Save'}
    </Btn>
  ) : null
  if (flow.step === 'error') {
    return (
      <>
        <Btn onClick={flow.back}>Back</Btn>
        {save}
        <Btn variant="primary" onClick={flow.runConnect}>
          Retry
        </Btn>
      </>
    )
  }
  if (flow.step === 'review') {
    const editing = flow.mode === 'edit'
    return (
      <>
        <Btn onClick={onCancel}>Discard</Btn>
        <Btn
          variant="primary"
          disabled={flow.saving || !flow.form.name.trim()}
          onClick={flow.finish}
        >
          {flow.saving
            ? editing
              ? 'Saving…'
              : 'Adding…'
            : editing
              ? 'Save changes'
              : 'Add server'}
        </Btn>
      </>
    )
  }
  return (
    <>
      <Btn onClick={onCancel}>Cancel</Btn>
      {save}
      <Btn variant="primary" disabled={!flow.canConnect} onClick={flow.runConnect}>
        {flow.form.auth === 'oauth' && flow.oauth.status === 'pending' ? 'Signing in…' : 'Connect'}
      </Btn>
    </>
  )
}
