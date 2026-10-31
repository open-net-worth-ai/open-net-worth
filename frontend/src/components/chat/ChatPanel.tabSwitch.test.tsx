// @vitest-environment jsdom
//
// ChatPanel.tabSwitch.test.tsx — owner report: "switching between tabs loses the input text in
// the composer, mention chips included."
//
// The phone's Lucius tab (LuciusMobile) and the desktop home tab (LuciusHome) mount ChatPanel
// with open={true} ONLY while their tab is showing, so switching tabs unmounts it — and the
// composer is DOM-owned (MentionComposer never reconciles its children from React state), so
// its content dies with the DOM. ChatPanel only saved/restored the shared composerDraft on the
// side panel's open↔close transitions; a mount that starts open and an unmount while open did
// neither. The same mocks as ChatPanel.pendingText.test.tsx: the real ChatPanel and the real
// composer are the seam under test.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { useEffect, useState } from 'react'
import { render, act, cleanup, fireEvent, screen } from '@testing-library/react'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'
import type { MentionRef } from '../../types/mentions'

// ── Mocks: everything ChatPanel needs that is not part of this seam ───────────

const chatState = {
  conversations: [] as unknown[],
  activeId: 'conv-1',
  createConversation: vi.fn(),
  switchConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  setConversationModel: vi.fn(),
  messages: [] as unknown[],
  queuedMessages: [] as unknown[],
  hydrated: true,
  streaming: false,
  multiTurnEnabled: false,
  following: false,
  sendMessage: vi.fn(),
  resolvePendingToolUse: vi.fn(),
  stopStreaming: vi.fn(),
  sending: false,
  liveOutputTokens: 0,
  compacting: null,
  retryLast: vi.fn(),
  respondToChange: vi.fn(),
  previewCheckpoint: vi.fn(),
  restoreCheckpoint: vi.fn(),
  suggestions: [] as string[],
  suggestionsLoading: false,
  fetchSuggestions: vi.fn(),
  statusFor: () => 'idle',
  stopConversation: vi.fn(),
}

let suggestionsEnabled = false
let profileData: { nickname?: string; name?: string } | null = null

vi.mock('../../state/luciusChat', () => ({
  useLuciusChat: () => chatState,
}))
vi.mock('../../state/chatSessions', () => ({
  chatSessions: { statusFor: () => 'idle' },
}))
vi.mock('../../state/chatReducer', () => ({
  loadEarlierMessages: vi.fn(async () => 0),
}))
vi.mock('../../utils/chatStore', () => ({
  positionalCounter: () => null,
}))
vi.mock('../../hooks/useChatSuggestionsEnabled', () => ({
  useChatSuggestionsEnabled: () => ({ enabled: suggestionsEnabled, loaded: true }),
}))
vi.mock('../../hooks/useModels', () => ({
  useModels: () => ({ models: [], defaultModel: '', imageReader: '' }),
  resolveModelOverride: () => '',
  effortChoicesFor: () => [],
  clampEffortChoice: (e: string) => e,
  effortChoiceLabel: (e: string) => e,
  noVisionLabel: () => null,
}))
vi.mock('../../hooks/serverState', () => ({
  useProfile: () => ({ data: profileData }),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))
vi.mock('../tabs/overview/attentionFlags', () => ({
  useAttentionFlags: () => ({ flags: [], loading: false }),
}))
vi.mock('../../lucius/visibleIds', () => ({
  useVisibleIdsValue: () => ({ visibleIds: new Set<string>() }),
}))
vi.mock('../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
  }),
}))
vi.mock('../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'T', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
  screenSuggestions: () => [],
}))
vi.mock('../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
  IdentityInner: () => null,
}))
vi.mock('../../hooks/useMentionables', () => ({
  useMentionables: () => ({ items: [], loading: false }),
}))
vi.mock('./MessageBubble', () => ({ MessageBubble: () => null }))
vi.mock('./TurnActivity', () => ({ TurnActivity: () => null }))
vi.mock('./ConversationBrowser', () => ({ ConversationBrowser: () => null }))
vi.mock('./ConversationMenu', () => ({
  ConversationMenu: () => null,
  shortWhen: () => '',
}))
vi.mock('./lucius/Autocomplete', () => ({ Autocomplete: () => null }))
vi.mock('./lucius/ScreenContextBar', () => ({ ScreenContextBar: () => null }))
// PermissionModeControl joined this mock when the composer gained the per-conversation
// approvals picker (2026-08-27) — ChatPanel now reaches ../ui through
// ConversationApprovalPicker as well as directly.
vi.mock('../ui', () => ({ ModelPicker: () => null, PermissionModeControl: () => null }))
vi.mock('./lucius/ModelEffortRow', () => ({ EffortChoices: () => null }))

