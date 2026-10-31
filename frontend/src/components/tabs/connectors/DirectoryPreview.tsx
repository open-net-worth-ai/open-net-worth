// The directory preview page (spec §6) — ported from the prototype's `dirPreview` branch.
//
// Top to bottom: who this is and one action, the description, the attribution line, the chip
// rows (tools / prompts / categories), and a quiet Details footer carrying the endpoint and
// the outbound links.
//
// THREE DELIBERATE DEVIATIONS from the prototype:
//
//  1. For an ALREADY-INSTALLED entry the prototype renders a Disconnect button and a kebab
//     here. That contradicts the spec's own two rules — §2 "nothing about a connector is
//     configured from a card action ... or a second place" and §3 "[the detail page] is the
//     only place a connector can be disabled". Duplicating the control here would be that
//     second place, so an installed entry gets its status badge plus one action routing to
//     the detail page, which owns connect/disconnect/remove.
//  2. The prototype's "More info" block is three `href="#"` placeholders. Shipping three dead
//     links is worse than shipping none, so the block renders REAL links from `repoUrl` /
//     `homepageUrl` and disappears entirely when an entry has neither — the ordinary case for
//     a hosted vendor server with no public repo.
//  3. Spec §5's boxed vendor-trust panel does NOT render here (D23). It stated the publisher
//     a second time, the endpoint a second time, and headed itself with our internal name for
//     the concept, on the one page whose job is "should I even look at this". Provenance is
//     an attribution line plus — for an MCP entry only, spec §5 being MCP-only — one muted
//     sentence (`PublisherLine`); the endpoint keeps its place, in Details. The panel still
//     renders where you COMMIT to an endpoint (the add flow's review step) or INSPECT one you
//     already run (the detail page).
//
// The header is the SHARED `.cn-idhead` block, not this file's own: the detail page renders
// the identical thing, so a connector looks the same before and after you add it (D26).

import { useEffect, useRef, useState, type JSX, type ReactNode } from 'react'
import { Badge, Btn, Field, Mono } from './primitives'
import { Icons } from './icons'
import { ExternalLink, FIRST_PARTY_PUBLISHER, PublisherLine } from './VendorTrust'
import { MODAL_BODY_MIN_HEIGHT } from './DirectoryBrowser'
import { previewEndpoint, type DirectoryRow } from './catalog'
import { resolveToolList } from './toolList'

export interface DirectoryPreviewProps {
  d: DirectoryRow
  /** Status text for an installed entry, from the live connector. */
  statusText?: string
  statusTone?: 'good' | 'bad' | ''
  prompts?: string[]
  onAdd: (entry: DirectoryRow) => void
  onConfigure: (entry: DirectoryRow) => void
  /** On a phone the modal pins the action in its footer (`PreviewAction`) instead, so the
   *  header drops it rather than show it twice. */
  actionInFooter?: boolean
  /** The tools the CONNECTED server actually advertised — beats any recorded list. */
  liveTools?: string[]
}

/** The one action the preview exists to drive. `lg`, not `xs`: at the dense in-row size it
 *  read as the lightest thing in a header built around a 52px tile. */
export function PreviewAction({
  d,
  onAdd,
  onConfigure,
  className,
}: Pick<DirectoryPreviewProps, 'd' | 'onAdd' | 'onConfigure'> & {
  className?: string
}): JSX.Element {
  return d.installed ? (
    <Btn size="lg" className={className} onClick={() => onConfigure(d)}>
      Open settings
    </Btn>
  ) : (
    <Btn size="lg" variant="primary" className={className} onClick={() => onAdd(d)}>
      Add{d.mcp ? '' : ' connector'}
    </Btn>
  )
}

/** Where the publisher lives. Docs page first — it is the one a reader wants; the repo is the
 *  fallback for an open-source server that has no product page. Never a fabricated URL. */
function publisherHref(d: DirectoryRow): string | undefined {
  return d.homepageUrl ?? d.repoUrl
}

/** One chip. A tool chip's label is the RAW registry name, unchanged — it is the exact
 *  identifier the permission ladder stores against (CLAUDE.md), and this page is a summary
 *  of the same tools that ladder governs. User-friendly phrasing belongs in the entry's
 *  prose description ("has web search functionality"), never in place of the identifier. */
interface Chip {
  key: string
  label: string
}

const plainChip = (value: string): Chip => ({ key: value, label: value })

/** A tool list longer than this folds behind "Show all N" — 37 Coinbase or ~400 Bybit chips
 *  would push the description's Details footer off the page (owner, 2026-10-31). */
const CHIP_FOLD = 9

