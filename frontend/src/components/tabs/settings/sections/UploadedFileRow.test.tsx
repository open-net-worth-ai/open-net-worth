// @vitest-environment jsdom
//
// An uploaded image must be recognisable before it is deleted: a row of
// `telegram_photo_<n>.jpg` names gave no way to tell which photo held what.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { UploadedFileRow } from './UploadedFileRow'
import type { UploadRow } from './uploadFiles'

const base: UploadRow = {
  id: 'att_0123456789ab',
  filename: 'telegram_photo_1.jpg',
  size_bytes: 81_000,
  created_at: '2026-11-13T10:00:00Z',
}

afterEach(cleanup)

describe('UploadedFileRow', () => {
  it('shows an image thumbnail that opens a preview with Delete', () => {
    const onDelete = vi.fn()
    render(
      <UploadedFileRow
        upload={{ ...base, kind: 'image', media_type: 'image/jpeg' }}
        onDelete={onDelete}
      />,
    )

    const thumb = screen.getByRole('button', { name: /preview telegram_photo_1\.jpg/i })
    expect(thumb.querySelector('img')?.getAttribute('src')).toBe(
      '/api/uploads/att_0123456789ab/image',
    )

    fireEvent.click(thumb)
    const preview = screen.getByAltText('telegram_photo_1.jpg')
    expect(preview.getAttribute('src')).toBe('/api/uploads/att_0123456789ab/image')

    const deletes = screen.getAllByRole('button', { name: 'Delete' })
    fireEvent.click(deletes[deletes.length - 1])
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 'att_0123456789ab' }))
    expect(screen.queryByAltText('telegram_photo_1.jpg')).toBeNull()
  })

  it('keeps the file icon for a non-image or an SVG', () => {
    render(
      <UploadedFileRow
        upload={{ ...base, filename: 'a.pdf', kind: 'pdf', media_type: 'application/pdf' }}
        onDelete={vi.fn()}
      />,
    )
    render(
      <UploadedFileRow
        upload={{
          ...base,
          id: 'att_2',
          filename: 'a.svg',
          kind: 'image',
          media_type: 'image/svg+xml',
        }}
        onDelete={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /preview/i })).toBeNull()
  })

  it('falls back to the file icon when the image fails to load', () => {
    render(
      <UploadedFileRow
        upload={{ ...base, kind: 'image', media_type: 'image/jpeg' }}
        onDelete={vi.fn()}
      />,
    )
    const img = screen.getByRole('button', { name: /preview/i }).querySelector('img')!
    fireEvent.error(img)
    expect(screen.queryByRole('button', { name: /preview/i })).toBeNull()
  })
})
