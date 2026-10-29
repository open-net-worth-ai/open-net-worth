// The connector detail page (spec §3) — ported from the prototype's `Detail`.
//
// ONE page per connector, identical in shape for all four families: the previous design
// gave MCP servers a tool list and everything else a blunt on/off switch. Here a channel,
// a bank feed, a web provider and a third-party server get the same instrument.
//
// This is also the ONLY place a connector can be disabled — the list row deliberately
// carries no toggle, because the consequences are only visible here — AND the only place
// it is configured, which the page hosts as a SECOND VIEW of its own modal rather than a
// dialog stacked on top of it: the title becomes `‹ <name>`, the body becomes the
// provider's credential form, the footer becomes that form's Save. That is exactly the shape
// `DirectoryModal` uses for its three views, so spec §9's pinned-header/footer,
// body-only-scroll geometry is applied once instead of nested, and a single Escape
// closes a single thing.
//
// TWO DELIBERATE EXTENSIONS to the prototype's detail page, both forced by the fact that
// native connectors now have real credential forms (the prototype's did not):
//
//  1. Connect, on a connector that holds NO credentials yet, opens its form instead of
//     PATCHing `enabled`. Flipping the flag on an unconfigured connector produces a row
//     that claims "Connected" over a connector that cannot do anything. The same predicate
//     (`setupPending`) also serves a caller that has ALREADY declared that intent — the
//     directory's Add — which seeds the config view directly via `configureOnOpen` rather
//     than asking for the same declaration a second time.
//  2. The kebab's `Edit configuration` (spec §3 called it `Edit connection`, MCP-only; renamed
//     2026-08-18 — "connection" clashes with Pause/Resume/Connect, which already own whether
//     you're *connected*, and this item edits saved credentials, not connectivity) — is
//     offered for any connector with a form. Without it there is no way to rotate a saved API
//     key on a connector that is currently connected, since its primary action reads Pause
//     (and pausing, deliberately, does not touch the saved credentials it would need to
//     rotate). The spec scoped it to MCP because in the prototype only MCP had a setup drawer;
//     the item's own description ("reopens the setup drawer pre-filled") is what a native
//     connector needs here, word for word.

import { useEffect, useState, type JSX, type ReactNode } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { templateKeyForConnector } from '../automations/automationTemplates'
import { BackTitle, Badge, Btn, Kebab, Modal, Mono, type KebabItem } from './primitives'
import { useConnectorSaveHost } from './formSave'
import { PermissionLadder } from './PermissionLadder'
import { ExternalLink, FIRST_PARTY_PUBLISHER, FIRST_PARTY_URL, VendorTrust } from './VendorTrust'
import {
  stampGroup,
  toolPermission,
  type ConnectorDetailEntry,
  type PermissionLevel,
  type PermissionMap,
} from './model'

