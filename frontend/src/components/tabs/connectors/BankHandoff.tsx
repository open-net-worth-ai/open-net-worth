import type { ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'

// The step after "Link bank": approving access at the bank's own site. It was a line of small
// print with a link in the middle, read as a footnote — when it is the one thing left to do. So
// it is a callout naming the bank, with a button: the main action when no tab could open (a
// browser that blocks it, an iOS home-screen app), a way back to the bank when one did.

export function BankHandoff({
  bank,
  url,
  opened,
  children,
}: {
  /** The bank's name, as picked. */
  bank: string
  /** Its consent page. */
  url: string
  /** Whether a tab already went there. */
  opened: boolean
  /** What happens after approving, when it isn't the usual return. */
  children?: ReactNode
}) {
  const name = bank || 'your bank'
  return (
    <div className="bank-handoff" role="status">
      <div className="bank-handoff-text">
        <strong>
          {opened ? `Approve access in the ${name} tab` : `Approve access at ${name}`}
        </strong>
        <span>
          {children ?? 'When you’re done, you come back here and the link finishes by itself.'}
        </span>
      </div>
      <a
        className={`ui-btn ui-btn--sm ${opened ? 'btn-secondary' : 'btn-primary'} bank-handoff-go`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {opened ? `Open ${name} again` : `Open ${name}`}
        <ExternalLink size={13} aria-hidden="true" />
      </a>
    </div>
  )
}
