import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  FormField,
  TextField,
  PasswordField,
  NumberField,
  Grid,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { EMAIL_PRESETS, emailPreset, type EmailPreset } from './presets'
import { revealConnectorSecret } from './secrets'

// Email is ONE connector: one screen, one row, one on/off, ONE save. The same account —
// provider, address, app password, TLS mode — serves IMAP (receive), SMTP (send) and the
// email alert channel, so it is entered once and saved with a single `PUT /api/email/config`
// (read back from one `GET /api/email/config`). Connector on/off is Pause/Resume/Remove ONLY
// (no per-form Enable checkbox here, matching every other connector).
//
// No status badges and no per-connector "Test connection"/"Send test" here (both removed
// 2026-08-18, owner decision): connection health belongs on the connector's own detail
// page, not duplicated inside its config form, and testing a connector is a generic action
// every connector needs — not something worth custom-building per form. A real
// cross-connector test flow is a future addition; until then, asking Lucius to read/send a
// message is the test.

interface EmailConfig {
  /** Credentials are SAVED (set up), independent of `enabled` — a Paused account is still
   *  configured. */
  configured: boolean
  enabled: boolean
  has_password: boolean
  /** The provider chip last explicitly SAVED — "gmail"/"outlook"/"icloud"/"fastmail"/
   *  "custom", or `null` for a row that never had an explicit choice. */
  provider: string | null
  address: string
  imap_host: string
  imap_port: number
  imap_folder: string
  smtp_host: string
  smtp_port: number
  last_error: string | null
}

interface FormState {
  email: string
  password: string
  // The user's EXPLICIT provider choice — a real piece of state, not re-derived from
  // host/port on every render. Re-deriving it (matching all four fields against
  // EMAIL_PRESETS every time) is exactly what broke: editing any ONE field to just look at
  // it made the match disappear, the active chip go dark, and the host/port fields flip
  // back to a half-filled, inconsistent "custom" view with no explanation. `null` means no
  // preset — a genuinely custom/self-hosted server, the one case host/port are shown.
  provider: string | null
  imap_host: string
  imap_port: number
  imap_folder: string
  smtp_host: string
  smtp_port: number
}

/** The preset whose servers the account actually uses, if any. It wins over a saved "custom":
 *  a server at `imap.gmail.com` IS Gmail, and showing its chip changes nothing (a preset only
 *  hides the host/port fields; the values are identical). A saved "custom" used to win here,
 *  so a Gmail account reopened as Custom after one stray save. */
function guessProvider(cfg: EmailConfig): string | null {
  return (
    EMAIL_PRESETS.find(
      (pr) =>
        pr.imap_host === cfg.imap_host &&
        pr.imap_port === cfg.imap_port &&
        pr.smtp_host === cfg.smtp_host &&
        pr.smtp_port === cfg.smtp_port,
    )?.key ?? null
  )
}

function formFrom(cfg: EmailConfig): FormState {
  // Never set up: open on Gmail, the provider most people have, rather than on Custom's bare
  // server fields (owner, 2026-10-10). Its servers come from the preset, hidden like any chip.
  const gmail = emailPreset('gmail')
  if (gmail && !cfg.imap_host && !cfg.address && !cfg.smtp_host) {
    return {
      email: '',
      password: '',
      provider: gmail.key,
      imap_host: gmail.imap_host,
      imap_port: gmail.imap_port,
      imap_folder: cfg.imap_folder,
      smtp_host: gmail.smtp_host,
      smtp_port: gmail.smtp_port,
    }
  }
  return {
    email: cfg.address,
    password: '',
    provider:
      guessProvider(cfg) ?? (cfg.provider && cfg.provider !== 'custom' ? cfg.provider : null),
    imap_host: cfg.imap_host,
    imap_port: cfg.imap_port,
    imap_folder: cfg.imap_folder,
    smtp_host: cfg.smtp_host,
    smtp_port: cfg.smtp_port,
  }
}