export interface ConnectorDetailProps {
  entry: ConnectorDetailEntry
  /** Back to the list — the title IS this control (spec §3). */
  onClose: () => void
  /** The primary action's write: Pause (after confirmation) when connected, Resume/Connect
   *  when not. Optionally returns a promise so the confirmation view can wait for it before
   *  closing, exactly like `onRemove`. */
  onSetEnabled: (enabled: boolean) => void | Promise<void>
  /** Replaces the primary action while the connector is erroring. For an OAuth MCP server this
   *  is a fresh popup + poll sign-in (RFC `2026-08-14-mcp-oauth` §4.7), not an instant call. */
  onReconnect: () => void
  /** True while a Reconnect click is waiting on an OAuth sign-in — the button says so instead
   *  of sitting there with no feedback while the popup runs. */
  reconnecting?: boolean
  onViewDetails: () => void
  /** MCP only — reopens the setup drawer pre-filled. A connector with a `configForm`
   *  reopens THAT instead, and never reaches this. */
  onEditConnection: () => void
  onRefreshTools: () => void
  /** Remove for real. Called only AFTER the confirmation step when `removable`; for a
   *  connector that cannot be removed it is called straight from the kebab, to say so. */
  onRemove: () => void | Promise<void>
  /** Whether removal is a real operation here. An MCP server can be deleted; a built-in
   *  connector has no removal concept at all — disconnect is the real operation — so it gets
   *  no confirmation step, because there is nothing to confirm. */
  removable?: boolean
  /** True for a connector that is not `pausable` (Yahoo Finance, CoinGecko today) — this
   *  app runs it unconditionally, so there is no Pause/Resume/Connect action to offer at
   *  all, not even a disabled one. The status badge in the identity header (real health,
   *  not a toggle) is the whole answer for "is this working". The kebab is UNCHANGED: Edit
   *  configuration still makes sense (CoinGecko has a real key to change); Remove does not
   *  — `removable` is always false for an always-on connector regardless of whether it
   *  holds a credential, and the kebab's Remove item calls `onRemove` straight through,
   *  surfacing the backend's honest, named refusal rather than a confirmation step for an
   *  action that can never succeed. */
  alwaysOn?: boolean
  /** Seeds the Lucius composer with a suggested prompt. */
  onUsePrompt: (prompt: string) => void
  /** Persist one level. Resolving `false` means the write did NOT land, and the ladder rolls
   *  the optimistic change back — a grid showing "Blocked" over a row the backend never
   *  stored is a tool the operator believes is off while Lucius is still offered it. */
  onSetToolPermission: (tool: string, level: PermissionLevel) => void | Promise<boolean>
  onSetGroupPermission: (tools: string[], level: PermissionLevel) => void | Promise<boolean>
  /** This connector's credential form. Absent → it has none (MCP, or `.env`-managed), so
   *  Connect falls back to arming the connector and the kebab omits Edit configuration. */
  configForm?: ReactNode
  /** Open ON the configuration form instead of the page itself — for a caller that has
   *  already declared intent to set this connector up (the directory's Add). Making it tap
   *  Connect afterwards is the same declaration twice.
   *
   *  A SEED for the first view, `defaultValue`-style — not a controlled one. The page owns
   *  which of its views is showing from then on, so a refetch landing while the form is open
   *  can never close it under the user, and leaving the form is a plain back. Read once per
   *  mount; the tab opens this page fresh for every focus request.
   *
   *  Ignored when there is nothing to set up — see `setupPending`. */
  configureOnOpen?: boolean
  /** Fired when the config view is left, so the caller can refetch — a saved credential
   *  changes `status`, and the page behind is showing it. */
  onConfigClosed?: () => void
  /** Fired ONLY when a save actually landed — distinct from `onConfigClosed`, which also
   *  fires on a plain back-out with nothing written. A failed save already shows its own
   *  inline error and stays open; this is the ONLY positive confirmation a successful one
   *  gets, so the operator isn't left inferring "it worked" from the modal merely closing. */
  onSaved?: () => void
}

