// Settings — the overlay redesign. Ported from
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx (its `App` component).
//
// Settings is a dialog over the app, not a destination: a fixed-width section list on the
// left, one scrolling pane on the right, searchable. Profile is no longer a tab — it is the
// first section here.
//
// The prototype hosts itself in a hand-rolled scrim. The real app hosts it in the design
// system's workspace Modal, which already owns the portal, focus trap, scroll lock, Escape
// and the Lucius-dock exemption; settings.css restyles that shell to the prototype's exact
// geometry. The route is kept (rather than becoming pure UI state) because Overview
// deep-links into it with ?sub=data.
import { Suspense, useEffect, useRef, useState } from 'react'
import { lazyChunk } from '../../lazyChunk'
import type { ComponentType } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Modal, PageTabs } from '../ui'
import { useIsMobile } from '../../hooks/useIsMobile'
import { desktopBridge, desktopClientBridge } from '../../platform/desktop'
import { useSwipeBack } from '../../hooks/useSwipeBack'
import {
  ChevronLeft,
  ChevronRight,
  ChartBar,
  Code2,
  Copy,
  Database,
  Info,
  LockKeyhole,
  MessageSquare,
  Brain,
  Monitor,
  Palette,
  Plug,
  Radar,
  Search,
  ShieldCheck,
  Smartphone,
  Terminal,
  User,
  WalletCards,
  X,
} from '../../ui/icons'
import type { IconProps } from '../../ui/icons'
import { SectionBackProvider, SectionBusyProvider, useToast } from './settings/ds'
import type { SectionBack } from './settings/ds'
import { Spark } from '../../lucius/identity'
import { PROFILE_KEY, fetchJson } from '../../hooks/serverState'
import { useTerminalAvailable } from '../../hooks/useTerminalAvailable'
import { useCachedFetchState } from '../../state/queryCache'
import type { SectionProps } from './settings/ds'
import { ProfileSection } from './settings/sections/ProfileSection'
const loadModelSection = () => import('./settings/sections/ModelSection')
const ModelSection = lazyChunk(() => loadModelSection().then((m) => ({ default: m.ModelSection })))
const loadAiUsageSection = () => import('./settings/sections/AiUsageSection')
const AiUsageSection = lazyChunk(() =>
  loadAiUsageSection().then((m) => ({ default: m.AiUsageSection })),
)
const loadChatSection = () => import('./settings/sections/ChatSection')
const ChatSection = lazyChunk(() => loadChatSection().then((m) => ({ default: m.ChatSection })))
const loadMemorySection = () => import('./settings/sections/MemorySection')
const MemorySection = lazyChunk(() =>
  loadMemorySection().then((m) => ({ default: m.MemorySection })),
)
const loadPermissionsSection = () => import('./settings/sections/PermissionsSection')
const PermissionsSection = lazyChunk(() =>
  loadPermissionsSection().then((m) => ({ default: m.PermissionsSection })),
)
const loadDedupSection = () => import('./settings/sections/DedupSection')
const DedupSection = lazyChunk(() => loadDedupSection().then((m) => ({ default: m.DedupSection })))
const loadIntelSection = () => import('./settings/sections/intel/IntelSection')
const IntelSection = lazyChunk(() => loadIntelSection().then((m) => ({ default: m.IntelSection })))
const loadAppearanceSection = () => import('./settings/sections/AppearanceSection')
const AppearanceSection = lazyChunk(() =>
  loadAppearanceSection().then((m) => ({ default: m.AppearanceSection })),
)
const loadCurrencySection = () => import('./settings/sections/CurrencySection')
const loadDesktopSection = () => import('./settings/sections/DesktopSection')
const DesktopSection = lazyChunk(() =>
  loadDesktopSection().then((m) => ({ default: m.DesktopSection })),
)
const CurrencySection = lazyChunk(() =>
  loadCurrencySection().then((m) => ({ default: m.CurrencySection })),
)
const loadSecuritySection = () => import('./settings/sections/SecuritySection')
const SecuritySection = lazyChunk(() =>
  loadSecuritySection().then((m) => ({ default: m.SecuritySection })),
)
const loadDataSection = () => import('./settings/sections/DataSection')
const DataSection = lazyChunk(() => loadDataSection().then((m) => ({ default: m.DataSection })))
const loadTerminalSection = () => import('./settings/sections/TerminalSection')
const TerminalSection = lazyChunk(() =>
  loadTerminalSection().then((m) => ({ default: m.TerminalSection })),
)
const loadHelpSection = () => import('./settings/sections/HelpSection')
const HelpSection = lazyChunk(() => loadHelpSection().then((m) => ({ default: m.HelpSection })))
const loadShortcutsSection = () => import('./settings/sections/ShortcutsSection')
const ShortcutsSection = lazyChunk(() =>
  loadShortcutsSection().then((m) => ({ default: m.ShortcutsSection })),
)
const loadMcpSection = () => import('./settings/sections/McpSection')
const McpSection = lazyChunk(() => loadMcpSection().then((m) => ({ default: m.McpSection })))
const loadDeveloperSection = () => import('./settings/sections/DeveloperSection')
const DeveloperSection = lazyChunk(() =>
  loadDeveloperSection().then((m) => ({ default: m.DeveloperSection })),
)
import './settings/settings.css'

