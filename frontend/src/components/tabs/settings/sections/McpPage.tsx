// Settings → MCP. This instance IS an MCP server; the first thing the page asks is how the AI app
// gets in — OAuth (an online app: Claude on the web and phone, ChatGPT) or the API token (an app
// on your computer, through a copied config). Each card says whether its path is ready, so the
// OAuth path is never a panel at the bottom of the token flow.
import { useState } from 'react'
import { Globe, KeyRound } from '../../../../ui/icons'
import type { SectionProps } from '../ds'
import { McpSection } from './McpSection'
import { OAuthPane } from './McpOAuthPane'
import { oauthState, useOAuthStatus } from './mcpOAuth'

type Mode = 'oauth' | 'token'

export function McpPage(props: SectionProps) {
  const { status, refresh } = useOAuthStatus()
  const oauth = oauthState(status)
  const [picked, setPicked] = useState<Mode | null>(null)
  const mode: Mode = picked ?? (oauth.tone === 'good' ? 'oauth' : 'token')

  return (
    <>
      <div className="mcp-choice" role="radiogroup" aria-label="How the AI app connects">
        <ChoiceCard
          on={mode === 'oauth'}
          onPick={() => setPicked('oauth')}
          icon={<Globe size={18} strokeWidth={1.75} />}
          title="OAuth"
          sub="For online apps: Claude on the web and phone, ChatGPT. Approve each with your password."
          state={oauth.label}
          tone={oauth.tone}
        />
        <ChoiceCard
          on={mode === 'token'}
          onPick={() => setPicked('token')}
          icon={<KeyRound size={18} strokeWidth={1.75} />}
          title="API token"
          sub="For apps on your computer: Claude Desktop, Claude Code, Cursor, VS Code. Copy a config."
          state="Ready"
          tone="good"
        />
      </div>
      {mode === 'oauth' ? (
        <OAuthPane
          status={status}
          refresh={refresh}
          toast={props.toast}
          openSection={props.openSection}
        />
      ) : (
        <McpSection {...props} />
      )}
    </>
  )
}

function ChoiceCard({
  on,
  onPick,
  icon,
  title,
  sub,
  state,
  tone,
}: {
  on: boolean
  onPick: () => void
  icon: React.ReactNode
  title: string
  sub: string
  state: string
  tone: 'good' | 'warn'
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      className={'mcp-choice__card' + (on ? ' on' : '')}
      onClick={onPick}
    >
      <span className="mcp-choice__title">
        <span className="mcp-choice__icon" aria-hidden="true">
          {icon}
        </span>
        {title}
      </span>
      <span className="mcp-choice__sub">{sub}</span>
      <span className={'rd-badge ' + tone}>{state}</span>
    </button>
  )
}
