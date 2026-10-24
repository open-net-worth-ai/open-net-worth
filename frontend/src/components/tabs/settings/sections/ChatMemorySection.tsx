// Settings → Chat & Memory. Split out of ModelSection (2026-08-25) — chat behavior and the
// memory system aren't model-routing decisions, so they get their own section rather than
// living inside "AI".
import { useState } from 'react'
import { PROFILE_KEY, fetchJson } from '../../../../hooks/serverState'
import {
  CHAT_COST_VISIBLE_CHANGED_EVENT,
  CHAT_THINKING_GROUPED_CHANGED_EVENT,
  CHAT_MULTI_TURN_CHANGED_EVENT,
  CHAT_SUGGESTIONS_CHANGED_EVENT,
} from '../../../../hooks/useChatSuggestionsEnabled'
import { authFetch } from '../../../../api/client'
import { setCachedQueryData, useCachedFetchState } from '../../../../state/queryCache'
import {
  MEMORY_FACTS_KEY,
  MEMORY_SETTINGS_KEY,
  getMemorySettings,
} from '../../../../hooks/useMemoryFacts'
import { Btn, Panel, Toggle, useSectionBusy } from '../ds'
import { MemoryDialog } from '../MemoryDialog'
import type { SectionProps } from '../ds'

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

export function ChatMemorySection({ toast }: SectionProps) {
  const memory = useCachedFetchState(MEMORY_SETTINGS_KEY, getMemorySettings, { staleTime: 0 })
  const memoryFacts = useCachedFetchState<{ total: number }>(
    MEMORY_FACTS_KEY,
    () => getJson<{ total: number }>(MEMORY_FACTS_KEY),
    { staleTime: 0 },
  )
  const [memOpen, setMemOpen] = useState(false)

  // Follow-up suggestion chips. The control existed in Settings → Appearance before the port
  // (ChatSuggestionsPanel) and was dropped; the profile field, the hook and ChatPanel's consumer
  // all survived, so only the switch was missing.
  const suggestions = useCachedFetchState<{
    chat_suggestions_enabled?: boolean
    chat_cost_visible?: boolean
    chat_multi_turn_enabled?: boolean
    chat_thinking_grouped?: boolean
  }>(
    PROFILE_KEY,
    () =>
      fetchJson<{
        chat_suggestions_enabled?: boolean
        chat_cost_visible?: boolean
        chat_multi_turn_enabled?: boolean
        chat_thinking_grouped?: boolean
      }>(PROFILE_KEY),
    { staleTime: 0 },
  )

  useSectionBusy(
    (memory.initialLoading && !memory.data) || (suggestions.initialLoading && !suggestions.data),
  )

  async function putCostVisible(visible: boolean) {
    try {
      const r = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_cost_visible: visible }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      const body = (await r.json()) as { profile?: unknown }
      setCachedQueryData(PROFILE_KEY, body.profile ?? body)
      window.dispatchEvent(new Event(CHAT_COST_VISIBLE_CHANGED_EVENT))
      toast(visible ? 'Cost shown on each turn' : 'Cost hidden')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putSuggestionsEnabled(enabled: boolean) {
    try {
      const r = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_suggestions_enabled: enabled }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      // PUT /api/profile answers {ok, profile} — caching the WRAPPER under PROFILE_KEY would
      // hand every other consumer of that key an object with no profile fields on it.
      const body = (await r.json()) as { profile?: unknown }
      setCachedQueryData(PROFILE_KEY, body.profile ?? body)
      // An already-open chat panel listens for this and reacts without a reload.
      window.dispatchEvent(new Event(CHAT_SUGGESTIONS_CHANGED_EVENT))
      toast(enabled ? 'Follow-up suggestions on' : 'Follow-up suggestions off')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putThinkingGrouped(grouped: boolean) {
    try {
      const r = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_thinking_grouped: grouped }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      const body = (await r.json()) as { profile?: unknown }
      setCachedQueryData(PROFILE_KEY, body.profile ?? body)
      window.dispatchEvent(new Event(CHAT_THINKING_GROUPED_CHANGED_EVENT))
      toast(grouped ? 'Reasoning shown as one block' : 'Reasoning shown step by step')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

  async function putMultiTurnEnabled(enabled: boolean) {
    try {
      const r = await authFetch(PROFILE_KEY, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_multi_turn_enabled: enabled }),
      })
      if (!r.ok) {
        toast(`Could not save (HTTP ${r.status})`)
        return
      }
      const body = (await r.json()) as { profile?: unknown }
      setCachedQueryData(PROFILE_KEY, body.profile ?? body)
      window.dispatchEvent(new Event(CHAT_MULTI_TURN_CHANGED_EVENT))
      toast(enabled ? 'Multi-turn chat on' : 'One turn at a time')
    } catch {
      toast('Could not reach the server. Try again.')
    }
  }

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
    <>
      <Panel title="Chat" sub="How the assistant behaves in the chat panel">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Follow-up suggestions</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              The suggested next questions under a reply. Off means none are generated — which also
              stops the model call that writes them.
            </div>
          </div>
          <Toggle
            on={suggestions.data?.chat_suggestions_enabled === true}
            disabled={!suggestions.data}
            onChange={(v) => void putSuggestionsEnabled(v)}
            label="Follow-up suggestions"
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Show cost per turn</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              The token counts, price and model under each reply. Hiding it changes nothing about
              what is spent or recorded — the session total and Cost panel still show it.
            </div>
          </div>
          <Toggle
            on={suggestions.data?.chat_cost_visible !== false}
            disabled={!suggestions.data}
            onChange={(v) => void putCostVisible(v)}
            label="Show cost per turn"
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Group reasoning into one block</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              Off: each round of reasoning appears where it happened, between the tool calls it led
              to. On: the whole turn's reasoning is merged into one block above the answer.
              Presentation only — the transcript records the rounds separately either way.
            </div>
          </div>
          <Toggle
            on={suggestions.data?.chat_thinking_grouped === true}
            disabled={!suggestions.data}
            onChange={(v) => void putThinkingGrouped(v)}
            label="Group reasoning into one block"
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Multi-turn chat</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              Off: Send is disabled until Lucius finishes answering. On: you can send while Lucius
              is still answering, and it's woven into that same reply.
            </div>
          </div>
          <Toggle
            on={suggestions.data?.chat_multi_turn_enabled === true}
            disabled={!suggestions.data}
            onChange={(v) => void putMultiTurnEnabled(v)}
            label="Multi-turn chat"
          />
        </div>
      </Panel>

      <Panel title="Memory" sub="Master switch for the memory system">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Memory extraction &amp; injection</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              When off, nothing is extracted from your conversations and nothing is recalled into
              them — whatever else is configured.
            </div>
          </div>
          <Toggle
            on={memory.data?.enabled ?? false}
            disabled={!memory.data}
            onChange={(v) => void putMemoryEnabled(v)}
            label="Memory extraction and injection"
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>Stored memories</div>
            <div className="rd-help" style={{ marginTop: 3, display: 'block', lineHeight: 1.5 }}>
              {memoryFacts.data?.total ?? 0} facts. Read what Lucius knows and forget anything
              that's wrong or out of date.
            </div>
          </div>
          <Btn size="xs" onClick={() => setMemOpen(true)}>
            Review…
          </Btn>
        </div>
      </Panel>

      {memOpen && <MemoryDialog onClose={() => setMemOpen(false)} toast={toast} />}
    </>
  )
}