export const SECTIONS = [
  { key: 'profile', label: 'Profile' },
  { key: 'model', label: 'AI' },
  { key: 'chat', label: 'Chat' },
  { key: 'memory', label: 'Memory' },
  { key: 'permissions', label: 'Permissions' },
  { key: 'dedup', label: 'Duplicate Detection' },
  { key: 'intel', label: 'Intelligence' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'desktop', label: 'Desktop' },
  { key: 'shortcuts', label: 'Phone shortcuts' },
  { key: 'mcp', label: 'MCP' },
  { key: 'currency', label: 'Currency & format' },
  { key: 'security', label: 'Security' },
  { key: 'data', label: 'Data' },
  { key: 'terminal', label: 'Terminal' },
  { key: 'help', label: 'Help' },
  { key: 'developer', label: 'Developer' },
  { key: 'aiUsage', label: 'Cost & Performance' },
] as const

export type Section = (typeof SECTIONS)[number]['key']

// The phone presents the sections as a DRILL-DOWN list rather than a tab row: all sections do not
// fit a phone's width, so the row scrolled horizontally and most sections sat off-screen with
// no affordance saying so. A grouped menu shows every section at once and matches what a phone user
// expects settings to be. Desktop keeps the rail — it has the width for it.
const SECTION_GROUPS: { title: string; keys: Section[] }[] = [
  { title: 'Account', keys: ['profile', 'security'] },
  { title: 'Assistant', keys: ['model', 'chat', 'memory', 'permissions'] },
  { title: 'App', keys: ['appearance', 'desktop', 'currency', 'dedup', 'intel'] },
  { title: 'Phone & other apps', keys: ['shortcuts', 'mcp'] },
  { title: 'Advanced', keys: ['data', 'terminal', 'developer', 'aiUsage'] },
  { title: 'Support', keys: ['help'] },
]

// Hovering a section starts its chunk download, so the click usually finds it already there.
const SECTION_LOADERS: Partial<Record<Section, () => Promise<unknown>>> = {
  model: loadModelSection,
  aiUsage: loadAiUsageSection,
  chat: loadChatSection,
  memory: loadMemorySection,
  permissions: loadPermissionsSection,
  dedup: loadDedupSection,
  intel: loadIntelSection,
  appearance: loadAppearanceSection,
  desktop: loadDesktopSection,
  currency: loadCurrencySection,
  security: loadSecuritySection,
  data: loadDataSection,
  terminal: loadTerminalSection,
  shortcuts: loadShortcutsSection,
  mcp: loadMcpSection,
  developer: loadDeveloperSection,
}

function SectionFallback() {
  return (
    <div className="rd-set-skel" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div className="rd-set-skelpanel" key={i}>
          <span className="rd-set-skelbar" />
          <span className="rd-set-skelbar short" />
        </div>
      ))}
    </div>
  )
}

function sectionIcon(key: Section) {
  if (key === 'model') return <Spark size={18} />
  const Icon = SECTION_ICONS[key]
  return <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
}

const SECTION_KEYS: readonly string[] = SECTIONS.map((s) => s.key)

