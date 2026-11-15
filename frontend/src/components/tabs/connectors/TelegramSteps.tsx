import type { ReactNode } from 'react'
import { Stack, Text } from '../../ui'
import { PendingApprovals, type AccessPolicy, type PendingRow } from './ChatAccessPolicy'

// The pieces of the Telegram setup that are not form fields: the numbered step frame, the
// bot's live status, and step 3 — getting yourself (or a friend) in once the bot is up.
//
// Step 3 is the reason this file exists. Setup used to end at Save with nothing on screen
// saying what came next: under Pairing the request box stayed invisible until a request
// arrived (and tapping Start in Telegram created none), under Allowlist you needed a chat ID
// from a bot that wasn't running yet. Now Save is the middle of the sequence, and the form
// shows the next move — a link to the bot and a visible "waiting" state — right where the
// approval will appear.

export type Connection = 'connected' | 'connecting' | 'off'

export function SetupStep({
  n,
  title,
  done = false,
  children,
}: {
  n: number
  title: string
  done?: boolean
  children: ReactNode
}) {
  return (
    <section className={`tg-step${done ? ' tg-step--done' : ''}`} aria-label={title}>
      <span className="tg-step__num" aria-hidden="true">
        {done ? '✓' : n}
      </span>
      <Stack gap="--space-3">
        <Text size="sm" weight={600} className="tg-step__title">
          {title}
        </Text>
        {children}
      </Stack>
    </section>
  )
}

function botLink(username: string): string {
  return `https://t.me/${encodeURIComponent(username)}`
}

function OpenBotLink({ username, quiet = false }: { username: string; quiet?: boolean }) {
  if (!username) return null
  return (
    <a
      className={`ui-btn ${quiet ? 'btn-secondary' : 'btn-primary'} ui-btn--sm tg-open-link`}
      href={botLink(username)}
      target="_blank"
      rel="noreferrer"
    >
      Open @{username} in Telegram ↗
    </a>
  )
}

function StatusLine({
  tone,
  size = 'xs',
  children,
}: {
  tone: 'good' | 'muted' | 'warn'
  size?: 'xs' | 'sm'
  children: ReactNode
}) {
  return (
    <span role="status" className={`ui-text ui-text--${size} ui-text--${tone} tg-status`}>
      {children}
    </span>
  )
}

/** One line under the token: is the bot actually up, and as whom. */
export function BotStatus({ connection, username }: { connection: Connection; username: string }) {
  if (connection === 'connected') {
    return (
      <StatusLine tone="good">
        <span className="tg-status__dot" aria-hidden="true" />
        {username ? `Online as @${username}` : 'Online'}
      </StatusLine>
    )
  }
  if (connection === 'connecting') {
    return (
      <StatusLine tone="muted">
        <span className="ui-btn__spinner" aria-hidden="true" />
        Connecting to Telegram…
      </StatusLine>
    )
  }
  return (
    <StatusLine tone="warn">
      The bot isn&apos;t running. Check the token, or restart Open Net Worth.
    </StatusLine>
  )
}

function Waiting({ label }: { label: string }) {
  return (
    <StatusLine tone="muted">
      <span className="ui-btn__spinner" aria-hidden="true" />
      {label}
    </StatusLine>
  )
}

/** What Save will do — before the first one, so Save reads as "bring the bot online" rather
 *  than "finish"; on a running bot, that it switches who can chat. */
export function BeforeSaveHint({
  policy,
  firstSave,
}: {
  policy: AccessPolicy
  firstSave: boolean
}) {
  let text: string
  if (!firstSave) {
    text =
      policy === 'allowlist'
        ? 'Save to switch to Allowlist — only the chat IDs below will reach Lucius.'
        : 'Save to switch to Pairing — anyone new who messages your bot will ask you first.'
  } else {
    text =
      policy === 'allowlist'
        ? 'Click Save to bring your bot online. Then send it /id in Telegram and paste the number it replies with below.'
        : 'Click Save to bring your bot online. Then message it from Telegram and approve yourself right here — no second save.'
  }
  return (
    <Text size="sm" tone="faint">
      {text}
    </Text>
  )
}

export function ConnectStep({
  policy,
  username,
  peopleIn,
  justApproved,
  onApproved,
}: {
  policy: AccessPolicy
  username: string
  peopleIn: number
  justApproved: { who: string; welcomed: boolean } | null
  onApproved: (row: PendingRow, welcomed: boolean) => void
}) {
  const bot = username ? `@${username}` : 'your bot'
  if (policy === 'allowlist') {
    return (
      <div className="tg-callout">
        <Stack gap="--space-3">
          <Text size="sm">
            Open {bot} in Telegram and send <code>/id</code>. It replies with your chat ID — paste
            it into <strong>Allowed chat IDs</strong> below and Save.
          </Text>
          <OpenBotLink username={username} />
        </Stack>
      </div>
    )
  }
  return (
    <Stack gap="--space-3">
      {justApproved && (
        <StatusLine tone="good" size="sm">
          ✓ {justApproved.who} can chat with Lucius now
          {justApproved.welcomed ? ' — the bot just told them.' : '.'}
        </StatusLine>
      )}
      <PendingApprovals
        connector="telegram"
        active
        onApproved={onApproved}
        empty={
          <div className="tg-callout">
            <Stack gap="--space-3">
              <Text size="sm">
                {peopleIn === 0
                  ? `Open ${bot} in Telegram and tap Start. Your request shows up here — approve it and you're in.`
                  : `To add someone, have them open ${bot} and tap Start. Their request shows up here to approve.`}
              </Text>
              <OpenBotLink username={username} quiet={peopleIn > 0} />
              {peopleIn === 0 && <Waiting label="Waiting for your first message…" />}
            </Stack>
          </div>
        }
      />
    </Stack>
  )
}
