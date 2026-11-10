// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ShortcutsDialog } from './ShortcutsDialog'
import { goCommandPath } from '../platform/desktop'
import { isShortcutsKey } from '../utils/shortcuts'

afterEach(() => {
  cleanup()
  delete window.onwDesktop
  vi.unstubAllGlobals()
})

describe('ShortcutsDialog', () => {
  it('lists the shortcuts that work in a browser, with platform glyphs', () => {
    vi.stubGlobal('navigator', { platform: 'MacIntel' })
    render(<ShortcutsDialog open onClose={() => {}} />)
    const row = screen.getByText('Command palette').closest('li')!
    expect(Array.from(row.querySelectorAll('kbd')).map((k) => k.textContent)).toEqual(['⌘', 'K'])
    const shortcuts = screen.getByText('Keyboard shortcuts', { selector: 'span' }).closest('li')!
    expect(Array.from(shortcuts.querySelectorAll('kbd')).map((k) => k.textContent)).toEqual([
      '⌘',
      '⇧',
      '/',
    ])
    expect(screen.queryByText('Desktop app')).toBeNull()
  })

  it('adds the desktop group, with Alt arrows for history off macOS', () => {
    vi.stubGlobal('navigator', { platform: 'Win32' })
    window.onwDesktop = { version: 1 } as unknown as typeof window.onwDesktop
    render(<ShortcutsDialog open onClose={() => {}} />)
    expect(screen.getByText('Desktop app')).toBeTruthy()
    const back = screen.getByText('Back').closest('li')!
    expect(Array.from(back.querySelectorAll('kbd')).map((k) => k.textContent)).toEqual(['Alt', '←'])
    const keys = (label: string) =>
      Array.from(screen.getByText(label).closest('li')!.querySelectorAll('kbd')).map(
        (k) => k.textContent,
      )
    expect(keys('New Lucius conversation')).toEqual(['Ctrl', 'N'])
    expect(keys('New transaction')).toEqual(['Ctrl', 'Shift', 'N'])
  })

  it('closes on Escape', () => {
    const onClose = vi.fn()
    render(<ShortcutsDialog open onClose={onClose} />)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })
})

describe('desktop go command and shortcuts key', () => {
  it('maps main screens to routes and refuses anything else', () => {
    expect(goCommandPath('accounts')).toBe('/accounts')
    expect(goCommandPath('dashboard')).toBe('/dashboard')
    for (const tab of ['settings', 'inbox', 'help', '../lucius', undefined]) {
      expect(goCommandPath(tab)).toBeNull()
    }
  })

  it('opens the shortcuts panel on mod+shift+/ and leaves mod+/ for the Lucius panel', () => {
    const key = (init: KeyboardEventInit) => isShortcutsKey(new KeyboardEvent('keydown', init))
    expect(key({ key: '?', metaKey: true, shiftKey: true })).toBe(true)
    expect(key({ key: '/', ctrlKey: true, shiftKey: true })).toBe(true)
    expect(key({ key: '/', metaKey: true })).toBe(false)
    expect(key({ key: '?', shiftKey: true })).toBe(false)
  })
})
