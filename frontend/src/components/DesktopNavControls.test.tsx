/**
 * @vitest-environment jsdom
 *
 * macOS desktop title-bar chrome: the sidebar / back / forward cluster and the CSS contract that
 * keeps it clear of the traffic lights (the app has no native title bar there).
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { DesktopNavControls } from './DesktopNavControls'
import { macDesktopChrome } from '../platform/desktop'

afterEach(() => {
  cleanup()
  delete document.documentElement.dataset.desktopChrome
})

function renderControls(overrides: Partial<Parameters<typeof DesktopNavControls>[0]> = {}) {
  const props = {
    sidebarExpanded: true,
    onToggleSidebar: vi.fn(),
    canGoBack: false,
    canGoForward: false,
    onBack: vi.fn(),
    onForward: vi.fn(),
    ...overrides,
  }
  render(<DesktopNavControls {...props} />)
  return props
}

describe('DesktopNavControls', () => {
  it('disables Back and Forward when there is nowhere to go, and leaves the sidebar toggle live', () => {
    const props = renderControls()
    expect((screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Forward' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(props.onToggleSidebar).toHaveBeenCalledTimes(1)
  })

  it('enables each arrow independently and calls its handler', () => {
    const props = renderControls({ canGoBack: true })
    const back = screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement
    const forward = screen.getByRole('button', { name: 'Forward' }) as HTMLButtonElement
    expect(back.disabled).toBe(false)
    expect(forward.disabled).toBe(true)
    fireEvent.click(back)
    expect(props.onBack).toHaveBeenCalledTimes(1)
    fireEvent.click(forward)
    expect(props.onForward).not.toHaveBeenCalled()
  })

  it('labels the toggle by what it will do', () => {
    renderControls({ sidebarExpanded: false })
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy()
  })

  it('exposes the toolbar landmark', () => {
    renderControls()
    expect(screen.getByRole('toolbar', { name: 'Navigation' })).toBeTruthy()
  })
})

describe('click behaviour', () => {
  it('toggles exactly once per click, and not on mousedown/pointerdown', () => {
    const props = renderControls()
    const toggle = screen.getByRole('button', { name: 'Collapse sidebar' })
    fireEvent.pointerDown(toggle)
    fireEvent.mouseDown(toggle)
    expect(props.onToggleSidebar).not.toHaveBeenCalled()
    fireEvent.mouseUp(toggle)
    fireEvent.click(toggle)
    expect(props.onToggleSidebar).toHaveBeenCalledTimes(1)
  })

  it('navigates once per click on each arrow', () => {
    const props = renderControls({ canGoBack: true, canGoForward: true })
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }))
    expect(props.onBack).toHaveBeenCalledTimes(1)
    expect(props.onForward).toHaveBeenCalledTimes(1)
  })

  it('reports hover on the toggle for the peek', () => {
    const onToggleHover = vi.fn()
    const onToggleLeave = vi.fn()
    renderControls({ onToggleHover, onToggleLeave })
    const toggle = screen.getByRole('button', { name: 'Collapse sidebar' })
    fireEvent.mouseEnter(toggle)
    fireEvent.mouseLeave(toggle)
    expect(onToggleHover).toHaveBeenCalledTimes(1)
    expect(onToggleLeave).toHaveBeenCalledTimes(1)
  })
})

describe('drag-region safety', () => {
  const shell = readFileSync(join(__dirname, '..', 'RouterShell.tsx'), 'utf8')

  it('renders the cluster after every drag strip, so its no-drag wins the region merge', () => {
    // Chromium resolves overlapping app-regions in DOM order: a no-drag element that precedes
    // .sidebar-header / .topbar is overridden by them and its clicks begin a window drag.
    const cluster = shell.indexOf('<DesktopNavControls')
    expect(cluster).toBeGreaterThan(-1)
    expect(cluster).toBeGreaterThan(shell.indexOf('className="sidebar-header"'))
    expect(cluster).toBeGreaterThan(shell.indexOf('className="topbar"'))
    expect(cluster).toBeGreaterThan(shell.indexOf('className="app-main"'))
  })

  it('marks the cluster, every descendant and its padding no-drag', () => {
    const css = readFileSync(join(__dirname, '..', 'index.css'), 'utf8').replace(/\s+/g, ' ')
    expect(css).toMatch(/\.desktop-nav, \.desktop-nav \* \{ -webkit-app-region: no-drag;/)
    expect(css).toMatch(/\.desktop-nav \{ padding: 4px; margin: -4px;/)
  })
})

describe('macDesktopChrome', () => {
  it('is true only once the desktop shell has stamped the marker', () => {
    expect(macDesktopChrome()).toBe(false)
    document.documentElement.dataset.desktopChrome = 'mac'
    expect(macDesktopChrome()).toBe(true)
  })
})

describe('title-bar CSS contract', () => {
  const css = readFileSync(join(__dirname, '..', 'index.css'), 'utf8').replace(/\s+/g, ' ')
  const rule = (selector: string) => {
    const start = css.indexOf(selector)
    expect(start, `missing rule: ${selector}`).toBeGreaterThan(-1)
    return css.slice(start, css.indexOf('}', start))
  }

  it('makes the strip draggable and every control in it, plus the cluster, no-drag', () => {
    expect(rule("html[data-desktop-chrome='mac'] :is(.sidebar-header, .topbar) {")).toContain(
      '-webkit-app-region: drag',
    )
    expect(rule(':is(button, a, input, select, textarea, label, summary')).toContain(
      '-webkit-app-region: no-drag',
    )
    expect(rule('.desktop-nav-btn {')).toContain('-webkit-app-region: no-drag')
  })

  it('keeps full-window overlays clickable over the drag strip, and first run clear of the lights', () => {
    expect(
      rule(
        "html[data-desktop-chrome='mac'] :is(.firstrun-shell, .modal-overlay, .artifact-fullscreen) {",
      ),
    ).toContain('-webkit-app-region: no-drag')
    expect(rule("html[data-desktop-chrome='mac'] .firstrun-topbar {")).toContain(
      'padding-left: 92px',
    )
  })

  it('drags the window by the Lucius header, except its controls and while an overlay covers it', () => {
    expect(rule("html[data-desktop-chrome='mac'] .lx-side-head {")).toContain(
      '-webkit-app-region: drag',
    )
    expect(rule("html[data-desktop-chrome='mac'] .lx-side-head :is(button,")).toContain(
      '-webkit-app-region: no-drag',
    )
    expect(
      rule(
        "html[data-desktop-chrome='mac'] .lx-side:has(.conv-menu, .modal-overlay) .lx-side-head {",
      ),
    ).toContain('-webkit-app-region: no-drag')
  })

  it('gives the bar a native 48px height in desktop chrome only', () => {
    expect(rule("html[data-desktop-chrome='mac'] .topbar {")).toContain('height: 48px')
    expect(rule("html[data-desktop-chrome='mac'] .sidebar-header {")).toContain('height: 48px')
    expect(rule(' .topbar { display: flex')).not.toContain('height: 48px')
  })

  it('keeps the divider while the rail is open and hides it when collapsed', () => {
    expect(rule("html[data-desktop-chrome='mac'] .sidebar:not(.expanded):not(.peek) {")).toContain(
      'border-right-color: transparent',
    )
  })

  it('expands and collapses instantly: no width, border, padding or label transitions', () => {
    expect(css).not.toMatch(/\.sidebar \{[^}]*transition: width/)
    expect(css).not.toMatch(/transition:[^;]*border-color 0\.2s/)
    expect(css).not.toMatch(/transition: padding-left/)
    expect(rule('.sidebar-label {')).not.toContain('transition')
  })

  it('floats the peek out of flow and drops its animation for reduced motion', () => {
    expect(rule('.sidebar.peek { position: fixed')).toContain('left: 0')
    expect(css).toMatch(/prefers-reduced-motion: reduce\) \{ \.sidebar\.peek \{ transition: none;/)
  })

  it('keeps bar content clear of the cluster in every rail state and in fullscreen', () => {
    // Cluster: left 84 + 3 x 28 + 2 x 2 gaps = 172. Content edge = rail width + topbar padding.
    expect(
      rule("html[data-desktop-chrome='mac'] .sidebar:not(.expanded) ~ .app-main .topbar {"),
    ).toContain('padding-left: 132px') // 52 + 132 = 184 >= 172
    expect(
      rule(
        "html[data-desktop-chrome='mac'][data-desktop-fullscreen] .sidebar:not(.expanded) ~ .app-main .topbar {",
      ),
    ).toContain('padding-left: 62px') // cluster moves to left 14 -> 102; 52 + 62 = 114
    expect(rule("html[data-desktop-chrome='mac'] .desktop-nav {")).toContain('left: 84px')
    expect(
      rule("html[data-desktop-chrome='mac'][data-desktop-fullscreen] .desktop-nav {"),
    ).toContain('left: 14px')
  })
})
