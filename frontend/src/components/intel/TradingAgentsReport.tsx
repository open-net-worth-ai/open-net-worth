import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { TA_FIELD_LABEL, TA_REPORT_SECTIONS } from '../../types/tradingAgentsConstants'
import type { TaReportField, TaRun } from '../../types/tradingAgents'
import { fCompact, fCurrency, fDate, timeAgo } from '../../utils/format'
import { condenseDecision, modelRef, modelText, runProviders } from '../../utils/tradingAgents'
import { Badge, Button, Cluster, Stack, Tag, Text } from '../ui'
import { SAFE_MARKDOWN_COMPONENTS } from '../ui/safeMarkdown'
import { TradingAgentsRating } from './TradingAgentsRating'
import { TradingAgentsShared } from './TradingAgentsShared'

/** The cap the backend keeps a report to (`runs.MAX_REPORT_CHARS`). */
const REPORT_CAP_TEXT = '40,000'

/** Report text is generated from web data: HTML is dropped, images go through the safe
 *  renderer, links open away from the app. */
export function ReportMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      skipHtml
      remarkPlugins={[remarkGfm]}
      components={{
        ...SAFE_MARKDOWN_COMPONENTS,
        a: ({ href, children: label }) => (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {label}
          </a>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  )
}

function ReportRow({
  label,
  parts,
  truncated,
}: {
  label: string
  parts: { field: TaReportField | null; text: string }[]
  truncated: TaReportField[]
}) {
  return (
    <details className="ta-disclose">
      <summary>{label}</summary>
      <div className="ta-disclose__body">
        <Stack gap="--space-4">
          {parts.map(({ field, text }) => (
            <div key={field ?? 'decision'} className="ta-report">
              {parts.length > 1 && field && (
                <Text size="xs" tone="faint" weight={600}>
                  {TA_FIELD_LABEL[field]}
                </Text>
              )}
              <ReportMarkdown>{text}</ReportMarkdown>
              {field && truncated.includes(field) && (
                <Text size="xs" tone="faint">
                  Shortened at {REPORT_CAP_TEXT} characters.
                </Text>
              )}
            </div>
          ))}
        </Stack>
      </div>
    </details>
  )
}

function footerParts(run: TaRun): string[] {
  const { deep, quick, transports } = run.config_snapshot
  const models = [
    deep ? modelRef(deep, transports?.deep) : null,
    quick && quick !== deep ? modelRef(quick, transports?.quick) : null,
  ].flatMap((m) => (m ? [modelText(m)] : []))
  return [
    ...models,
    `${fCurrency(run.cost_usd, 'USD')} metered`,
    run.plan_included_calls > 0 ? `${run.plan_included_calls} plan-included calls` : '',
    `${run.llm_calls} calls`,
    `${fCompact(run.input_tokens)} tokens in / ${fCompact(run.output_tokens)} out`,
  ].filter(Boolean)
}

/** The decision, short by default (first sentences, cut at a sentence boundary) with the whole
 *  text one tap away. */
function Decision({ text }: { text: string | null }) {
  const [more, setMore] = useState(false)
  const id = useId()
  if (!text) {
    return (
      <Text size="sm" tone="faint">
        No decision text was recorded.
      </Text>
    )
  }
  const short = condenseDecision(text)
  return (
    <Stack gap="--space-2">
      <div className="ta-decision" id={id}>
        <ReportMarkdown>{more || !short.cut ? text : short.text}</ReportMarkdown>
      </div>
      {short.cut && (
        <Button
          variant="ghost"
          size="sm"
          className="ta-toggle"
          aria-expanded={more}
          aria-controls={id}
          onClick={() => setMore((m) => !m)}
        >
          {more ? 'Show less' : 'Show more'}
        </Button>
      )}
    </Stack>
  )
}

/** A finished run: the rating, how it sits against the owner's thesis (as the server judged it)
 *  and a short decision up front; the reports behind it, what was shared, the run's history and
 *  its cost behind one "Show full analysis" control (`history` is that slot). Presentational: the
 *  open state is the caller's. */
export function TradingAgentsReport({
  run,
  view = null,
  onDelete,
  open,
  onToggle,
  history,
}: {
  run: TaRun
  /** The server's one-line reading of the rating for this position, when the take used the run. */
  view?: string | null
  onDelete: () => void
  open: boolean
  onToggle: () => void
  history: ReactNode
}) {
  const fields = run.reports?.fields ?? {}
  const truncated = run.reports?.truncated ?? []
  const sections = TA_REPORT_SECTIONS.flatMap((section) => {
    const parts = section.fields.flatMap((field) => {
      const text = fields[field]
      return text ? [{ field, text }] : []
    })
    return parts.length > 0 ? [{ id: section.id, label: section.label, parts }] : []
  })
  const judgment = run.thesis_judgment
  const when = run.finished_at ?? run.created_at
  const fullId = useId()

  return (
    <Stack gap="--space-4">
      <div className="ta-result">
        {run.signal && <TradingAgentsRating signal={run.signal} />}
        {judgment?.read && (
          <span title={judgment.cite}>
            <Badge tone="neutral">Your thesis: {judgment.read}</Badge>
          </span>
        )}
      </div>
      {view && <Text size="sm">{view}</Text>}
      <Cluster gap="--space-3">
        <Text size="xs" tone="faint">
          {run.ticker} · as of {fDate(run.trade_date)} · run {timeAgo(when)}
        </Text>
        {run.requested_by === 'lucius' && <Tag tone="lucius">Asked by Lucius</Tag>}
        {run.requested_by === 'automation' && <Tag tone="muted">Automation</Tag>}
      </Cluster>
      <Decision key={run.id} text={run.decision_text} />
      <Button
        variant="ghost"
        size="sm"
        className="ta-toggle"
        aria-expanded={open}
        aria-controls={fullId}
        onClick={onToggle}
      >
        {open ? 'Hide full analysis' : 'Show full analysis'}
      </Button>
      <section id={fullId} aria-label="Full analysis" hidden={!open}>
        {open && (
          <Stack gap="--space-5">
            <div>
              {sections.map((section) => (
                <ReportRow
                  key={section.id}
                  label={section.label}
                  parts={section.parts}
                  truncated={truncated}
                />
              ))}
              {run.decision_text && (
                <ReportRow
                  label="Portfolio manager"
                  parts={[{ field: null, text: run.decision_text }]}
                  truncated={truncated}
                />
              )}
              <details className="ta-disclose">
                <summary>What was shared</summary>
                <div className="ta-disclose__body">
                  <TradingAgentsShared
                    context={run.context_sent}
                    providers={runProviders(run.config_snapshot)}
                  />
                </div>
              </details>
            </div>
            {history}
            <p className="ta-footer">
              {footerParts(run).join(' · ')} ·{' '}
              <Button variant="ghost" size="sm" onClick={onDelete}>
                Delete run
              </Button>
            </p>
          </Stack>
        )}
      </section>
    </Stack>
  )
}
