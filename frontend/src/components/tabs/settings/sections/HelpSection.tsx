// Settings → Help. The Help Center's home in the IA.
//
// Help deliberately has no sidebar row and no topbar control (owner, 2026-08-23): it is a
// reference surface, not a place you work, and both of those are prime space a destination
// has to earn. It lives here instead — beside the other things you go looking for rather
// than use daily — and opens as the workspace-tier modal over whatever you were doing
// (2026-08-24), because a doc browser needs the room without costing you your place.
import { useNavigate } from '@tanstack/react-router'
import { Panel, row } from '../ds'
import { useChatComposerOptional } from '../../../../state/chatComposer'
import { openReportProblem } from '../../../../state/reportProblem'

const DOCS_URL = 'https://docs.opennetworth.ai'

// No SectionProps: this section renders no data and raises no toast, and an unused
// parameter is a lint error the section registry does not require anyone to pay.
export function HelpSection() {
  const navigate = useNavigate()
  const composer = useChatComposerOptional()

  return (
    <>
      <Panel
        title="Help center"
        sub="Guides, concepts, and answers — bundled with the app, so they work offline."
      >
        {row(
          'Browse the docs',
          'Installing it, the ideas behind it, every feature, and what Lucius can do on your behalf.',
          // A button, not an <a href="/help">: that was a real page load, which threw away the
          // whole app just to read a doc — and Help is now an overlay this navigation opens
          // over Settings, returning here when it closes.
          <button
            className="rd-btn secondary"
            type="button"
            onClick={() => void navigate({ to: '/help' })}
          >
            Open
          </button>,
        )}
        {row(
          'Docs on the web',
          'The same pages, published — easier to send someone a link to.',
          <a className="rd-btn secondary" href={DOCS_URL} target="_blank" rel="noopener noreferrer">
            Visit
          </a>,
        )}
      </Panel>

      <Panel title="Ask Lucius" sub="He reads the same pages you do.">
        {row(
          'Ask instead of searching',
          'Lucius searches these docs before answering questions about how the app works, and cites the page he used.',
          // Opens a FRESH thread, for the same reason "Ask Lucius about <asset>" does: a
          // question about how the app works has nothing to do with whatever conversation
          // happens to be open. The composer is left empty — we don't know what they want to
          // ask, and a canned question in your own composer reads like a placeholder.
          // Lucius is exempt from the Settings modal's outside-click dismissal
          // (ui/Modal.tsx::isOutsideExempt), so it opens cleanly over this pane.
          <button
            className="rd-btn secondary"
            type="button"
            disabled={!composer}
            onClick={() => composer?.requestOpen(true)}
          >
            Ask Lucius
          </button>,
        )}
      </Panel>

      <Panel title="Send feedback" sub="Report a bug or suggest a feature.">
        {row(
          'Report a bug or request a feature',
          'You see and can edit the whole report before it is sent. Never your book or your files.',
          // The dialog (or, in the desktop app, its native twin) is where the user reads and
          // edits the report; this row only opens it, so nothing here sends anything.
          <button className="rd-btn secondary" type="button" onClick={() => openReportProblem()}>
            Send feedback…
          </button>,
        )}
      </Panel>
    </>
  )
}
