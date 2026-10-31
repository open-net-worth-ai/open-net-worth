import { describe, it, expect } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChangePreviewV2 } from './ChangePreviewV2'
import type { ProposedChange } from '../../types/chat'

// Confirm-safety: a proposed-change card must NEVER render only a header + Confirm/Dismiss
// buttons with no body — that would ask the user to confirm a change they can't see
// (the agent-change blind-confirm bug). These render the component to static HTML (no DOM
// dependency) and assert the body between the header and the action buttons is non-empty.

const noop = () => {}

function render(change: ProposedChange): string {
  return renderToStaticMarkup(createElement(ChangePreviewV2, { change, onRespond: noop }))
}

// The body = everything after the header div and before the actions block. We strip the
// header + actions and assert what remains carries real content, not just whitespace/markup.
function bodyText(html: string): string {
  // Drop the header block (it always has a title) so we only measure the BODY.
  const afterHeader = html.split('</div>').slice(1).join('</div>')
  // Drop the trailing action buttons / resolved state.
  const beforeActions = afterHeader.split('change-preview-actions')[0] ?? ''
  // Strip tags + collapse whitespace → the human-visible body text.
  return beforeActions
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

describe('ChangePreviewV2 confirm-safety', () => {
  it('renders a non-empty body for an agent_create change (name, prompt, tools)', () => {
    const change: ProposedChange = {
      type: 'agent_create',
      agent: {},
      display: {
        name: 'Fraud & Scam Monitor',
        description: 'Watches for suspicious transactions',
        prompt_preview: 'Review recent transactions for fraud signals',
        tools_used: 2,
        allowed_tools: ['get_transactions', 'notify'],
        auto_apply: null,
      },
    }
    const html = render(change)
    expect(html).toContain('Create agent')
    expect(html).toContain('Fraud &amp; Scam Monitor')
    expect(html).toContain('Watches for suspicious transactions')
    expect(html).toContain('get_transactions')
    // It must say it only proposes when there's no auto-apply grant.
    expect(html).toContain('Propose only')
    expect(bodyText(html).length).toBeGreaterThan(20)
  })

  it('surfaces an auto-apply GRANT on an agent_create so the authorization is visible', () => {
    const change: ProposedChange = {
      type: 'agent_create',
      display: {
        name: 'Bookkeeper',
        allowed_tools: ['get_transactions'],
        auto_apply: { capabilities: ['categorize'], max_writes_per_run: 10 },
      },
    }
    const html = render(change)
    expect(html).toContain('May apply: categorize')
    expect(html).toContain('10')
  })

  it('renders a non-empty body for an agent_update change (changed fields + values)', () => {
    const change: ProposedChange = {
      type: 'agent_update',
      agent_id: 'agt_1',
      changes: { prompt: 'A brand new prompt for the agent' },
      display: {
        name: 'Monitor',
        changed_fields: ['prompt'],
        prompt_preview: 'A brand new prompt for the agent',
      },
    }
    const html = render(change)
    expect(html).toContain('Update agent')
    expect(html).toContain('A brand new prompt for the agent')
    expect(bodyText(html).length).toBeGreaterThan(20)
  })

  // D7 (RFC 2026-07-20 §14.2) — an EDIT card renders the fragment the model supplied,
  // never the 280-char whitespace-collapsed preview of a whole rewritten prompt.
  it('renders the old→new fragment for an agent_update prompt EDIT', () => {
    const change: ProposedChange = {
      type: 'agent_update',
      agent_id: 'agt_1',
      display: {
        name: 'Fraud Watch',
        changed_fields: ['prompt'],
        prompt_edit: { old: 'Be terse.', new: 'Be thorough.', old_chars: 9, new_chars: 12 },
      },
    }
    const html = render(change)
    expect(html).toContain('Prompt edit')
    expect(html).toContain('Be terse.')
    expect(html).toContain('Be thorough.')
    // Both sides are visibly marked as removed/added, not a bare blob.
    expect(html).toContain('ac-diff-line ac-diff-removed')
    expect(html).toContain('ac-diff-line ac-diff-added')
  })

  it('still renders the whole-prompt preview for an agent_update full REPLACE', () => {
    const change: ProposedChange = {
      type: 'agent_update',
      agent_id: 'agt_1',
      display: {
        name: 'Fraud Watch',
        changed_fields: ['prompt'],
        prompt_preview: 'A completely new set of instructions',
      },
    }
    const html = render(change)
    expect(html).toContain('A completely new set of instructions')
    expect(bodyText(html).length).toBeGreaterThan(20)
  })

  it('renders a skill_update body EDIT fragment', () => {
    const change: ProposedChange = {
      type: 'skill_update',
      skill_id: 'skl_1',
      display: {
        name: 'monthly-close',
        changed_fields: ['body'],
        body_edit: { old: 'Step 3: reconcile', new: 'Step 3: reconcile and tag' },
      },
    }
    const html = render(change)
    expect(html).toContain('Body edit')
    expect(html).toContain('Step 3: reconcile and tag')
  })
})
