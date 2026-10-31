// A problem report, as the browser dialog shows it and sends it. The shape and limits match
// backend/models/problem_report.py (and the desktop shell's copy in desktop/src/main/problem-report.ts);
// the text formats below are the same ones the desktop app copies and emails, so a report reads
// identically whichever way it was made.

export const SUPPORT_EMAIL = 'support@opennetworth.ai'
export const COMMUNITY_URL = 'https://discord.gg/eCGBd4zg3B'

export const REPORT_LIMITS = {
  description: 5000,
  email: 254,
  screen: 256,
  error: 4000,
  logs: 65536,
} as const

export type ReportMode = 'desktop' | 'web' | 'server'

export interface ProblemReport {
  description: string
  contact_email: string | null
  app_version: string
  platform: string
  mode: ReportMode
  screen: string
  error: string | null
  logs: string | null
}

const EMAIL_BODY_CHARS = 1000

/** The report as plain text: what Copy report puts on the clipboard and Email instead pastes in. */
export function reportText(report: ProblemReport): string {
  const lines = [
    'Open Net Worth problem report',
    '',
    `Version: ${report.app_version}`,
    `Platform: ${report.platform}`,
    `Mode: ${report.mode}`,
    `Screen: ${report.screen || '(not recorded)'}`,
    `Contact: ${report.contact_email ?? '(none given)'}`,
    '',
    'What went wrong',
    report.description,
  ]
  if (report.error) lines.push('', 'Error', report.error)
  if (report.logs) lines.push('', 'Recent logs (redacted)', report.logs)
  return `${lines.join('\n')}\n`
}

/** A mail draft carries the top of the report; the whole text, logs included, goes on the clipboard. */
export function emailHref(report: ProblemReport): string {
  const subject = `Open Net Worth problem report (${report.app_version})`
  const body = reportText(report).slice(0, EMAIL_BODY_CHARS)
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}
