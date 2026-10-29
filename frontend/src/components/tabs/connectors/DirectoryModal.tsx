// Browse & add — the modal host (spec §6 and §7).
//
// ONE modal hosts three views: the directory grid, an entry's preview page, and the
// add-an-MCP-server flow. It owns the routing between them, the title (which becomes a back
// control as you go deeper, matching the detail page's `‹ Connectors`), and the footer.
//
// Spec §9's geometry: header and footer are `flex: none` and only `.rd-modal-body` scrolls, so
// each view supplies its own body and the footer is composed HERE — never inside a step.

import { useState, type JSX } from 'react'
import { BackTitle, Btn, Modal } from './primitives'
import { DirectoryBrowser } from './DirectoryBrowser'
import { DirectoryPreview } from './DirectoryPreview'
import { McpAddFlowBody, McpAddFlowFooter, useMcpAddFlow } from './McpAddFlow'
import { type DirectoryRow, type DirectoryFamilyFilter, type DirectorySort } from './catalog'
import { resolveDirectory } from './directoryRows'
import { toEntry } from './adapter'
import type { ConnectorCategory, FocusConnectorOptions } from './model'
import type { Connector } from './types'

const MCP_PREFIX = 'mcp:'

export interface DirectoryModalProps {
  open: boolean
  onClose: () => void
  /** Live connector state — what makes a catalog entry read as installed. */
  connectors: Connector[]
  /** Pre-filter, so a section's "+ Add a data source" opens the directory already scoped. */
  family?: DirectoryFamilyFilter
  /** Open a connector's detail page. The directory closes first. `opts` says whether the
   *  caller wants more than the page itself — see `FocusConnectorOptions`. */
  onFocusConnector: (connectorId: string, opts?: FocusConnectorOptions) => void
  /** Refresh connector state after a server is added. */
  onAdded?: () => void
  /** Open straight onto ONE entry's landing page instead of the grid — the detail page's
   *  "View details" wants THIS connector's page, not a browse of everything else. Seeds the
   *  first render only (the modal is unmounted while closed, so it re-seeds per open); after
   *  that the modal owns its own view and Back returns to the grid as usual. */
  previewId?: string
  toast?: (message: string) => void
}

