// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

import { downloadMemoryExport } from './memoryImport'

describe('downloadMemoryExport', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('fetches the zip with the session and saves it under the name the server gave', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] })
    authFetch.mockResolvedValue(
      new Response('zip-bytes', {
        headers: { 'Content-Disposition': 'attachment; filename="memory-2026-10-27.zip"' },
      }),
    )
    URL.createObjectURL = vi.fn(() => 'blob:memory')
    URL.revokeObjectURL = vi.fn()
    let saved = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved = `${this.download} ${this.href}`
    })

    await downloadMemoryExport()

    expect(authFetch).toHaveBeenCalledWith('/api/memory/export')
    expect(saved).toBe('memory-2026-10-27.zip blob:memory')
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(60_000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:memory')
  })

  it('reports a refused export', async () => {
    authFetch.mockResolvedValue(new Response('{}', { status: 500 }))

    await expect(downloadMemoryExport()).rejects.toThrow('Could not export (HTTP 500)')
  })
})
