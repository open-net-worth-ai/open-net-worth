// @vitest-environment jsdom
//
// While the flag list has not loaded, every permissions_v2 switch point renders a placeholder
// and NEITHER component: the old one and the new one would both be a guess.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import type { ComponentProps } from 'react'

vi.mock('./useFlag', () => ({
  useFlag: () => undefined,
  overrideFlagsForTests: () => () => {},
}))
vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('../components/chat/ChangePreview', () => ({
  ChangePreview: () => <div data-testid="card-old" />,
}))
vi.mock('../components/chat/ChangePreviewV2', () => ({
  ChangePreviewV2: () => <div data-testid="card-new" />,
}))
vi.mock('../components/tabs/settings/sections/PermissionsSection', () => ({
  PermissionsSection: () => <div data-testid="perm-old" />,
}))
vi.mock('../components/tabs/settings/sections/PermissionsSectionV2', () => ({
  PermissionsSectionV2: () => <div data-testid="perm-new" />,
}))
vi.mock('../components/tabs/settings/sections/DeveloperSection', () => ({
  DeveloperSection: () => <div data-testid="dev-old" />,
}))
vi.mock('../components/tabs/settings/sections/DeveloperSectionV2', () => ({
  DeveloperSectionV2: () => <div data-testid="dev-new" />,
}))
vi.mock('../components/tabs/automations/AutomationDetail', () => ({
  AutomationDetail: () => <div data-testid="auto-old" />,
}))
vi.mock('../components/tabs/automations/AutomationDetailV2', () => ({
  AutomationDetailV2: () => <div data-testid="auto-new" />,
}))

import type { ProposedChange } from '../types/chat'
import { ChangePreviewSwitch } from '../components/chat/ChangePreviewSwitch'
import { PermissionsSectionSwitch } from '../components/tabs/settings/sections/PermissionsSectionSwitch'
import { DeveloperSectionSwitch } from '../components/tabs/settings/sections/DeveloperSectionSwitch'
import { AutomationDetailSwitch } from '../components/tabs/automations/AutomationDetailSwitch'
import { PermissionLadderSwitch } from '../components/tabs/connectors/PermissionLadderSwitch'
import { buildToolGroups } from '../components/tabs/connectors/model'

afterEach(cleanup)

const change: ProposedChange = { type: 'tool_permission', grant_tool: 'x', id: 't1' }

describe('permissions_v2 switch points before the flag has loaded', () => {
  it('the approval card renders a placeholder, not either card', () => {
    const { container, queryByTestId } = render(<ChangePreviewSwitch change={change} />)
    expect(queryByTestId('card-old')).toBeNull()
    expect(queryByTestId('card-new')).toBeNull()
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
  })

  it('Settings → Permissions renders neither section', () => {
    const { container } = render(<PermissionsSectionSwitch toast={vi.fn()} />)
    expect(container.innerHTML).toBe('')
  })

  it('Settings → Developer renders neither section', () => {
    const { container } = render(<DeveloperSectionSwitch toast={vi.fn()} />)
    expect(container.innerHTML).toBe('')
  })

  it('the Automation page renders a skeleton, not either detail', () => {
    const props = { automation: {}, runs: [] } as unknown as ComponentProps<
      typeof AutomationDetailSwitch
    >
    const { queryByTestId } = render(<AutomationDetailSwitch {...props} />)
    expect(queryByTestId('auto-old')).toBeNull()
    expect(queryByTestId('auto-new')).toBeNull()
  })

  it('the connector ladder renders a placeholder, not buttons', () => {
    const { container } = render(
      <PermissionLadderSwitch
        groups={buildToolGroups(['get_positions'])}
        perms={{}}
        onSetTool={vi.fn()}
        onSetGroup={vi.fn()}
      />,
    )
    expect(container.querySelector('.cn-permbtn')).toBeNull()
    expect(container.querySelector('.ui-skeleton')).not.toBeNull()
  })
})
