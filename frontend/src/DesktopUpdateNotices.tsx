// DesktopUpdateNotices — the desktop app downloads an update quietly and installs it only on a
// restart or the next quit; its "ready" notice is an OS notification, which the user may have
// switched off. So the window says it too, and after the update installed it says once what
// changed. Never restarts on its own: the button opens the app's own confirm. Older desktop shells
// lack these bridge methods and show nothing.
import { useEffect, useState, type CSSProperties } from 'react'
import { ExternalLink, PartyPopper } from 'lucide-react'
import type { DesktopUpdateState, DesktopWhatsNew } from '../../desktop/src/contracts'
import { Button } from './components/ui/Button'
import { Modal } from './components/ui/Modal'
import { desktopClientBridge, openExternal } from './platform/desktop'
import { topicOf } from './whatsNewTopics'

// The main process hands the note over once; a remount of this component must not lose it.
const whatsNewTaken = new WeakMap<object, Promise<DesktopWhatsNew | null>>()

function takeWhatsNewOnce(): Promise<DesktopWhatsNew | null> {
  const bridge = desktopClientBridge()
  if (!bridge?.takeWhatsNew) return Promise.resolve(null)
  let taken = whatsNewTaken.get(bridge)
  if (!taken) {
    taken = bridge.takeWhatsNew().catch(() => null)
    whatsNewTaken.set(bridge, taken)
  }
  return taken
}

export function DesktopUpdateNotices() {
  return (
    <>
      <UpdateReadyBanner />
      <WhatsNewDialog />
    </>
  )
}

function UpdateReadyBanner() {
  const [state, setState] = useState<DesktopUpdateState | null>(null)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const bridge = desktopClientBridge()
    if (!bridge?.getUpdateState || !bridge.onUpdateState) return
    let live = true
    bridge
      .getUpdateState()
      .then((initial) => {
        if (live) setState(initial)
      })
      .catch(() => {})
    const off = bridge.onUpdateState((next) => setState(next))
    return () => {
      live = false
      off()
    }
  }, [])
  const bridge = desktopClientBridge()
  const ready = Boolean(state?.ready && bridge?.restartToUpdate)
  const available = Boolean(!ready && state?.available && bridge?.offerUpdate)
  if (!bridge || !state || (!ready && !available)) return null
  // Later is per version AND per stage: hiding "available" must not hide the "ready" that follows.
  const key = `${ready ? 'ready' : 'available'}:${state.version ?? ''}`
  if (dismissed === key) return null
  const name = state.version ? `Open Net Worth ${state.version}` : 'An update'
  return (
    // data-portal-layer: tappable over an open modal — see NewVersionBanner.
    <div className="app-update" role="status" data-portal-layer>
      <span>{ready ? `${name} is ready` : `${name} is available`}</span>
      <button type="button" className="app-update-later" onClick={() => setDismissed(key)}>
        Later
      </button>
      {ready ? (
        // Opens the app's own Restart Now / Later confirm: the restart is the person's answer
        // there, never this click alone.
        <button
          type="button"
          className="app-update-btn"
          aria-label="Restart to update"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            bridge.restartToUpdate!().finally(() => setBusy(false))
          }}
        >
          Restart
        </button>
      ) : (
        // Opens the same choice as the menu's Update Available…: download it, open the download
        // page when this install cannot update itself, or skip the version.
        <button
          type="button"
          className="app-update-btn"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            bridge.offerUpdate!().finally(() => setBusy(false))
          }}
        >
          Update
        </button>
      )}
    </div>
  )
}

const CONFETTI_HUES = ['--good', '--ibkr', '--apt', '--recv', '--crypto', '--bond']
// Fixed pseudo-random pieces (the same every time, no Math.random in render): a real burst is
// uneven. Each flies out sideways from the badge, peaks, then falls with gravity and fades —
// mostly sideways, because straight up runs into the dialog's top edge.
function seeded(seed: number): () => number {
  let state = seed
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296
    return state / 4294967296
  }
}
const random = seeded(31)
const CONFETTI = Array.from({ length: 26 }, (_, i) => {
  const side = i % 2 ? 1 : -1
  return {
    x: side * (50 + random() * 150),
    peak: 14 + random() * 34,
    fall: 70 + random() * 70,
    spin: side * (200 + random() * 400),
    flip: 180 + random() * 540,
    hue: `var(${CONFETTI_HUES[Math.floor(random() * CONFETTI_HUES.length)]})`,
    round: random() < 0.3,
    delay: Math.round(random() * 140),
    duration: Math.round(1300 + random() * 500),
  }
})

function WhatsNewDialog() {
  const [note, setNote] = useState<DesktopWhatsNew | null>(null)
  useEffect(() => {
    let live = true
    void takeWhatsNewOnce().then((value) => {
      if (live) setNote(value)
    })
    return () => {
      live = false
    }
  }, [])
  if (!note) return null
  const close = () => setNote(null)
  const openNotes = () => void openExternal(note.releaseUrl)
  const hasHighlights = note.highlights.length > 0
  return (
    <Modal
      open
      onClose={close}
      title={`What’s new in ${note.version}`}
      size="md"
      className="whats-new"
      footer={
        hasHighlights ? (
          <>
            <Button variant="ghost" onClick={openNotes}>
              All changes
              <ExternalLink size={14} aria-hidden="true" />
            </Button>
            <Button variant="primary" onClick={close}>
              Let’s go
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={close}>
              Not now
            </Button>
            <Button variant="primary" onClick={openNotes}>
              See what’s new
              <ExternalLink size={14} aria-hidden="true" />
            </Button>
          </>
        )
      }
    >
      {/* Opening focus lands here, not on a control: the dialog opens on its own, so a focus ring
          on the close button or an action would read as a selection nobody made. Tab still
          reaches every control with its ring. */}
      <div className="whats-new__hero" tabIndex={-1} data-autofocus>
        <div className="whats-new__burst" aria-hidden="true">
          {CONFETTI.map((piece, i) => (
            <span
              key={i}
              className={`whats-new__confetti${piece.round ? ' whats-new__confetti--round' : ''}`}
              style={
                {
                  '--x': `${piece.x}px`,
                  '--peak': `${piece.peak}px`,
                  '--fall': `${piece.fall}px`,
                  '--spin': `${piece.spin}deg`,
                  '--flip': `${piece.flip}deg`,
                  '--hue': piece.hue,
                  animationDelay: `${piece.delay}ms`,
                  animationDuration: `${piece.duration}ms`,
                } as CSSProperties
              }
            />
          ))}
        </div>
        <span className="whats-new__badge" aria-hidden="true">
          <PartyPopper size={30} strokeWidth={1.75} />
        </span>
        <p className="whats-new__headline">You’re on {note.version}</p>
        <p className="whats-new__lede">
          {hasHighlights
            ? 'Here’s what’s new. Your book was not touched.'
            : 'The highlights couldn’t load right now. They’re on the release page.'}
        </p>
      </div>
      {hasHighlights && (
        <ul className="whats-new-list">
          {note.highlights.map((item) => {
            const { icon: Icon, hue } = topicOf(item)
            return (
              <li key={item} className="whats-new-list__item">
                <span
                  className="whats-new-list__icon"
                  style={{ '--hue': hue } as CSSProperties}
                  aria-hidden="true"
                >
                  <Icon size={16} strokeWidth={2} />
                </span>
                <span>{item}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Modal>
  )
}
