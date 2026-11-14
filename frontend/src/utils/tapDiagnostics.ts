// Tap diagnostics — an opt-in trace of what a phone's touches did, sent to the server log.
//
// Built for a bug a desktop browser cannot show: after iOS's edge swipe-back, a screen still
// scrolled but took no taps until the next swipe-back (owner, 2026-11-14). Turn it on with
// `?diag=taps` in the address (remembered on the device), off with `?diag=off`; or for every
// device with `ONW_CLIENT_DIAG=taps` on the server. Each event is
// one `client-diag` line in the app log (`GET /api/logs?q=client-diag`). It records only
// interaction metadata — event type, position, the element's tag and classes, the path.

import { authFetch } from '../api/client'

const KEY = 'onw.diag'
const FLUSH_MS = 2000
const NO_CLICK_MS = 600
const TAP_SLOP_PX = 10

type Event = Record<string, unknown>

let queue: Event[] = []
let session = ''

function describe(el: Element | null): string {
  if (!el) return 'null'
  const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3) : []
  return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${cls.length ? `.${cls.join('.')}` : ''}`
}

// What sits under a point, top first, with what decides whether it takes the tap.
function stackAt(x: number, y: number): string[] {
  return document
    .elementsFromPoint(x, y)
    .slice(0, 5)
    .map((el) => {
      const s = getComputedStyle(el)
      return `${describe(el)} pe=${s.pointerEvents} pos=${s.position} z=${s.zIndex} op=${s.opacity}${el.closest('[inert]') ? ' INERT' : ''}`
    })
}

// Anything that could be blocking the page: open dialogs, inert regions, fixed layers that
// cover most of the screen, and the body's own lock styles.
function pageState(): Event {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const covers = [...document.body.querySelectorAll<HTMLElement>('*')]
    .filter((el) => {
      const s = getComputedStyle(el)
      if (s.position !== 'fixed' || s.pointerEvents === 'none' || s.display === 'none') return false
      const r = el.getBoundingClientRect()
      return r.width * r.height > vw * vh * 0.5
    })
    .slice(0, 6)
    .map((el) => `${describe(el)} z=${getComputedStyle(el).zIndex}`)
  return {
    dialogs: [...document.querySelectorAll('[role=dialog],[aria-modal=true],dialog[open]')].map(
      describe,
    ),
    inert: [...document.querySelectorAll('[inert]')].map(describe),
    covers,
    body: document.body.getAttribute('style') ?? '',
    html: document.documentElement.className.toString().slice(0, 120),
    active: describe(document.activeElement),
    scrollY: window.scrollY,
    vvTop: window.visualViewport?.offsetTop,
    vvPageTop: window.visualViewport?.pageTop,
  }
}

function push(type: string, extra: Event = {}): void {
  queue.push({
    t: Math.round(performance.now()),
    type,
    path: location.pathname + location.search,
    ...extra,
  })
}

async function flush(keepalive = false): Promise<void> {
  if (!queue.length) return
  const events = queue
  queue = []
  try {
    await authFetch('/api/diagnostics/client', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session, events }),
      keepalive,
    })
  } catch {
    // a lost batch is only a gap in the trace
  }
}

function enabled(): boolean {
  const param = new URLSearchParams(location.search).get('diag')
  try {
    if (param === 'off') localStorage.removeItem(KEY)
    else if (param === 'taps') localStorage.setItem(KEY, 'taps')
    return localStorage.getItem(KEY) === 'taps'
  } catch {
    return param === 'taps'
  }
}

// On if this device asked for it, or if the operator turned it on for every device
// (`ONW_CLIENT_DIAG=taps` on the server): the home-screen app has no address bar.
export function startTapDiagnostics(): void {
  if (enabled()) return record()
  authFetch('/api/diagnostics/client')
    .then((r) => (r.ok ? r.json() : null))
    .then((body: { enabled?: boolean } | null) => body?.enabled && record())
    .catch(() => undefined)
}

function record(): void {
  session = Math.random().toString(36).slice(2, 10)
  const opts = { capture: true, passive: true }
  let press: { x: number; y: number; moved: boolean } | null = null
  let waiting: ReturnType<typeof setTimeout> | null = null

  window.addEventListener(
    'touchstart',
    (e) => {
      const p = e.touches[0]
      if (!p) return
      press = { x: p.clientX, y: p.clientY, moved: false }
      push('touchstart', {
        x: p.clientX,
        y: p.clientY,
        n: e.touches.length,
        target: describe(e.target as Element),
      })
    },
    opts,
  )
  window.addEventListener(
    'touchmove',
    (e) => {
      const p = e.touches[0]
      if (press && p && Math.hypot(p.clientX - press.x, p.clientY - press.y) > TAP_SLOP_PX)
        press.moved = true
    },
    opts,
  )
  window.addEventListener(
    'touchend',
    (e) => {
      const p = e.changedTouches[0]
      const tap = press && !press.moved
      push('touchend', {
        x: p?.clientX,
        y: p?.clientY,
        tap,
        cancelable: e.cancelable,
        target: describe(e.target as Element),
      })
      if (tap && p) {
        const { clientX: x, clientY: y } = p
        if (waiting) clearTimeout(waiting)
        waiting = setTimeout(() => {
          push('TAP-NO-CLICK', { x, y, stack: stackAt(x, y), state: pageState() })
          void flush()
        }, NO_CLICK_MS)
      }
      press = null
    },
    opts,
  )
  window.addEventListener('touchcancel', () => push('touchcancel'), opts)
  for (const type of ['pointerdown', 'pointerup', 'pointercancel'] as const) {
    window.addEventListener(
      type,
      (e) =>
        push(type, {
          x: e.clientX,
          y: e.clientY,
          kind: e.pointerType,
          target: describe(e.target as Element),
        }),
      opts,
    )
  }
  window.addEventListener(
    'click',
    (e) => {
      if (waiting) clearTimeout(waiting)
      waiting = null
      push('click', { x: e.clientX, y: e.clientY, target: describe(e.target as Element) })
    },
    { capture: true },
  )
  // Bubble phase: did something on the way stop or cancel it?
  window.addEventListener('click', (e) => push('click-arrived', { prevented: e.defaultPrevented }))
  window.addEventListener('popstate', () => {
    push('popstate')
    setTimeout(() => push('after-popstate', pageState()), 500)
  })
  window.addEventListener('pageshow', (e) => push('pageshow', { persisted: e.persisted }))
  window.addEventListener('pagehide', () => {
    push('pagehide')
    void flush(true)
  })
  document.addEventListener('visibilitychange', () =>
    push('visibility', { state: document.visibilityState }),
  )

  push('diag-start', { ua: navigator.userAgent, w: innerWidth, h: innerHeight, ...pageState() })
  setInterval(() => void flush(), FLUSH_MS)
}
