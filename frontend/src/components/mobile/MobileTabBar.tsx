// MobileTabBar — the phone navigation shell (<640px).
//
// Implements `.claude/rfcs/mobile-shell-tabbar-and-lucius-sheet.md` §2.1 option A, reshaped
// twice since: Lucius moved from a floating dock into the bar itself as an in-flow tab
// (`docs/adr/2026-07-22-lucius-tab-not-sheet.md`), then the bar dropped from four money
// destinations to three so Lucius could sit at the true center of an odd-count bar instead of
// off-centre in a six-slot one (`docs/adr/2026-08-06-mobile-tab-bar-drops-to-three-money-destinations.md`).
// Current shape: five slots — Overview · Accounts · Lucius · Investments · More — with the rest
// of the IA (PRIMARY_TABS remainder incl. Plan + CONFIG_TABS) living in a bottom sheet behind
// the More slot.
//
// Badge rule (RFC §2.1): a demoted tab's count is mirrored onto the More slot as a
// --bad dot — an alert must never be invisible because it was demoted.

import * as Dialog from '@radix-ui/react-dialog'
import { useRef, useState, type ReactElement } from 'react'
import { useSheetDragToDismiss } from '../../hooks/useSheetDragToDismiss'
import {
  LayoutGrid,
  Wallet,
  TrendingUp,
  MoreHorizontal,
  ChevronRight,
  Search,
  Eye,
  EyeOff,
  X,
  LogOut,
  ExternalLink,
  Bug,
  NAV_ICON_PROPS,
} from '../../ui/icons'
import { openReportProblem } from '../../state/reportProblem'
import { CONFIG_TABS, TAB_LABELS, type MobileNavTarget, type TabId } from '../../tabs'
import { useLuciusChat } from '../../state/luciusChat'
import { Spark } from '../../lucius/identity'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { SIGN_OUT_COPY, type SignOutScope } from '../../utils/gatewaySignOut'
import type { GatewayIdentity } from '../../hooks/useGatewayIdentity'

/** The money destinations that get a permanent slot.
 *
 * THREE, not four: with Lucius and More the bar was six items, and Lucius — the thing the app
 * is built around — sat off-centre in the 4th slot. Three slots puts it dead centre of five
 * (two left, two right) and gives every target a wider tap area. Plan moved to the sheet as
 * the least-often-tapped of the four. */
const TAB_SLOTS: { id: TabId; icon: ReactElement }[] = [
  { id: 'accounts', icon: <Wallet {...NAV_ICON_PROPS} /> },
  { id: 'investments', icon: <TrendingUp {...NAV_ICON_PROPS} /> },
  { id: 'dashboard', icon: <LayoutGrid {...NAV_ICON_PROPS} /> },
]

/** Everything the four slots don't carry — grouped exactly as the sidebar zones are. */
// 'inbox' deliberately excluded — that tab is hidden pending further work.
const SHEET_SECTIONS: TabId[] = ['assets', 'plan', 'commitments', 'intel']

export type PriceStatus = 'live' | 'loading' | 'cached'

export const PRICE_STATUS_LABEL: Record<PriceStatus, string> = {
  live: 'Prices: live',
  loading: 'Prices: loading…',
  cached: 'Prices: cached',
}

interface Props {
  /** The ONE thing that is currently selected — a tab, or Lucius. Never a pair. */
  selected: MobileNavTarget
  /** Every slot and every sheet row reports through here; the shell decides what a
   *  selection means (navigate, or open the Lucius tab). */
  onSelect: (target: MobileNavTarget) => void
  /** Per-tab badge counts (inbox unread + dedup reviews, intel action-needed). */
  badges: Partial<Record<TabId, number>>
  icons: Record<TabId, ReactElement>
  moreOpen: boolean
  onMoreOpenChange: (open: boolean) => void
  /** Search + privacy toggle — demoted here from the topbar on mobile (they'd otherwise wrap to a second row). */
  onOpenSearch: () => void
  onTogglePrivacy: () => void
  amountsHidden: boolean
  /** Log out — demoted here from the topbar (MC-82: it doesn't belong in the header). */
  authRequired: boolean
  onLogout: (scope?: SignOutScope) => Promise<void>
  /** Private Cloud only: who is signed in and the portal link; sign-out then offers two scopes. */
  gateway?: GatewayIdentity | null
  /** "As of … · Prices: …" — demoted here from the topbar. */
  stamp: { asOf: string; prices: PriceStatus }
}

