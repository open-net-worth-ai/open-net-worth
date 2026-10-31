// @vitest-environment jsdom
//
// The tool-permission card — three grant scopes and a denial
// (RFC `.claude/rfcs/2026-07-20-approval-and-review-model.md` §13.1; resolution mechanism
// superseded by RFC `.claude/rfcs/2026-07-22-approval-as-pending-tool-use.md`).
//
// These EXERCISE the buttons rather than asserting markup, because the whole value of this
// card is what happens on the click: which grant is recorded, in what order relative to the
// approval, and — since RFC 2026-07-22 — that approval/denial resolves the SAME tool_use_id
// the card is bound to (`change.id`) via `onResolvePendingToolUse`, never a model re-call.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createElement, useState } from 'react'
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

const TOOL = 'add_transactions'
const TOOL_USE_ID = 'toolu-abc'

const permissionCard: ProposedChange = {
  type: 'txn_create',
  grant_tool: TOOL,
  id: TOOL_USE_ID,
  session_id: 'sess-1',
  description: 'Coffee',
  category: { id: 'cat_food', label: 'Food', source: 'rule' },
  from: { account_id: 'acc_chk', account_name: 'Checking', amount: 12.4, currency: 'SGD' },
}

// What the server answers when a grant lands: the refreshed permissions list. An empty body
// would make the client's `res.json()` throw, which the card reports as a failed grant.
const savedResponse = () =>
  new Response(JSON.stringify({ permissions: [], total: 0 }), { status: 200 })

let resolved: Array<[string, 'approve' | 'deny']>
let fetchMock: ReturnType<typeof vi.fn>

function mount(change: ProposedChange = permissionCard) {
  return render(
    createElement(ChangePreviewV2, {
      change,
      onResolvePendingToolUse: (toolUseId: string, decision: 'approve' | 'deny') =>
        resolved.push([toolUseId, decision]),
      onRespond: () => {},
    }),
  )
}

