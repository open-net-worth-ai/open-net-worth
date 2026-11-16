import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, PasswordField, useSecretReveal } from '../../ui'
import { AccessPolicyField, type AccessPolicy, type PendingRow } from './ChatAccessPolicy'
import { unrevealed, useConnectorSave, useConnectorWrote, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ChatList, parseChatIds } from './TelegramChats'
import {
  BeforeSaveHint,
  BotStatus,
  ConnectStep,
  SaveTokenNudge,
  SetupStep,
  type Connection,
} from './TelegramSteps'

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

const POLL_BOT_MS = 1000

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

/** Names for the saved chat IDs — read live from Telegram through the running bot. */
async function fetchChatLabels(ids: string): Promise<Record<string, string>> {
  const r = await authFetch(`/api/telegram/chats?ids=${encodeURIComponent(ids)}`)
  if (!r.ok) return {}
  const d = (await r.json()) as { labels?: Record<string, string> }
  return d.labels || {}
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
  const [chatLabels, setChatLabels] = useState<Record<string, string>>({})
  // IDs Telegram has ANSWERED for — a name missing from one of these is worth a warning; one
  // still being looked up is not (it flashed "doesn't know this ID" before the name came).
  const [checkedIds, setCheckedIds] = useState<string[]>([])
  const cfgRef = useRef<TelegramConfig | null>(null)
  const wrote = useConnectorWrote()

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

  const botOnline = !!cfg?.enabled && cfg.connection === 'connected'
  const shownIds = form ? parseChatIds(form.allowed_chat_ids).join(',') : ''
  useEffect(() => {
    // While the bot reconnects (a new token, a resume) keep the names already on screen —
    // clearing them flashed every row back to a bare number.
    if (!botOnline) return
    if (!shownIds) {
      setChatLabels({})
      setCheckedIds([])
      return
    }
    let gone = false
    fetchChatLabels(shownIds)
      .then((l) => {
        if (gone) return
        setChatLabels(l)
        setCheckedIds(shownIds.split(','))
      })
      .catch(() => {})
    return () => {
      gone = true
    }
  }, [botOnline, shownIds, cfg?.bot_username])

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

  /** Add or take off ONE chat NOW — like Approve, no Save — touching nothing else the user has
   *  unsaved: the PUT carries only the allowlist, and the form's own list gets the same edit. */
  async function writeChat(id: string, add: boolean): Promise<boolean> {
    if (!cfg) return false
    setSaveErr(null)
    const saved = cfg.allowed_chat_ids || []
    const next = add ? [...saved.filter((x) => x !== id), id] : saved.filter((x) => x !== id)
    const verb = add ? 'add' : 'remove'
    try {
      const r = await authFetch('/api/telegram/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allowed_chat_ids: next.join(', ') }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || `Could not ${verb} that chat.`)
        return false
      }
    } catch {
      setSaveErr(`Could not ${verb} that chat — could not reach the server.`)
      return false
    }
    setForm((f) => {
      if (!f) return f
      const ids = parseChatIds(f.allowed_chat_ids).filter((x) => x !== id)
      return { ...f, allowed_chat_ids: (add ? [...ids, id] : ids).join(', ') }
    })
    refresh()
    wrote()
    return true
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
    wrote()
  }

  return (
    <Stack gap="--space-5">
      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      {/* Once a step is done its how-to goes: a set-up bot's form is its settings, not a guide
          (owner, 2026-11-16 — "full of text"). */}
      <SetupStep n={1} title="Create your bot" done={cfg.configured}>
        {!cfg.configured && (
          <Text size="sm" tone="faint">
            In Telegram, message{' '}
            <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
              @BotFather
            </a>
            , create a new bot with <code>/newbot</code>, and paste the token it gives you.
          </Text>
        )}
        <FormField
          label="Bot token"
          htmlFor="conn-tg-token"
          required
          help={
            token.caption ||
            (cfg.has_bot_token
              ? 'To switch bots, paste a new token from @BotFather.'
              : 'Looks like 123456789:ABC-DEF…')
          }
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
        {unrevealed(form.bot_token, token).trim() && !tokenRefused ? (
          <SaveTokenNudge
            switching={cfg.configured}
            saving={saving}
            onSave={() => void save().then((ok) => ok && wrote())}
          />
        ) : (
          live && <BotStatus connection={cfg.connection ?? 'off'} username={username} />
        )}
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
        title={
          peopleIn
            ? 'People who can chat'
            : form.access_policy === 'allowlist'
              ? 'Add who can chat'
              : 'Say hi to your bot'
        }
        done={peopleIn > 0}
      >
        {!cfg.configured ? (
          <BeforeSaveHint policy={form.access_policy} firstSave />
        ) : form.access_policy !== savedPolicy && form.access_policy === 'pairing' ? (
          <BeforeSaveHint policy="pairing" firstSave={false} />
        ) : live ? (
          <ConnectStep
            policy={form.access_policy}
            username={username}
            peopleIn={peopleIn}
            justApproved={justApproved}
            onApproved={onApproved}
          />
        ) : null}

        {/* Still shown under Pairing: it's the SAME list approvals write into, so it is where
            you review — or revoke — who got in (2026-08-22). */}
        {(cfg.configured || form.access_policy === 'allowlist') && (
          <ChatList
            title={form.access_policy === 'pairing' ? 'Approved chats' : 'Allowed chats'}
            ids={parseChatIds(form.allowed_chat_ids)}
            labels={chatLabels}
            checkedIds={checkedIds}
            savedIds={cfg.allowed_chat_ids || []}
            regainAccess={
              savedPolicy === 'pairing'
                ? 'To let them back in, they message the bot again and you approve them.'
                : 'To let them back in, add their chat ID again and Save.'
            }
            onChange={(ids) => set('allowed_chat_ids', ids.join(', '))}
            onRevoke={(id) => writeChat(id, false)}
            onAdd={cfg.configured ? (id) => writeChat(id, true) : undefined}
          />
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