export function EmailUnifiedSection() {
  const [cfg, setCfg] = useState<EmailConfig | null>(null)
  const [form, setForm] = useState<FormState | null>(null)
  const [loadErr, setLoadErr] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setLoadErr(false)
    authFetch('/api/email/config')
      .then((r) => (r.ok ? (r.json() as Promise<EmailConfig>) : null))
      .then((c) => {
        if (!c) {
          setLoadErr(true)
          return
        }
        setCfg(c)
        setForm((f) => f ?? formFrom(c))
      })
      .catch(() => setLoadErr(true))
  }

  useEffect(load, [])

  const setPassword = useCallback((v: string) => setForm((f) => f && { ...f, password: v }), [])
  const password = useSecretReveal({
    saved: !!cfg?.has_password,
    value: form?.password ?? '',
    onChange: setPassword,
    reveal: useCallback(() => revealConnectorSecret('email', 'password'), []),
  })

  const validPort = (n: number) => n >= 1 && n <= 65535
  const imapPortValid = !form || validPort(form.imap_port)
  const smtpPortValid = !form || validPort(form.smtp_port)
  const passwordValid = !form || !!form.password.trim() || !!cfg?.has_password
  const imapPortTouched = useTouched()
  const smtpPortTouched = useTouched()
  const passwordTouched = useTouched()

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      // One shared identity drives IMAP, SMTP and the alert recipient — no per-field override
      // anywhere (owner decision, 2026-08-18): the address is the login, the From and the
      // recipient, and TLS is always on (matches connectors/email_out/channel.py's own
      // behavior — port 465 is always implicit SSL there regardless of any flag, and every
      // other port needs STARTTLS to work at all, so there was never a real "off" case for a
      // NAMED provider; a genuinely custom/self-hosted server that needs something else is
      // out of scope for this form).
      const body: Record<string, unknown> = {
        address: form.email.trim(),
        provider: form.provider ?? 'custom',
        imap_host: form.imap_host.trim(),
        imap_port: form.imap_port,
        imap_folder: form.imap_folder.trim() || 'INBOX',
        smtp_host: form.smtp_host.trim(),
        smtp_port: form.smtp_port,
        use_tls: true,
        enabled: cfg.configured ? cfg.enabled : true,
      }
      const sharedPassword = unrevealed(form.password, password)
      if (sharedPassword) body.password = sharedPassword

      const r = await authFetch('/api/email/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the email settings.')
        return false
      }
      setForm((f) => f && { ...f, password: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: !!form && !!cfg && imapPortValid && smtpPortValid && passwordValid,
    values: form && { ...form, password: unrevealed(form.password, password) },
    baseline: cfg ? formFrom(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (loadErr) {
    return (
      <Text size="sm" tone="bad">
        Could not load the email settings.
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

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  const preset: EmailPreset | undefined = form.provider ? emailPreset(form.provider) : undefined
  // Tapping the chip that is already selected does nothing — the Custom chip is the way to a
  // self-hosted setup. (It used to deselect, so one stray tap saved a Gmail account as Custom.)
  const applyPreset = (key: string) => {
    if (form.provider === key) return
    const pr = emailPreset(key)
    if (!pr) return
    setForm(
      (f) =>
        f && {
          ...f,
          provider: key,
          imap_host: pr.imap_host,
          imap_port: pr.imap_port,
          smtp_host: pr.smtp_host,
          smtp_port: pr.smtp_port,
        },
    )
  }

  return (
    <Stack gap="--space-5">
      {/* ── Account (shared) ───────────────────────────────────────────── */}
      <Stack gap="--space-3">
        <Cluster gap="--space-2" align="center" className="conn-preset-row">
          <Text size="xs" tone="faint">
            Provider:
          </Text>
          {EMAIL_PRESETS.map((pr) => (
            <button
              key={pr.key}
              type="button"
              className={`preset-chip${form.provider === pr.key ? ' preset-chip--active' : ''}`}
              onClick={() => applyPreset(pr.key)}
            >
              {pr.label}
            </button>
          ))}
          <button
            type="button"
            className={`preset-chip${form.provider === null ? ' preset-chip--active' : ''}`}
            // Switching to Custom must put back the account's OWN host/port, not just drop
            // the preset marker — leaving whichever preset was last clicked sitting in the
            // (now-editable) fields is exactly how a real Gmail account ended up saved with
            // `imap.fastmail.com`: click Fastmail to look at it, click Custom to back out,
            // Save without noticing the fields never actually reverted.
            onClick={() =>
              setForm(
                (f) =>
                  f && {
                    ...f,
                    provider: null,
                    imap_host: cfg.imap_host,
                    imap_port: cfg.imap_port,
                    smtp_host: cfg.smtp_host,
                    smtp_port: cfg.smtp_port,
                  },
              )
            }
          >
            Custom
          </button>
        </Cluster>
        {preset && (
          <ol className="conn-preset-steps">
            {preset.steps.map((step) => (
              <li key={step.text}>
                <Text size="xs" tone="faint">
                  {step.text}{' '}
                  {step.link && (
                    <a href={step.link.href} target="_blank" rel="noreferrer">
                      {step.link.label} ↗
                    </a>
                  )}
                </Text>
              </li>
            ))}
          </ol>
        )}
        {!preset && (
          <Stack gap="--space-4">
            <Grid min={140} gap="--space-4">
              <FormField label="IMAP host" htmlFor="email-imap-host" required>
                <TextField
                  id="email-imap-host"
                  value={form.imap_host}
                  placeholder="imap.example.com"
                  onChange={(e) => set('imap_host', e.target.value)}
                />
              </FormField>
              <FormField
                label="IMAP port"
                htmlFor="email-imap-port"
                required
                info="993 is the standard IMAPS port — right for almost every server."
                error={
                  imapPortTouched.touched && !imapPortValid
                    ? 'Enter a port between 1 and 65535.'
                    : undefined
                }
              >
                <NumberField
                  id="email-imap-port"
                  value={form.imap_port}
                  onChange={(e) => set('imap_port', Number(e.target.value))}
                  onBlur={imapPortTouched.onBlur}
                />
              </FormField>
            </Grid>
            <Grid min={140} gap="--space-4">
              <FormField label="SMTP host" htmlFor="email-smtp-host" required>
                <TextField
                  id="email-smtp-host"
                  value={form.smtp_host}
                  placeholder="smtp.example.com"
                  onChange={(e) => set('smtp_host', e.target.value)}
                />
              </FormField>
              <FormField
                label="SMTP port"
                htmlFor="email-smtp-port"
                required
                info="587 is the standard SMTP submission port — right for almost every server."
                error={
                  smtpPortTouched.touched && !smtpPortValid
                    ? 'Enter a port between 1 and 65535.'
                    : undefined
                }
              >
                <NumberField
                  id="email-smtp-port"
                  value={form.smtp_port}
                  onChange={(e) => set('smtp_port', Number(e.target.value))}
                  onBlur={smtpPortTouched.onBlur}
                />
              </FormField>
            </Grid>
          </Stack>
        )}
        <Grid min={200} gap="--space-4">
          <FormField label="Email / username" htmlFor="email-user" required>
            <TextField
              id="email-user"
              value={form.email}
              placeholder="you@example.com"
              onChange={(e) => set('email', e.target.value)}
            />
          </FormField>
          <FormField
            label="App password"
            htmlFor="email-pwd"
            required
            info="Use an app-specific password, not your regular account password — most providers block plain-password sign-in once 2FA is on."
            help={password.caption || undefined}
            error={
              password.error ||
              (passwordTouched.touched && !passwordValid
                ? 'An app-specific password is required.'
                : undefined)
            }
          >
            <PasswordField
              id="email-pwd"
              autoComplete="off"
              value={form.password}
              placeholder={cfg.has_password ? '••••••••' : 'app-specific password'}
              onChange={(e) => set('password', e.target.value)}
              onBlur={passwordTouched.onBlur}
              {...password.fieldProps}
            />
          </FormField>
        </Grid>
      </Stack>

      {/* ── Receive (IMAP-specific) ────────────────────────────────────── */}
      <Stack gap="--space-3">
        {cfg.last_error && (
          <Text size="xs" tone="bad">
            Last error: {cfg.last_error}
          </Text>
        )}
        <FormField
          label="Mail folder"
          htmlFor="email-imap-folder"
          info="INBOX works for almost every provider and reads all your email. Want to limit what Lucius sees? Point this at a specific folder instead — for example, one you filter relevant emails into."
        >
          <TextField
            id="email-imap-folder"
            value={form.imap_folder}
            placeholder="INBOX"
            onChange={(e) => set('imap_folder', e.target.value)}
          />
        </FormField>
      </Stack>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