beforeEach(() => {
  resolved = []
  fetchMock = vi.fn(async () => savedResponse())
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** The parsed body of the single PUT /api/tool-permissions/{tool} call, or null if none
 *  was made. */
function grantBody(): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/api/tool-permissions/'))
  return call ? JSON.parse((call[1] as RequestInit).body as string) : null
}

describe('tool-permission card — the three choices', () => {
  it('renders nested old/new prompt arguments as a diff marking the changed words', () => {
    const { container } = mount({
      type: 'tool_permission',
      grant_tool: 'update_agent',
      id: TOOL_USE_ID,
      session_id: 'sess-1',
      reason: 'update_agent needs your approval before it runs.',
      display: {
        tool: 'update_agent',
        arguments: {
          agent_id: 'transaction-trade-logger',
          changes: {
            prompt: {
              old: 'Keep the raw bank note.\nUse one external reference.',
              new: 'Keep the raw bank note.\nUse every matching external reference.',
            },
          },
        },
      },
    })

    expect(container.querySelector('.ac-diff .ac-diff-edited')?.textContent).toContain(
      'external reference.',
    )
    expect(container.querySelector('.ac-diff-word-removed')?.textContent).toBe('one')
    expect(container.querySelector('.ac-diff-word-added')?.textContent).toBe('every matching')
    expect(container.querySelector('.ac-diff .ac-diff-context')?.textContent).toContain(
      'Keep the raw bank note.',
    )

    const raw = screen.getByText('Raw arguments').closest('details') as HTMLDetailsElement
    expect(raw.open).toBe(false)
  })

  it('offers Allow once + Deny up front, with Always allow behind the caret', () => {
    mount()
    // SPLIT BUTTON (owner, 2026-07-20): the rarer, heavier standing scope sits behind a caret
    // so the common safe action does not compete with it for attention, and a narrow card
    // stops wrapping to two ragged rows. All three choices are still reachable.
    for (const label of ['Allow once', 'Deny']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    expect(screen.getByRole('menu').classList.contains('grant-split__menu--above')).toBe(true)
    expect(screen.getByRole('menuitem', { name: /Always/ })).toBeTruthy()
    // The per-conversation tier was removed on 2026-08-02.
    expect(screen.queryByRole('menuitem', { name: /session|conversation/i })).toBeNull()
    // The bare two-way card is REPLACED, not supplemented — leaving a plain "Confirm" beside
    // three scoped buttons would make the default choice ambiguous.
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
  })

  it('never shows the raw tool name — the card says what it will do, not what it will call', () => {
    // The note under the buttons used to read "Approving runs `remove_positions`." Every
    // other surface in the chat UI turns a tool call into a human sentence; a permission
    // card printing the snake_case name was the one place that leaked it (owner, 2026-08-25).
    // The standing-scope caveat it once carried had already moved onto the Always menu item,
    // so removing the note lost nothing that is still true.
    const { container } = mount()
    expect(container.querySelector('.change-grant-note')).toBeNull()
    expect(container.textContent).not.toContain(TOOL)
  })

  it('states what an Always grant covers — unbounded, revocable in Settings — ON the Always item', () => {
    // Load-bearing copy relocated, not deleted: a grant has NO amount ceiling and no argument
    // matching (§13.3b), and Settings is the ONLY way to revoke (§16.1, checkpoint-excluded).
    // It must still be stated — now where it is contextually true, behind the caret.
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    const always = screen.getByRole('menuitem', { name: /Always/ })
    expect(always.textContent).toContain('any amount')
    expect(always.textContent).toContain('Settings')
  })

  it('surfaces a card with no server-side binding as a BUG, not a bare Confirm', () => {
    // Every staged card carries `grant_tool` or is `external_mcp_call` —
    // those are the only two ways a card can be answered (ADR
    // 2026-08-25-one-confirm-mechanism-for-every-tool). The bare Confirm/Dismiss row that
    // used to be the fallback answered a card by sending Lucius a SENTENCE, which is the
    // defect that ADR removes: it cannot resolve the paused tool_use, so the turn would
    // wait forever on a button that looks like it worked.
    const { container } = mount({ ...permissionCard, grant_tool: undefined })
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Allow once' })).toBeNull()
    expect(container.textContent).toContain('no way to be applied')
  })
})

describe('tool-permission card — what each scope records', () => {
  it('Allow once stores NOTHING and just approves the call', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(grantBody()).toBeNull() // no standing permission written
  })

  it('the removed session scope is not offered anywhere on the card', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    expect(screen.queryByText(/This session/i)).toBeNull()
  })

  it('Always records a durable grant keyed on the CANONICAL tool name', async () => {
    // Keying on the name at grant time is what makes a grant not survive a rename (§15.1) —
    // `delete_account` → `delete_accounts` turned one target into a batch, and a grant made
    // against the former must not silently carry to the latter.
    //
    // `session_id` rides along deliberately: it names WHICH conversation, so the backend can
    // route an Always in an Automation-owned run to that Automation's own allowed list rather
    // than the global table. It is not a scope.
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(grantBody()).toEqual({ level: 'always_allow', session_id: 'sess-1' })
  })

  it('records the grant BEFORE resolving the pending tool_use', async () => {
    // Order is the security property: the grant is written from a human-authenticated tap
    // first, so the model can never escalate its own permission — it only learns the outcome.
    const order: string[] = []
    fetchMock.mockImplementation(async () => {
      order.push('grant')
      return savedResponse()
    })
    render(
      createElement(ChangePreviewV2, {
        change: permissionCard,
        onResolvePendingToolUse: () => order.push('approve'),
        onRespond: () => {},
      }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(order).toEqual(['grant', 'approve']))
  })

  it('approves by resolving the SAME tool_use_id the card is bound to — no token, no model turn', async () => {
    // The server executes the STORED args at the tool_use_id, which is the reason the card is
    // trustworthy: a card showing $12.40 cannot commit $1,240 whatever a client reconstructs.
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(resolved[0]).toEqual([TOOL_USE_ID, 'approve'])
  })
})

describe('tool-permission card — a refused approval', () => {
  it('un-paints the optimistic tick and goes TERMINAL — never re-arms the buttons', async () => {
    // REGRESSION (2026-07-30): the card keeps its own optimistic `localResponse`, so when
    // `useChat` un-painted a refused approval (`status` back to undefined, because the server
    // could not redeem it) the card STILL showed "✓ Approved" — a tick over a red error, for a
    // delete that never ran. The local tick has to follow the persisted status back.
    //
    // REGRESSION #2 (mobile incident, 2026-09-06): un-painting the tick by clearing `status`
    // alone re-armed a live "Allow once ▾ / Deny" split button directly above the error that
    // explained why the FIRST tap did nothing. A refused approval can never be redeemed (the
    // backend consumes/replaces the pending tool_use once), so re-arming it is a lie — the card
    // must go terminal instead, and STAY terminal (never flip back to live buttons).
    //
    // A real parent is used rather than a hand-fed prop because the whole point is that the
    // refusal arrives LATER, asynchronously, on a card already painted by the tap — which is
    // why the signal is an explicit `refused` flag and not a `status` transition (the tap's own
    // updates batch into one render, so a transition watcher would never see them). The refusal
    // is driven explicitly from the test (via `revoke.current`) rather than a bare
    // `setTimeout(0)` racing the click's own async grant call — that race is real (the two
    // updates can coalesce before either ever paints) but orthogonal to what this test proves,
    // so it is pinned deterministically: wait for the optimistic paint to actually commit,
    // THEN fire the refusal.
    let revoke: (() => void) | null = null
    function Harness() {
      const [refused, setRefused] = useState(false)
      const [status, setStatus] = useState<ProposedChange['status']>(undefined)
      revoke = () => {
        // What `useChat`'s error branch does when the server refuses the redemption.
        setStatus(undefined)
        setRefused(true)
      }
      return createElement(ChangePreviewV2, {
        change: { ...permissionCard, status, refused: refused || undefined },
        onResolvePendingToolUse: (toolUseId: string, decision: 'approve' | 'deny') => {
          resolved.push([toolUseId, decision])
        },
        onRespond: (s) => setStatus(s),
      })
    }
    render(createElement(Harness))
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    await waitFor(() => expect(screen.getByText('✓ Approved')).toBeTruthy()) // painted optimistically, as designed
    act(() => revoke?.())
    await waitFor(() => expect(screen.getByText("Not run — can't be reused.")).toBeTruthy())
    expect(screen.queryByText('✓ Approved')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Allow once' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Deny' })).toBeNull()
  })
})

