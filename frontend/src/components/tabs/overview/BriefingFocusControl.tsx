// The daily briefing widget's prompt box. Unlike a Lucius-generated widget (whose prompt goes to
// chat), this edits the briefing's own standing instruction on the server — and Regenerate
// rewrites the card with it.
import { LuciusMark } from '../../../ui/LuciusMark'
import { useEffect, useState } from 'react'
import { Text } from '../../ui'
import { readBriefingFocus, saveBriefingFocus } from '../../shared/briefingFocus'

// What the box showed before it was wired to the server; never a real instruction.
const OLD_PLACEHOLDER_PROMPT = 'Give me my morning money briefing'

export function BriefingFocusControl({
  legacyPrompt,
  onSaved,
}: {
  /** A prompt typed into this widget before the box reached the server — kept, not lost. */
  legacyPrompt?: string
  onSaved: () => void
}) {
  const [value, setValue] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'saving' | 'error'>('idle')
  useEffect(() => {
    let live = true
    readBriefingFocus()
      .then((stored) => {
        if (!live) return
        const legacy = legacyPrompt?.trim()
        setValue(legacy && legacy !== OLD_PLACEHOLDER_PROMPT ? legacy : stored)
      })
      .catch(() => live && setValue(''))
    return () => {
      live = false
    }
  }, [legacyPrompt])

  const regenerate = async () => {
    if (value == null) return
    setStatus('saving')
    try {
      await saveBriefingFocus(value)
      onSaved()
      setStatus('idle')
    } catch {
      setStatus('error')
    }
  }

  return (
    <div className="ov-fs-ctl" key="prompt">
      <span className="ov-fs-ctl-l">Briefing instructions</span>
      <textarea
        className="ov-fs-prompt"
        value={value ?? ''}
        disabled={value == null}
        placeholder="What should Lucius focus on, and how long should it be?"
        onChange={(e) => setValue(e.target.value)}
      />
      <Text as="p" size="xs" tone={status === 'error' ? 'bad' : 'faint'}>
        {status === 'error'
          ? "Couldn't save — try again."
          : 'Lucius follows this every time it writes your briefing.'}
      </Text>
      <button
        type="button"
        className="ov-fs-regen"
        disabled={value == null || status === 'saving'}
        onClick={() => void regenerate()}
      >
        {status === 'saving' ? (
          'Regenerating…'
        ) : (
          <>
            <LuciusMark size="1em" aria-hidden="true" /> Regenerate
          </>
        )}
      </button>
    </div>
  )
}