// ── Shell ─────────────────────────────────────────────────────────────────────

import { ChatPanel } from './ChatPanel'

let switchTab: (tab: 'lucius' | 'accounts') => void
let stageMention: (m: MentionRef) => void

/** RouterShell's phone shape: the Lucius tab's panel exists only while that tab is showing. */
function TabShell() {
  const [tab, setTab] = useState<'lucius' | 'accounts'>('lucius')
  const { addMention } = useChatComposer()
  useEffect(() => {
    switchTab = setTab
    stageMention = (m) => addMention(m, { open: false })
  }, [addMention])
  return tab === 'lucius' ? (
    <div data-testid="panel">
      <ChatPanel open onOpenChange={() => {}} fillWidth autoFocusComposer={false} homeMode />
    </div>
  ) : (
    <div data-testid="accounts" />
  )
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 30))
  })
}

function editor(): HTMLElement {
  return document.querySelector<HTMLElement>('[data-testid="panel"] .chat-composer')!
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  suggestionsEnabled = false
  profileData = null
  vi.useRealTimers()
})

const AAPL: MentionRef = { type: 'holding', ref: 'pos_123', label: 'AAPL holding' }

describe('the home composer draft survives a tab switch', () => {
  it('text AND a mention chip typed on the Lucius tab are back after Accounts → Lucius', async () => {
    render(
      <ChatComposerProvider>
        <TabShell />
      </ChatComposerProvider>,
    )
    await settle()

    act(() => stageMention(AAPL))
    await settle()
    act(() => {
      editor().appendChild(document.createTextNode('merge with'))
      fireEvent.input(editor())
    })
    expect(editor().querySelector('.mention-chip')).not.toBeNull()
    expect(editor().textContent).toContain('merge with')

    act(() => switchTab('accounts'))
    await settle()
    expect(document.querySelector('[data-testid="panel"]')).toBeNull()

    act(() => switchTab('lucius'))
    await settle()

    const chip = editor().querySelector<HTMLElement>('.mention-chip')
    expect(chip?.dataset.chipRef).toBe('pos_123')
    expect(editor().textContent).toContain('merge with')
  })

  it('restoring on mount never steals focus — the phone hides its tab bar while the composer is focused', async () => {
    // Owner report after the first fix: "first app open already has the bottom nav bar
    // disappeared". seed() focused the composer, and on the phone
    // `.mb-shell:has([contenteditable]:focus) .mb-tabbar { display: none }` hid the bar — with
    // no keyboard, since a programmatic focus raises none on iOS. Focus on mount belongs to
    // autoFocusComposer alone (LuciusMobile passes false), never to the draft restore.
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus')
    const editorFocused = () =>
      focusSpy.mock.contexts.some(
        (el) => el instanceof HTMLElement && el.classList.contains('chat-composer'),
      )
    try {
      render(
        <ChatComposerProvider>
          <TabShell />
        </ChatComposerProvider>,
      )
      await settle()
      expect(editorFocused()).toBe(false)

      act(() => stageMention(AAPL))
      await settle()
      act(() => switchTab('accounts'))
      await settle()
      focusSpy.mockClear()
      act(() => switchTab('lucius'))
      await settle()

      expect(editor().querySelector('.mention-chip')).not.toBeNull()
      expect(editorFocused()).toBe(false)
    } finally {
      focusSpy.mockRestore()
    }
  })

  it('a composer left EMPTY comes back empty (no stale draft resurrected)', async () => {
    render(
      <ChatComposerProvider>
        <TabShell />
      </ChatComposerProvider>,
    )
    await settle()
    act(() => switchTab('accounts'))
    await settle()
    act(() => switchTab('lucius'))
    await settle()
    expect(editor().querySelector('.mention-chip')).toBeNull()
    expect(editor().textContent.trim()).toBe('')
  })
})

describe('the dock empty state', () => {
  it('reuses the home greeting with one mention hint and does not generate starter suggestions', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 7, 12, 19))
    suggestionsEnabled = true
    profileData = { nickname: 'Alex' }

    render(
      <ChatComposerProvider>
        <ChatPanel open onOpenChange={() => {}} autoFocusComposer={false} />
      </ChatComposerProvider>,
    )

    expect(screen.getByText('Good evening, Alex')).toBeTruthy()
    expect(
      screen.getByText(
        'Use @, or drag anything on screen in, to add context with accounts, transactions, and more. Use / for skills.',
      ),
    ).toBeTruthy()
    expect(screen.queryByText('Your Personal Wealth Manager')).toBeNull()
    expect(chatState.fetchSuggestions).not.toHaveBeenCalled()
  })
})
