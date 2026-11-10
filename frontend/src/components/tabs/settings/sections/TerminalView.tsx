// The terminal itself: xterm.js on a WebSocket to /api/terminal (api/terminal.py describes the
// wire format). Loaded only when someone opens a terminal.
import '@xterm/xterm/css/xterm.css'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { useEffect, useRef, useState } from 'react'
import { Btn } from '../ds'

type EndReason = 'ended' | 'sign-in-again'

const CLOSE_NOTES: Record<number, string> = {
  4401: 'Not signed in.',
  4403: 'The connection was refused.',
  4404: 'The terminal is not available.',
  4408: 'Closed after 15 minutes without activity.',
  4409: 'Another terminal is already open. Close it first.',
  1009: 'That message was too large.',
}

function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/terminal`
}

export function TerminalView({ onEnded }: { onEnded: (reason: EndReason) => void }) {
  const host = useRef<HTMLDivElement>(null)
  const [note, setNote] = useState<string | null>(null)
  const [signInAgain, setSignInAgain] = useState(false)

  useEffect(() => {
    const el = host.current
    if (!el) return
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      // eslint-disable-next-line no-restricted-syntax -- an xterm.js option, not CSS
      fontSize: 13,
      scrollback: 5000,
      theme: { background: '#0e0f12', foreground: '#e6e6e6' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    fit.fit()

    const ws = new WebSocket(socketUrl())
    ws.binaryType = 'arraybuffer'
    const send = (message: object) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message))
    }
    ws.onopen = () => {
      send({ type: 'resize', cols: term.cols, rows: term.rows })
      term.focus()
    }
    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        const message = JSON.parse(event.data) as { type?: string }
        if (message.type === 'exit') setNote('The session ended.')
        return
      }
      term.write(new Uint8Array(event.data as ArrayBuffer))
    }
    ws.onclose = (event) => {
      if (event.reason === 'fresh_session_required') {
        setSignInAgain(true)
        setNote('For your security the terminal asks for a recent sign-in.')
        return
      }
      setNote((current) => current ?? CLOSE_NOTES[event.code] ?? 'The session ended.')
    }
    const data = term.onData((input) => send({ type: 'input', data: input }))
    const size = term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }))
    const observer = new ResizeObserver(() => fit.fit())
    observer.observe(el)

    return () => {
      observer.disconnect()
      data.dispose()
      size.dispose()
      ws.onclose = null
      ws.close()
      term.dispose()
    }
  }, [])

  return (
    <div className="rd-term">
      {/* A shell's output can be anything, secrets included: never into the screen snapshot. */}
      <div className="rd-term-screen" ref={host} data-ctx-secret="" />
      {note && (
        <div className="rd-term-note" role="status">
          <span className="rd-help">{note}</span>
          <Btn onClick={() => onEnded(signInAgain ? 'sign-in-again' : 'ended')}>
            {signInAgain ? 'Continue' : 'Close'}
          </Btn>
        </div>
      )}
    </div>
  )
}