export function DirectoryModal({
  open,
  onClose,
  connectors,
  family = 'all',
  onFocusConnector,
  onAdded,
  previewId,
  toast,
}: DirectoryModalProps): JSX.Element | null {
  const entries = resolveDirectory(connectors)

  const [directoryFamily, setDirectoryFamily] = useState<DirectoryFamilyFilter>(family)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<DirectorySort>('popular')
  const [category, setCategory] = useState<ConnectorCategory | 'all'>('all')
  const [preview, setPreview] = useState<DirectoryRow | null>(
    () => entries.find((e) => e.id === previewId) ?? null,
  )
  const [mcpOpen, setMcpOpen] = useState(false)

  const takenIds = connectors
    .filter((c) => c.id.startsWith(MCP_PREFIX))
    .map((c) => c.id.slice(MCP_PREFIX.length))

  const flow = useMcpAddFlow({
    takenIds,
    onSaved: (name, toolCount) => {
      toast?.(`${name} added — ${toolCount} tool${toolCount === 1 ? '' : 's'} ready for Lucius`)
      onAdded?.()
      close()
    },
  })

  function close() {
    flow.discardAbandonedOAuthServer()
    setMcpOpen(false)
    setPreview(null)
    setQuery('')
    onClose()
  }

  /** Leaves the MCP add flow's view ENTIRELY, without closing the whole modal — the flow
   *  footer's own Discard lands here always (a deliberate "abandon this attempt" action,
   *  distinct from stepping back to fix something). Same cleanup `close()` does: a dormant
   *  OAuth server row this session signed into but never saved must not survive the dismiss
   *  (`McpAddFlow.tsx`'s `discardAbandonedOAuthServer` doc comment). */
  function leaveMcpFlow() {
    flow.discardAbandonedOAuthServer()
    setMcpOpen(false)
  }

  /** The header's "‹ Back" control (owner review, 2026-08-22: previously always
   *  `leaveMcpFlow`, so backing out of a typed-by-hand custom server's REVIEW step threw
   *  the whole command/URL away and dropped you at the empty directory grid — "must go back
   *  to the custom server one!"). Now a real step-back through the wizard: on `connecting` /
   *  `error` / `review`, it returns to the FORM step with everything you typed still there
   *  (`flow.back()` never resets `formState`) — only from the form step itself, where there
   *  is nowhere left to step back TO, does it fall through to actually leaving the flow.
   *
   *  Still discards an abandoned OAuth sign-in either way: `flow.back()` itself does NOT
   *  (it only resets `step`, on purpose — see its own comment), but a dormant server row
   *  from THIS attempt must not survive a "let me reconsider" any more than a full exit
   *  does, or retrying sign-in after editing the URL would orphan the first row's live
   *  credential forever. Safe to call unconditionally: a no-op when there is no dormant row. */
  function handleFlowBack() {
    if (flow.step === 'form') {
      leaveMcpFlow()
    } else {
      flow.discardAbandonedOAuthServer()
      flow.back()
    }
  }

  /** The gear on an installed entry: "show me its settings page", NOT "reopen the credential
   *  form" — it is already set up. Deliberately the bare focus call. */
  const configure = (entry: DirectoryRow) => {
    close()
    onFocusConnector(entry.installedId ?? entry.id)
  }

  const add = (entry: DirectoryRow) => {
    if (entry.mcp) {
      flow.start(entry)
      setPreview(null)
      setMcpOpen(true)
      return
    }
    // A native connector has no add flow of its own — its detail page IS where it is set up,
    // and Add already declared the intent to set it up, so it lands ON the form rather than
    // one tap short of it. Same page, same single config surface; only the entry view differs.
    close()
    onFocusConnector(entry.id, { configure: true })
  }

  if (!open) return null

  const live = preview
    ? connectors.find((c) => c.id === (preview.installedId ?? preview.id))
    : undefined
  const liveEntry = live ? toEntry(live) : undefined

  const title = mcpOpen ? (
    <BackTitle
      label={flow.step === 'form' ? 'Back to directory' : 'Back'}
      onClick={handleFlowBack}
    />
  ) : preview ? (
    <BackTitle label="Back" onClick={() => setPreview(null)} />
  ) : (
    'Browse & add'
  )

  const footer = mcpOpen ? (
    <McpAddFlowFooter flow={flow} onCancel={leaveMcpFlow} />
  ) : preview ? (
    <Btn onClick={() => setPreview(null)}>Back</Btn>
  ) : (
    <Btn onClick={close}>Close</Btn>
  )

  const scrollKey = mcpOpen ? 'mcp' : preview ? `preview:${preview.id}` : 'browse'

  return (
    <Modal title={title} size="xl" onClose={close} footer={footer} scrollKey={scrollKey}>
      {mcpOpen ? (
        <McpAddFlowBody flow={flow} />
      ) : preview ? (
        <DirectoryPreview
          d={preview}
          statusText={liveEntry?.statusText}
          statusTone={liveEntry?.tone}
          prompts={liveEntry?.prompts}
          onAdd={add}
          onConfigure={configure}
        />
      ) : (
        <DirectoryBrowser
          entries={entries}
          family={directoryFamily}
          onFamily={(f) => {
            setDirectoryFamily(f)
            setCategory('all')
          }}
          query={query}
          onQuery={setQuery}
          sort={sort}
          onSort={setSort}
          category={category}
          onCategory={setCategory}
          onAdd={add}
          onPreview={setPreview}
          onConfigure={configure}
        />
      )}
    </Modal>
  )
}
