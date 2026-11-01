// @vitest-environment jsdom
/**
 * MobileTabBar.test.tsx — the phone navigation shell.
 *
 * Covers the acceptance criteria that are pure component behaviour
 * (docs/qa/acceptance/mobile-shell.md):
 *   MB1.1 — exactly 5 slots: Overview, Accounts, Lucius, Investments, More (Plan lives in
 *           the More sheet — docs/adr/2026-08-06-mobile-tab-bar-drops-to-three-money-destinations.md)
 *   MB1.2 — tapping a slot navigates and marks it active
 *   MB1.3 — a tab that lives in the More sheet keeps More reading as active
 *   MB2.1 — the More sheet carries "Tabs" + "Manage" groups
 *   MB2.2 — a sheet row navigates and dismisses
 *   MB2.4 — a demoted Inbox count still surfaces (dot on More + count in the row)
 *   MB3   — the Lucius tab: active state, streaming indicator, unread badge
 *   MB4.1 — Search + Hide amounts, demoted from the topbar, live in an "Actions" group
 *   MB5.1 — Log out, demoted from the topbar, lives at the bottom of the sheet in its
 *           own "Account" group, only when auth is required (MC-82)
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, within, fireEvent, cleanup } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MobileTabBar } from './MobileTabBar'
import { TABS, type MobileNavTarget, type TabId } from '../../tabs'

// Mutable chat state used across tests — override per-test by reassigning fields.
// Mirrors the stub in LuciusDock.test.tsx (the same useLuciusChat contract).
// `conversations`/`streamingIds`/`statusFor` are the concurrent-conversations additions
// (E4) — default to "nothing else going on" so pre-existing single-conversation tests
// don't have to know about them.
const mockChatState = {
  messages: [] as { id: string; role: string }[],
  streaming: false,
  conversations: [] as { id: string }[],
  streamingIds: new Set<string>(),
  statusFor: (_id: string) => 'idle' as 'idle' | 'needs_you' | 'working' | 'done' | 'failed',
}

vi.mock('../../state/luciusChat', () => ({
  useLuciusChat: () => mockChatState,
}))

const ICONS = Object.fromEntries(
  TABS.map((t) => [t, <span key={t} data-testid={`icon-${t}`} />]),
) as Record<TabId, ReactElement>

function setup(over: Partial<Parameters<typeof MobileTabBar>[0]> = {}) {
  const onSelect = vi.fn()
  const onMoreOpenChange = vi.fn()
  const onOpenSearch = vi.fn()
  const onTogglePrivacy = vi.fn()
  const onLogout = vi.fn(() => Promise.resolve())
  const utils = render(
    <MobileTabBar
      selected="dashboard"
      onSelect={onSelect}
      badges={{}}
      icons={ICONS}
      moreOpen={false}
      onMoreOpenChange={onMoreOpenChange}
      onOpenSearch={onOpenSearch}
      onTogglePrivacy={onTogglePrivacy}
      amountsHidden={false}
      authRequired={false}
      onLogout={onLogout}
      stamp={{ asOf: '6 August 2026, 09:41', prices: 'live' }}
      {...over}
    />,
  )
  return {
    onSelect,
    onMoreOpenChange,
    onOpenSearch,
    onTogglePrivacy,
    onLogout,
    ...utils,
  }
}

afterEach(() => {
  mockChatState.messages = []
  mockChatState.streaming = false
  mockChatState.conversations = []
  mockChatState.streamingIds = new Set()
  mockChatState.statusFor = () => 'idle'
})

// No global auto-cleanup in this project's vitest setup — unmount between cases so
// queries never see a previous render's DOM.
afterEach(() => cleanup())

describe('MobileTabBar', () => {
  it('MB1.1 — renders exactly five slots in the designed order', () => {
    const { container } = setup()
    const labels = Array.from(container.querySelectorAll('.mb-tab-label')).map((n) => n.textContent)
    expect(labels).toEqual(['Accounts', 'Investments', 'Lucius', 'Dashboard', 'More'])
  })

  it('MB1.2 — tapping a slot navigates and the active slot carries .active', () => {
    const { onSelect, container } = setup({ selected: 'accounts' })
    const slots = container.querySelectorAll('.mb-tab')
    expect(slots[0]!.className).toContain('active')
    expect(slots[1]!.className).not.toContain('active')
    fireEvent.click(slots[1]!)
    expect(onSelect).toHaveBeenCalledWith('investments')
  })

  it('MB1.3 — a tab that lives in the sheet keeps More reading as active', () => {
    const { container } = setup({ selected: 'intel' })
    const slots = container.querySelectorAll('.mb-tab')
    expect(slots[4]!.className).toContain('active')
  })

  // The bar takes ONE selection value, not an (activeTab, luciusOpen) pair. With a pair,
  // every slot has to re-derive "…and Lucius isn't open" and any slot that forgets lights
  // up alongside Lucius. There is no pair to disagree with itself any more — this walks
  // every reachable selection to prove it.
  it('MB1.4 — exactly one slot reads as active, for every possible selection', () => {
    for (const selected of [...TABS, 'lucius'] as MobileNavTarget[]) {
      const { container } = setup({ selected })
      expect(container.querySelectorAll('.mb-tab.active')).toHaveLength(1)
      expect(container.querySelectorAll('.mb-tab[aria-current="page"]')).toHaveLength(1)
      cleanup()
    }
  })

  it('MB1.5 — every slot reports through the same onSelect handler', () => {
    const { onSelect, container } = setup()
    const slots = container.querySelectorAll('.mb-tab')

    fireEvent.click(slots[2]!)
    expect(onSelect).toHaveBeenCalledWith('lucius')

    fireEvent.click(slots[3]!)
    expect(onSelect).toHaveBeenCalledWith('dashboard')
  })

  it('MB2.1 — the sheet carries the Actions + Tabs + Manage groups', () => {
    setup({ moreOpen: true })
    expect(within(document.body).getByText('Actions')).toBeTruthy()
    expect(within(document.body).getByText('Tabs')).toBeTruthy()
    expect(within(document.body).getByText('Manage')).toBeTruthy()
    const rows = Array.from(document.body.querySelectorAll('.mb-nav-row-label')).map(
      (n) => n.textContent,
    )
    expect(rows).toEqual([
      'Search',
      'Hide amounts',
      'Send feedback',
      // 'Plan' (the 'plan' tab) joined the Tabs group in the 2026-08-06 tab-bar rework —
      // it moved off the bar itself so Lucius could sit at the true center of a 5-slot bar.
      'Assets',
      'Plan',
      'Commitments',
      'Intel',
      'Automations',
      'Skills',
      'Connectors',
      // No 'Profile' row: since the 2026-08-05 redesign Profile is the first SECTION of the
      // Settings overlay, not a tab of its own, so the sheet offers one way in, not two.
      // No 'Help' row either: Help is a SECTION of Settings, not a tab — same reasoning
      // as Profile above. It has no CONFIG_TABS entry, so this sheet never offers it.
      'Settings',
    ])
  })

  it('the ✕ button closes the sheet (existing dismiss mechanism)', () => {
    const { onMoreOpenChange } = setup({ moreOpen: true })
    fireEvent.click(within(document.body).getByRole('button', { name: 'Close' }))
    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('a downward drag on the handle past the distance threshold dismisses the sheet', async () => {
    const { onMoreOpenChange } = setup({ moreOpen: true })
    const sheet = document.body.querySelector('.mb-sheet') as HTMLElement
    const handle = sheet.querySelector('.mb-sheet-handle-wrap') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    // No matchMedia override in this file (see vitest.setup.ts's default stub), so
    // prefers-reduced-motion reads false — await the real ~200ms commit animation.
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('a short drag on the handle springs back without dismissing the sheet', async () => {
    const { onMoreOpenChange } = setup({ moreOpen: true })
    const sheet = document.body.querySelector('.mb-sheet') as HTMLElement
    const handle = sheet.querySelector('.mb-sheet-handle-wrap') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(handle, { clientY: 230, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 230, pointerId: 1 })

    expect(onMoreOpenChange).not.toHaveBeenCalled()
  })

  it('dragging the sheet body content directly (no scrollable overflow) drags the whole sheet, same as the handle', async () => {
    const { onMoreOpenChange } = setup({ moreOpen: true })
    const sheet = document.body.querySelector('.mb-sheet') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })

    fireEvent.pointerDown(sheet, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(sheet, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(sheet, { clientY: 400, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('dragging DOWN on scrolled body content is left to the browser — the sheet never moves and never dismisses', async () => {
    const { onMoreOpenChange } = setup({ moreOpen: true })
    const sheet = document.body.querySelector('.mb-sheet') as HTMLElement
    const body = sheet.querySelector('.mb-sheet-body') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
    Object.defineProperty(body, 'scrollHeight', { value: 600, configurable: true })
    Object.defineProperty(body, 'clientHeight', { value: 300, configurable: true })
    body.style.overflowY = 'auto'
    body.scrollTop = 150

    fireEvent.pointerDown(body, { clientY: 200, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 150))
    fireEvent.pointerMove(body, { clientY: 500, pointerId: 1 }) // 300px pull, well past the 35% dismiss threshold
    fireEvent.pointerUp(body, { clientY: 500, pointerId: 1 })
    await new Promise((resolve) => setTimeout(resolve, 250))

    expect(body.scrollTop).toBe(150) // never touched — the hook never writes scrollTop
    expect(sheet.style.transform).toBe('') // never armed — declined at its first move
    expect(onMoreOpenChange).not.toHaveBeenCalled()
  })

  it('MB2.2 — a sheet row navigates and closes the sheet', () => {
    const { onSelect, onMoreOpenChange } = setup({ moreOpen: true })
    fireEvent.click(
      within(document.body).getByText('Commitments', { selector: '.mb-nav-row-label' }),
    )
    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
    expect(onSelect).toHaveBeenCalledWith('commitments')
  })

  it('MB2.2b — the Inbox and Build tabs are not in the sheet (hidden pending further work)', () => {
    setup({ moreOpen: true })
    const rows = Array.from(document.body.querySelectorAll('.mb-nav-row-label')).map(
      (n) => n.textContent,
    )
    expect(rows).not.toContain('Inbox')
    // 'build' was pulled from CONFIG_TABS in the same 2026-08-06 change (tabs.ts), "same
    // treatment as inbox" — so its row (and the description the row() renderer special-cased
    // for it) can never appear; that description branch was dead code and has been removed.
    expect(rows).not.toContain('Build')
  })

  it('MB2.4 — a demoted count still surfaces on More and in its row', () => {
    const { container } = setup({ moreOpen: true, badges: { intel: 3 } })
    expect(container.querySelector('.mb-tab-badge')).toBeTruthy()
    expect(document.body.querySelector('.mb-nav-row-badge')!.textContent).toBe('3')
  })

  it('MB2.4 — no badge at all when nothing is pending', () => {
    const { container } = setup({ moreOpen: true })
    expect(container.querySelector('.mb-tab-badge')).toBeNull()
    expect(document.body.querySelector('.mb-nav-row-badge')).toBeNull()
  })

  it('MB2.5 — Tab and Shift+Tab stay inside the open More sheet', () => {
    setup({ moreOpen: true })
    const sheet = document.body.querySelector<HTMLElement>('.mb-sheet')!
    expect(document.querySelectorAll('[data-radix-focus-guard]')).toHaveLength(2)

    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    fireEvent.focusIn(outside)
    expect(sheet.contains(document.activeElement)).toBe(true)
    outside.remove()
  })

  it('MB4.1 — tapping Search fires onOpenSearch and closes the sheet', () => {
    const { onOpenSearch, onMoreOpenChange } = setup({
      moreOpen: true,
    })
    fireEvent.click(within(document.body).getByText('Search', { selector: '.mb-nav-row-label' }))
    expect(onOpenSearch).toHaveBeenCalledTimes(1)
    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('MB4.1 — tapping the privacy row fires onTogglePrivacy and closes the sheet, label reflects amountsHidden', () => {
    const { onTogglePrivacy, onMoreOpenChange } = setup({
      moreOpen: true,
      amountsHidden: false,
    })
    expect(within(document.body).getByText('Hide amounts')).toBeTruthy()
    fireEvent.click(
      within(document.body).getByText('Hide amounts', {
        selector: '.mb-nav-row-label',
      }),
    )
    expect(onTogglePrivacy).toHaveBeenCalledTimes(1)
    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('MB4.1 — privacy row reads "Show amounts" once amounts are hidden', () => {
    setup({ moreOpen: true, amountsHidden: true })
    expect(within(document.body).getByText('Show amounts')).toBeTruthy()
  })

  it('MB5.1 — no Account/Log out row when auth is not required', () => {
    setup({ moreOpen: true, authRequired: false })
    expect(within(document.body).queryByText('Account')).toBeNull()
    expect(within(document.body).queryByText('Log out')).toBeNull()
  })

  it('MB5.1 — Log out sits in its own Account group at the very bottom of the sheet', () => {
    setup({ moreOpen: true, authRequired: true })
    const groups = Array.from(document.body.querySelectorAll('.mb-sheet-group-h')).map(
      (n) => n.textContent,
    )
    expect(groups[groups.length - 1]).toBe('Account')
    const rows = Array.from(document.body.querySelectorAll('.mb-nav-row-label')).map(
      (n) => n.textContent,
    )
    expect(rows[rows.length - 1]).toBe('Log out')
  })

  it('MB5.1 — tapping Log out asks for confirmation before doing anything', () => {
    const { onLogout, onMoreOpenChange } = setup({
      moreOpen: true,
      authRequired: true,
    })
    fireEvent.click(within(document.body).getByText('Log out', { selector: '.mb-nav-row-label' }))
    expect(onLogout).not.toHaveBeenCalled()
    expect(onMoreOpenChange).not.toHaveBeenCalled()
    expect(within(document.body).getByText('Log out?')).toBeTruthy()
  })

  it('MB5.1 — confirming the Log out dialog fires onLogout and closes the sheet', () => {
    const { onLogout, onMoreOpenChange } = setup({
      moreOpen: true,
      authRequired: true,
    })
    fireEvent.click(within(document.body).getByText('Log out', { selector: '.mb-nav-row-label' }))
    fireEvent.click(document.body.querySelector('.btn-danger')!)
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(onMoreOpenChange).toHaveBeenCalledWith(false)
  })

  it('MB5.1 — cancelling the Log out dialog does not log out', () => {
    const { onLogout, onMoreOpenChange } = setup({
      moreOpen: true,
      authRequired: true,
    })
    fireEvent.click(within(document.body).getByText('Log out', { selector: '.mb-nav-row-label' }))
    fireEvent.click(within(document.body).getByText('Cancel'))
    expect(onLogout).not.toHaveBeenCalled()
    expect(onMoreOpenChange).not.toHaveBeenCalled()
    expect(within(document.body).queryByText('Log out?')).toBeNull()
  })

  // MB2.6 — the topbar's "As of … · Prices" stamp, demoted into the sheet. On a phone the
  // topbar held nothing else, so this line cost a row of every tab.
  it('MB2.6 — the sheet header carries the As-of / prices stamp', () => {
    setup({ moreOpen: true, stamp: { asOf: '6 August 2026, 09:41', prices: 'live' } })
    const foot = document.body.querySelector('.mb-sheet-stamp')!
    expect(foot.textContent).toBe('As of 6 August 2026, 09:41 • Prices: live')
    // Live is the only state that gets the --good treatment; the rest read as muted.
    expect(foot.querySelector('.mb-sheet-stamp-live')).toBeTruthy()
  })

  // It sits in the fixed head, NOT the scrolling body: as a footer row it landed below
  // the fold and under the home indicator — you had to scroll a nav sheet to read status.
  it('MB2.6 — the stamp is in the sheet head, not the scrollable body', () => {
    setup({ moreOpen: true })
    const stamp = document.body.querySelector('.mb-sheet-stamp')!
    expect(stamp.closest('.mb-sheet-head')).toBeTruthy()
    expect(stamp.closest('.mb-sheet-body')).toBeNull()
  })

  it('MB2.6 — the stamp reports loading and cached without the live styling', () => {
    setup({ moreOpen: true, stamp: { asOf: '6 August 2026, 09:41', prices: 'loading' } })
    let foot = document.body.querySelector('.mb-sheet-stamp')!
    expect(foot.textContent).toContain('Prices: loading…')
    expect(foot.querySelector('.mb-sheet-stamp-live')).toBeNull()
    cleanup()

    setup({ moreOpen: true, stamp: { asOf: '6 August 2026, 09:41', prices: 'cached' } })
    foot = document.body.querySelector('.mb-sheet-stamp')!
    expect(foot.textContent).toContain('Prices: cached')
    expect(foot.querySelector('.mb-sheet-stamp-live')).toBeNull()
  })

  it('MB2.6 — the stamp is not a tap target and does not sit in the tab bar', () => {
    const { container } = setup({ moreOpen: true })
    expect(container.querySelector('.mb-sheet-stamp')).toBeNull()
    expect(document.body.querySelector('.mb-sheet-stamp')!.closest('button')).toBeNull()
  })

  it('MB3 — tapping the Lucius tab selects it', () => {
    const { onSelect, container } = setup()
    const slots = container.querySelectorAll('.mb-tab')
    fireEvent.click(slots[2]!)
    expect(onSelect).toHaveBeenCalledExactlyOnceWith('lucius')
  })

  it('MB3 — the Lucius tab carries .active while it is the selection', () => {
    const { container } = setup({ selected: 'lucius' })
    const slots = container.querySelectorAll('.mb-tab')
    expect(slots[2]!.className).toContain('active')
    // A destination tab and the Lucius tab are never simultaneously .active.
    expect(slots[0]!.className).not.toContain('active')
  })

  it('MB3 — Lucius is the only aria-current destination while its tab is open', () => {
    const { container } = setup({ selected: 'lucius' })
    const current = Array.from(container.querySelectorAll('.mb-tab')).filter(
      (slot) => slot.getAttribute('aria-current') === 'page',
    )
    expect(current).toHaveLength(1)
    expect(current[0]!.textContent).toContain('Lucius')
  })

  it('MB3 — a streaming turn shows the spinner in place of the spark icon', () => {
    mockChatState.streaming = true
    mockChatState.streamingIds = new Set(['active-conv'])
    const { container } = setup()
    const luciusIcon = container.querySelectorAll('.mb-tab')[2]!.querySelector('.mb-tab-icon')
    expect(luciusIcon!.querySelector('.lx-dock-dot')).toBeTruthy()
    expect(luciusIcon!.querySelector('.acct-spark')).toBeNull()
  })

  it('MB3 — a completed answer shows an unread badge only while the tab is closed', () => {
    mockChatState.messages = [{ id: 'm1', role: 'assistant' }]
    const closed = setup({ selected: 'dashboard' })
    const closedSlot = closed.container.querySelectorAll('.mb-tab')[2]!
    expect(closedSlot.querySelector('.mb-tab-badge')).toBeTruthy()
    cleanup()

    const open = setup({ selected: 'lucius' })
    const openSlot = open.container.querySelectorAll('.mb-tab')[2]!
    expect(openSlot.querySelector('.mb-tab-badge')).toBeNull()
  })

  // E4 — the Lucius tab badges when a background conversation completes while you are
  // on another tab (docs/qa/acceptance/mobile-shell.md MB3.3, widened for concurrent
  // conversations).
  it('E4 — a background conversation completing while another tab is active badges Lucius', () => {
    mockChatState.conversations = [{ id: 'bg-conv' }]
    mockChatState.statusFor = (id) => (id === 'bg-conv' ? 'done' : 'idle')
    const { container } = setup({ selected: 'dashboard' })
    const luciusSlot = container.querySelectorAll('.mb-tab')[2]!
    expect(luciusSlot.querySelector('.mb-tab-badge')).toBeTruthy()
  })

  it("E4 — viewing Lucius clears only the viewed conversation's unread, not a background one", () => {
    // The loaded conversation has its own unread answer (would badge if the tab were
    // closed) AND a second, different conversation is separately unread in the
    // background. Opening the tab (`selected: 'lucius'`) clears the loaded one's
    // contribution but must not touch the background conversation's.
    mockChatState.messages = [{ id: 'm1', role: 'assistant' }]
    mockChatState.conversations = [{ id: 'bg-conv' }]
    mockChatState.statusFor = (id) => (id === 'bg-conv' ? 'done' : 'idle')
    const { container } = setup({ selected: 'lucius' })
    const luciusSlot = container.querySelectorAll('.mb-tab')[2]!
    expect(luciusSlot.querySelector('.mb-tab-badge')).toBeTruthy()
  })

  it('E4 — any conversation working shows the spinner, even when the active one is idle', () => {
    mockChatState.streaming = false
    mockChatState.streamingIds = new Set(['bg-conv'])
    const { container } = setup({ selected: 'dashboard' })
    const luciusIcon = container.querySelectorAll('.mb-tab')[2]!.querySelector('.mb-tab-icon')
    expect(luciusIcon!.querySelector('.lx-dock-dot')).toBeTruthy()
    expect(luciusIcon!.querySelector('.acct-spark')).toBeNull()
  })

  it('E4 — a background conversation needing approval badges Lucius', () => {
    mockChatState.conversations = [{ id: 'bg-conv' }]
    mockChatState.statusFor = (id) => (id === 'bg-conv' ? 'needs_you' : 'idle')
    const { container } = setup({ selected: 'dashboard' })
    const luciusSlot = container.querySelectorAll('.mb-tab')[2]!
    expect(luciusSlot.querySelector('.mb-tab-badge')).toBeTruthy()
  })
})
