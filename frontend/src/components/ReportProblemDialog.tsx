import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { fetchReportDraft, sendProblemReport } from '../api/problemReports'
import {
  closeReportProblem,
  currentReportRequest,
  subscribeReportProblem,
  type ReportRequest,
} from '../state/reportProblem'
import {
  COMMUNITY_URL,
  emailHref,
  REPORT_LIMITS,
  reportText,
  SUPPORT_EMAIL,
  type ProblemReport,
} from '../utils/problemReport'
import { Button } from './ui/Button'
import { Checkbox } from './ui/Checkbox'
import { FormField } from './ui/FormField'
import { Modal } from './ui/Modal'
import { Loading } from './ui'
import { Stack } from './ui/Stack'
import { openExternal } from '../platform/desktop'
import { Text } from './ui/Text'
import { TextArea } from './ui/TextArea'
import { TextField } from './ui/TextField'

type Phase = 'loading' | 'editing' | 'sending' | 'sent'

// What the page can say for itself when the server does not answer (the boot-unreachable screen):
// no logs, no masking, and no way to send. The report can still be copied or emailed.
function offlineDraft(error: string | undefined): ProblemReport {
  return {
    description: '',
    contact_email: null,
    app_version: 'unknown (the server did not answer)',
    platform: window.navigator.userAgent.slice(0, 128),
    mode: 'web',
    screen: window.location.pathname.slice(0, REPORT_LIMITS.screen),
    error: error?.slice(0, REPORT_LIMITS.error) || null,
    logs: null,
  }
}

