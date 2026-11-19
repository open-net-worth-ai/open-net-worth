import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { TA_FIELD_LABEL, TA_REPORT_SECTIONS } from '../../types/tradingAgents'
import type { TaReportField, TaRun } from '../../types/tradingAgents'
import { fCompact, fCurrency, fDate, timeAgo } from '../../utils/format'
import { modelRef, modelText, runProviders } from '../../utils/tradingAgents'
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

/** A finished run: the rating, how it sits against the owner's thesis (as the server judged it),
 *  the decision, the reports behind it, and what was shared. Presentational. */
export function TradingAgentsReport({ run, onDelete }: { run: TaRun; onDelete: () => void }) {
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

  return (
    <Stack gap="--space-5">
      <div className="ta-result">
        {run.signal && <TradingAgentsRating signal={run.signal} />}
        {judgment?.read && (
          <span title={judgment.cite}>
            <Badge tone="neutral">Your thesis: {judgment.read}</Badge>
          </span>
        )}
      </div>
      <Cluster gap="--space-3">
        <Text size="xs" tone="faint">
          {run.ticker} · as of {fDate(run.trade_date)} · run {timeAgo(when)}
        </Text>
        {run.requested_by === 'lucius' && <Tag tone="lucius">Asked by Lucius</Tag>}
        {run.requested_by === 'automation' && <Tag tone="muted">Automation</Tag>}
      </Cluster>
      {run.decision_text ? (
        <div className="ta-decision">
          <ReportMarkdown>{run.decision_text}</ReportMarkdown>
        </div>
      ) : (
        <Text size="sm" tone="faint">
          No decision text was recorded.
        </Text>
      )}
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
      <p className="ta-footer">
        {footerParts(run).join(' · ')} ·{' '}
        <Button variant="ghost" size="sm" onClick={onDelete}>
          Delete run
        </Button>
      </p>
    </Stack>
  )
}
