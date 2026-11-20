import { useState } from 'react'
import { installTa, TaError } from '../../../hooks/useTradingAgents'
import type { TaStatus } from '../../../types/tradingAgents'
import { Badge, Button, Cluster, Stack, Text } from '../../ui'
import type { BadgeTone } from '../../ui'
import { useConnectorWrote } from './formSave'

const STATE: Record<TaStatus['install']['status'], { label: string; tone: BadgeTone }> = {
  absent: { label: 'Not installed', tone: 'neutral' },
  installing: { label: 'Installing…', tone: 'warn' },
  ready: { label: 'Installed', tone: 'pos' },
  failed: { label: 'Install failed', tone: 'neg' },
}

const BUTTON: Record<TaStatus['install']['status'], string> = {
  absent: 'Install',
  installing: 'Installing…',
  ready: 'Reinstall',
  failed: 'Try again',
}

/** The install block: an isolated Python environment in the data folder, installed once. A
 *  failure shows the install's own error as the log tail (there is no separate log). */
export function TradingAgentsInstall({
  install,
  reinstallMessage = null,
}: {
  install: TaStatus['install']
  reinstallMessage?: string | null
}) {
  const wrote = useConnectorWrote()
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const installing = install.status === 'installing'
  const state = STATE[install.status]

  async function start() {
    setStarting(true)
    setError(null)
    try {
      await installTa()
      wrote()
    } catch (e) {
      setError(e instanceof TaError ? e.message : 'Could not start the install.')
    } finally {
      setStarting(false)
    }
  }

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        TradingAgents runs in its own isolated Python environment inside your Open Net Worth data
        folder. It is installed once and is not part of Open Net Worth.
      </Text>
      <div className="ta-notice" role="note" aria-label="Third-party software">
        <strong>Third-party software.</strong> TradingAgents (TauricResearch, Apache-2.0) is
        downloaded when you click Install, pinned to one commit. The installed TradingAgents code
        and every package it depends on run with your computer user&apos;s permissions. They can
        read everything Open Net Worth stores on this machine, including the database and your saved
        keys, and can use your network. TradingAgents holds no AI key of its own. It also keeps
        local analysis logs inside your data folder, which are deleted when you remove the connector
        or the run.
      </div>
      {reinstallMessage && (
        <div role="status">
          <Text size="sm" tone="warn">
            {reinstallMessage}
          </Text>
        </div>
      )}
      <Cluster justify="between" gap="--space-4">
        <Cluster gap="--space-4">
          <Badge tone={state.tone}>{state.label}</Badge>
          {install.version && (
            <Text size="xs" tone="faint" className="ta-mono" title={install.version}>
              commit {install.version.slice(0, 7)}
            </Text>
          )}
        </Cluster>
        <Button
          size="sm"
          variant={install.status === 'ready' && !reinstallMessage ? 'secondary' : 'primary'}
          loading={installing || starting}
          onClick={() => void start()}
        >
          {BUTTON[install.status]}
        </Button>
      </Cluster>
      {installing && (
        <Text size="xs" tone="faint">
          <span role="status">This takes a few minutes. You can leave this page.</span>
        </Text>
      )}
      {install.status === 'failed' && install.error && (
        <pre className="ta-log" role="alert">
          {install.error}
        </pre>
      )}
      {error && (
        <Text size="xs" tone="bad">
          {error}
        </Text>
      )}
    </Stack>
  )
}
