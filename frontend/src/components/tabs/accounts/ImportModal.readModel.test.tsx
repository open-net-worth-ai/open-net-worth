// @vitest-environment jsdom
//
// The import popup can pick which model reads THIS statement. Untouched, nothing is sent and
// the server reads with the saved Statement import pin; a pick rides the preview request as
// its last argument (`model`), for this import only.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn().mockResolvedValue({ ok: false }) }))
vi.mock('../../../hooks/useModels', () => ({
  useModels: () => ({
    models: [{ id: 'picked', provider_key: 'anthropic' }],
    defaultModel: 'base',
    taskModels: { statement_import: 'anthropic:pinned' },
    loaded: true,
  }),
}))
// The real picker is a searchable popover; the modal's contract is only value + onChange.
vi.mock('../../ui/ModelPicker', () => ({
  ModelPicker: (p: { value: string; defaultModelId?: string; onChange: (v: string) => void }) => (
    <button
      type="button"
      data-testid="read-model"
      data-default={p.defaultModelId}
      onClick={() => p.onChange('anthropic:picked')}
    >
      {p.value || 'saved'}
    </button>
  ),
}))

afterEach(() => cleanup())

const acct = {
  id: 'a1',
  name: 'Mega Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
} as unknown as Account

async function readWith(pick: boolean) {
  const onPreview = vi.fn().mockReturnValue(new Promise(() => {}))
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={vi.fn()}
      onPreview={onPreview as never}
      onDecisionCommit={vi.fn()}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  const picker = await screen.findByTestId('read-model')
  // Names the saved pin as the default, not the instance default.
  expect(picker.getAttribute('data-default')).toBe('anthropic:pinned')
  if (pick) fireEvent.click(picker)
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
  return onPreview.mock.calls[0]
}

describe('ImportModal — the model that reads this statement', () => {
  it('sends no model when untouched (the saved pin reads it)', async () => {
    const args = await readWith(false)
    expect(args[10]).toBeUndefined()
  })

  it('sends the picked model with the read', async () => {
    const args = await readWith(true)
    expect(args[10]).toBe('anthropic:picked')
  })
})