export function MobileTabBar({
  selected,
  onSelect,
  badges,
  icons,
  moreOpen,
  onMoreOpenChange,
  onOpenSearch,
  onTogglePrivacy,
  amountsHidden,
  authRequired,
  onLogout,
  gateway,
  stamp,
}: Props) {
  const luciusOpen = selected === 'lucius'
  const inSheet = !luciusOpen && !TAB_SLOTS.some((s) => s.id === selected)
  const sheetBadgeTotal = [...SHEET_SECTIONS, ...CONFIG_TABS].reduce(
    (n, t) => n + (badges[t] ?? 0),
    0,
  )
  // Mirrors the retired MobileLuciusFab's own indicators (streaming dot / unread
  // badge) — same affordances, now living on the tab bar item instead of a FAB.
  //
  // Concurrent conversations widen this from "the one conversation" to a union across
  // all of them (E4, docs/qa/acceptance/mobile-shell.md MB3.2/MB3.3):
  // - spinner: ANY conversation currently streaming (`streamingIds`), not just the one
  //   loaded in the panel.
  // - badge: the loaded conversation's own "finished while this tab was closed" signal
  //   is unchanged — still gated on `luciusOpen` (the mobile tab's own visibility), because
  //   `statusFor` treats it as already-seen the moment it's selected as active, regardless
  //   of which mobile tab happens to be on screen; that's the wrong gate here, so we keep
  //   reading `messages`/`streaming` directly instead. It is UNIONED with every OTHER
  //   conversation's `statusFor` — a background conversation is never "already looked at"
  //   just because you're sitting on the Lucius tab looking at a different one, so that half
  //   is intentionally NOT gated on `luciusOpen`. `needs_you` is folded into the same union
  //   (and, per `resolveRowStatus`, is never suppressed by being the active conversation
  //   either — it's cleared by answering, not by viewing) rather than getting a distinct
  //   glyph like `ConversationStatusBadge`'s bell: the plain dot is the only mobile badge
  //   shape that exists today, and it still needs to surface rather than being dropped.
  const { messages, streaming, conversations, streamingIds, statusFor } = useLuciusChat()
  const anyWorking = streamingIds.size > 0
  const hasOwnUnreadAnswer = !streaming && messages.some((m) => m.role === 'assistant')
  const otherConversationUnread = conversations.some((c) => {
    const status = statusFor(c.id)
    return status === 'done' || status === 'failed' || status === 'needs_you'
  })
  const hasBadge = (hasOwnUnreadAnswer && !luciusOpen) || otherConversationUnread

  const renderMoneySlot = ({ id, icon }: (typeof TAB_SLOTS)[number]) => (
    <button
      key={id}
      type="button"
      className={`mb-tab${selected === id ? ' active' : ''}`}
      onClick={() => {
        onMoreOpenChange(false)
        onSelect(id)
      }}
      aria-current={selected === id ? 'page' : undefined}
    >
      <span className="mb-tab-indicator" aria-hidden="true" />
      <span className="mb-tab-icon">{icon}</span>
      <span className="mb-tab-label">{TAB_LABELS[id]}</span>
    </button>
  )

  return (
    <>
      {/* Lucius sits in the middle of the bar (after Accounts/Investments, before
          Dashboard/More) rather than trailing at the far right — a central, equally
          weighted slot like every other destination, not an afterthought tacked on the end. */}
      <nav className="mb-tabbar" aria-label="Primary" data-ctx-ignore>
        {TAB_SLOTS.slice(0, 2).map(renderMoneySlot)}
        <button
          type="button"
          className={`mb-tab${luciusOpen ? ' active' : ''}`}
          onClick={() => {
            onMoreOpenChange(false)
            onSelect('lucius')
          }}
          aria-current={luciusOpen ? 'page' : undefined}
          aria-label="Lucius"
        >
          <span className="mb-tab-indicator" aria-hidden="true" />
          <span className="mb-tab-icon">
            {anyWorking ? <span className="lx-dock-dot" aria-hidden="true" /> : <Spark size={20} />}
          </span>
          <span className="mb-tab-label">Lucius</span>
          {hasBadge && <span className="mb-tab-badge" aria-hidden="true" />}
        </button>
        {TAB_SLOTS.slice(2).map(renderMoneySlot)}
        <button
          type="button"
          className={`mb-tab${moreOpen || inSheet ? ' active' : ''}`}
          onClick={() => onMoreOpenChange(!moreOpen)}
          aria-expanded={moreOpen}
          // More stands in for the current page whenever that page was demoted into the
          // sheet — otherwise a demoted tab leaves the bar with no current destination.
          aria-current={inSheet ? 'page' : undefined}
          aria-label="More"
        >
          <span className="mb-tab-indicator" aria-hidden="true" />
          <span className="mb-tab-icon">
            <MoreHorizontal {...NAV_ICON_PROPS} />
          </span>
          <span className="mb-tab-label">More</span>
          {sheetBadgeTotal > 0 && <span className="mb-tab-badge" aria-hidden="true" />}
        </button>
      </nav>

      <MobileMoreSheet
        open={moreOpen}
        selected={selected}
        badges={badges}
        icons={icons}
        onClose={() => onMoreOpenChange(false)}
        onNavigate={(t) => {
          onMoreOpenChange(false)
          onSelect(t)
        }}
        onOpenSearch={() => {
          onMoreOpenChange(false)
          onOpenSearch()
        }}
        onTogglePrivacy={() => {
          onMoreOpenChange(false)
          onTogglePrivacy()
        }}
        amountsHidden={amountsHidden}
        authRequired={authRequired}
        gateway={gateway}
        onLogout={(scope) => {
          onMoreOpenChange(false)
          void onLogout(scope)
        }}
        stamp={stamp}
      />
    </>
  )
}

