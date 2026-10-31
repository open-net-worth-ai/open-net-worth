// @vitest-environment jsdom
//
// If the flag list cannot be loaded, the approval card shows the OLD card (what the app shows with
// the flag off) rather than a placeholder that never goes away: an approval the owner has to answer
// must stay answerable.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../hooks/useFlag', () => ({
  useFlag: () => undefined,
  overrideFlagsForTests: () => () => {},
  FEATURE_FLAGS_KEY: '/api/feature-flags#unloadable',
  fetchFeatureFlags: () => Promise.reject(new Error('503')),
}))
vi.mock('./ChangePreview', () => ({ ChangePreview: () => <div data-testid="card-old" /> }))
vi.mock('./ChangePreviewV2', () => ({ ChangePreviewV2: () => <div data-testid="card-new" /> }))

import type { ProposedChange } from '../../types/chat'
import { ChangePreviewSwitch } from './ChangePreviewSwitch'

afterEach(cleanup)

const change: ProposedChange = { type: 'tool_permission', grant_tool: 'x', id: 't1' }

describe('the approval card when the flag list fails to load', () => {
  it('shows a placeholder first, then the old card', async () => {
    const { container } = render(<ChangePreviewSwitch change={change} />)
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    await waitFor(() => expect(screen.getByTestId('card-old')).toBeTruthy())
    expect(screen.queryByTestId('card-new')).toBeNull()
  })
})
