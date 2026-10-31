import type { ProposedChange } from '../../../../types/chat'
import { humanizeType, permissionArguments } from '../cardTypes'
import { ApprovalCode, ApprovalLead, ApprovalMeta, ApprovalNote } from '../ApprovalCard'

const languageName = (language: unknown) =>
  typeof language === 'string' && language ? humanizeType(language) : null

/** run_code — Lucius's own short script, run in the sandbox. The user approves the source
 * itself, so it is never folded. Read off the generic permission card's own arguments. */
export function CodeRunPreviewV2({ change }: { change: ProposedChange }) {
  const args = permissionArguments(change)
  const purpose = typeof args.purpose === 'string' ? args.purpose : ''
  const source = typeof args.source === 'string' ? args.source : ''
  return (
    <>
      <ApprovalLead>{purpose || 'Run a script'}</ApprovalLead>
      <ApprovalMeta items={[languageName(args.language)]} />
      {source && <ApprovalCode>{source}</ApprovalCode>}
      <ApprovalNote>Runs sandboxed: no internet, ledger or secrets.</ApprovalNote>
    </>
  )
}