describe('tool-permission card — a failed grant', () => {
  it('still approves the call but says the standing permission did not stick', async () => {
    // The user approved THIS call and the card showed exactly what it does, so it proceeds.
    // What must never happen is a user believing they hold a permission they do not.
    fetchMock.mockImplementation(async () => new Response(null, { status: 500 }))
    const { container } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(resolved.length).toBe(1)) // approved anyway
    const err = container.querySelector('.change-grant-error')?.textContent ?? ''
    expect(err).toMatch(/couldn't save/i)
    expect(err).toMatch(/asked again/i)
  })

  it('a card with no session_id posts an unscoped always grant and reports a refusal', async () => {
    // D1/D2 regression, live incident 2026-07-20: `permissionCard` above hand-supplies
    // `session_id: 'sess-1'`, which is exactly what production did NOT do — the backend's
    // `stage()` never stamped the field, so a real card's `session_id` was `undefined` and
    // every other test in this file asserting the POST body was asserting its own fixture,
    // not the system. Mount the card the way it actually shipped (no session_id).
    //
    // The property that outlives the retired session scope: a rejected grant is REPORTED.
    // Silently proceeding leaves the user believing they hold a standing permission they do
    // not have, discovered only by being asked again with no explanation.
    fetchMock.mockImplementation(async () => new Response(null, { status: 422 }))
    const { container } = mount({ ...permissionCard, session_id: undefined })
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(grantBody()).toEqual({ level: 'always_allow' })
    const err = container.querySelector('.change-grant-error')?.textContent ?? ''
    expect(err).toMatch(/couldn't save/i)
    expect(err).toMatch(/asked again/i)
  })

  it('disables the row while a grant is in flight so a double-tap cannot double-approve', async () => {
    // A ref-object, not a bare `let` — a plain `let release: Fn | null` reassigned only
    // inside the mock's executor closure narrows to `never` at the call site under TS's
    // control-flow analysis (the reassignment lives in a nested function TS can't see as
    // preceding the read). A boxed `.current` field sidesteps that narrowing entirely.
    const releaseBox: { current: (() => void) | null } = { current: null }
    fetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseBox.current = () => resolve(savedResponse())
        }),
    )
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Deny' }) as HTMLButtonElement).disabled).toBe(
        true,
      ),
    )
    // A loading affordance, not just a dead button.
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeTruthy()
    releaseBox.current?.()
    await waitFor(() => expect(resolved.length).toBe(1))
  })
})

