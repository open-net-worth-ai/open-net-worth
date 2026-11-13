import { afterEach, describe, expect, it, vi } from 'vitest'

import { authFetch } from '../../../api/client'
import { previewDeleteEffects } from './transactionApi'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

const fetchMock = vi.mocked(authFetch)

afterEach(() => {
  fetchMock.mockReset()
})

describe('previewDeleteEffects', () => {
  it('says what the delete would change on each linked record', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          links: [{ label: '10 shares bought 2026-01-05', effect: 'The lot keeps its shares.' }],
        }),
      ),
    )

    await expect(previewDeleteEffects(['txn_1'])).resolves.toEqual([
      '10 shares bought 2026-01-05: The lot keeps its shares.',
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ ids: ['txn_1'] })
  })

  it('leaves the dialog plain when nothing points at the row', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ links: [] })))

    await expect(previewDeleteEffects(['txn_1'])).resolves.toEqual([])
  })

  it('never stops a delete: a failed read just shows the plain dialog', async () => {
    fetchMock.mockResolvedValue(new Response('nope', { status: 500 }))
    await expect(previewDeleteEffects(['txn_1'])).resolves.toEqual([])

    fetchMock.mockRejectedValue(new Error('offline'))
    await expect(previewDeleteEffects(['txn_1'])).resolves.toEqual([])
  })
})
