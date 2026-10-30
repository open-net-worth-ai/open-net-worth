// A fenced code block in Lucius's reply, with a Copy button: shown on hover, and always on a
// touch screen, which has no hover (owner, 2026-10-31). Copies the block's text exactly as
// shown — terminal commands are the usual case, and retyping them invites mistakes.
import { useRef, useState, type ComponentPropsWithoutRef } from 'react'
import { Check, Copy } from '../../ui/icons'

export function CodeBlock({ children, ...props }: ComponentPropsWithoutRef<'pre'>) {
  const pre = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)

  const copy = () => {
    const el = pre.current
    if (!el) return
    const done = () => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }
    navigator.clipboard?.writeText(el.textContent ?? '').then(done, () => {
      // Clipboard refused (an older webview): select the text so Cmd/Ctrl+C still works.
      const range = document.createRange()
      range.selectNodeContents(el)
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(range)
    })
  }

  return (
    <div className="msg-code">
      <pre ref={pre} {...props}>
        {children}
      </pre>
      <button
        type="button"
        className={'msg-code-copy' + (copied ? ' copied' : '')}
        onClick={copy}
        aria-label={copied ? 'Copied' : 'Copy code'}
      >
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
        <span>{copied ? 'Copied' : 'Copy'}</span>
      </button>
    </div>
  )
}
