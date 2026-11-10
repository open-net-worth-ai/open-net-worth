// @vitest-environment jsdom
//
// permissions_v2: run_code is an ordinary permission card (`tool_permission` with
// `grant_tool: 'run_code'`) whose body shows the script. What the user decides on must be on
// the card: the FULL source, never folded, and the sandbox note. Rendered through
// ChangePreviewV2, so the header, lead and body are exactly what ships.
import { afterEach, describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { cleanup, render } from '@testing-library/react'
import { ChangePreviewV2 } from '../../ChangePreviewV2'
import type { ProposedChange } from '../../../../types/chat'

afterEach(cleanup)

const SOURCE = 'rows = payload["rows"]\nprint(sum(r["amount"] for r in rows) / len(rows))'

function runCode(args: Record<string, unknown>): ProposedChange {
  return {
    type: 'tool_permission',
    id: 'toolu_code_run',
    grant_tool: 'run_code',
    session_id: 'sess_demo',
    display: { tool: 'run_code', arguments: args },
  }
}

function mount(change: ProposedChange) {
  return render(createElement(ChangePreviewV2, { change, onRespond: () => {} }))
}

describe('run_code card', () => {
  it('leads with the purpose and shows the FULL source, unfolded', () => {
    const { container } = mount(
      runCode({ purpose: 'Compute the monthly savings rate', source: SOURCE, language: 'python' }),
    )
    expect(container.querySelector('.ac-lead')?.textContent).toBe(
      'Compute the monthly savings rate',
    )
    expect(container.querySelector('.ac-code')?.textContent).toBe(SOURCE)
  })

  it('carries the sandbox note as one short line', () => {
    const { container } = mount(runCode({ purpose: 'Average', source: SOURCE }))
    const note = container.querySelector('.ac-note')
    expect(note?.textContent).toMatch(/sandboxed/i)
    expect(note?.textContent).toMatch(/no internet, ledger or secrets/i)
  })

  it('falls back to a plain lead when the purpose is missing', () => {
    const { container } = mount(runCode({ purpose: '', source: SOURCE }))
    expect(container.querySelector('.ac-lead')?.textContent).toBe('Run a script')
  })
})
