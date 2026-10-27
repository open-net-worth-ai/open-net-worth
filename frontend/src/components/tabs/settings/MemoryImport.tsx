// Settings → Memory → Start import: copy a prompt for another assistant, paste what it printed,
// review where every line would go, and only then save the ones you keep. Nothing is written
// before "Import N".
import { useEffect, useState } from 'react'
import { invalidateQueryCache } from '../../../state/queryCache'
import { MEMORY_FACTS_KEY } from '../../../hooks/useMemoryFacts'
import {
  IMPORT_MAX_CHARS,
  applyImport,
  getImportPrompt,
  previewImport,
  type ImportItem,
} from '../../../hooks/memoryImport'
import { Btn, Modal } from './ds'
import type { Toast } from './ds'
import { SECTIONS } from './memoryGroups'

type Step = 'prompt' | 'paste' | 'review'

const FIELD_LABELS: Record<string, string> = {
  name: 'Name',
  nickname: 'Preferred name',
  marital_status: 'Marital status',
  employment_types: 'Employment',
  risk_tolerance: 'Risk tolerance',
}

const GROUP_TITLES: Record<string, string> = {
  profile: 'Profile',
  instructions: 'Instructions for Lucius',
  skip: 'Skipped',
}

const sectionLabel = (key?: string) => SECTIONS.find((s) => s.key === key)?.label ?? 'Other'

/** The words the user reads for an item: what would be saved, not the raw line. */
function itemText(i: ImportItem): string {
  if (i.destination === 'profile') return `${FIELD_LABELS[i.field ?? ''] ?? i.field}: ${i.value}`
  if (i.destination === 'instructions') return i.value || i.line
  if (i.destination === 'memory') return i.content || i.line
  return i.line
}

/** The review's groups, in the order the user reads them. Memory is split per section · topic. */
function groupItems(items: ImportItem[]): { title: string; entries: [ImportItem, number][] }[] {
  const groups = new Map<string, [ImportItem, number][]>()
  items.forEach((item, n) => {
    const title =
      item.destination === 'memory'
        ? item.topic
          ? `${sectionLabel(item.section)} · ${item.topic}`
          : sectionLabel(item.section)
        : (GROUP_TITLES[item.destination] ?? 'Skipped')
    groups.set(title, [...(groups.get(title) ?? []), [item, n]])
  })
  const order = (title: string) => {
    if (title === 'Skipped') return 3
    const fixed = ['Profile', 'Instructions for Lucius'].indexOf(title)
    return fixed >= 0 ? fixed : 2
  }
  return [...groups]
    .map(([title, entries]) => ({ title, entries }))
    .sort((x, y) => order(x.title) - order(y.title))
}

function summary(applied: number, rejected: number): string {
  const parts = [`Imported ${applied}`]
  if (rejected) parts.push(`${rejected} not imported`)
  return parts.join(' · ')
}

