// @vitest-environment jsdom
import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VaultItem } from '../../api/vault'
import { getScreenSnapshot } from '../../lucius/screenContract'
import type { OwnedAsset, AssetDoc } from '../../types/assets'
import { AttachmentViewer } from '../tabs/assets/AttachmentViewer'
import { DocumentPreview } from './DocumentPreview'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

const item = (overrides: Partial<VaultItem>): VaultItem => ({
  id: 'i1',
  kind: 'file',
  source_id: 's1',
  title: 'acme-notes.txt',
  filename: 'acme-notes.txt',
  media_type: 'text/plain',
  size_bytes: 20,
  added_at: null,
  year: null,
  private: false,
  file_url: '/api/uploads/s1/file',
  ...overrides,
})

// The screen the capture reads, as the app's shell labels it.
function onScreen(node: React.ReactNode) {
  return render(<div data-ctx-screen="Documents">{node}</div>)
}

const captured = () => getScreenSnapshot().raw

describe('a Private file on screen', () => {
  beforeEach(() => {
    authFetch.mockImplementation(async () => new Response('SECRET-MARKER,1\nother,2\n'))
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('reaches the screen capture only when the file is not Private', async () => {
    const shown = onScreen(<DocumentPreview item={item({})} />)
    await waitFor(() => expect(shown.container.textContent).toContain('SECRET-MARKER'))
    expect(captured()).toContain('SECRET-MARKER')
  })

  it('keeps a Private text file out of the capture, though the owner can read it', async () => {
    const shown = onScreen(<DocumentPreview item={item({ private: true })} />)
    await waitFor(() => expect(shown.container.textContent).toContain('SECRET-MARKER'))
    expect(captured()).not.toContain('SECRET-MARKER')
  })

  it("keeps an asset file with Lucius can't read out of the capture", async () => {
    authFetch.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            rows: [['SECRET-MARKER', '1']],
            columns: 2,
            offset: 0,
            total_rows: 1,
            more_columns: false,
            sheets: [],
            sheet: null,
          }),
        ),
    )
    const doc = {
      ref_id: 'r1',
      name: 'acme.csv',
      filename: 'acme.csv',
      media_type: 'text/csv',
      size_bytes: 20,
      added_on: '2026-01-01',
      lucius_readable: false,
    } as AssetDoc
    render(
      <div data-ctx-screen="Assets">
        <AttachmentViewer
          a={{ id: 'a1', name: 'ACME' } as OwnedAsset}
          doc={doc}
          onClose={() => {}}
        />
      </div>,
    )
    await waitFor(() => expect(document.body.textContent).toContain('SECRET-MARKER'))
    expect(captured()).not.toContain('SECRET-MARKER')
  })
})