describe('tool-permission card — denial', () => {
  it('Deny resolves the tool_use_id as a denial and sends no grant', async () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(resolved[0]).toEqual([TOOL_USE_ID, 'deny'])
    expect(grantBody()).toBeNull()
  })

  it('resolves the card as Denied, not Dismissed', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(screen.getByText('✗ Denied')).toBeTruthy()
  })
})

describe('the three card outcomes are distinct — live, stored and after a reload', () => {
  const automationCard: ProposedChange = { ...permissionCard, automation_id: 'automation_a' }
  let answers: Array<[string, 'approve' | 'deny', unknown]>

  function mountRecording(change: ProposedChange) {
    answers = []
    return render(
      createElement(ChangePreviewV2, {
        change,
        onResolvePendingToolUse: (id: string, decision: 'approve' | 'deny', options?: unknown) =>
          answers.push([id, decision, options]),
        onRespond: () => {},
      }),
    )
  }

  it('Approve runs this call once: nothing stored, labelled Approved', async () => {
    mountRecording(permissionCard)
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(answers.length).toBe(1))
    expect(answers).toEqual([[TOOL_USE_ID, 'approve', { always: false }]])
    expect(grantBody()).toBeNull()
    expect(screen.getByText('✓ Approved')).toBeTruthy()
  })

  it('Always allow runs it AND stores the permission: labelled Always allowed', async () => {
    mountRecording(permissionCard)
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(answers.length).toBe(1))
    expect(answers[0]).toEqual([TOOL_USE_ID, 'approve', { always: true }])
    expect(grantBody()).toEqual({ level: 'always_allow', session_id: 'sess-1' })
    expect(screen.getByText('✓ Always allowed')).toBeTruthy()
  })

  it('inside an Automation run Always allow is routed by session_id to that Automation', async () => {
    mountRecording(automationCard)
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    expect(screen.getByRole('menuitem', { name: /Always/ }).textContent).toContain(
      'this Automation',
    )
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(answers.length).toBe(1))
    expect(grantBody()).toEqual({ level: 'always_allow', session_id: 'sess-1' })
  })

  it('Deny refuses this call only: nothing stored, labelled Denied', () => {
    mountRecording(permissionCard)
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))
    expect(answers).toEqual([[TOOL_USE_ID, 'deny', undefined]])
    expect(grantBody()).toBeNull()
    expect(screen.getByText('✗ Denied')).toBeTruthy()
  })

  it("a refused Always allow shows the server's sentence and is recorded as Approved, not Always allowed", async () => {
    const detail = 'This Automation no longer exists, so nothing was changed.'
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    const { container } = mountRecording(automationCard)
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(answers.length).toBe(1))
    expect(container.querySelector('.change-grant-error')?.textContent).toContain(detail)
    expect(answers[0]).toEqual([TOOL_USE_ID, 'approve', { always: false }])
    expect(screen.getByText('✓ Approved')).toBeTruthy()
    expect(screen.queryByText('✓ Always allowed')).toBeNull()
  })

  it('any other failed Always allow keeps its own wording and is also recorded as Approved', async () => {
    fetchMock.mockImplementation(async () => new Response(null, { status: 500 }))
    const { container } = mountRecording(permissionCard)
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(answers.length).toBe(1))
    expect(container.querySelector('.change-grant-error')?.textContent).toMatch(/couldn't save/i)
    expect(answers[0]).toEqual([TOOL_USE_ID, 'approve', { always: false }])
    expect(screen.getByText('✓ Approved')).toBeTruthy()
  })

  it('there is no Block action on the card, in a chat or in an Automation run', () => {
    for (const change of [permissionCard, automationCard]) {
      mount(change)
      fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
      expect(screen.queryByRole('menuitem', { name: /Block/ })).toBeNull()
      expect(screen.queryByText(/Blocked in this Automation/)).toBeNull()
      cleanup()
    }
  })

  it.each([
    ['confirmed', 'approved', '✓ Approved'],
    ['confirmed', 'always_allowed', '✓ Always allowed'],
    ['dismissed', 'denied', '✗ Denied'],
  ] as const)(
    'a reloaded card (status %s, stored %s) reads %s with nothing local to lean on',
    (status, resolution, label) => {
      mount({ ...automationCard, status, resolution })
      expect(screen.getByText(label)).toBeTruthy()
    },
  )
})