export function MemoryImport({ onClose, toast }: { onClose: () => void; toast: Toast }) {
  const [step, setStep] = useState<Step>('prompt')
  const [prompt, setPrompt] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [items, setItems] = useState<ImportItem[]>([])
  const [kept, setKept] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    getImportPrompt().then(
      (p) => live && setPrompt(p),
      () => live && setError('Could not load the prompt. Close this and try again.'),
    )
    return () => {
      live = false
    }
  }, [])

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(prompt ?? '')
      toast('Prompt copied')
    } catch {
      toast('Could not copy. Select the text and copy it yourself.')
    }
  }

  async function review() {
    setBusy(true)
    setError('')
    try {
      const found = await previewImport(text)
      setItems(found)
      setKept(new Set(found.flatMap((i, n) => (i.destination === 'skip' ? [] : [n]))))
      setStep('review')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    setError('')
    try {
      const out = await applyImport(items.filter((_, n) => kept.has(n)))
      const applied = out.applied.profile + out.applied.instructions + out.applied.memory
      invalidateQueryCache(MEMORY_FACTS_KEY)
      invalidateQueryCache('/api/profile')
      toast(summary(applied, out.rejected.length))
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not import. Try again.')
      setBusy(false)
    }
  }

  const toggle = (n: number) =>
    setKept((k) => {
      const next = new Set(k)
      if (!next.delete(n)) next.add(n)
      return next
    })

  const tooLong = text.length > IMPORT_MAX_CHARS
  const footer = (
    <div className="rd-mi-foot">
      {step !== 'prompt' && (
        <Btn onClick={() => setStep(step === 'review' ? 'paste' : 'prompt')} disabled={busy}>
          Back
        </Btn>
      )}
      <span className="rd-mi-foot-gap" />
      <Btn onClick={onClose} disabled={busy}>
        Cancel
      </Btn>
      {step === 'prompt' && (
        <Btn variant="primary" onClick={() => setStep('paste')} disabled={!prompt}>
          Next
        </Btn>
      )}
      {step === 'paste' && (
        <Btn
          variant="primary"
          onClick={() => void review()}
          disabled={busy || !text.trim() || tooLong}
        >
          {busy ? 'Reading…' : 'Review'}
        </Btn>
      )}
      {step === 'review' && (
        <Btn variant="primary" onClick={() => void save()} disabled={busy || kept.size === 0}>
          {busy ? 'Importing…' : `Import ${kept.size}`}
        </Btn>
      )}
    </div>
  )

  return (
    <Modal title="Import memory from another assistant" size="lg" onClose={onClose} footer={footer}>
      <div className="rd-mi">
        <div className="rd-help">Step {['prompt', 'paste', 'review'].indexOf(step) + 1} of 3</div>
        {error && (
          <div className="rd-mi-error" role="alert">
            {error}
          </div>
        )}
        {step === 'prompt' && (
          <>
            <p className="rd-mi-lead">
              Run this in ChatGPT, Claude or Gemini. It asks the assistant to list what it knows
              about you.
            </p>
            <pre className="rd-mi-prompt">{prompt ?? 'Loading…'}</pre>
            <Btn size="xs" onClick={() => void copyPrompt()} disabled={!prompt}>
              Copy prompt
            </Btn>
          </>
        )}
        {step === 'paste' && (
          <>
            <p className="rd-mi-lead">
              Paste the answer here. Lucius sorts it, and you review every line before anything is
              saved.
            </p>
            <textarea
              className="rd-textarea rd-mi-paste"
              aria-label="Pasted memory"
              rows={10}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="[2026-03-01] - …"
            />
            <div className={'rd-mi-count' + (tooLong ? ' over' : '')}>
              {text.length.toLocaleString('en-US')} / {IMPORT_MAX_CHARS.toLocaleString('en-US')}
            </div>
          </>
        )}
        {step === 'review' && <ReviewList items={items} kept={kept} onToggle={toggle} />}
      </div>
    </Modal>
  )
}

function ReviewList({
  items,
  kept,
  onToggle,
}: {
  items: ImportItem[]
  kept: Set<number>
  onToggle: (n: number) => void
}) {
  if (!items.length) return <p className="rd-mi-lead">Nothing in that text could be sorted.</p>
  return (
    <>
      <p className="rd-mi-lead">
        Untick anything you don’t want. Nothing is saved until you import.
      </p>
      {groupItems(items).map((g) => (
        <section key={g.title} className="rd-mi-group">
          <h3 className="rd-mi-group-h">{g.title}</h3>
          {g.entries.map(([item, n]) => {
            const skip = item.destination === 'skip'
            return (
              <div key={n} className="rd-mi-item">
                <input
                  id={`mi-${n}`}
                  type="checkbox"
                  checked={kept.has(n)}
                  disabled={skip}
                  onChange={() => onToggle(n)}
                />
                <div className="rd-mi-item-body">
                  <label htmlFor={`mi-${n}`}>{itemText(item)}</label>
                  <div className="rd-help">{item.reason}</div>
                </div>
              </div>
            )
          })}
        </section>
      ))}
    </>
  )
}
