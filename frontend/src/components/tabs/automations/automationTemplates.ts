import {
  BellRing,
  CalendarRange,
  ChartNoAxesCombined,
  FileText,
  Link2,
  Gauge,
  Mail,
  ReceiptText,
  Sunrise,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'
import type { AutomationDraft } from '../../../api/automations'

export type AutomationTemplate = AutomationDraft & { key: string }

/** "Recommended": what a new user most likely wants — getting transactions in, then the reports.
 *  Shown first and always; the other lists exclude them. */
export const RECOMMENDED_TEMPLATES: AutomationTemplate[] = [
  {
    key: 'mail_ledger',
    name: 'Auto-log transactions from email',
    description:
      'Reads bank, card and trade confirmation mail and logs it — payees resolved, nothing ' +
      'double-booked.',
    instructions:
      'Check my email for bank, card and trade confirmations and log them, ' +
      'using the Log transactions from email skill. Message me with what you logged, or if ' +
      'anything went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 43200 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'daily-briefing',
    name: 'Daily briefing',
    description: "A short, friendly update on yesterday's money and investments.",
    instructions:
      'Send me my daily briefing on yesterday, using the Daily briefing skill. ' +
      "Send it on Telegram, or by webhook if that's what I use; if I have neither, show it " +
      'here.',
    trigger: { kind: 'schedule', type: 'daily', time: '09:00' },
    // Read-only reports: nothing to approve, and nobody is there at 9am to approve it.
    permission_mode: 'auto',
  },
  {
    key: 'weekly-report',
    name: 'Weekly report',
    description: 'How last week went: spending, bills, investments and anything unusual.',
    instructions:
      'Send me my weekly report for last week, using the Weekly report ' +
      'skill. Add a short note of a few lines to go with it. Send it on Telegram, or by ' +
      "webhook if that's what I use; if I have neither, show it here. Don't change anything " +
      'in my records.',
    trigger: { kind: 'schedule', type: 'weekly', day: 'mon', time: '08:30' },
    permission_mode: 'auto',
  },
  {
    key: 'monthly-report',
    name: 'Monthly report',
    description: 'How last month went: what you earned and kept, spending and investments.',
    instructions:
      'Send me my monthly report for last month, using the Monthly ' +
      'report skill. Add a short note of a few lines to go with it. Send it on Telegram, or ' +
      "by webhook if that's what I use; if I have neither, show it here. Don't change " +
      'anything in my records.',
    trigger: { kind: 'schedule', type: 'monthly', day: 1, time: '09:30' },
    permission_mode: 'auto',
  },
]

export const AUTOMATION_TEMPLATES: AutomationTemplate[] = [
  {
    key: 'subscriptions',
    name: 'Subscription creep',
    description: 'Flags renewals that came in higher than the time before.',
    instructions:
      'List every subscription that renewed at a higher price than the time before, with the old and new amount, plus anything I have not used in 60 days.',
    trigger: { kind: 'schedule', type: 'monthly', day: 1, time: '09:00' },
  },
  {
    key: 'pacing',
    name: 'Budget pacing',
    description: 'Which envelopes are running hot.',
    instructions:
      'Compare each envelope to where it should be this far into the month. Name the two most at risk and the daily amount they need to slow to.',
    trigger: { kind: 'schedule', type: 'weekly', day: 'fri', time: '17:00' },
  },
  {
    key: 'floor',
    name: 'Cash floor guard',
    description: 'Warns when the buffer looks thin for the fortnight ahead.',
    instructions:
      'Project checking forward two weeks using known commitments and typical spending. If the projected low point falls under my floor, warn me with the date and the shortfall.',
    trigger: { kind: 'schedule', type: 'daily', time: '07:00' },
  },
  {
    key: 'large-charge',
    name: 'Large charge alert',
    description: 'Fires the moment a charge over your threshold lands.',
    instructions:
      'Tell me the payee, the account, whether I have used them before, and whether the charge looks like a duplicate. Two lines unless something is wrong.',
    trigger: {
      kind: 'event',
      event: 'txn',
      when: {
        conditions: [
          { field: 'kind', op: 'is', value: 'expense' },
          { field: 'amount', op: 'over', value: 500 },
        ],
      },
      limit: 'hour',
    },
  },
  {
    key: 'new-payee',
    name: 'New payee check',
    description: 'Every first-time payee gets a second look.',
    instructions:
      'If this transaction is from a payee I have never used before, tell me who they are, what the charge was for if you can tell, and whether the amount is typical for that kind of merchant. Otherwise do nothing.',
    trigger: { kind: 'event', event: 'txn', when: {}, limit: 'each' },
  },
  {
    key: 'drift',
    name: 'Portfolio drift',
    description: 'Checks allocation against target.',
    instructions:
      'Compare my actual allocation to the target mix. Show the drift per sleeve in percentage points and the trade sizes that would bring it back, but do not place anything.',
    trigger: { kind: 'schedule', type: 'weekly', day: 'mon', time: '09:00' },
  },
]

/** One template per OFFERED connector (Yodlee, Alpaca and IBKR Flex excluded — `disabled=True`
 *  server-side, hidden from the directory; Alpaca's and IBKR Flex's kept in `RETIRED_SYNC_TEMPLATES`). Display names are the exact provider labels from
 *  `backend/services/aggregation/providers.py`, not the raw connector key. Email's template
 *  (`mail_ledger`) is in RECOMMENDED_TEMPLATES; its instructions live in the
 *  log-transactions-from-email starter skill, so a fix reaches every unedited copy. */
export const CONNECTOR_SYNC_TEMPLATES: AutomationTemplate[] = [
  {
    key: 'sync_simplefin',
    name: 'Sync SimpleFIN',
    description: 'Fetch new SimpleFIN transactions and book them.',
    instructions:
      'Fetch new SimpleFIN transactions and book them. Use the linked account ' +
      'for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_plaid',
    name: 'Sync Plaid',
    description: 'Fetch new Plaid transactions and book them.',
    instructions:
      'Fetch new Plaid transactions and book them. Use the linked account ' +
      'for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_lunch_money',
    name: 'Sync Lunch Money',
    description: 'Fetch new Lunch Money transactions and book them.',
    instructions:
      'Fetch new Lunch Money transactions and book them. Use the linked account ' +
      'for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_lunch_flow',
    name: 'Sync Lunch Flow',
    description: 'Fetch new Lunch Flow transactions and book them.',
    instructions:
      'Fetch new Lunch Flow transactions and book them. Use the linked account ' +
      'for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_ynab',
    name: 'Sync YNAB',
    description: 'Fetch new YNAB transactions and book them.',
    instructions:
      'Fetch new YNAB transactions and book them. Use the linked account ' +
      'for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_enable_banking',
    name: 'Sync Enable Banking',
    description: 'Fetch new Enable Banking transactions and book them.',
    instructions:
      'Fetch new Enable Banking transactions and book them. Use the linked ' +
      'account for each one. If an account isn’t linked, ask me instead of guessing. Pick a category ' +
      'from the merchant or the bank’s category hint when it clearly matches one of mine ' +
      'and the payee has no default category of its own; otherwise leave it empty. If the bank changed something already booked, ' +
      'bring it in line, but never override something I changed by hand. If the bank ' +
      'withdrew a transaction it already booked, flag it and tell me. Never delete ' +
      'anything. Message me only when something was ' +
      'booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
]

export const ALL_TEMPLATES: AutomationTemplate[] = [
  ...RECOMMENDED_TEMPLATES,
  ...AUTOMATION_TEMPLATES,
  ...CONNECTOR_SYNC_TEMPLATES,
]

/** The template that puts a connector to work — Email logs from the mailbox, a bank or broker
 *  syncs. Null for a connector with nothing to automate (web search, Telegram…). */
export function templateKeyForConnector(providerKind: string): string | null {
  const key = providerKind === 'email' ? 'mail_ledger' : `sync_${providerKind}`
  return ALL_TEMPLATES.some((t) => t.key === key) ? key : null
}

/** Keyed, never positional — reordering the gallery must not move an icon onto another card. */
export const TEMPLATE_ICONS: Record<string, LucideIcon> = {
  'daily-briefing': Sunrise,
  'weekly-report': CalendarRange,
  'monthly-report': FileText,
  subscriptions: BellRing,
  pacing: Gauge,
  floor: WalletCards,
  'large-charge': BellRing,
  'new-payee': ReceiptText,
  drift: ChartNoAxesCombined,
  mail_ledger: Mail,
  ...Object.fromEntries(CONNECTOR_SYNC_TEMPLATES.map((template) => [template.key, Link2])),
}

/** Sync templates for retired connectors — kept so a revert is one move back, never shown.
 *  IBKR Flex: ADR 2026-10-29-retire-ibkr-flex-for-ibkrs-own-mcp-server. */
export const RETIRED_SYNC_TEMPLATES: AutomationTemplate[] = [
  {
    key: 'sync_alpaca',
    name: 'Sync Alpaca',
    description: 'Fetch Alpaca positions and keep the portfolio current.',
    instructions:
      'Fetch my Alpaca positions and cash. Add or update positions from the ' +
      'snapshot. Never remove a position; tell me about any that are missing. Message me ' +
      'only when something changed or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 3600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
  {
    key: 'sync_ibkr',
    name: 'Sync Interactive Brokers',
    description: 'Fetch IBKR activity and book trades and cash events.',
    instructions:
      'Fetch new IBKR activity and book it. Add or update positions from the ' +
      'snapshot. Never remove a position; tell me about any that are missing. Use the linked ' +
      'account for each transaction; if it isn’t linked, ask me instead of guessing. If ' +
      'something changed after booking, bring it in line, but never override something I ' +
      'changed by hand. If something was withdrawn, flag it and tell me. Never delete ' +
      'anything. Message me only when something was booked or something went wrong.',
    trigger: { kind: 'schedule', type: 'interval', seconds: 21600 },
    execution: { kind: 'lucius' },
    permission_mode: 'auto',
  },
]