/** A labelled chip row. `note` is a muted aside after the count (where a tool list came from). */
function ChipRow({
  label,
  items,
  note,
  fold = false,
}: {
  label: string
  items: Chip[]
  note?: string
  fold?: boolean
}): JSX.Element | null {
  const [expanded, setExpanded] = useState(false)
  if (!items.length) return null
  const folded = fold && !expanded && items.length > CHIP_FOLD
  const shown = folded ? items.slice(0, CHIP_FOLD) : items
  return (
    <div>
      <div className="cn-sublabel" style={{ marginBottom: 8 }}>
        {label} <Badge>{items.length}</Badge>
        {note ? <span className="cn-chipnote">{note}</span> : null}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        {shown.map((c) => (
          <span key={c.key} className="cn-tagchip">
            {c.label}
          </span>
        ))}
        {fold && items.length > CHIP_FOLD ? (
          <button
            type="button"
            className="cn-tagchip cn-chipmore"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? 'Show less' : `Show all ${items.length}`}
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** Copy-to-clipboard for the endpoint. `navigator.clipboard` is absent over plain http and in
 *  jsdom, so the failure is swallowed rather than thrown at the user — the value is on screen
 *  and selectable either way. */
function CopyButton({ value }: { value: string }): JSX.Element {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )
  return (
    <Btn
      size="xs"
      aria-label={copied ? 'Copied' : 'Copy'}
      title={copied ? 'Copied' : 'Copy'}
      className="cn-copybtn"
      onClick={() => {
        navigator.clipboard?.writeText(value).catch(() => {})
        setCopied(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 1500)
      }}
    >
      {copied ? <Icons.check /> : <Icons.copy />}
    </Btn>
  )
}

/**
 * The Details footer: author, the endpoint this entry actually connects to, and the outbound
 * links. Everything here is optional — a native connector has no endpoint, and a hosted
 * vendor server routinely has no repo or docs page — so the whole block, its divider
 * included, disappears when an entry has none of it.
 */
function Details({ d }: { d: DirectoryRow }): JSX.Element | null {
  const endpoint = previewEndpoint(d)
  const links: [string, string][] = []
  if (d.homepageUrl) links.push(['Documentation', d.homepageUrl])
  if (d.repoUrl) links.push(['Source repository', d.repoUrl])
  if (d.supportUrl) links.push(['Support', d.supportUrl])
  if (d.privacyUrl) links.push(['Privacy policy', d.privacyUrl])
  if (!d.publisher && !endpoint && !links.length) return null

  const href = publisherHref(d)
  // `previewEndpoint` returns a URL for a hosted server and a full command line for a local
  // one. Calling `npx -y firecrawl-mcp` a URL would simply be wrong, so the label follows the
  // value.
  const isUrl = !!endpoint && /^https?:\/\//i.test(endpoint)

  return (
    <>
      <hr className="rd-hr" />
      <div>
        <div className="cn-sublabel" style={{ marginBottom: 8 }}>
          Details
        </div>
        <div className="cn-details-grid">
          {d.publisher ? (
            // "Author" is only true for a real MCP entry (their code runs here). A native
            // connector's integration is ours — the named publisher just operates the API it
            // calls — so it reads "Vendor" instead (owner correction, 2026-08-21; same
            // `mcp`-bound distinction as `PublisherLine`'s "Developed by"/"Powered by" verb).
            <Field label={d.mcp || d.publisher === FIRST_PARTY_PUBLISHER ? 'Author' : 'Vendor'}>
              <span style={{ fontSize: 13, color: 'var(--muted)' }}>
                {href ? <ExternalLink href={href}>{d.publisher}</ExternalLink> : d.publisher}
              </span>
            </Field>
          ) : null}
          {links.length ? (
            <Field label="More info">
              <span
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: 5,
                  fontSize: 13,
                }}
              >
                {links.map(([label, url]) => (
                  <ExternalLink key={label} href={url}>
                    {label}
                  </ExternalLink>
                ))}
              </span>
            </Field>
          ) : null}
          {endpoint ? (
            <Field label={isUrl ? 'Connector URL' : 'Command'}>
              <span style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
                <code className="rd-code" style={{ padding: '7px 10px', flex: 1, minWidth: 0 }}>
                  {endpoint}
                </code>
                <CopyButton value={endpoint} />
              </span>
            </Field>
          ) : null}
        </div>
      </div>
    </>
  )
}

/** The identity block (`.cn-idhead`): a large tile, the name and its one-line sub stacked
 *  beside it and vertically centred against it, and ONE action pinned right. Shares its shape
 *  and its stylesheet rules with the detail page's `Identity`, so the two cannot drift. */
function Header({
  d,
  status,
  action,
}: {
  d: DirectoryRow
  status: ReactNode
  action: ReactNode
}): JSX.Element {
  return (
    <div className="cn-idhead">
      <Mono icon={d.icon} hue={d.hue} logo={d.logo} size="lg" />
      <div className="cn-idhead-main">
        <div className="cn-idhead-name">
          {d.name}
          {d.verification === 'verified' ? <span className="cn-verified-tag">Verified</span> : null}
          {d.tag ? <span className="cn-tag">{d.tag}</span> : null}
          {status}
        </div>
        <div className="cn-idhead-sub">{d.sub}</div>
      </div>
      {action ? <div className="cn-idhead-actions">{action}</div> : null}
    </div>
  )
}

export function DirectoryPreview({
  d,
  statusText,
  statusTone = '',
  prompts,
  onAdd,
  onConfigure,
  actionInFooter = false,
  liveTools,
}: DirectoryPreviewProps): JSX.Element {
  const toolList = resolveToolList(d, liveTools)
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        minHeight: MODAL_BODY_MIN_HEIGHT,
      }}
    >
      <Header
        d={d}
        status={d.installed && statusText ? <Badge tone={statusTone}>{statusText}</Badge> : null}
        action={
          actionInFooter ? null : <PreviewAction d={d} onAdd={onAdd} onConfigure={onConfigure} />
        }
      />

      <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, margin: 0 }}>{d.desc}</p>

      <PublisherLine
        publisher={d.publisher}
        href={publisherHref(d)}
        verification={d.verification}
        mcp={d.mcp}
      />

      <ChipRow
        label={toolList.label}
        items={toolList.names.map(plainChip)}
        note={toolList.note}
        fold
      />
      <ChipRow label="Prompt suggestions" items={(prompts ?? []).map(plainChip)} />
      {/* The connector's TOPICS, never its family. The family is the lane the reader is
          already browsing — printing it back as the sole "category" (Stripe → `MCP`) said
          nothing. `families` keeps its real job: the list sections and the directory filter. */}
      <ChipRow label="Categories" items={d.categories.map(plainChip)} />

      <Details d={d} />
    </div>
  )
}
