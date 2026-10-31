// Settings → Developer with permissions_v2 on. The same section as DeveloperSection, minus the
// switches that a tool's own value in Settings → Permissions replaced: the run_code "Always
// allow" switch and "Unattended script runs". What is left here only turns a feature on or off.
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { ExperimentalPanel } from '../ExperimentalPanel'
import { SqlAccessPanel } from '../SqlAccessPanel'
import { Panel, Toggle, row, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'
import { Extensions, LogViewer } from './DeveloperSection'

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

function CodeExecutionV2({ toast }: SectionProps) {
  const { data, initialLoading, refresh } = useCachedFetchState<{ enabled: boolean }>(
    '/api/config/code-execution',
    () => getJson<{ enabled: boolean }>('/api/config/code-execution'),
    { staleTime: 0 },
  )
  useSectionBusy(initialLoading && !data)
  const codeExec = data?.enabled ?? false

  async function patch(next: { enabled: boolean }, message: string) {
    try {
      const r = await authFetch('/api/config/code-execution', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void refresh()
      toast(message)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <Panel title="Code execution" sub="Lucius can write & run short Python on this server">
      <span className="rd-help" style={{ display: 'block', lineHeight: 1.55 }}>
        When on, Lucius can write a small script during a chat and run it in an isolated subprocess
        to do a calculation. The sandbox scrubs secrets from the environment, enforces
        CPU/memory/file limits, and the script sees only the JSON data Lucius passes it — it cannot
        read or write your ledger or accounts, and has no network. This switch only turns the
        feature on or off: whether Lucius asks first is the Run code setting in Settings →
        Permissions.
      </span>
      {row(
        'Code execution',
        codeExec
          ? 'On. Run code asks you first by default — change it in Settings → Permissions.'
          : "Lucius's run_code is blocked. This is the safe default.",
        <Toggle
          on={codeExec}
          disabled={!data}
          onChange={(v) =>
            void patch({ enabled: v }, v ? 'Code execution enabled' : 'Code execution blocked')
          }
          label="Code execution"
        />,
      )}
    </Panel>
  )
}

function ScriptSkillsV2({ toast }: SectionProps) {
  const kill = useCachedFetchState<{ enabled: boolean }>(
    '/api/config/script-skills',
    () => getJson<{ enabled: boolean }>('/api/config/script-skills'),
    { staleTime: 0 },
  )
  useSectionBusy(kill.initialLoading && !kill.data)
  const scripts = kill.data?.enabled ?? false

  async function put(enabled: boolean) {
    const r = await authFetch('/api/config/script-skills', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!r.ok) throw new Error(String(r.status))
  }

  return (
    <Panel
      title="Bundled script skills"
      sub="The feature switch — whether a script asks first is its permission in Settings → Permissions"
    >
      {row(
        'Bundled script skills',
        'The instance kill switch. When off, no script-bearing skill can run its Python, whatever else is set.',
        <Toggle
          on={scripts}
          disabled={!kill.data}
          onChange={(v) => {
            void (async () => {
              try {
                await put(v)
                void kill.refresh()
                toast(v ? 'Script skills enabled' : 'Script skills blocked')
              } catch {
                toast('Could not save — try again')
              }
            })()
          }}
          label="Bundled script skills"
        />,
      )}
    </Panel>
  )
}

export function DeveloperSectionV2({ toast }: SectionProps) {
  return (
    <>
      <LogViewer toast={toast} />
      <CodeExecutionV2 toast={toast} />
      <SqlAccessPanel toast={toast} />
      <Extensions toast={toast} />
      <ScriptSkillsV2 toast={toast} />
      <ExperimentalPanel toast={toast} />
    </>
  )
}