function ReportProblemDialog({
  trigger,
  onClose,
}: {
  trigger: ReportRequest
  onClose: () => void
}) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [base, setBase] = useState<ProblemReport | null>(null)
  const [offline, setOffline] = useState(false)
  const [description, setDescription] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [screen, setScreen] = useState('')
  const [logs, setLogs] = useState('')
  const [includeLogs, setIncludeLogs] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [reference, setReference] = useState<string | null>(null)
  const statusRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    fetchReportDraft(trigger.error)
      .then((draft) => ({ draft, offline: false }))
      .catch(() => ({ draft: offlineDraft(trigger.error), offline: true }))
      .then(({ draft, offline: unreachable }) => {
        if (!live) return
        setBase(draft)
        setOffline(unreachable)
        setError(draft.error ?? '')
        setScreen(draft.screen)
        setLogs(draft.logs ?? '')
        setIncludeLogs(draft.logs !== null)
        setPhase('editing')
      })
    return () => {
      live = false
    }
  }, [trigger])

  // The message sits above the form; a Send pressed from the foot of a long form must still be answered in view.
  useEffect(() => {
    if (failure || note) statusRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [failure, note])
  // The thank-you view replaces the form inside the SAME dialog, so the dialog's open-focus step
  // doesn't run again: without this, focus stays on the card where the removed Send button was.
  useEffect(() => {
    if (phase === 'sent')
      document.querySelector<HTMLElement>('[data-report-close]')?.focus({ preventScroll: true })
  }, [phase])

  // The preview and the payload are one object: what the exact-data box shows is what Send posts.
  const report: ProblemReport | null = useMemo(
    () =>
      base && {
        description: description.trim(),
        contact_email: email.trim() || null,
        app_version: base.app_version,
        platform: base.platform,
        mode: base.mode,
        screen: screen.trim(),
        error: error.trim() || null,
        logs: includeLogs && logs.trim() ? logs.trim() : null,
      },
    [base, description, email, screen, error, logs, includeLogs],
  )

  const busy = phase !== 'editing'
  const ready = Boolean(report?.description) && !busy

  async function send() {
    if (!report || !ready) return
    setPhase('sending')
    setFailure(null)
    setNote(null)
    try {
      const sent = await sendProblemReport(report)
      setReference(sent.id)
      setPhase('sent')
    } catch (failed) {
      setFailure(failed instanceof Error ? failed.message : 'The report could not be sent.')
      setPhase('editing')
    }
  }

  async function copy() {
    if (!report) return
    try {
      await window.navigator.clipboard.writeText(reportText(report))
      setNote('Copied. Paste it wherever you like.')
    } catch {
      setNote('Your browser blocked copying. Open the exact data below and copy it from there.')
    }
  }

  async function mail() {
    if (!report) return
    let copied = true
    try {
      await window.navigator.clipboard.writeText(reportText(report))
    } catch {
      copied = false
    }
    window.location.href = emailHref(report)
    setNote(
      copied
        ? 'Your mail app should open with the start of the report. The whole report, logs included, is on your clipboard to paste.'
        : 'Your mail app should open with the start of the report.',
    )
  }

  if (phase === 'sent') {
    return (
      <Modal
        open
        onClose={onClose}
        title="Thank you. Your report was sent."
        size="sm"
        footer={
          <Button size="sm" variant="primary" data-report-close onClick={onClose}>
            Close
          </Button>
        }
      >
        <Stack gap="--space-4">
          <Text as="p" size="sm">
            We will investigate and fix this within 48 hours!
          </Text>
          {reference && (
            <Text as="p" size="sm" tone="muted">
              Reference <span className="report-problem__mono">{reference}</span>
            </Text>
          )}
        </Stack>
      </Modal>
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Report a problem"
      size="lg"
      footer={
        <>
          <Button size="sm" variant="ghost" onClick={onClose} disabled={phase === 'sending'}>
            Cancel
          </Button>
          {/* The fallbacks appear only once sending can't work (it failed, or the server is
              down); before that, Send is the one action. */}
          {(failure || offline) && report && (
            <>
              <Button size="sm" onClick={() => void copy()} disabled={!ready}>
                Copy report
              </Button>
              <Button size="sm" onClick={() => void mail()} disabled={!ready}>
                Email instead
              </Button>
            </>
          )}
          {report && !offline && (
            <Button
              size="sm"
              variant="primary"
              loading={phase === 'sending'}
              disabled={!ready}
              onClick={() => void send()}
            >
              Send report
            </Button>
          )}
        </>
      }
    >
      {phase === 'loading' || !report ? (
        <Loading label="Preparing your report…" />
      ) : (
        <Stack gap="--space-5">
          {offline && (
            <Text as="p" size="sm" tone="bad">
              Can&rsquo;t reach the server, so nothing was masked. Check it, then copy it or email
              it to <span className="report-problem__address">{SUPPORT_EMAIL}</span>.
            </Text>
          )}
          <div ref={statusRef} className="report-problem__status" role="status" aria-live="polite">
            {failure && (
              <Text as="p" size="sm" tone="bad">
                {failure} You can copy the report or email it to{' '}
                <span className="report-problem__address">{SUPPORT_EMAIL}</span>.
              </Text>
            )}
            {!failure && note && (
              <Text as="p" size="sm" tone="muted">
                {note}
              </Text>
            )}
          </div>
          <FormField label="What happened?" htmlFor="report-description" required>
            <TextArea
              id="report-description"
              rows={4}
              maxLength={REPORT_LIMITS.description}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              autoFocus
            />
          </FormField>
          <FormField
            label="Email (optional)"
            htmlFor="report-email"
            help="We’ll only use it to tell you when the fix is out."
          >
            <TextField
              id="report-email"
              type="email"
              autoComplete="off"
              maxLength={REPORT_LIMITS.email}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </FormField>
          {/* Only a report opened from an error screen has one; from the sidebar it would be an
              empty, redundant second "what happened". */}
          {base?.error && (
            <FormField label="Error" htmlFor="report-error">
              <TextArea
                id="report-error"
                rows={3}
                className="report-problem__mono"
                maxLength={REPORT_LIMITS.error}
                value={error}
                onChange={(event) => setError(event.target.value)}
              />
            </FormField>
          )}
          <dl className="report-problem__meta" aria-label="Sent with the report">
            <dt>Version</dt>
            <dd>{report.app_version}</dd>
            <dt>Platform</dt>
            <dd>{report.platform}</dd>
            <dt>Mode</dt>
            <dd>{report.mode}</dd>
            {screen && (
              <>
                <dt>Screen</dt>
                <dd>{screen}</dd>
              </>
            )}
          </dl>
          <Checkbox
            id="report-include-logs"
            label="Include recent logs"
            description={base?.logs ? 'Amounts, names and keys are masked.' : 'No logs yet.'}
            checked={includeLogs}
            disabled={!base?.logs}
            onChange={(event) => setIncludeLogs(event.target.checked)}
          />
          <TextArea
            aria-label="Recent logs"
            rows={8}
            className="report-problem__mono"
            maxLength={REPORT_LIMITS.logs}
            spellCheck={false}
            value={logs}
            disabled={!includeLogs}
            onChange={(event) => setLogs(event.target.value)}
          />
          <details className="report-problem__exact">
            <summary>Show exactly what is sent</summary>
            <pre>{JSON.stringify(report, null, 2)}</pre>
          </details>
          <p className="report-problem__community">
            Prefer to chat?{' '}
            <a
              href={COMMUNITY_URL}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(event) => {
                event.preventDefault()
                void openExternal(COMMUNITY_URL)
              }}
            >
              Join us on Discord
            </a>
          </p>
        </Stack>
      )}
    </Modal>
  )
}

/** Mounted once at the root, outside the error boundary, so a crashed app can still be reported. */
export function ReportProblemHost() {
  const trigger = useSyncExternalStore(subscribeReportProblem, currentReportRequest)
  return trigger ? <ReportProblemDialog trigger={trigger} onClose={closeReportProblem} /> : null
}
