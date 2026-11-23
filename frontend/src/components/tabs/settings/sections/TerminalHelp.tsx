// What the terminal is mostly for: signing in to a Claude Code or Codex subscription on this server.
// The two commands, ready to copy (docs/product/operations/terminal.md).
import { useState } from 'react'
import { Btn } from '../ds'

const COMMANDS = [
  { label: 'Claude Code', command: 'claude' },
  { label: 'Codex (ChatGPT)', command: 'codex login --device-auth' },
]

function Copy({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Btn
      size="xs"
      variant="secondary"
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1500)
          })
          .catch(() => {})
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </Btn>
  )
}

export function TerminalHelp() {
  return (
    <div className="rd-term-help">
      <div className="rd-term-cmds rd-help">
        {COMMANDS.map(({ label, command }) => (
          <span key={command} className="rd-term-cmd">
            <span className="rd-term-cmd-label">{label}</span>
            <code>{command}</code>
            <Copy text={command} />
          </span>
        ))}
      </div>
    </div>
  )
}