// ── The More sheet ───────────────────────────────────────────────────────────

function MobileMoreSheet({
  open,
  selected,
  badges,
  icons,
  onClose,
  onNavigate,
  onOpenSearch,
  onTogglePrivacy,
  amountsHidden,
  authRequired,
  onLogout,
  gateway,
  stamp,
}: {
  open: boolean
  selected: MobileNavTarget
  badges: Partial<Record<TabId, number>>
  icons: Record<TabId, ReactElement>
  onClose: () => void
  onNavigate: (tab: TabId) => void
  onOpenSearch: () => void
  onTogglePrivacy: () => void
  amountsHidden: boolean
  authRequired: boolean
  onLogout: (scope?: SignOutScope) => void
  gateway?: GatewayIdentity | null
  stamp: { asOf: string; prices: PriceStatus }
}) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const [confirmingLogout, setConfirmingLogout] = useState<SignOutScope | null>(null)
  const dragHandlers = useSheetDragToDismiss({
    sheetRef,
    overlayRef,
    onDismiss: onClose,
    enabled: open,
  })

  const row = (t: TabId) => (
    <button
      key={t}
      type="button"
      className={`mb-nav-row${selected === t ? ' active' : ''}`}
      onClick={() => onNavigate(t)}
      aria-current={selected === t ? 'page' : undefined}
    >
      <span className="mb-nav-row-icon">{icons[t]}</span>
      <span className="mb-nav-row-copy">
        <span className="mb-nav-row-label">{TAB_LABELS[t]}</span>
      </span>
      {(badges[t] ?? 0) > 0 && (
        <span className="mb-nav-row-badge">{(badges[t] ?? 0) > 9 ? '9+' : badges[t]}</span>
      )}
      <span className="mb-nav-row-chevron">
        <ChevronRight size={15} strokeWidth={1.6} aria-hidden="true" />
      </span>
    </button>
  )

  // Demoted from the topbar (MB4.1) — same row chrome as a nav row, but an
  // action rather than a navigation target, so no chevron/active state.
  const actionRow = (
    key: string,
    icon: ReactElement,
    label: string,
    onClick: () => void,
    danger = false,
  ) => (
    <button
      key={key}
      type="button"
      className={`mb-nav-row${danger ? ' mb-nav-row--danger' : ''}`}
      onClick={onClick}
    >
      <span className="mb-nav-row-icon">{icon}</span>
      <span className="mb-nav-row-label">{label}</span>
    </button>
  )

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay ref={overlayRef} className={`mb-sheet-scrim${open ? ' on' : ''}`} />
        <Dialog.Content
          ref={sheetRef}
          className={`mb-sheet${open ? ' on' : ''}`}
          aria-describedby={undefined}
          data-ctx-ignore
          // Radix's default autofocus lands on the ✕ button — the first tabbable element in
          // the sheet — leaving a visible focus ring on it the instant the sheet opens, even
          // though nothing was tabbed to. Redirect to the sheet's own root (Radix already
          // gives Content `tabIndex={-1}`) so opening the sheet doesn't visibly "select" ✕.
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            sheetRef.current?.focus({ preventScroll: true })
          }}
          {...dragHandlers}
        >
          <div className="mb-sheet-handle-wrap">
            <span className="mb-sheet-handle" />
          </div>
          <div className="mb-sheet-head">
            <div className="mb-sheet-heading">
              <Dialog.Title className="mb-sheet-title">More</Dialog.Title>
              <div className="mb-sheet-stamp">
                As of {stamp.asOf} •{' '}
                <span className={stamp.prices === 'live' ? 'mb-sheet-stamp-live' : undefined}>
                  {PRICE_STATUS_LABEL[stamp.prices]}
                </span>
              </div>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="mb-sheet-x" aria-label="Close">
                <X size={18} strokeWidth={1.6} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>
          <div className="mb-sheet-body">
            <div className="mb-sheet-group">
              <div className="mb-sheet-group-h">Actions</div>
              {actionRow(
                'search',
                <Search size={17} strokeWidth={1.6} aria-hidden="true" />,
                'Search',
                onOpenSearch,
              )}
              {actionRow(
                'privacy',
                amountsHidden ? (
                  <EyeOff size={17} strokeWidth={1.6} aria-hidden="true" />
                ) : (
                  <Eye size={17} strokeWidth={1.6} aria-hidden="true" />
                ),
                amountsHidden ? 'Show amounts' : 'Hide amounts',
                onTogglePrivacy,
              )}
              {actionRow(
                'feedback',
                <Bug size={17} strokeWidth={1.6} aria-hidden="true" />,
                'Send feedback',
                () => {
                  onClose()
                  // After the sheet's own close has run: it stays mounted to animate out and
                  // would otherwise take focus back from the dialog that just opened.
                  setTimeout(() => openReportProblem(), 0)
                },
              )}
            </div>
            <div className="mb-sheet-group">
              <div className="mb-sheet-group-h">Tabs</div>
              {SHEET_SECTIONS.map(row)}
            </div>
            <div className="mb-sheet-group">
              <div className="mb-sheet-group-h">Manage</div>
              {CONFIG_TABS.map(row)}
            </div>
            {authRequired && (
              <div className="mb-sheet-group">
                <div className="mb-sheet-group-h">Account</div>
                {gateway?.email && (
                  <div className="mb-sheet-note">Signed in as {gateway.email}</div>
                )}
                {gateway?.portal_url && (
                  <a
                    className="mb-nav-row"
                    href={gateway.portal_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <span className="mb-nav-row-icon">
                      <ExternalLink size={17} strokeWidth={1.6} aria-hidden="true" />
                    </span>
                    <span className="mb-nav-row-label">Private Cloud portal</span>
                  </a>
                )}
                {actionRow(
                  'logout',
                  <LogOut size={17} strokeWidth={1.6} aria-hidden="true" />,
                  gateway ? SIGN_OUT_COPY.app.label : 'Log out',
                  () => setConfirmingLogout('app'),
                  true,
                )}
                {gateway &&
                  actionRow(
                    'logout-everywhere',
                    <LogOut size={17} strokeWidth={1.6} aria-hidden="true" />,
                    SIGN_OUT_COPY.everywhere.label,
                    () => setConfirmingLogout('everywhere'),
                    true,
                  )}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
      <ConfirmDialog
        data={
          confirmingLogout && gateway
            ? {
                title: SIGN_OUT_COPY[confirmingLogout].title,
                body: SIGN_OUT_COPY[confirmingLogout].body,
                confirmLabel: 'Sign out',
                danger: true,
              }
            : confirmingLogout
              ? {
                  title: 'Log out?',
                  body: "You'll need your password to sign back in.",
                  confirmLabel: 'Log out',
                  danger: true,
                }
              : null
        }
        onConfirm={() => {
          const scope = confirmingLogout ?? 'app'
          setConfirmingLogout(null)
          onLogout(scope)
        }}
        onCancel={() => setConfirmingLogout(null)}
      />
    </Dialog.Root>
  )
}