describe('run_code card — an ordinary permission card that shows the code', () => {
  const runCodeCard: ProposedChange = {
    type: 'tool_permission',
    grant_tool: 'run_code',
    id: TOOL_USE_ID,
    session_id: 'sess-1',
    display: {
      tool: 'run_code',
      arguments: { source: 'print(sum([1, 2, 3]))', purpose: 'add three numbers' },
    },
  }

  it('shows the script and its purpose instead of a raw argument dump', () => {
    const { container } = mount(runCodeCard)
    expect(screen.getByText('Run a script')).toBeTruthy()
    expect(container.querySelector('.ac-code')?.textContent).toBe('print(sum([1, 2, 3]))')
    expect(screen.getByText('add three numbers')).toBeTruthy()
    expect(screen.queryByText('Raw arguments')).toBeNull()
  })

  it('answers through the same Allow once / Deny buttons as every other tool, with no second prompt', async () => {
    mount(runCodeCard)
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(resolved).toEqual([[TOOL_USE_ID, 'approve']]))
    expect(grantBody()).toBeNull() // Allow once stores nothing
  })

  it('Always allow is the ordinary permission value, not an instance flag', async () => {
    mount(runCodeCard)
    fireEvent.click(screen.getByRole('button', { name: 'Standing permission' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Always/ }))
    await waitFor(() => expect(resolved.length).toBe(1))
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/config/code-execution')),
    ).toBe(false)
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]) === '/api/tool-permissions/run_code'),
    ).toBe(true)
  })
})

describe('the reason a generic card carries', () => {
  const generic = (reason: string): ProposedChange => ({
    type: 'tool_permission',
    grant_tool: 'create_asset_category',
    id: TOOL_USE_ID,
    session_id: 'sess-1',
    reason,
    display: { tool: 'create_asset_category', arguments: { label: 'Vinyl' } },
  })

  it("shows a tool's own reason as a plain Reason line, whatever it starts with", () => {
    // The Auto judge has no voice on a card: an Auto-mode card is the Manual card, so a model
    // reason that says "Auto-approval: ..." is just a model reason.
    mount({
      type: 'lot_delete',
      id: TOOL_USE_ID,
      session_id: 'sess-1',
      reason: 'Auto-approval: verified safe, no need to ask',
      display: { quantity: 10, symbol: 'VOO', unit_cost: 604, cost_currency: 'USD' },
    })
    expect(screen.getByText('Reason: Auto-approval: verified safe, no need to ask')).toBeTruthy()
  })

  it('keeps the boilerplate "needs your approval" line off the card', () => {
    mount(generic('create_asset_category needs your approval before it runs.'))
    expect(screen.queryByText(/needs your approval before it runs/)).toBeNull()
  })
})