export function ConnectorDetail({
  entry,
  onClose,
  onSetEnabled,
  onReconnect,
  reconnecting = false,
  onViewDetails,
  onEditConnection,
  onRefreshTools,
  onRemove,
  onUsePrompt,
  onSetToolPermission,
  onSetGroupPermission,
  removable = false,
  alwaysOn = false,
  configForm,
  configureOnOpen = false,
  onConfigClosed,
  onSaved,
}: ConnectorDetailProps): JSX.Element {
  // Optimistic mirror of the saved levels: the ladder must respond to a click without
  // waiting for a round trip. Reseeded whenever a different connector is shown, and
  // whenever the server's own view of the levels changes underneath us.
  const [perms, setPerms] = useState<PermissionMap>(entry.perms)
  useEffect(() => setPerms(entry.perms), [entry.id, entry.perms])

  /**
   * Show the new level immediately — then ROLL IT BACK if the write did not land.
   *
   * Optimism alone was a fail-open bug: the grid rendered "Blocked" while the backend held no
   * row at all, so the operator read a tool as off while it sat at `ask` and was still being
   * offered to Lucius. Only the tools in THIS change are reverted, so a slower failing write
   * cannot stomp a later successful one on a different tool.
   */
  function applyLevel(tools: string[], level: PermissionLevel, saved: void | Promise<boolean>) {
    const before = perms
    setPerms((p) => stampGroup(p, tools, level))
    void Promise.resolve(saved).then((ok) => {
      if (ok === false) {
        setPerms((p) => {
          const next = { ...p }
          for (const t of tools) next[t] = toolPermission(before, t)
          return next
        })
      }
    })
  }

  // What "set this connector up" means HERE, and the one predicate that decides whether the
  // form is the right destination — for Connect and for `configureOnOpen` alike. No form
  // (MCP, or `.env`-managed, whose per-provider endpoints refuse the write) means there is
  // nothing to open; credentials already held means there is nothing to set up.
  const setupPending = Boolean(configForm) && !entry.configured

  // Which of the page's views is showing. Held as the connector id being configured
  // rather than a boolean, so opening a DIFFERENT connector lands on its detail view with
  // no reset effect to run — the config form can never carry over to the connector after it.
  //
  // Seeded at mount rather than from an effect: an effect runs after paint, so a caller that
  // asked for the form would get one frame of the detail page it deliberately routed past.
  // A connector that is erroring (`attention`) and holds a form is exactly the "Reconnect"
  // row action's target — its actual fix lives in its credentials, so this seed also fires
  // for `configureOnOpen` there, alongside the already-unconfigured `setupPending` case.
  const [configuringId, setConfiguringId] = useState<string | null>(
    configureOnOpen && (setupPending || (Boolean(configForm) && entry.attention)) ? entry.id : null,
  )
  const configuring = configuringId === entry.id
  // The config view's footer is the credential form's OWN Save (see ./formSave) — there is
  // one primary action per screen, and it writes rather than dismissing.
  const formSave = useConnectorSaveHost()
  const navigate = useNavigate()
  const automateKey = templateKeyForConnector(entry.kind)
  const openTemplate = (key: string) =>
    void navigate({ to: '/automations', search: { template: key } as never } as never)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [confirmingPause, setConfirmingPause] = useState(false)
  const [pausing, setPausing] = useState(false)

  /** Step back to the detail view — bound to the two EXPLICIT "I'm finished editing" actions
   *  (the `‹ <name>` back-arrow, and Save/Done in the config view's footer, including
   *  `saveConfig`'s own call below on a successful write) — never to the config Modal's own
   *  `onClose` (owner review, 2026-08-22). That prop fires on ✕, Escape AND a backdrop click
   *  alike, and wiring it to this made all three quietly mean "go back one level" — surprising,
   *  since a backdrop tap/Escape conventionally means "get me out of this dialog", and every
   *  OTHER view of this same Modal (the detail page itself) already treats those three as a
   *  full close. The config view's `onClose` is `onClose` (this component's own prop) instead,
   *  so it matches; the back-arrow stays the one dedicated "just step back" control. */
  function leaveConfig() {
    setConfiguringId(null)
    onConfigClosed?.()
  }

  /** Save — but ONLY if the write landed. A failed save leaves the form up with its own error
   *  showing, rather than closing over a change the server never took.
   *
   *  Stays ON the config view on success (owner review, 2026-08-22, all connectors — was
   *  `leaveConfig()`, auto-navigating back to the detail page). That auto-close was itself the
   *  bug behind Telegram's pairing flow: switching to Pairing + Save immediately bounced you
   *  off the one screen that shows "Pending approvals", the exact moment you'd want to watch
   *  it. Save now just saves; `onConfigClosed?.()` still fires so the caller refetches (the
   *  DETAIL page you'll see once you actually leave needs fresh status), but leaving itself is
   *  the `‹ <name>` back-arrow's job — same as every other explicit "I'm done" action, and
   *  consistent with ✕/Escape/backdrop now fully closing the page (see `Modal`'s `onClose`
   *  above) rather than also meaning "step back". */
  function saveConfig() {
    void formSave.run().then((ok) => {
      if (ok) {
        onSaved?.()
        onConfigClosed?.()
      }
    })
  }

  function remove() {
    setRemoving(true)
    void Promise.resolve(onRemove()).finally(() => {
      // The caller closes this page on success; on failure it toasts and leaves it open, so
      // the confirmation must give way rather than sit there mid-flight.
      setRemoving(false)
      setConfirmingRemove(false)
    })
  }

  /** Pause is reversible and low-stakes compared to Remove, but still a real interruption
   *  (scheduled syncing/notifying stops), so it asks first — same confirm-then-write shape
   *  as `remove()` above, closing the confirmation once the write settles either way. */
  function pause() {
    setPausing(true)
    void Promise.resolve(onSetEnabled(false)).finally(() => {
      setPausing(false)
      setConfirmingPause(false)
    })
  }

  const kebabItems: KebabItem[] = [
    { label: 'View details', onClick: onViewDetails },
    ...(configForm
      ? [{ label: 'Edit configuration', onClick: () => setConfiguringId(entry.id) }]
      : entry.mcp
        ? [{ label: 'Edit configuration', onClick: onEditConnection }]
        : []),
    { label: 'Refresh tools list', onClick: onRefreshTools },
    {
      label: 'Remove connector',
      danger: true,
      onClick: removable ? () => setConfirmingRemove(true) : onRemove,
    },
  ]

  function connect() {
    // Credentials first: arming a connector that has none would render "Connected" over
    // something that cannot run. Once it holds them, Connect is the one-click re-arm.
    if (setupPending) setConfiguringId(entry.id)
    else onSetEnabled(true)
  }

  // Removal is destructive and irreversible, so it asks first — as a SEPARATE dialog STACKED
  // on top of the page it was opened from (its own smaller Modal, no `size`, rendered after
  // the base view so it paints over it), not a swap of that page's own content. The base
  // view stays mounted and visible behind it, and its Escape listener is disabled while this
  // is up (`escapeDisabled`) so one Escape press cancels only this, not both.
  const confirmRemoveView = confirmingRemove ? (
    <Modal
      title={<BackTitle label={entry.name} onClick={() => setConfirmingRemove(false)} />}
      onClose={() => setConfirmingRemove(false)}
      footer={
        <>
          <Btn onClick={() => setConfirmingRemove(false)}>Cancel</Btn>
          <Btn variant="danger" disabled={removing} onClick={remove}>
            {removing ? 'Removing…' : 'Remove'}
          </Btn>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Identity entry={entry} />
        <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, margin: 0 }}>
          {entry.mcp ? (
            <>
              Remove <strong>{entry.name}</strong>? Lucius loses{' '}
              {entry.tools.length > 0
                ? `the ${entry.tools.length} tool${entry.tools.length === 1 ? '' : 's'} it offers`
                : 'access to it'}
              , and every permission level you set for them is forgotten — so adding this server
              again starts it back at Needs approval rather than inheriting what you allowed before.
              Nothing else on this instance is touched. This can&apos;t be undone. If you just want
              to pause it without losing your saved credentials, use Pause instead.
            </>
          ) : (
            <>
              Remove <strong>{entry.name}</strong>? This clears your saved credentials and resets
              its settings — you&apos;ll need to set it up again from scratch to use it again. This
              can&apos;t be undone. {removeProviderRevokeNote(entry)}If you just want to pause it
              without losing your saved credentials, use Pause instead.
            </>
          )}
        </p>
      </div>
    </Modal>
  ) : null

  // Pausing is reversible, but it is still a real interruption (scheduled syncing/notifying
  // stops) and the credentials it LEAVES BEHIND are exactly what distinguishes it from Remove,
  // so it asks first too — the same stacked-dialog shape as Remove, one step lighter.
  const confirmPauseView = confirmingPause ? (
    <Modal
      title={<BackTitle label={entry.name} onClick={() => setConfirmingPause(false)} />}
      onClose={() => setConfirmingPause(false)}
      footer={
        <>
          <Btn onClick={() => setConfirmingPause(false)}>Cancel</Btn>
          <Btn variant="danger" disabled={pausing} onClick={pause}>
            {pausing ? 'Pausing…' : 'Pause'}
          </Btn>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Identity entry={entry} />
        <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, margin: 0 }}>
          Pause <strong>{entry.name}</strong>? {pauseVerb(entry)} until you resume. Your saved
          credentials and settings stay put — resuming won&apos;t ask for them again. To clear your
          saved credentials instead, use Remove connector from the menu.
        </p>
      </div>
    </Modal>
  ) : null

  if (configuring && configForm) {
    const save = formSave.state
    return (
      <Modal
        title={<BackTitle label={entry.name} onClick={leaveConfig} />}
        onClose={onClose}
        size="lg"
        footer={
          save ? (
            <Btn variant="primary" disabled={!save.dirty || save.saving} onClick={saveConfig}>
              {save.saving ? 'Saving…' : 'Save'}
            </Btn>
          ) : (
            // A form that publishes no save (still loading, or failed to load) gets the
            // plain dismiss — offering Save over a form with nothing in it would be a lie.
            <Btn variant="primary" onClick={leaveConfig}>
              Done
            </Btn>
          )
        }
      >
        <formSave.Provider>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <Identity entry={entry} />
            {configForm}
          </div>
        </formSave.Provider>
      </Modal>
    )
  }

  return (
    <>
      <Modal
        title={<BackTitle label="Connectors" onClick={onClose} />}
        onClose={onClose}
        size="lg"
        escapeDisabled={confirmingRemove || confirmingPause}
        footer={
          <Btn variant="primary" onClick={onClose}>
            Done
          </Btn>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Identity
            entry={entry}
            // `lg` matches the landing page's Add — this page's primary action is the same
            // weight of decision, and the two headers are now the same block. The kebab keeps
            // its dense icon size: spec §9 excludes small icon-only actions from any bump.
            actions={
              <>
                {alwaysOn ? null : entry.attention ? (
                  configForm ? (
                    // A native connector's "Reconnect" here is ALWAYS an already-enabled,
                    // already-configured connector (that's the only way `attention` is true —
                    // see `services/connectors.py::_status`), so there is nothing to flip and
                    // no connection test to run yet (EmailUnifiedSection's own note: "a real
                    // cross-connector test flow is a future addition"). The fix lives in its
                    // credentials, so this opens the same config view "Edit configuration"
                    // does, rather than routing through `onReconnect`'s enable-flip/OAuth path.
                    <Btn size="lg" variant="primary" onClick={() => setConfiguringId(entry.id)}>
                      Reconnect
                    </Btn>
                  ) : (
                    <Btn size="lg" variant="primary" disabled={reconnecting} onClick={onReconnect}>
                      {reconnecting ? 'Reconnecting…' : 'Reconnect'}
                    </Btn>
                  )
                ) : entry.enabled ? (
                  <>
                    {automateKey ? (
                      // Puts the connector to work: opens its Automation template (logging from
                      // the mailbox, a bank or broker sync) as an unsaved draft.
                      <Btn size="lg" onClick={() => openTemplate(automateKey)}>
                        Automate
                      </Btn>
                    ) : null}
                    <Btn size="lg" variant="danger" onClick={() => setConfirmingPause(true)}>
                      Pause
                    </Btn>
                  </>
                ) : (
                  <Btn size="lg" variant="primary" onClick={connect}>
                    {setupPending ? 'Connect' : 'Resume'}
                  </Btn>
                )}
                <Kebab items={kebabItems} />
              </>
            }
          />

          {entry.desc ? (
            <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, margin: 0 }}>
              {entry.desc}
            </p>
          ) : null}

          {/* A connector we did not publish claims NO author — never "Developed by Open Net
            Worth" over a third-party server (spec §5). The publisher for an MCP entry is
            stated a second time, with the endpoint, by the panel below.

            The line is the WHOLE answer for a native connector. It used to carry a standing
            caution about third-party servers whenever the publisher was not us — which put
            "Open Net Worth does not control third-party servers" under Serper, a Web provider
            we build and operate against an API we chose. Naming Serper as the developer is
            the accurate half; there is no third-party server in that sentence to warn about.
            Spec §5 is "Vendor trust (MCP only)" and `CNR-TRUST-NATIVE-ABSENT` says a non-MCP
            connector shows no trust panel — this was that panel's prose half, and it is now
            bound by the same rule (see `PublisherLine`'s `mcp` prop for the browse-time twin).

            The NAME is bound by the same `mcp` distinction too (owner correction, 2026-08-21,
            simplified same day): a native line always reads "Developed by Open Net Worth" —
            the vendor's own name (Tavily, Serper, …) is dropped from THIS line entirely, since
            "Developed by Tavily" claims Tavily's code runs here, which is only true for a real
            MCP server. The vendor is still visible elsewhere on the card (name, logo,
            description) and in the Details footer's "Vendor" field, so nothing is hidden — this
            line just answers one question cleanly: did WE build this. */}
          {entry.developer ? (
            <div style={{ fontSize: 13, fontWeight: 400, color: 'var(--ink)' }}>
              Developed by{' '}
              {entry.mcp ? (
                entry.developerUrl ? (
                  <ExternalLink href={entry.developerUrl}>{entry.developer}</ExternalLink>
                ) : (
                  entry.developer
                )
              ) : (
                <ExternalLink href={FIRST_PARTY_URL}>{FIRST_PARTY_PUBLISHER}</ExternalLink>
              )}
            </div>
          ) : null}

          {entry.mcp ? (
            <VendorTrust
              developer={entry.developer}
              verification={entry.verification}
              url={entry.url}
            />
          ) : null}

          {entry.prompts.length > 0 ? (
            <div>
              <div className="cn-sublabel" style={{ marginBottom: 8 }}>
                Prompt suggestions
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {entry.prompts.map((p) => (
                  <button
                    key={p}
                    className="cn-tagchip"
                    style={{ cursor: 'pointer' }}
                    onClick={() => onUsePrompt(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <div>
            <div className="cn-sublabel" style={{ marginBottom: 4 }}>
              Tool permissions
            </div>
            {entry.toolGroups.length > 0 ? (
              <>
                <div className="rd-help" style={{ marginBottom: 8, fontSize: 12.5 }}>
                  Choose when Lucius is allowed to use these tools.
                </div>
                <PermissionLadder
                  groups={entry.toolGroups}
                  perms={perms}
                  onSetTool={(tool, level) =>
                    applyLevel([tool], level, onSetToolPermission(tool, level))
                  }
                  onSetGroup={(tools, level) =>
                    applyLevel(tools, level, onSetGroupPermission(tools, level))
                  }
                />
              </>
            ) : (
              <div className="rd-help" style={{ fontSize: 12.5 }}>
                This connector gives Lucius no tools yet, so there is nothing to set here.
                {entry.mcp ? ' Use Refresh tools list once the server is reachable.' : ''}
              </div>
            )}
          </div>
        </div>
      </Modal>
      {confirmRemoveView}
      {confirmPauseView}
    </>
  )
}

/** Icon, name, optional tag, status badge and sub-line (spec §3). Shared by every view:
 *  the config view keeps it so a form always states which connector it is writing to and
 *  whether that connector is currently erroring.
 *
 *  Same `.cn-idhead` block, same large tile, same vertical centring as the landing page's
 *  header — one stylesheet rule for both, so the page you browse and the page you land on
 *  after adding cannot look like two different products. */
function Identity({
  entry,
  actions,
}: {
  entry: ConnectorDetailEntry
  actions?: ReactNode
}): JSX.Element {
  return (
    <div className="cn-idhead">
      <Mono icon={entry.icon} hue={entry.hue} logo={entry.logo} size="lg" />
      <div className="cn-idhead-main">
        <div className="cn-idhead-name">
          {entry.name}
          {entry.tag ? <span className="cn-tag">{entry.tag}</span> : null}
          <Badge tone={entry.tone}>{entry.statusText}</Badge>
        </div>
        <div className="cn-idhead-sub">{entry.sub}</div>
      </div>
      {actions ? <div className="cn-idhead-actions">{actions}</div> : null}
    </div>
  )
}

/** What "pausing" actually stops, worded per connector family — the one fact that changes
 *  across the Pause confirmation's otherwise-fixed copy (credentials kept, Remove is the
 *  alternative). A data-aggregation connector "syncs"; Telegram, email and webhook do not,
 *  so each gets its own verb rather than a stretched "syncing" that would be wrong for them. */
function pauseVerb(entry: ConnectorDetailEntry): string {
  if (entry.mcp) return 'Lucius loses access to its tools'
  switch (entry.id) {
    case 'telegram':
      return 'It stops responding to messages and sending alerts'
    case 'email':
      return 'It stops checking for mail and sending'
    case 'webhook':
      return 'It stops sending notifications'
    default:
      return entry.id.startsWith('web_search:')
        ? 'Lucius stops being able to search with it'
        : 'Scheduled syncing stops'
  }
}

/** Only Lunch Flow's revoke is actually wired (its `disconnect_connection` primitive
 *  already existed and Remove now calls it — see `services.connectors._reset_lunch_flow`).
 *  For these three, clearing the credential HERE does not revoke the underlying grant at
 *  the provider — the confirm copy says so honestly rather than implying it does, per the
 *  2026-08-17 security review (finding 3). Returns '' (no note) for every other connector,
 *  including Lunch Flow itself. Trailing space included so the caller can splice it inline
 *  with no extra whitespace bookkeeping. */
function removeProviderRevokeNote(entry: ConnectorDetailEntry): string {
  const providerLabel: Record<string, string> = {
    plaid: 'Plaid',
    enable_banking: 'bank',
    yodlee: 'Yodlee',
  }
  const label = providerLabel[entry.id]
  return label ? `You may also want to revoke access from your ${label} account directly. ` : ''
}
