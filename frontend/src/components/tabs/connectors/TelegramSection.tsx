import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, TextField, PasswordField, useSecretReveal } from '../../ui'
import { AccessPolicyField, type AccessPolicy, type PendingRow } from './ChatAccessPolicy'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { BeforeSaveHint, BotStatus, ConnectStep, SetupStep, type Connection } from './TelegramSteps'

// Telegram connector config over /api/telegram/config (per-provider plane), mirroring the
// SMTP channel section. A blank token field leaves the saved one unchanged; the
// GET echoes only a `has_bot_token` flag, and the saved token is read back through the eye
// (GET /api/connectors/telegram/secret/bot_token). The detail page's top-level Pause/Resume
// button is the ONLY place `enabled` is switched now; this form owns the config fields
// (token / allowed chats) and PASSES THROUGH whatever `enabled` already was —
// never hardcodes it — so saving a config edit can't have the side effect of silently
// resuming a bot the operator deliberately paused (security review, 2026-08-17).
//
// The intro used to explain why Telegram needs no "Operator trust" toggle "unlike Slack and
// Discord" — both of which were REMOVED in 2026-08, so it contrasted this connector with two
// that no longer exist, using a control name the user has never seen.
//
// Setup is three numbered steps (2026-11-15, owner + a friend both got lost): the token, who
// can chat, then "say hi to your bot" — which only appears once Save has brought the bot up,
// and under Pairing finishes WITHOUT a second save (Approve writes the allowlist itself). A new
// connector starts on Pairing for that reason; Allowlist needs a chat ID from a running bot.
//
// NO webhook URL field (removed 2026-08-22): it called Telegram's set_webhook but nothing in
// this codebase ever received the pushed updates — configuring one silently broke the bot
// (stopped polling, delivered nowhere). Long-polling only; see backend/api/integrations.py.

interface TelegramConfig {
  configured: boolean
  enabled: boolean
  allowed_chat_ids: string[]
  access_policy: AccessPolicy
  has_bot_token: boolean
  bot_username?: string
  connection?: Connection
}

interface TelegramForm {
  bot_token: string
  allowed_chat_ids: string
  access_policy: AccessPolicy
}

// The only two policies still offered as a PICK (see ChatAccessPolicy.tsx) — 'open'/'disabled'
// are still real stored values (Remove deliberately writes 'disabled' as a security measure;
// see TelegramConfigStore.reset()), just no longer selectable here.
const PICKABLE_POLICIES: AccessPolicy[] = ['pairing', 'allowlist']

const POLL_BOT_MS = 2000

/** The form as the server last returned it — both the seed for `load()` and the baseline
 *  the footer's Save compares against, so the two can never drift apart.
 *
 *  A stored policy outside `PICKABLE_POLICIES` (2026-08-22, caught live: Remove → re-add)
 *  falls back to 'pairing', never passed through raw. A raw 'disabled' bound to a <select>
 *  with no matching <option> renders the browser's default (the FIRST option, "Pairing") —
 *  so the dropdown visibly showed Pairing while the real form state was still 'disabled', and
 *  hitting Save without ever touching the dropdown silently re-persisted 'disabled': a
 *  freshly re-added, fully "Connected" bot that answered nobody, with nothing in the UI
 *  saying why. A connector with no token yet starts on Pairing too, whatever the stored
 *  default — it is the one policy that finishes setup in a single save. */
function formOf(c: TelegramConfig): TelegramForm {
  const stored = PICKABLE_POLICIES.includes(c.access_policy) ? c.access_policy : 'pairing'
  return {
    bot_token: '',
    allowed_chat_ids: (c.allowed_chat_ids || []).join(', '),
    access_policy: c.configured ? stored : 'pairing',
  }
}

async function fetchConfig(): Promise<TelegramConfig> {
  const r = await authFetch('/api/telegram/config')
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as TelegramConfig
}

