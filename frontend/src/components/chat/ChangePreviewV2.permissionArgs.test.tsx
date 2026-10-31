// @vitest-environment jsdom
// A gated tool's card shows each argument as its own row (found live 2026-10-09: a
// record_release card put vest_event_id, release_date and the note on ONE line of JSON).
import { describe, it, expect, afterEach } from 'vitest'
import { createElement } from 'react'
import { render, screen, cleanup } from '@testing-library/react'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

afterEach(() => cleanup())

function mount(change: ProposedChange) {
  return render(
    createElement(ChangePreviewV2, {
      change,
      onResolvePendingToolUse: () => {},
      onRespond: () => {},
    }),
  )
}

describe('tool-permission card arguments', () => {
  it('renders one labelled row per argument instead of a JSON blob', () => {
    const change: ProposedChange = {
      type: 'tool_permission',
      grant_tool: 'record_release',
      id: 'toolu-release',
      reason: 'approval',
      display: {
        tool: 'record_release',
        arguments: {
          vest_event_id: 'vest_0ffde305',
          release_date: '2026-10-09',
          note: 'Released at JP Morgan',
        },
      },
    }

    const { container } = mount(change)

    expect(screen.getByText('Vest event id')).toBeTruthy()
    expect(screen.getByText('vest_0ffde305')).toBeTruthy()
    expect(screen.getByText('Release date')).toBeTruthy()
    expect(screen.getByText('2026-10-09')).toBeTruthy()
    expect(screen.getByText('Released at JP Morgan')).toBeTruthy()
    expect(container.textContent).not.toContain('{"vest_event_id"')
  })

  it('keeps a nested argument compact rather than dropping it', () => {
    const change: ProposedChange = {
      type: 'tool_permission',
      grant_tool: 'some_tool',
      id: 'toolu-nested',
      reason: 'approval',
      display: { tool: 'some_tool', arguments: { filter: { account: 'IBKR' } } },
    }

    mount(change)

    expect(screen.getByText('Filter')).toBeTruthy()
    expect(screen.getByText('account: IBKR')).toBeTruthy()
  })
})
