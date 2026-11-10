// Settings → Memory. Its own section since 2026-10-27 (was half of "Chat & Memory"), laid out
// like Claude's memory settings. ADR 2026-10-25-memory-keeps-only-what-has-no-other-home.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import {
  MEMORY_SETTINGS_KEY,
  getMemorySettings,
  type MemorySettings,
} from '../../../../hooks/useMemoryFacts'
import { downloadMemoryExport } from '../../../../hooks/memoryImport'
import { Btn, Panel, Toggle, row, useSectionBack, useSectionBusy } from '../ds'
import { MemoryBrowser } from '../MemoryBrowser'
import { MemoryImport } from '../MemoryImport'
import type { SectionProps } from '../ds'

type Key = keyof MemorySettings

export function MemorySection({ toast }: SectionProps) {
  const memory = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  const settings = memory.data
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)

  useSectionBusy(memory.initialLoading && !settings)
  const placeBack = useSectionBack()

  async function putSetting(key: Key, value: boolean, done: string) {
    try {
      const r = await authFetch(MEMORY_SETTINGS_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void memory.refresh()
      toast(done)
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function exportMemory() {
    setExporting(true)
    try {
      await downloadMemoryExport()
      toast('Memory exported')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not export. Try again.')
    } finally {
      setExporting(false)
    }
  }

  const toggle = (key: Key, label: string, done: (on: boolean) => string) => (
    <Toggle
      on={settings?.[key] ?? false}
      disabled={!settings}
      onChange={(v) => void putSetting(key, v, done(v))}
      label={label}
    />
  )

  return (
    <>
      {importing && <MemoryImport toast={toast} onClose={() => setImporting(false)} />}
      <MemoryBrowser
        toast={toast}
        placeBack={placeBack}
        disabled={settings?.enabled === false}
        header={
          <Panel sub="What Lucius remembers about you between conversations">
            {row(
              'Search and reference chats',
              'Let Lucius search your past conversations for relevant details. Even when this is off, he can still read them with a SQL query.',
              toggle('search_chats', 'Search and reference chats', (on) =>
                on ? 'Lucius can search your past chats' : 'Lucius won’t search your past chats',
              ),
            )}
            {row(
              'Generate memory from chats',
              'Let Lucius remember useful details from your conversations and automations, and use them in replies.',
              toggle('enabled', 'Generate memory from chats', (on) =>
                on ? 'Memory on' : 'Memory off — nothing is extracted or recalled',
              ),
            )}
            {row(
              'Include sensitive topics in memory',
              'Let Lucius save details about sensitive topics like health conditions or religious beliefs.',
              toggle('sensitive_topics', 'Include sensitive topics in memory', (on) =>
                on ? 'Sensitive topics can be remembered' : 'Sensitive topics won’t be remembered',
              ),
            )}
            {row(
              'Import memory from other AI providers',
              'Bring relevant context and data from another AI provider to Lucius. We’ll provide a prompt you can use to fetch the memory from your other account.',
              <Btn size="xs" onClick={() => setImporting(true)}>
                Start import
              </Btn>,
            )}
            {row(
              'Export memory',
              'Download your memory as a .zip of Markdown files.',
              <Btn size="xs" disabled={exporting} onClick={() => void exportMemory()}>
                {exporting ? 'Exporting…' : 'Export'}
              </Btn>,
            )}
          </Panel>
        }
      />
    </>
  )
}