export function TelegramSection() {
  const [cfg, setCfg] = useState<TelegramConfig | null>(null)
  const [form, setForm] = useState<TelegramForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  // A token Telegram refused is reported ON the token field, not at the bottom of the form.
  const [tokenRefused, setTokenRefused] = useState<string | null>(null)
  const [justApproved, setJustApproved] = useState<{ who: string; welcomed: boolean } | null>(null)
  const cfgRef = useRef<TelegramConfig | null>(null)

  function adopt(c: TelegramConfig) {
    cfgRef.current = c
    setCfg(c)
  }

  function load() {
    setError(false)
    fetchConfig()
      .then((c) => {
        adopt(c)
        setForm(formOf(c))
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  /** Pick up what the SERVER changed (the bot came online, an approval added a chat) without
   *  wiping what the user is typing: a field they've edited keeps their edit. */
  const refresh = useCallback(() => {
    fetchConfig()
      .then((c) => {
        const prev = cfgRef.current
        adopt(c)
        setForm((f) => {
          if (!f || !prev) return formOf(c)
          const untouched = f.allowed_chat_ids === formOf(prev).allowed_chat_ids
          return untouched ? { ...f, allowed_chat_ids: formOf(c).allowed_chat_ids } : f
        })
      })
      .catch(() => {})
  }, [])

  const botComingUp = !!cfg?.configured && !!cfg.enabled && cfg.connection === 'connecting'
  useEffect(() => {
    if (!botComingUp) return
    const id = setInterval(refresh, POLL_BOT_MS)
    return () => clearInterval(id)
  }, [botComingUp, refresh])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.configured ? cfg.enabled : true,
        allowed_chat_ids: form.allowed_chat_ids,
        access_policy: form.access_policy,
      }
      if (form.bot_token.trim()) body.bot_token = form.bot_token.trim()
      const r = await authFetch('/api/telegram/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        const message = d.detail || 'Could not save the Telegram configuration.'
        setSaveErr(message)
        if (r.status === 400 && body.bot_token) setTokenRefused(message)
        return false
      }
      setForm((f) => f && { ...f, bot_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setToken = useCallback((v: string) => {
    setTokenRefused(null)
    setForm((f) => f && { ...f, bot_token: v })
  }, [])
  const token = useSecretReveal({
    saved: !!cfg?.has_bot_token,
    value: form?.bot_token ?? '',
    onChange: setToken,
    reveal: useCallback(() => revealConnectorSecret('telegram', 'bot_token'), []),
  })

  const tokenValid = !form || !!form.bot_token.trim() || !!cfg?.has_bot_token
  const tokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid,
    values: form && { ...form, bot_token: unrevealed(form.bot_token, token) },
    baseline: cfg ? formOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Telegram connector config.
      </Text>
    )
  }
  if (!cfg || !form) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const set = <K extends keyof TelegramForm>(k: K, v: TelegramForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  const username = cfg.bot_username || ''
  const live = cfg.configured && cfg.enabled
  const savedPolicy = formOf(cfg).access_policy
  const peopleIn = (cfg.allowed_chat_ids || []).length
  const onApproved = (row: PendingRow, welcomed: boolean) => {
    setJustApproved({ who: row.sender_label || row.sender_id, welcomed })
    refresh()
  }

  return (
    <Stack gap="--space-5">
      <Text size="sm" tone="faint">
        Ask questions, send photos, and approve changes from your own Telegram bot — the same things
        Lucius can do in the app.
      </Text>
      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      <SetupStep n={1} title="Create your bot" done={cfg.configured}>
        <Text size="sm" tone="faint">
          In Telegram, message{' '}
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
            @BotFather
          </a>
          , send <code>/newbot</code>, pick a name, and paste the token it gives you.
        </Text>
        <FormField
          label="Bot token"
          htmlFor="conn-tg-token"
          required
          help={token.caption || 'Looks like 123456789:ABC-DEF…'}
          error={
            tokenRefused ||
            token.error ||
            (tokenTouched.touched && !tokenValid ? 'A bot token is required.' : undefined)
          }
        >
          <PasswordField
            id="conn-tg-token"
            value={form.bot_token}
            placeholder={cfg.has_bot_token ? '••••••••' : '123456:ABC-DEF…'}
            onChange={(e) => setToken(e.target.value)}
            onBlur={tokenTouched.onBlur}
            {...token.fieldProps}
          />
        </FormField>
        {live && <BotStatus connection={cfg.connection ?? 'off'} username={username} />}
      </SetupStep>

      <SetupStep n={2} title="Choose who can chat" done={cfg.configured}>
        <AccessPolicyField
          idPrefix="conn-tg"
          value={form.access_policy}
          onChange={(v) => set('access_policy', v)}
        />
      </SetupStep>

      <SetupStep
        n={3}
        title={peopleIn ? 'People who can chat' : 'Say hi to your bot'}
        done={peopleIn > 0}
      >
        {!cfg.configured || form.access_policy !== savedPolicy ? (
          <BeforeSaveHint policy={form.access_policy} firstSave={!cfg.configured} />
        ) : live ? (
          <ConnectStep
            policy={savedPolicy}
            username={username}
            peopleIn={peopleIn}
            justApproved={justApproved}
            onApproved={onApproved}
          />
        ) : null}

        {/* Still shown under Pairing: it's the SAME list approvals write into, so it is where
            you review — or revoke — who got in (2026-08-22). */}
        {(cfg.configured || form.access_policy === 'allowlist') && (
          <FormField
            label={form.access_policy === 'pairing' ? 'Approved chat IDs' : 'Allowed chat IDs'}
            htmlFor="conn-tg-allowed"
            help={
              form.access_policy === 'pairing'
                ? 'Fills in as you approve. Remove an ID and Save to revoke it.'
                : 'Separate several with commas. A group chat ID starts with -. Blank means no one can reach Lucius.'
            }
          >
            <TextField
              id="conn-tg-allowed"
              value={form.allowed_chat_ids}
              placeholder="123456789, -100987654321"
              onChange={(e) => set('allowed_chat_ids', e.target.value)}
            />
          </FormField>
        )}
      </SetupStep>

      {saveErr && !tokenRefused && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
