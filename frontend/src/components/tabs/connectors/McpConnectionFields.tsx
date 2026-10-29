// Step 1 of the MCP flow — connection info (spec §7), for both an add and an EDIT.
//
// No Name and no Server ID: both are derived in step 3, from what the server reports about
// itself once it has actually been reached. Split out of `McpAddFlow` purely for size.
//
// WHAT AN EDIT CHANGES HERE, and why each is forced rather than chosen:
//
//   * TRANSPORT IS FIXED. `McpClientStore.update` refuses to patch it (switch by re-adding),
//     so offering the toggle would be a control that silently does nothing. The paste pill
//     goes with it — pasting a snippet switches transport, and it is a first-run affordance
//     for a README anyway.
//   * SECRETS START EMPTY, and the EYE fills them in. `public()` still returns only
//     `has_token` / `env_keys`, so nothing is echoed into the payload the list is built from;
//     the saved value is fetched on demand from `GET /api/connectors/mcp:<id>/secret/<field>`
//     when the operator taps the eye (see `useSecretReveal`). A blank box still means "keep"
//     — the store's own rule for a token, and `updateMcpServer`'s rule for env ("keep the
//     whole set") — so a field the user never opens is never rewritten.
//   * TEST CONNECT RUNS WITHOUT THE SAVED CREDENTIAL unless the box holds one.
//     `POST /api/mcp-clients/probe` is deliberately id-less — it exists so an unsaved server
//     can be reached — so it cannot look a stored secret up. Revealing the credential first
//     is now the one-tap way to give the probe what it needs.

import { useCallback, useState, type JSX } from 'react'
import { Eye, EyeOff } from '../../../ui/icons'
import { useSecretReveal, type SecretReveal } from '../../ui'
import { Icons } from './icons'
import { mcpConnectorId } from './mcpApi'
import { McpOAuthField } from './McpOAuthField'
import { isValidMcpUrl, parsePastedConfig, type McpAuth } from './mcpConfig'
import { revealConnectorSecret } from './secrets'
import type { McpAddFlow } from './McpAddFlow'

/** The eye that reveals a SAVED value, in the design-handoff (`rd-*`) input language.
 *  `useSecretReveal` owns the behaviour; this is only its button, because these fields are
 *  raw `rd-input`/`rd-textarea` elements rather than the `ui/` `PasswordField`. */
function SecretEye({
  shown,
  onToggle,
  block,
}: {
  shown: boolean
  onToggle: () => void
  /** Anchor to the top rather than the middle — for the multi-line env box. */
  block?: boolean
}): JSX.Element {
  return (
    <button
      type="button"
      className={'rd-secret-eye' + (block ? ' rd-secret-eye--block' : '')}
      onClick={onToggle}
      aria-label={shown ? 'Hide secret' : 'Show secret'}
      aria-pressed={shown}
      title={shown ? 'Hide' : 'Show'}
      tabIndex={-1}
    >
      {shown ? <EyeOff size={15} strokeWidth={1.7} /> : <Eye size={15} strokeWidth={1.7} />}
    </button>
  )
}

const AUTH_OPTIONS: [McpAuth, string][] = [
  ['none', 'None'],
  ['key', 'API key'],
  ['oauth', 'OAuth'],
]

/** One line under Authentication — a wrong pick otherwise surfaced only as a raw HTTP 401. */
const AUTH_HINT: Record<McpAuth, string> = {
  none: 'For a public server that needs no login.',
  key: 'Paste a key or token from the vendor’s dashboard.',
  oauth: 'Sign in on the vendor’s own page — no key to copy.',
}

