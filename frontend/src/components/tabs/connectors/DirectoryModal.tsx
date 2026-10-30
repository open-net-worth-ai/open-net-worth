// Browse & add — the modal host (spec §6 and §7).
//
// ONE modal hosts three views: the directory grid, an entry's preview page, and the
// add-an-MCP-server flow. It owns the routing between them, the title (which becomes a back
// control as you go deeper, matching the detail page's `‹ Connectors`), and the footer.
//
// Spec §9's geometry: header and footer are `flex: none` and only `.rd-modal-body` scrolls, so
// each view supplies its own body and the footer is composed HERE — never inside a step.

import { useEffect, useLayoutEffect, useRef, useState, type JSX } from 'react'
import { BackTitle, Btn, Modal } from './primitives'
import { DirectoryBrowser } from './DirectoryBrowser'
import { Icons } from './icons'
import { DirectoryPreview, PreviewAction } from './DirectoryPreview'
import { useIsMobile } from '../../../hooks/useIsMobile'
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
  /** True until the connectors read first lands. Until then nothing is known to be installed,
   *  so the list waits rather than offering what the user already has. */
  loading?: boolean
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
  /** Open straight onto the MCP add form of this NOT-yet-installed entry, preset pre-filled —
   *  the path its card's `+` takes. Seeds the first render only, like `previewId`. */
  addId?: string
  /** With `addId`: start the sign-in at once — the user already clicked Connect on Lucius's
   *  card, which opened the sign-in window on that click (`preopenSignInWindow`). */
  autoConnect?: boolean
  toast?: (message: string) => void
}

export function DirectoryModal({
  open,
  onClose,
  connectors,
  loading = false,
  family = 'all',
  onFocusConnector,
  onAdded,
  previewId,
  addId,
  autoConnect = false,
  toast,
}: DirectoryModalProps): JSX.Element | null {
  const entries = resolveDirectory(connectors)

  const [directoryFamily, setDirectoryFamily] = useState<DirectoryFamilyFilter>(family)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<DirectorySort>('popular')
  const isMobile = useIsMobile()
  const [category, setCategory] = useState<ConnectorCategory | 'all'>('all')
  const [preview, setPreview] = useState<DirectoryRow | null>(
    // `installedId` too: an installed server merged into its catalog card (`mcp-ibkr` for
    // `mcp:api-ibkr-com`) has no row of its own, so its "View details" must land on that card.
    () =>
      (previewId
        ? entries.find((e) => e.id === previewId || e.installedId === previewId)
        : undefined) ?? null,
  )
  const addEntry = addId ? entries.find((e) => e.id === addId && e.mcp && !e.installed) : undefined
  const [mcpOpen, setMcpOpen] = useState(!!addEntry)

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

  // Before paint, so the add form never shows empty for a frame. Runs once: the flow is then the
  // user's to drive.
  useLayoutEffect(() => {
    if (addEntry) flow.start(addEntry)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Lucius's Connect: once the preset has filled the form, run the same Connect the footer's
  // button runs — for a sign-in server, that is the sign-in. Once, and only for OAuth presets.
  const autoConnected = useRef(false)
  useEffect(() => {
    if (!autoConnect || !addEntry || autoConnected.current) return
    if (flow.step !== 'form' || flow.form.auth !== 'oauth' || !flow.form.url) return
    autoConnected.current = true
    flow.runConnect()
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

  const customEntry = entries.find((e) => e.generic)

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
  ) : preview && isMobile ? (
    // The header's ‹ Back and the sheet's ✕ already leave, so on a phone the footer carries
    // the action instead: always in view, in thumb reach, and steady while the body scrolls.
    <PreviewAction d={preview} onAdd={add} onConfigure={configure} className="cn-foot-action" />
  ) : preview ? (
    <Btn onClick={() => setPreview(null)}>Back</Btn>
  ) : (
    <>
      {/* Always in reach, whatever the list is scrolled to (owner, 2026-10-31). */}
      {customEntry ? (
        <Btn variant="primary" onClick={() => add(customEntry)}>
          <Icons.plus /> Add custom server
        </Btn>
      ) : null}
      <Btn onClick={close}>Close</Btn>
    </>
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
          actionInFooter={isMobile}
        />
      ) : (
        <DirectoryBrowser
          entries={entries}
          loading={loading}
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