// AI keeps the Lucius Spark identity mark; ordinary section icons come from the app-owned
// Lucide seam so Settings has no remote icon-font dependency.
const SECTION_ICONS: Record<Exclude<Section, 'model'>, ComponentType<IconProps>> = {
  profile: User,
  aiUsage: ChartBar,
  chat: MessageSquare,
  memory: Brain,
  permissions: ShieldCheck,
  dedup: Copy,
  intel: Radar,
  appearance: Palette,
  desktop: Monitor,
  shortcuts: Smartphone,
  mcp: Plug,
  currency: WalletCards,
  security: LockKeyhole,
  data: Database,
  terminal: Terminal,
  help: Info,
  developer: Code2,
}

// extra words that should match a section in search but aren't in its label
const SECTION_TERMS: Record<Section, string> = {
  profile:
    'name age income expenses fire retirement notes lucius country nationality residence employment tax',
  model: 'ai anthropic openai claude provider api key routing auto local ollama custom endpoint',
  aiUsage: 'ai usage cost spend tokens telemetry performance latency speed retry models providers',
  chat: 'chat suggestions follow-up cost thinking reasoning multi-turn conversation',
  memory:
    'memory remember forget facts recall sensitive import export past chats search lucius knows',
  permissions: 'tool grants revoke confirm approval write send email unattended tasks',
  dedup: 'duplicate merge import matching',
  intel:
    'intel intelligence monitoring price alert check news scan interval rss freshness outlet web search budget digest pre-market notify telegram webhook email crash threshold spike watch explained read opinion lean suggestion advice verbosity brief summary lucius',
  appearance: 'theme accent colour color logo name',
  desktop: 'desktop background tray startup login notifications folder statements imports',
  currency: 'base currency sgd usd date number format week',
  security: 'password token auth session login loopback',
  data: 'backup restore reset export danger uploads files credits market data yahoo finance coingecko crypto api key',
  terminal:
    'terminal shell console command line ssh bash claude code codex login sign in subscription server',
  help: 'help docs documentation guide manual support how to getting started',
  shortcuts:
    'iphone ios android apple pay shortcut phone spend log home screen tap to pay tasker macrodroid capture setup code',
  mcp: 'mcp claude desktop cursor vs code copilot chatgpt cline continue client external ai apps api token connect stdio onw',
  developer:
    'extensions logs script skills python code execution sql database query queries read-only lucius reads',
}

const BODY: Record<Exclude<Section, 'profile'>, ComponentType<SectionProps>> = {
  model: ModelSection,
  aiUsage: AiUsageSection,
  chat: ChatSection,
  memory: MemorySection,
  permissions: PermissionsSection,
  dedup: DedupSection,
  intel: IntelSection,
  appearance: AppearanceSection,
  desktop: DesktopSection,
  shortcuts: ShortcutsSection,
  mcp: McpSection,
  currency: CurrencySection,
  security: SecuritySection,
  data: DataSection,
  terminal: TerminalSection,
  help: HelpSection,
  developer: DeveloperSection,
}

export interface SettingsProps {
  /** Deep-link from ?sub= — e.g. Overview's "Import a statement" sends ?sub=data. */
  subTabRequest?: string
  /** Closing the overlay returns to whatever was underneath it. */
  onClose?: () => void
  /** Threads import_review_default into an already-open Accounts tab without a refetch. */
  onReviewDefaultChange?: (v: 'skip' | 'merge') => void
  /** Behind an authenticating proxy, sign-in is not this app's business: Security keeps only the API token. */
  gatewayMode?: boolean
}

