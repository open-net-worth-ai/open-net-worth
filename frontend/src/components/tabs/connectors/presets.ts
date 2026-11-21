// Connector presets (P3-CONN-S3 catalog expansion). Thin, declarative adapters —
// NO bespoke per-provider feature code: a preset is just a set of field values the
// generic Email / Webhook config forms prefill. The user still enters their own
// credentials; the preset only spares them the host/port/URL-shape lookup.

export interface EmailPreset {
  key: string
  label: string
  imap_host: string
  imap_port: number
  smtp_host: string
  smtp_port: number
  /** How to get the app password this provider requires — numbered steps under the chips. */
  steps: EmailPresetStep[]
}

/** One setup step; `link` opens the provider's own page for that step in a new tab. */
export interface EmailPresetStep {
  text: string
  link?: { label: string; href: string }
}

// IMAP is uniformly port 993 (implicit TLS) across all four. SMTP submission is either
// 587 (STARTTLS) or 465 (implicit TLS) depending on the provider — both are genuinely
// secure (backend/connectors/email_out/channel.py handles either correctly), so this
// picks whichever each provider's own docs list as its primary/first-listed option;
// Gmail supports both equally but 587 keeps this list's story consistent (3 of 4 on
// 587) with the port field's own help text ("587 is the standard... almost every
// server"). All four refuse the account's normal password over IMAP/SMTP, so each preset
// carries the app-password steps with a direct link to the provider's own page.
export const EMAIL_PRESETS: EmailPreset[] = [
  {
    key: 'gmail',
    label: 'Gmail',
    imap_host: 'imap.gmail.com',
    imap_port: 993,
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    steps: [
      {
        text: 'Your normal Gmail password will not work here — Google requires an app password.',
      },
      {
        text: 'Turn on 2-Step Verification (app passwords only appear once it is on).',
        link: {
          label: 'Open 2-Step Verification',
          href: 'https://myaccount.google.com/signinoptions/twosv',
        },
      },
      {
        text: 'Create an app password named "Open Net Worth" and paste the 16-letter code below.',
        link: { label: 'Open App passwords', href: 'https://myaccount.google.com/apppasswords' },
      },
    ],
  },
  {
    key: 'outlook',
    label: 'Outlook',
    imap_host: 'outlook.office365.com',
    imap_port: 993,
    smtp_host: 'smtp-mail.outlook.com',
    smtp_port: 587,
    steps: [
      {
        text: 'Your normal Microsoft password will not work here — use an app password.',
      },
      {
        text: 'Turn on two-step verification for your Microsoft account.',
        link: { label: 'Open Microsoft security', href: 'https://account.microsoft.com/security' },
      },
      {
        text: 'Create an app password and paste it below.',
        link: { label: 'Open App passwords', href: 'https://account.live.com/proofs/AppPassword' },
      },
    ],
  },
  {
    key: 'icloud',
    label: 'iCloud',
    imap_host: 'imap.mail.me.com',
    imap_port: 993,
    smtp_host: 'smtp.mail.me.com',
    smtp_port: 587,
    steps: [
      {
        text: 'Your Apple Account password will not work here — use an app-specific password.',
      },
      {
        text: 'Sign in, open Sign-In and Security → App-Specific Passwords, create one and paste it below.',
        link: { label: 'Open Apple Account', href: 'https://account.apple.com/account/manage' },
      },
    ],
  },
  {
    key: 'fastmail',
    label: 'Fastmail',
    imap_host: 'imap.fastmail.com',
    imap_port: 993,
    smtp_host: 'smtp.fastmail.com',
    smtp_port: 465,
    steps: [
      {
        text: 'Your normal Fastmail password will not work here — use an app password.',
      },
      {
        text: 'Open Privacy & Security → Manage app passwords, create one with Mail (IMAP/POP/SMTP) access and paste it below.',
        link: {
          label: 'Open Fastmail settings',
          href: 'https://app.fastmail.com/settings/security',
        },
      },
    ],
  },
]

export function emailPreset(key: string | undefined): EmailPreset | undefined {
  return EMAIL_PRESETS.find((p) => p.key === key)
}

export interface WebhookPreset {
  key: string
  label: string
  /** Placeholder URL shape shown in the URL field. */
  url_placeholder: string
  /** Short hint on where to get the URL. */
  note: string
}

// Slack + Discord both accept a plain incoming-webhook POST with a JSON body, which
// is exactly what the webhook channel emits — so they are pure presets (URL shape +
// where-to-find-it), no custom transport.
export const WEBHOOK_PRESETS: WebhookPreset[] = [
  {
    key: 'slack',
    label: 'Slack',
    url_placeholder: 'https://hooks.slack.com/services/T…/B…/…',
    note: 'Create an Incoming Webhook in your Slack app → Incoming Webhooks, then paste its URL.',
  },
  {
    key: 'discord',
    label: 'Discord',
    url_placeholder: 'https://discord.com/api/webhooks/…/…',
    note: 'Channel → Edit → Integrations → Webhooks → New Webhook → Copy Webhook URL.',
  },
]

export function webhookPreset(key: string | undefined): WebhookPreset | undefined {
  return WEBHOOK_PRESETS.find((p) => p.key === key)
}
