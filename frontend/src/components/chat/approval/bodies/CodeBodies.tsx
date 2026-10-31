import type { ProposedChange } from '../../../../types/chat'
import { humanizeType } from '../cardTypes'
import {
  ApprovalCode,
  ApprovalDetails,
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
  ApprovalNote,
} from '../ApprovalCard'

const argText = (v: unknown) => (typeof v === 'object' ? JSON.stringify(v) : String(v))

const languageName = (language: unknown) =>
  typeof language === 'string' && language ? humanizeType(language) : null

/** external_mcp_call — the args about to be sent, so the user sees exactly what will run. The
 * tool and server are the card's title (CardHeader); the raw call is folded below the facts,
 * byte-for-byte what goes to the server. The money-movement note only says what the
 * call looks like — never that it is gated, since the permission config alone decides that. */
export function ExternalMcpCallPreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const args = (d.args || change.args || {}) as Record<string, unknown>
  const entries = Object.entries(args).filter(([, v]) => v != null && v !== '')
  return (
    <>
      {entries.length === 0 ? (
        <ApprovalMeta items={['No arguments']} />
      ) : (
        <ApprovalFacts>
          {entries.map(([k, v]) => (
            <ApprovalFact key={k} label={humanizeType(k)}>
              {argText(v)}
            </ApprovalFact>
          ))}
        </ApprovalFacts>
      )}
      {entries.length > 0 && (
        <ApprovalDetails summary="Raw call">
          <ApprovalCode>
            {JSON.stringify({ name: d.name || change.tool, arguments: args }, null, 2)}
          </ApprovalCode>
        </ApprovalDetails>
      )}
      {d.is_money_movement && (
        <ApprovalNote tone="bad">
          Looks like money movement: a trade, transfer or payment.
        </ApprovalNote>
      )}
    </>
  )
}

/** code_run — Lucius's own short script, run in the sandbox. The user approves the source
 * itself, so it is never folded. */
export function CodeRunPreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  return (
    <>
      <ApprovalLead>{d.purpose || 'Run a script'}</ApprovalLead>
      <ApprovalMeta items={[languageName(d.language)]} />
      {d.source && <ApprovalCode>{d.source}</ApprovalCode>}
      <ApprovalNote>Runs sandboxed: no internet, ledger or secrets.</ApprovalNote>
    </>
  )
}

/** code_skill_create — the full source being saved as a reusable script-skill. The skill is
 * saved disabled, which is the one fact the user must see before approving. */
export function CodeSkillPreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  return (
    <>
      <ApprovalMeta items={[d.purpose, languageName(d.language)]} />
      {d.source && <ApprovalCode>{d.source}</ApprovalCode>}
      <ApprovalNote tone="warn">
        Saved disabled: enable it in Settings → Skills, with code execution on in Developer.
      </ApprovalNote>
    </>
  )
}
