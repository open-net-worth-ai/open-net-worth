// @vitest-environment jsdom
//
// The generic tool-permission card, specialized for the two pluggable-budget-engines write
// tools (RFC `.claude/rfcs/2026-08-22-pluggable-budget-engines.md`, Decision F/Phase 2-3:
// apply_budget_operation — apply_budget_verb until 2026-09-26 — and set_home_budget_engine).
// Neither tool carries a bespoke proposed_change
// shape of its own — the backend confirm gate (`stage_generic`) stages BOTH as an ordinary
// `type: 'tool_permission'` card, same as any other gated tool. Before this test/feature,
// that card rendered `display.arguments` as either a raw JSON dump (GenericChangePreview) or
// nothing at all for a plain-object param (PermissionArgumentsPreview only shows scalar
// identifiers + {old,new} diffs) — these tests pin the friendlier, declaration-driven-by-id
// rendering that replaced it, and that an UNRELATED tool-permission card (e.g. update_agent)
// is untouched.
import { describe, it, expect, afterEach } from 'vitest'
import { createElement } from 'react'
import { render, screen, cleanup } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

afterEach(cleanup)

function mount(change: ProposedChange) {
  return render(
    createElement(ChangePreviewV2, {
      change,
      onRespond: () => {},
    }),
  )
}

describe('apply_budget_operation permission card', () => {
  it('renders a humanized header from engine_id/operation, not the raw tool name', () => {
    mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_operation',
      id: 'toolu-1',
      reason: 'apply_budget_operation needs your approval before it runs.',
      display: {
        tool: 'apply_budget_operation',
        arguments: {
          engine_id: 'proportional',
          operation: 'set_income_categories',
          params: { category_ids: ['cat_salary', 'cat_bonus'] },
        },
      },
    })
    expect(screen.getByText('Set income categories (50/30/20)')).toBeTruthy()
  })

  it('renders the operation params as labelled rows, not a raw JSON blob', () => {
    const { container } = mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_operation',
      id: 'toolu-1',
      display: {
        tool: 'apply_budget_operation',
        arguments: {
          engine_id: 'proportional',
          operation: 'set_income_categories',
          params: { category_ids: ['cat_salary', 'cat_bonus'] },
        },
      },
    })
    expect(container.textContent).not.toContain('{"category_ids"')
    expect(container.textContent).toContain('Category ids')
    expect(container.textContent).toContain('cat_salary, cat_bonus')
    // Named as the Plan tab names things: the method, never an engine id or "verb".
    expect(container.textContent).toContain('50/30/20')
    expect(container.textContent?.toLowerCase()).not.toContain('verb')
    expect(container.textContent).not.toContain('Proportional')
  })

  it('a card from before the rename (apply_budget_verb + verb_id) reads the same', () => {
    mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_verb',
      id: 'toolu-old',
      display: {
        tool: 'apply_budget_verb',
        arguments: { engine_id: 'proportional', verb_id: 'set_caps', params: { month: '2026-09' } },
      },
    })
    expect(screen.getByText('Set caps (50/30/20)')).toBeTruthy()
  })

  it('renders a zero_based operation (assign) the same way', () => {
    const { container } = mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_operation',
      id: 'toolu-2',
      display: {
        tool: 'apply_budget_operation',
        arguments: {
          engine_id: 'zero_based',
          operation: 'assign',
          params: { month: '2026-06', envelope_id: 'env_food', amount: 500 },
        },
      },
    })
    expect(screen.getByText('Assign (Zero-Based)')).toBeTruthy()
    expect(container.textContent).toContain('2026-06')
    expect(container.textContent).toContain('env_food')
    expect(container.textContent).toContain('500')
  })

  it('still offers the standard Allow once / Deny grant buttons', () => {
    mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_operation',
      id: 'toolu-3',
      display: {
        tool: 'apply_budget_operation',
        arguments: { engine_id: 'proportional', operation: 'set_config', params: {} },
      },
    })
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeTruthy()
  })

  it('shows a fallback message when params is empty', () => {
    mount({
      type: 'tool_permission',
      grant_tool: 'apply_budget_operation',
      id: 'toolu-4',
      display: {
        tool: 'apply_budget_operation',
        arguments: { engine_id: 'proportional', operation: 'set_config', params: {} },
      },
    })
    expect(screen.getByText('No parameters.')).toBeTruthy()
  })
})

describe('set_home_budget_engine permission card', () => {
  it('renders a switch-lens header naming the target engine', () => {
    mount({
      type: 'tool_permission',
      grant_tool: 'set_home_budget_engine',
      id: 'toolu-5',
      display: {
        tool: 'set_home_budget_engine',
        arguments: { engine_id: 'proportional' },
      },
    })
    expect(screen.getByText('Switch home budget lens to 50/30/20')).toBeTruthy()
  })

  it('says in one line that other methods are left untouched (Decision A)', () => {
    const { container } = mount({
      type: 'tool_permission',
      grant_tool: 'set_home_budget_engine',
      id: 'toolu-6',
      display: {
        tool: 'set_home_budget_engine',
        arguments: { engine_id: 'proportional' },
      },
    })
    expect(container.textContent).toContain('Other methods')
    expect(container.textContent).toContain('untouched')
  })
})

describe('an unrelated tool-permission card is unaffected', () => {
  it('update_agent still gets the generic header + raw-argument fallback, not a budget one', () => {
    const { container } = mount({
      type: 'tool_permission',
      grant_tool: 'update_agent',
      id: 'toolu-7',
      reason: 'update_agent needs your approval before it runs.',
      display: {
        tool: 'update_agent',
        arguments: { agent_id: 'my-agent', name: 'My Agent' },
      },
    })
    // Unchanged generic header — my budget-specific label is never reached for this tool.
    // A generic gate card is titled by its own tool, not a bare "Tool permission".
    expect(screen.getByText('Update agent')).toBeTruthy()
    expect(screen.queryByText(/Switch home budget lens/)).toBeNull()
    // GenericChangePreview (no {old,new} shape here, so PermissionArgumentsPreview never
    // kicks in) — one labelled row per argument (2026-10-09; it used to be one JSON blob).
    expect(screen.getByText('Agent id')).toBeTruthy()
    expect(container.textContent).toContain('my-agent')
  })
})