export function McpConnectionFields({ flow }: { flow: McpAddFlow }): JSX.Element {
  const { form, setForm, editing } = flow
  const parsed = parsePastedConfig(form.paste)
  const serverId = editing?.serverId ?? ''

  // The API key. Identical contract to every other connector's credential field.
  const key: SecretReveal = useSecretReveal({
    saved: !!editing?.hasToken,
    value: form.key,
    onChange: useCallback((v: string) => setForm((f) => ({ ...f, key: v })), [setForm]),
    reveal: useCallback(() => revealConnectorSecret(mcpConnectorId(serverId), 'token'), [serverId]),
  })
  // Local mask state for a freshly TYPED key — the parent only owns the eye while there is a
  // saved value to fetch or a fetched one on screen (see `useSecretReveal`).
  const [keyShown, setKeyShown] = useState(false)

  // The env overlay. One box, so it reveals as one `KEY=value` block. Revealing makes the box
  // hold the true full set, which is exactly what `updateMcpServer`'s whole-map replace needs
  // — and leaving it untouched still sends no `env` at all, so nothing is wiped.
  const env: SecretReveal = useSecretReveal({
    saved: (editing?.envKeys.length ?? 0) > 0,
    value: form.env,
    onChange: useCallback((v: string) => setForm((f) => ({ ...f, env: v })), [setForm]),
    reveal: useCallback(() => revealConnectorSecret(mcpConnectorId(serverId), 'env'), [serverId]),
  })

  return (
    // Fields here use aria-labelledby with no id/name and no type="password", so
    // isSecretControl never fires — data-ctx-secret is the only thing keeping argv/url/env
    // out of the screen snapshot. See screenSnapshot.secrets.test.ts.
    <div
      className="rd-formgrid"
      style={{ minHeight: 400, alignContent: 'start' }}
      data-ctx-secret=""
    >
      {editing ? (
        <span className="rd-help full">
          Editing <strong>{editing.name}</strong>. Test connect runs against what you type here;
          nothing is saved until it succeeds.
        </span>
      ) : (
        <div className="rd-field full">
          {/* Pasting is NOT a separate mode — it fills the very same fields below, and shares the
              identical next step. That is the whole point of folding the old paste mode in here. */}
          <button
            type="button"
            className="cn-pastelink"
            style={{ alignSelf: 'flex-start' }}
            aria-expanded={flow.pasteOpen}
            onClick={() => flow.setPasteOpen(!flow.pasteOpen)}
          >
            <span
              style={{
                display: 'inline-flex',
                width: 14,
                height: 14,
                transform: flow.pasteOpen ? 'rotate(45deg)' : 'none',
                transition: 'transform .15s',
              }}
            >
              <Icons.plus />
            </span>{' '}
            Paste from a config snippet
          </button>
          {flow.pasteOpen && (
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <textarea
                className="rd-textarea cn-pastebox"
                rows={6}
                aria-label="Config snippet"
                placeholder={
                  '{\n  "mcpServers": {\n    "my-server": { "command": "npx", "args": ["-y", "@vendor/server"] }\n  }\n}'
                }
                value={form.paste}
                onChange={(e) => {
                  const text = e.target.value
                  const next = parsePastedConfig(text)
                  setForm((f) => ({ ...f, paste: text, ...(next ?? {}) }))
                }}
              />
              <span className="rd-help">
                {parsed
                  ? 'Parsed — fields below are filled in.'
                  : form.paste.trim()
                    ? "Couldn't parse that yet — paste the server's JSON block from its README."
                    : "Paste a single-server config block (e.g. from a README's Quick Start) and it fills in the fields below."}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="rd-field full">
        <span className="rd-label">Transport</span>
        <div className="rd-seg">
          <button
            type="button"
            className={'rd-seg-b' + (form.transport === 'remote' ? ' on' : '')}
            aria-pressed={form.transport === 'remote'}
            disabled={!!editing}
            onClick={() => setForm((f) => ({ ...f, transport: 'remote' }))}
          >
            Remote server (URL)
          </button>
          <button
            type="button"
            className={'rd-seg-b' + (form.transport === 'local' ? ' on' : '')}
            aria-pressed={form.transport === 'local'}
            disabled={!!editing}
            onClick={() => setForm((f) => ({ ...f, transport: 'local' }))}
          >
            Local command (stdio)
          </button>
        </div>
        {editing ? (
          <span className="rd-help">
            Transport is fixed once a server exists — add it again to move it between a URL and a
            local command.
          </span>
        ) : null}
      </div>

      {form.transport === 'remote' ? (
        <>
          <div className="rd-field full">
            <span className="rd-label" id="mcp-url-label">
              Server URL
              <span className="rd-req" aria-hidden="true">
                *
              </span>
            </span>
            <input
              className="rd-input"
              aria-labelledby="mcp-url-label"
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="https://mcp.example.com"
            />
            {form.url.trim() && !isValidMcpUrl(form.url) ? (
              <span className="rd-help rd-help--bad" role="alert">
                Enter a full URL, starting with http:// or https://.
              </span>
            ) : null}
          </div>
          <div className="rd-field">
            <span className="rd-label" id="mcp-auth-label">
              Authentication
            </span>
            {/* Three short choices — a segmented control like Transport above, so all of them
                are visible without opening a menu. */}
            <div className="rd-seg" role="group" aria-labelledby="mcp-auth-label">
              {AUTH_OPTIONS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={'rd-seg-b' + (form.auth === value ? ' on' : '')}
                  aria-pressed={form.auth === value}
                  onClick={() => setForm((f) => ({ ...f, auth: value }))}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="rd-help">{AUTH_HINT[form.auth]}</span>
          </div>
          {form.auth === 'key' && (
            <div className="rd-field">
              <span className="rd-label" id="mcp-key-label">
                API key
                <span className="rd-req" aria-hidden="true">
                  *
                </span>
              </span>
              <div className="rd-secret">
                <input
                  className="rd-input"
                  type={(key.fieldProps.shown ?? keyShown) ? 'text' : 'password'}
                  aria-labelledby="mcp-key-label"
                  // Format-NEUTRAL on purpose. This read `sk-…`, which is OpenAI's shape and
                  // wrong for essentially every server in the catalog: Stripe issues `sk_`/`rk_`
                  // with an underscore (and its MCP docs ask for a RESTRICTED `rk_` key),
                  // Firecrawl uses `fc-`. A placeholder that looks like one specific vendor's
                  // key invites pasting the wrong credential, which is worse than saying nothing.
                  placeholder={editing?.hasToken ? '••••••••' : 'Paste your API key'}
                  value={form.key}
                  onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))}
                />
                <SecretEye
                  shown={key.fieldProps.shown ?? keyShown}
                  onToggle={key.fieldProps.onToggleShown ?? (() => setKeyShown((v) => !v))}
                />
              </div>
              {key.error ? (
                <span className="rd-help rd-help--bad">{key.error}</span>
              ) : editing?.hasToken ? (
                <span className="rd-help">
                  {key.caption} Test connect runs with whatever is in this box, so reveal or
                  re-enter the key if this server needs one to answer.
                </span>
              ) : null}
            </div>
          )}
          {form.auth === 'oauth' && <McpOAuthField flow={flow} />}
        </>
      ) : (
        <>
          <div className="rd-field full">
            <span className="rd-label" id="mcp-cmd-label">
              Command
              <span className="rd-req" aria-hidden="true">
                *
              </span>
            </span>
            <input
              className="rd-input"
              aria-labelledby="mcp-cmd-label"
              value={form.command}
              onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
              placeholder="npx"
            />
          </div>
          <div className="rd-field full">
            <span className="rd-label" id="mcp-args-label">
              Arguments
            </span>
            <input
              className="rd-input"
              aria-labelledby="mcp-args-label"
              value={form.args}
              onChange={(e) => setForm((f) => ({ ...f, args: e.target.value }))}
              placeholder="-y @modelcontextprotocol/server-filesystem /path"
            />
          </div>
          <div className="rd-field full">
            <span className="rd-label" id="mcp-env-label">
              Environment variables (optional)
            </span>
            <div
              className={'rd-secret' + (env.fieldProps.onToggleShown ? '' : ' rd-secret--plain')}
            >
              <textarea
                className="rd-textarea"
                rows={3}
                aria-labelledby="mcp-env-label"
                value={form.env}
                onChange={(e) => setForm((f) => ({ ...f, env: e.target.value }))}
                // Same reason as the API-key field above: no vendor's key shape here. When a
                // set is already saved, the placeholder points at the eye instead of showing
                // the generic example — the box looking blank next to "already set" text read
                // as "did this not save?" rather than "click the eye to view it".
                placeholder={
                  env.fieldProps.onToggleShown
                    ? 'Click 👁 to view the saved variables'
                    : 'API_KEY=your-key\nOTHER_VAR=value'
                }
              />
              {/* The box shows plain text, so the eye here means "fill it with the saved set"
                  rather than "unmask" — and hiding an untouched reveal empties it again, which
                  is what "keep every saved value" looks like on save. Offered only when there
                  is something saved to fetch; a box the user has typed into needs no reveal. */}
              {env.fieldProps.onToggleShown ? (
                <SecretEye
                  block
                  shown={env.fieldProps.shown ?? false}
                  onToggle={env.fieldProps.onToggleShown}
                />
              ) : null}
            </div>
            {env.error ? (
              <span className="rd-help rd-help--bad">{env.error}</span>
            ) : editing &&
              editing.envKeys.length > 0 &&
              form.env !== '' &&
              !env.fieldProps.onToggleShown ? (
              // Typing while the eye is gone means the box no longer holds the saved set at
              // all (useSecretReveal drops the eye the instant a saved-but-untouched box gets
              // typed into) — so Connect would REPLACE it with only what's here, silently
              // dropping IB_FORCE_STANDALONE_GATEWAY-style vars the user never meant to touch.
              // Louder than the default caption below for exactly that reason.
              <span className="rd-help rd-help--bad">
                This replaces the saved set ({editing.envKeys.join(', ')}) with only what's typed
                here. Clear the box first if you meant to keep it and add to it.
              </span>
            ) : editing && editing.envKeys.length > 0 ? (
              <span className="rd-help">
                Already set: {editing.envKeys.join(', ')}. Leave this empty to keep them, use the
                eye to load the saved set, or type the FULL set to replace it. Test connect launches
                the command with only what is in this box.
              </span>
            ) : null}
          </div>
          <span className="rd-help full">
            This launches a local process on the machine running Lucius, with the same access as the
            account it runs under. Only run commands you trust.
          </span>
        </>
      )}
    </div>
  )
}