export function Settings({
  subTabRequest,
  onClose,
  onReviewDefaultChange,
  gatewayMode = false,
}: SettingsProps) {
  const terminalAvailable = useTerminalAvailable(gatewayMode)
  const sectionAvailable = (key: string) =>
    key === 'desktop'
      ? Boolean(desktopBridge() || desktopClientBridge())
      : key !== 'terminal' || terminalAvailable
  const navigate = useNavigate()
  // A ?sub= deep-link seeds the INITIAL section rather than being applied by an effect after
  // the fact. Applying it afterwards meant the first paint opened the default section, the
  // mirror effect below wrote that back to the URL, and the deep-link effect then corrected
  // it — two competing writes to the same search param on one mount, which the router
  // resolved by re-running load() until it exceeded its update depth.
  // Only a ?sub= naming one of OUR sections counts: goTo('settings') carries the backdrop
  // tab's search along, so /investments?sub=activity arrives here as sub=activity.
  const requested =
    subTabRequest && SECTION_KEYS.includes(subTabRequest) && sectionAvailable(subTabRequest)
      ? (subTabRequest as Section)
      : null
  // Nothing is remembered across a close (owner: "let's not cache or store that") — every
  // open starts at Profile, or at the section the URL names.
  const [section, setSection] = useState<Section>(requested ?? 'profile')
  const [q, setQ] = useState('')
  // Phone only: false = the section list, true = one section's content.
  const [drilled, setDrilled] = useState(requested !== null)
  // Same cache key ProfileSection reads, so naming the user here costs no extra request.
  const { data: me } = useCachedFetchState<{ name?: string | null; nickname?: string | null }>(
    PROFILE_KEY,
    () => fetchJson<{ name?: string | null; nickname?: string | null }>(PROFILE_KEY),
    { staleTime: 0 },
  )
  const myName = (me?.name || '').trim()
  const myInitials =
    myName
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]!.toUpperCase())
      .join('') || '?'
  const [toast, toastNode] = useToast()
  const bodyRef = useRef<HTMLDivElement>(null)
  // A callback ref, not useRef: a section (ProfileSection's sticky save bar) portals into this
  // node, so Settings must RE-RENDER once it mounts or the section keeps the `null` it got on
  // Settings' own first pass forever — a plain ref's `.current` update doesn't trigger that.
  const [actionBarSlot, setActionBarSlot] = useState<HTMLDivElement | null>(null)
  // Below the phone breakpoint the rail becomes a top bar. It uses the app's OWN section
  // switcher (PageTabs) rather than the prototype's bespoke chip row, so Settings switches
  // sections the same way every other tab in this app does.
  const isMobile = useIsMobile()
  // A right swipe over the drilled-in section pane goes back to the section list — the touch
  // equivalent of the ‹ Back chevron in the sheethead.
  const swipeBackHandlers = useSwipeBack(() => setDrilled(false), isMobile && drilled)

  // ── ?sub= has exactly ONE writer per direction ──────────────────────────────
  // Mirroring `section` into the URL from an effect on [section] looked simpler, but it
  // fought the inbound deep-link: an incoming ?sub= change re-runs that effect BEFORE the
  // deep-link effect has moved `section`, so it rewrote the URL back to the section the user
  // was leaving, which moved `section`, which rewrote the URL… TanStack re-ran load() until it
  // exceeded its update depth. Outbound writes now happen where the user acts; inbound ones
  // only ever set state.
  const setSec = (s: Section) => {
    setSection(s)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    void navigate({
      to: '/settings',
      search: ((prev: any) => ({ ...prev, sub: s })) as any,
      replace: true,
    } as never)
  }

  // Stamp the URL once on open, so a reload or a shared link lands where you left — unless it
  // already names a section, in which case there is nothing to say.
  const stamped = useRef(false)
  useEffect(() => {
    if (stamped.current) return
    stamped.current = true
    if (requested) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    void navigate({
      to: '/settings',
      search: ((prev: any) => ({ ...prev, sub: section })) as any,
      replace: true,
    } as never)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A LATER ?sub= change — Overview's "Import a statement" while Settings is already open.
  // Applied once per distinct value so the user can still switch sections by hand afterwards.
  const lastSubReq = useRef<string | null>(null)
  useEffect(() => {
    if (!subTabRequest || lastSubReq.current === subTabRequest) return
    lastSubReq.current = subTabRequest
    if (SECTION_KEYS.includes(subTabRequest) && sectionAvailable(subTabRequest))
      setSection(subTabRequest as Section)
  }, [subTabRequest])

  // a fresh section always starts at the top of the pane
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0
  }, [section])

  // ── Scroll indicator ────────────────────────────────────────────────────────
  // A native scrollbar is IN FLOW: it eats width on the scrollbar side, so the content can
  // have equal left/right padding or a visible scrollbar, never both — unless you reserve a
  // gutter on both edges, which wastes a strip of the pane for nothing. So the native one is
  // sized to zero and this draws the thumb over the padding instead: equal padding, a real
  // indicator, and zero layout cost on every platform.
  // Sections declare their first load via useSectionBusy; the pane renders the skeleton.
  const [busyCount, setBusyCount] = useState(0)
  const bumpBusy = useRef((delta: number) => setBusyCount((n) => Math.max(0, n + delta))).current

  const active = SECTIONS.find((s) => s.key === section) || SECTIONS[0]!
  // A section's own way back (a memory topic page → the memory list). The section clears it when it
  // unmounts, so leaving the section needs no reset here.
  const [back, setBack] = useState<SectionBack | null>(null)

  const needle = q.trim().toLowerCase()
  const availableSections = SECTIONS.filter((s) => sectionAvailable(s.key))
  const shown = needle
    ? availableSections.filter((s) =>
        (s.label + ' ' + SECTION_TERMS[s.key]).toLowerCase().includes(needle),
      )
    : availableSections

  const close = () => onClose?.()

  const closeBtn = (
    <button type="button" className="rd-set-x" onClick={close} aria-label="Close settings">
      <X size={16} strokeWidth={1.8} aria-hidden="true" />
    </button>
  )

  const Body = section === 'profile' ? null : BODY[section]

  return (
    <Modal
      open
      onClose={close}
      title="Settings"
      size="workspace"
      className="onw-settings"
      overlayClassName="onw-settings-overlay"
      mobileDockedSheet
    >
      {isMobile ? (
        <>
          {/* Grab handle — visual affordance only, same as every other bottom sheet: the
              whole card is the actual drag-to-dismiss target, wired in by Modal itself
              (`mobileDockedSheet`) via the shared useSheetDragToDismiss gesture. */}
          <div className="rd-set-grab" aria-hidden="true">
            <span className="rd-set-grabbar" />
          </div>
          <div className="rd-set-sheethead">
            {drilled ? (
              <button
                type="button"
                className="rd-set-x"
                onClick={() => (back ? back.onBack() : setDrilled(false))}
                aria-label={back ? `Back to ${back.label}` : 'Back to all settings'}
              >
                <ChevronLeft size={22} strokeWidth={1.8} aria-hidden="true" />
              </button>
            ) : (
              closeBtn
            )}
            <span className="rd-set-sheettitle">{drilled ? active.label : 'Settings'}</span>
          </div>
        </>
      ) : null}
      {isMobile ? null : (
        <nav className="rd-set-nav" aria-label="Settings section">
          {/* No search on a phone: all sections are visible in the drill-down menu, so the
            field was a permanent row of chrome buying nothing. */}
          {isMobile ? null : (
            <div className="rd-set-search">
              <span className="rd-set-search-ic">
                <Search size={18} strokeWidth={1.8} aria-hidden="true" />
              </span>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search"
                aria-label="Search settings"
              />
              {q ? (
                <button
                  type="button"
                  className="rd-set-search-x"
                  onClick={() => setQ('')}
                  aria-label="Clear search"
                >
                  <X size={12} strokeWidth={2.2} aria-hidden="true" />
                </button>
              ) : null}
            </div>
          )}
          {isMobile ? (
            shown.length ? (
              <PageTabs
                tabs={shown.map((s) => ({ key: s.key, label: s.label }))}
                value={section}
                onChange={setSec}
                ariaLabel="Settings section"
                className="rd-set-pagetabs"
              />
            ) : (
              <span className="rd-set-nohits">No matching settings</span>
            )
          ) : (
            <div className="rd-set-navlist">
              {shown.map((s) => (
                <button
                  type="button"
                  key={s.key}
                  onClick={() => setSec(s.key)}
                  onPointerEnter={() => void SECTION_LOADERS[s.key]?.()}
                  aria-current={section === s.key ? 'page' : undefined}
                  aria-label={s.label}
                  className={'rd-set-navitem' + (section === s.key ? ' on' : '')}
                >
                  <span className="ic">{sectionIcon(s.key)}</span>
                  <span className="rd-set-navlabel">{s.label}</span>
                </button>
              ))}
              {!shown.length ? <span className="rd-set-nohits">No matching settings</span> : null}
            </div>
          )}
        </nav>
      )}
      <div className="rd-set-pane" {...swipeBackHandlers}>
        {/* A real header bar, NOT the prototype's title-inside-the-scroller. The close button
            is pinned to the pane, so with the title scrolling away the ✕ was left floating
            over whatever row happened to be under it. Making it a sibling of the scroller
            keeps both parked without sticky positioning, negative margins or a z-index
            fight with the rows. */}
        {isMobile ? null : (
          <div className="rd-set-topbar">
            {back ? (
              <div className="rd-set-hslot">
                <button type="button" className="rd-set-back" onClick={back.onBack}>
                  <ChevronLeft size={16} strokeWidth={1.8} aria-hidden="true" />
                  {back.label}
                </button>
              </div>
            ) : (
              <h2 className="rd-set-h">{active.label}</h2>
            )}
            {closeBtn}
          </div>
        )}
        {/* data-autofocus parks the dialog's initial focus on the scroller. Without it Radix
            focuses the first focusable child — the search box — which on a phone means the
            keyboard springs up and the field wears a focus ring before you've touched it. */}
        <div className="rd-set-scrollwrap">
          <div
            className="rd-set-body"
            ref={bodyRef}
            tabIndex={-1}
            data-autofocus=""
            data-screen-label={'Settings — ' + section}
            aria-busy={busyCount > 0 || undefined}
          >
            {isMobile && !drilled ? (
              <div className="rd-set-menu">
                {/* Tapping the card opens Profile — the block names the account, so the only
                  thing it can usefully do is take you to that account's settings. */}
                <button
                  type="button"
                  className="rd-set-me"
                  onClick={() => {
                    setSec('profile')
                    setDrilled(true)
                  }}
                >
                  <span className="rd-set-meavatar" aria-hidden="true">
                    {myInitials}
                  </span>
                  <span className="rd-set-mename">{myName || 'Your profile'}</span>
                  <span className="rd-set-menuchev" aria-hidden="true">
                    <ChevronRight size={18} strokeWidth={1.8} />
                  </span>
                </button>
                {SECTION_GROUPS.map((g) => (
                  <div className="rd-set-menugroup" key={g.title}>
                    <h3 className="rd-set-menuhead">{g.title}</h3>
                    <div className="rd-set-menucard">
                      {g.keys.flatMap((k) => {
                        const item = availableSections.find((x) => x.key === k)
                        if (!item) return []
                        return (
                          <button
                            type="button"
                            key={k}
                            className="rd-set-menurow"
                            onPointerDown={() => void SECTION_LOADERS[k]?.()}
                            onClick={() => {
                              setSec(k)
                              setDrilled(true)
                            }}
                          >
                            <span className="rd-set-menuic">{sectionIcon(k)}</span>
                            <span className="rd-set-menulabel">{item.label}</span>
                            <span className="rd-set-menuchev" aria-hidden="true">
                              <ChevronRight size={18} strokeWidth={1.8} />
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <SectionBusyProvider onChange={bumpBusy}>
                <SectionBackProvider value={setBack}>
                  {Body ? (
                    <Suspense fallback={<SectionFallback />}>
                      <Body
                        toast={toast}
                        actionBarSlot={actionBarSlot}
                        gatewayMode={gatewayMode}
                        openSection={(key) => setSec(key as Section)}
                      />
                    </Suspense>
                  ) : (
                    <ProfileSection
                      toast={toast}
                      onReviewDefaultChange={onReviewDefaultChange}
                      actionBarSlot={actionBarSlot}
                    />
                  )}
                </SectionBackProvider>
              </SectionBusyProvider>
            )}
          </div>
        </div>
        {/* A section with its own sticky save bar (ProfileSection) portals into this — a
            sibling of the scroller, exactly like the topbar's close button above, so the bar
            never floats, sticky, over content still inside `.rd-set-body`'s own scroll. Empty
            and invisible for every other section. */}
        <div className="rd-set-actionbar-slot" ref={setActionBarSlot} />
      </div>
      {toastNode}
    </Modal>
  )
}
