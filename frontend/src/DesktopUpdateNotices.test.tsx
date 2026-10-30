// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type {
  DesktopClientBridge,
  DesktopUpdateState,
  DesktopWhatsNew,
} from '../../desktop/src/contracts'
import { DesktopUpdateNotices } from './DesktopUpdateNotices'

function installBridge(
  initial: DesktopUpdateState,
  whatsNew: DesktopWhatsNew | null = null,
): { push: (state: DesktopUpdateState) => void; bridge: DesktopClientBridge } {
  let listener: ((state: DesktopUpdateState) => void) | null = null
  const bridge = {
    version: 1,
    getConnection: vi.fn(),
    changeConnection: vi.fn(),
    openBrowser: vi.fn(),
    onCommand: () => () => {},
    getUpdateState: vi.fn().mockResolvedValue(initial),
    onUpdateState: (next: (state: DesktopUpdateState) => void) => {
      listener = next
      return () => {
        listener = null
      }
    },
    restartToUpdate: vi.fn().mockResolvedValue(undefined),
    offerUpdate: vi.fn().mockResolvedValue(undefined),
    takeWhatsNew: vi.fn().mockResolvedValue(whatsNew),
  } as unknown as DesktopClientBridge
  window.onwDesktopClient = bridge
  return { bridge, push: (state) => act(() => listener?.(state)) }
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

afterEach(() => {
  cleanup()
  delete window.onwDesktopClient
})

describe('DesktopUpdateNotices', () => {
  it('shows nothing in a browser or an older desktop shell', async () => {
    render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says when a downloaded update is ready, and Restart is the only thing that restarts', async () => {
    const { bridge, push } = installBridge({ ready: false, version: null })
    render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.queryByRole('status')).toBeNull()
    push({ ready: true, version: '0.2.0' })
    expect(screen.getByRole('status').textContent).toMatch(/Open Net Worth 0\.2\.0 is ready/)
    expect(bridge.restartToUpdate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Restart to update' }))
    expect(bridge.restartToUpdate).toHaveBeenCalledTimes(1)
  })

  it('an update that has not downloaded says it is available, and Update opens the update choice', async () => {
    const { bridge, push } = installBridge({ ready: false, version: '0.2.0', available: true })
    render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.getByRole('status').textContent).toMatch(/Open Net Worth 0\.2\.0 is available/)
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    expect(bridge.offerUpdate).toHaveBeenCalledTimes(1)
    expect(bridge.restartToUpdate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Later' }))
    expect(screen.queryByRole('status')).toBeNull()
    push({ ready: true, version: '0.2.0', available: false })
    expect(screen.getByRole('status').textContent).toMatch(/0\.2\.0 is ready/)
  })

  it('shows nothing when no update is waiting', async () => {
    installBridge({ ready: false, version: null, available: false })
    render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('Later hides the pill for that version only', async () => {
    const { push } = installBridge({ ready: true, version: '0.2.0' })
    render(<DesktopUpdateNotices />)
    await settle()
    fireEvent.click(screen.getByRole('button', { name: 'Later' }))
    expect(screen.queryByRole('status')).toBeNull()
    push({ ready: true, version: '0.2.1' })
    expect(screen.getByRole('status').textContent).toMatch(/0\.2\.1/)
  })

  it('after an update, shows what changed once, as text', async () => {
    const { bridge } = installBridge(
      { ready: false, version: null },
      {
        version: '0.2.0',
        highlights: ['Split a transaction', '<img src=x onerror=alert(1)>'],
        releaseUrl: 'https://github.com/o/r/releases/tag/v0.2.0',
      },
    )
    const { unmount } = render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('What’s new in 0.2.0')).toBeTruthy()
    expect(screen.getByText('Split a transaction')).toBeTruthy()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
    expect(document.querySelector('img')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Let’s go' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    unmount()
    render(<DesktopUpdateNotices />)
    await settle()
    expect(bridge.takeWhatsNew).toHaveBeenCalledTimes(1)
  })

  it('without highlights (offline), says so and makes opening the release notes the main action', async () => {
    installBridge(
      { ready: false, version: null },
      {
        version: '0.2.0',
        highlights: [],
        releaseUrl: 'https://github.com/o/r/releases/tag/v0.2.0',
      },
    )
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    render(<DesktopUpdateNotices />)
    await settle()
    expect(screen.getByText(/couldn’t load right now/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /See what’s new/ }))
    expect(open).toHaveBeenCalledWith(
      'https://github.com/o/r/releases/tag/v0.2.0',
      '_blank',
      'noopener,noreferrer',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.queryByText(/couldn’t load right now/)).toBeNull()
  })
})
