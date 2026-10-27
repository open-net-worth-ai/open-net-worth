// Settings → Memory. Its own section since 2026-10-27 (was half of "Chat & Memory"), laid out
// like Claude's memory settings. ADR 2026-10-25-memory-keeps-only-what-has-no-other-home.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { MEMORY_SETTINGS_KEY, getMemorySettings } from '../../../../hooks/useMemoryFacts'
import { Btn, Panel, Toggle, row, useSectionBusy } from '../ds'
import { MemoryBrowser } from '../MemoryBrowser'
import type { SectionProps } from '../ds'

export function MemorySection({ toast }: SectionProps) {
  const memory = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  // DESIGN PREVIEW — not persisted yet: these two switches get their stored settings with the
  // memory redesign (ADR 2026-10-25-memory-keeps-only-what-has-no-other-home).
  const [searchChats, setSearchChats] = useState(true)
  const [sensitiveTopics, setSensitiveTopics] = useState(false)

  useSectionBusy(memory.initialLoading && !memory.data)

  async function putMemoryEnabled(enabled: boolean) {
    try {
      const r = await authFetch('/api/memory/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      void memory.refresh()
      toast(enabled ? 'Memory on' : 'Memory off — nothing is extracted or recalled')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  return (
    <MemoryBrowser
      toast={toast}
      disabled={memory.data?.enabled === false}
      header={
        <Panel sub="What Lucius remembers about you between conversations">
          {row(
            'Search and reference chats',
            'Let Lucius search your past conversations for relevant details.',
            <Toggle
              on={searchChats}
              onChange={setSearchChats}
              label="Search and reference chats"
            />,
          )}
          {row(
            'Generate memory from chats',
            'Let Lucius remember useful details from your conversations and automations, and use them in replies.',
            <Toggle
              on={memory.data?.enabled ?? false}
              disabled={!memory.data}
              onChange={(v) => void putMemoryEnabled(v)}
              label="Generate memory from chats"
            />,
          )}
          {row(
            'Include sensitive topics in memory',
            'Let Lucius save details about sensitive topics like health conditions or religious beliefs.',
            <Toggle
              on={sensitiveTopics}
              onChange={setSensitiveTopics}
              label="Include sensitive topics in memory"
            />,
          )}
          {row(
            'Import memory from other AI assistants',
            'Bring what ChatGPT, Claude or Gemini know about you. We’ll give you a prompt to run there, then you review everything before it’s saved.',
            <Btn size="xs" onClick={() => toast('Import is coming soon')}>
              Start import
            </Btn>,
          )}
          {row(
            'Export memory',
            'Download your memory as a .zip of Markdown files.',
            <Btn size="xs" onClick={() => toast('Export is coming soon')}>
              Export
            </Btn>,
          )}
        </Panel>
      }
    />
  )
}
