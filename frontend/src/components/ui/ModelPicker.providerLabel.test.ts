// A chip for a model whose provider returned no rows (an unreachable custom endpoint) must show
// the owner's name for it, not the raw `custom_<id>` key.

import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../api/client'
import { fetchModels } from '../../api/models'
import { resolveModelSelection } from './ModelPicker'

describe('resolveModelSelection — provider with no model rows', () => {
  it('shows the key until the labels are known, then the owner’s name', async () => {
    expect(resolveModelSelection('custom_ab12cd34:my-model', []).providerLabel).toBe(
      'custom_ab12cd34',
    )
    ;(authFetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(
        JSON.stringify({
          models: [],
          source: 'fallback',
          provider_labels: { custom_ab12cd34: 'Home server' },
        }),
        { status: 200 },
      ),
    )
    await fetchModels(true)

    expect(resolveModelSelection('custom_ab12cd34:my-model', []).providerLabel).toBe('Home server')
  })
})
