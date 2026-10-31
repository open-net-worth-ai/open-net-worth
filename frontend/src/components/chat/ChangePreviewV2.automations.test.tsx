// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createElement } from 'react'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

let resolved: Array<[string, 'approve' | 'deny']>
let fetchMock: ReturnType<typeof vi.fn>

function mount(change: ProposedChange) {
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
  fetchMock = vi.fn(async () => new Response(null, { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('update_automations approval preview', () => {
  it('keeps rendering legacy resolved update cards after the envelope migration', () => {
    const change: ProposedChange = {
      type: 'tool_permission',
      grant_tool: 'update_automations',
      id: 'toolu-auto-legacy',
      reason: 'approval',
      display: {
        arguments: {
          updates: [
            {
              id: 'auto_123',
              name: 'Month-end close',
              changes: { instructions: { old: 'old step', new: 'new step' } },
            },
          ],
        },
      },
    }

    mount(change)

    expect(screen.getByText('Month-end close')).toBeTruthy()
    expect(screen.getByText('new step')).toBeTruthy()
  })

  it('renders diff with name, changed fields, added/removed lines, and collapsed context', () => {
    const longOldPrompt = [
      'Line 1: preamble',
      'Line 2: preamble',
      'Line 3: preamble',
      'Line 4: preamble',
      'Line 5: preamble',
      'Old trigger logic',
      'Line 7: postamble',
      'Line 8: postamble',
      'Line 9: postamble',
      'Line 10: postamble',
    ].join('\n')

    const longNewPrompt = [
      'Line 1: preamble',
      'Line 2: preamble',
      'Line 3: preamble',
      'Line 4: preamble',
      'Line 5: preamble',
      'New trigger logic',
      'Line 7: postamble',
      'Line 8: postamble',
      'Line 9: postamble',
      'Line 10: postamble',
    ].join('\n')

    const change: ProposedChange = {
      type: 'tool_permission',
      grant_tool: 'update_automations',
      id: 'toolu-auto-1',
      session_id: 'sess-1',
      reason: 'update_automations needs your approval before it runs.',
      display: {
        tool: 'update_automations',
        preview: {
          kind: 'field_diff',
          data: {
            items: [
              {
                id: 'auto_123',
                name: 'Payroll Rule',
                baseline_updated_at: '2026-08-01T00:00:00Z',
                changes: {
                  prompt: {
                    old: longOldPrompt,
                    new: longNewPrompt,
                  },
                  paused: {
                    old: false,
                    new: true,
                  },
                },
              },
            ],
          },
          raw_arguments: {
            updates: [{ id: 'auto_123', prompt: longNewPrompt, paused: true }],
          },
        },
      },
    }

    const { container } = mount(change)

    expect(screen.getByText('Payroll Rule')).toBeTruthy()
    expect(screen.getByText('Prompt')).toBeTruthy()
    expect(screen.getByText('Paused')).toBeTruthy()

    expect(container.querySelector('.ac-diff-edited')?.textContent).toContain('trigger logic')
    expect(container.querySelector('.ac-diff-word-removed')?.textContent).toBe('Old')
    expect(container.querySelector('.ac-diff-word-added')?.textContent).toBe('New')

    // Check collapsible button for unchanged context lines
    const expandButtons = screen.getAllByRole('button', { name: /Show \d+ unchanged line/i })
    expect(expandButtons.length).toBeGreaterThan(0)
    fireEvent.click(expandButtons[0]!)
    expect(container.textContent).toContain('Line 1: preamble')

    // Check raw arguments disclosure
    const raw = screen.getByText('Raw arguments').closest('details') as HTMLDetailsElement
    expect(raw.open).toBe(false)
    expect(raw.textContent).toContain('auto_123')
  })

  it('renders no-changes message and disables approval when updates array has no field changes', () => {
    const change: ProposedChange = {
      type: 'tool_permission',
      grant_tool: 'update_automations',
      id: 'toolu-auto-2',
      session_id: 'sess-1',
      reason: 'update_automations needs your approval before it runs.',
      display: {
        tool: 'update_automations',
        preview: {
          kind: 'field_diff',
          data: {
            items: [
              {
                id: 'auto_123',
                name: 'Payroll Rule',
                baseline_updated_at: '2026-08-01T00:00:00Z',
                changes: {},
              },
            ],
          },
          raw_arguments: { updates: [{ id: 'auto_123' }] },
        },
      },
    }

    mount(change)

    expect(screen.getByText('The proposed values match the current automation')).toBeTruthy()

    const allowOnceBtn = screen.getByRole('button', { name: 'Allow once' })
    expect((allowOnceBtn as HTMLButtonElement).disabled).toBe(true)

    const denyBtn = screen.getByRole('button', { name: 'Deny' })
    expect((denyBtn as HTMLButtonElement).disabled).toBe(false)
  })
})
