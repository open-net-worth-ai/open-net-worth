// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import {
  clearQueryCache,
  getCachedQueryUpdatedAt,
  setCachedQueryData,
} from '../../../state/queryCache'
import { MEMORY_FACTS_KEY } from '../../../hooks/useMemoryFacts'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { MemoryImport } from './MemoryImport'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

const PROMPT = 'List everything you know about me, one [YYYY-MM-DD] - line each.'

const ITEMS = [
  {
    line: 'Name is Dana Example',
    destination: 'profile',
    field: 'name',
    value: 'Dana Example',
    reason: 'Your name has a profile field.',
  },
  {
    line: 'Always answer in plain English',
    destination: 'instructions',
    value: 'Always answer in plain English',
    reason: 'A standing rule.',
  },
  {
    line: 'Rents a flat with a roommate',
    destination: 'memory',
    section: 'you',
    topic: 'About you',
    content: 'Rents a flat with a roommate',
    reason: 'Personal context.',
  },
  {
    line: 'Planning a move to Lisbon',
    destination: 'memory',
    section: 'plans',
    topic: 'Move to Lisbon',
    content: 'Plans to move to Lisbon',
    reason: 'An upcoming move.',
  },
  {
    line: 'Tracks coffee spending',
    destination: 'skip',
    reason: 'The ledger already has it.',
  },
]

function serve(previewStatus = 200) {
  authFetch.mockImplementation((url: string) => {
    if (url === '/api/memory/import/prompt') return json({ prompt: PROMPT })
    if (url === '/api/memory/import/preview') {
      return previewStatus === 200
        ? json({ items: ITEMS })
        : json({ detail: 'Paste what the other assistant gave you.' }, previewStatus)
    }
    if (url === '/api/memory/import/apply') {
      return json({ applied: { profile: 1, instructions: 0, memory: 1 }, rejected: [{}] })
    }
    return json({})
  })
}

async function toReview() {
  fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
  fireEvent.change(screen.getByLabelText('Pasted memory'), {
    target: { value: '[2026-03-01] - Name is Dana Example' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Review' }))
  await screen.findByText('Rents a flat with a roommate')
}

describe('MemoryImport', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
    serve()
  })
  afterEach(cleanup)

  it('copies the export prompt', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const toast = vi.fn()
    render(<MemoryImport onClose={vi.fn()} toast={toast} />)

    await screen.findByText(PROMPT)
    fireEvent.click(screen.getByRole('button', { name: 'Copy prompt' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PROMPT))
    expect(toast).toHaveBeenCalledWith('Prompt copied')
  })

  it('counts the paste against the limit and blocks one that is too long', async () => {
    render(<MemoryImport onClose={vi.fn()} toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }))

    const box = screen.getByLabelText('Pasted memory')
    fireEvent.change(box, { target: { value: 'hello' } })
    expect(screen.getByText('5 / 20,000')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Review' }) as HTMLButtonElement).disabled).toBe(
      false,
    )

    fireEvent.change(box, { target: { value: 'x'.repeat(20_001) } })
    expect((screen.getByRole('button', { name: 'Review' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('groups the review by destination, with Skipped unchecked and disabled', async () => {
    render(<MemoryImport onClose={vi.fn()} toast={vi.fn()} />)
    await toReview()

    for (const heading of ['Profile', 'Instructions for Lucius', 'Skipped']) {
      expect(screen.getByRole('heading', { name: heading })).toBeTruthy()
    }
    expect(screen.getByRole('heading', { name: 'You · About you' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Plans · Move to Lisbon' })).toBeTruthy()
    expect(screen.getByText('The ledger already has it.')).toBeTruthy()
    const skipped = screen.getByLabelText('Tracks coffee spending') as HTMLInputElement
    expect(skipped.checked).toBe(false)
    expect(skipped.disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Import 4' })).toBeTruthy()
  })

  it('review imports only checked lines', async () => {
    const toast = vi.fn()
    const onClose = vi.fn()
    setCachedQueryData(MEMORY_FACTS_KEY, { facts: [], total: 0 })
    expect(getCachedQueryUpdatedAt(MEMORY_FACTS_KEY)).toBeDefined()
    render(<MemoryImport onClose={onClose} toast={toast} />)
    await toReview()

    fireEvent.click(screen.getByLabelText('Plans to move to Lisbon'))
    fireEvent.click(screen.getByRole('button', { name: 'Import 3' }))

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const call = authFetch.mock.calls.find((c) => c[0] === '/api/memory/import/apply')!
    expect(call[1].method).toBe('POST')
    const sent = JSON.parse(call[1].body).items as { line: string }[]
    expect(sent.map((i) => i.line)).toEqual([
      'Name is Dana Example',
      'Always answer in plain English',
      'Rents a flat with a roommate',
    ])
    expect(toast).toHaveBeenCalledWith('Imported 2 · 1 not imported')
    expect(getCachedQueryUpdatedAt(MEMORY_FACTS_KEY)).toBeUndefined()
  })

  it('shows why a paste was refused and lets the user fix it', async () => {
    serve(422)
    render(<MemoryImport onClose={vi.fn()} toast={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
    fireEvent.change(screen.getByLabelText('Pasted memory'), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review' }))

    expect((await screen.findByRole('alert')).textContent).toContain('Paste what the other')
    expect(screen.getByLabelText('Pasted memory')).toBeTruthy()
  })

  it('says so when the prompt cannot be loaded', async () => {
    authFetch.mockImplementation(() => json({}, 500))
    render(<MemoryImport onClose={vi.fn()} toast={vi.fn()} />)

    expect((await screen.findByRole('alert')).textContent).toContain('Could not load')
  })
})
