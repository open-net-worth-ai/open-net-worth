// The OAuth path of Settings → MCP as three steps: set up (a checklist built from the server's own
// reasons, each open item with a way to fix it), connect (the address to paste and where), and the
// apps already connected. Credentials for any other app sit behind a disclosure at the end.
import { useState } from 'react'
import { Circle, CircleCheck } from '../../../../ui/icons'
import { InfoPopover } from '../../../ui'
import { Btn, Panel } from '../ds'
import type { Toast } from '../ds'
import {
  BLOCKERS,
  ConnectedApps,
  ConnectorAddress,
  Credentials,
  PublicUrl,
  REASONS,
} from './McpOAuthPanel'
import type { OAuthStatus } from './McpOAuthPanel'

interface Step {
  done: boolean
  label: string
  hint?: string
  fix?: { label: string; section: string }
}

function steps(status: OAuthStatus | null): Step[] {
  const flag: Step = {
    done: status !== null,
    label: 'OAuth is switched on',
    hint: 'An experimental switch, under Developer → Experimental.',
    fix: { label: 'Open Developer', section: 'developer' },
  }
  if (!status) return [flag]
  const r = new Set(status.reasons)
  return [
    flag,
    {
      done: !r.has('no_password') && !r.has('auth_disabled'),
      label: 'A password is set, and sign-in is on',
      hint: 'You type it to approve each app.',
      fix: { label: 'Open Security', section: 'security' },
    },
    {
      done: !r.has('loopback_trusted'),
      label: 'The password is always required',
      hint: 'Turn off "Don\'t ask for the password on this computer": behind a proxy, visitors from the internet count as this computer.',
      fix: { label: 'Open Security', section: 'security' },
    },
    {
      done: !r.has('no_public_url') && !r.has('invalid_public_url'),
      label: 'A public HTTPS address is saved',
      hint: r.has('invalid_public_url')
        ? 'The saved address must be https:// and reachable from the internet.'
        : 'Enter it below.',
    },
  ]
}

export function OAuthPane({
  status,
  refresh,
  toast,
  openSection,
}: {
  status: OAuthStatus | null
  refresh: () => void
  toast: Toast
  openSection?: (key: string) => void
}) {
  const blockers = status ? status.reasons.filter((r) => BLOCKERS.has(r)) : []
  if (blockers.length) {
    return (
      <Panel title="OAuth isn't available here">
        <ul className="mcp-hosted-list">
          {blockers.map((r) => (
            <li key={r}>{REASONS[r] ?? r}</li>
          ))}
        </ul>
      </Panel>
    )
  }
  return (
    <>
      <Panel title="Set up" sub="What an online app needs before it can connect">
        <ul className="mcp-check">
          {steps(status).map((step) => (
            <li key={step.label} className={'mcp-check__item' + (step.done ? ' done' : '')}>
              <span className="mcp-check__mark" aria-hidden="true">
                {step.done ? <CircleCheck size={17} /> : <Circle size={17} />}
              </span>
              <span className="mcp-check__text">
                {step.label}
                {step.hint && !step.done ? (
                  <InfoPopover text={step.hint} label={`Why: ${step.label}`} />
                ) : null}
              </span>
              {!step.done && step.fix && openSection ? (
                <Btn size="xs" onClick={() => openSection(step.fix!.section)}>
                  {step.fix.label}
                </Btn>
              ) : null}
            </li>
          ))}
        </ul>
        {status ? <PublicUrl status={status} refresh={refresh} toast={toast} /> : null}
      </Panel>
      {status?.available ? (
        <Panel
          title="Connect"
          sub="Paste the address into the app, then approve with your password"
        >
          <ConnectorAddress mcpUrl={status.mcp_url} toast={toast} />
        </Panel>
      ) : null}
      {status ? (
        <Panel title="Connected apps">
          <ConnectedApps apps={status.apps} refresh={refresh} toast={toast} />
        </Panel>
      ) : null}
      {status ? <OtherApps status={status} refresh={refresh} toast={toast} /> : null}
    </>
  )
}

function OtherApps({
  status,
  refresh,
  toast,
}: {
  status: OAuthStatus
  refresh: () => void
  toast: Toast
}) {
  const [open, setOpen] = useState(false)
  const show = open || status.clients.length > 0
  return (
    <Panel title="Client ID and secret">
      {show ? (
        <Credentials clients={status.clients} refresh={refresh} toast={toast} />
      ) : (
        <div className="mcp-other">
          <span className="rd-help">
            If an app asks for a client ID and secret, create them here.
          </span>
          <Btn size="xs" onClick={() => setOpen(true)}>
            Create client ID and secret
          </Btn>
        </div>
      )}
    </Panel>
  )
}
