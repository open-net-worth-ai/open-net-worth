import type { ClipboardEvent, KeyboardEvent } from 'react'
import { FormField, TextField } from '../ui'
import { X, Plus } from '../../ui/icons'

const MAX_FEEDS = 5

// "Advanced: news sources" header count badge — "{n} linked · {m} feeds", each half
// dropped when 0, "" (omit the badge entirely) when both are 0. Shared by the topic
// editor (both halves) and the ticker editor (feeds only, linked always 0).
export function advancedSourcesBadge(linkedCount: number, feedsCount: number): string {
  const parts: string[] = []
  if (linkedCount > 0) parts.push(`${linkedCount} linked`)
  if (feedsCount > 0) parts.push(`${feedsCount} feed${feedsCount === 1 ? '' : 's'}`)
  return parts.join(' • ')
}

interface CustomFeedsFieldProps {
  urls: string[]
  onChange: (next: string[]) => void
  /** Verbatim custom_feed_urls validation message from a 422, or null/undefined. */
  error?: string | null
  /** The single quoted URL substring off that error — the matching row gets a red border. */
  invalidUrl?: string | null
}

/** The Custom news feeds row-list, shared verbatim by the topic and ticker editors. */
export function CustomFeedsField({ urls, onChange, error, invalidUrl }: CustomFeedsFieldProps) {
  const setAt = (i: number, v: string) => onChange(urls.map((u, j) => (j === i ? v : u)))
  const removeAt = (i: number) => onChange(urls.filter((_, j) => j !== i))
  const addRow = () => {
    if (urls.length >= MAX_FEEDS) return
    onChange([...urls, ''])
  }
  const blockEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') e.preventDefault()
  }

  // A paste of a multi-URL block splits across the remaining empty/current row + new
  // rows, up to the cap — extras are silently dropped (the cap notice below covers it).
  const onPaste = (i: number) => (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text')
    const tokens = text
      .split(/\s+/)
      .map((t) => t.trim())
      .filter(Boolean)
    if (tokens.length <= 1) return
    e.preventDefault()
    const next = [...urls]
    next[i] = tokens[0] ?? ''
    for (const t of tokens.slice(1)) {
      if (next.length >= MAX_FEEDS) break
      next.push(t)
    }
    onChange(next)
  }

  return (
    <FormField
      label="Custom news feeds"
      error={error}
      help={
        error
          ? undefined
          : 'Headlines from these feeds are watched every ~2 minutes. Up to 5, http(s) only.'
      }
    >
      <div className="intel-pa-rows">
        {urls.length === 0 && <div className="intel-pa-empty">No custom feeds yet.</div>}
        {urls.map((u, i) => (
          <div key={i} className="intel-td-feed-row">
            <TextField
              value={u}
              placeholder="https://example.com/feed.xml"
              invalid={!!invalidUrl && u === invalidUrl}
              onChange={(e) => setAt(i, e.target.value)}
              onKeyDown={blockEnter}
              onPaste={onPaste(i)}
            />
            <button
              type="button"
              className="intel-pa-row-remove"
              title="Remove this feed"
              onClick={() => removeAt(i)}
            >
              <X size={11} />
            </button>
          </div>
        ))}
        {urls.length < MAX_FEEDS ? (
          <button type="button" className="intel-pa-add" onClick={addRow}>
            <Plus size={12} aria-hidden="true" /> Add feed URL
          </button>
        ) : (
          <p className="ui-field__help">Maximum 5 feeds.</p>
        )}
      </div>
    </FormField>
  )
}
