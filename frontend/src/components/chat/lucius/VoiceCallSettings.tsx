import { useRef, useState } from 'react'
import { ChevronsUpDown, Settings, X } from 'lucide-react'
import { authFetch } from '../../../api/client'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import { invalidateQueryCache } from '../../../state/queryCache'
import {
  DEFAULT_VOICE_MODEL,
  LANGUAGE_OPTIONS,
  languagePatch,
  OTHER_VOICE,
  speakerChoice,
  speakerOptions,
} from '../../tabs/connectors/voiceOptions'
import { useVoiceReadiness, VOICE_CONFIG_KEY } from './VoiceCallButton'
import { restartCall } from './voiceCall'

// The gear on the full call screen, and the sheet it opens (owner, 2026-11-06). Open from the moment
// the call starts: you may want another language before Lucius has said a word. A call reads these
// once, when it starts, so a change restarts it: hang up, save, call back into the same conversation.

interface Option {
  value: string
  label: string
}

function SettingRow({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
}) {
  return (
    <label className="lx-callsettings-row">
      <span>{label}</span>
      <span className="lx-callsettings-value">
        {options.find((o) => o.value === value)?.label ?? value}
        <ChevronsUpDown size={16} aria-hidden="true" />
      </span>
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function VoiceCallSettings({ ended }: { ended: boolean }) {
  const config = useVoiceReadiness()
  const [open, setOpen] = useState(false)
  const sheet = useRef<HTMLDivElement>(null)
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: sheet,
    onDismiss: () => setOpen(false),
    enabled: open,
  })
  if (!config) return null

  const language = config.language ?? 'en'
  const model = config.tts_model ?? DEFAULT_VOICE_MODEL
  const voice = speakerChoice(model, config.tts_voice ?? '', false)
  const voices = speakerOptions(model).filter((o) => o.value !== OTHER_VOICE)
  // An id typed in Voice calls settings isn't on the menu, and a select can't show what it lacks.
  if (voice === OTHER_VOICE) voices.push({ value: OTHER_VOICE, label: 'Your own voice' })

  const save = (patch: Record<string, string>) => {
    setOpen(false)
    void restartCall(async () => {
      const r = await authFetch('/api/voice/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Could not change the voice settings.')
      }
      invalidateQueryCache(VOICE_CONFIG_KEY)
    })
  }

  return (
    <>
      <button
        type="button"
        className="lx-callstage-min"
        data-ended={ended || undefined}
        tabIndex={ended ? -1 : undefined}
        aria-label="Voice settings"
        title="Voice settings"
        onClick={() => setOpen(true)}
      >
        <Settings size={20} />
      </button>
      {open && !ended && (
        <div
          className="lx-callsettings"
          onKeyDown={(e) => {
            if (e.key !== 'Escape') return
            e.stopPropagation()
            setOpen(false)
          }}
        >
          <button
            type="button"
            className="lx-callsettings-scrim"
            aria-label="Close voice settings"
            tabIndex={-1}
            onClick={() => setOpen(false)}
          />
          <div
            ref={sheet}
            className="lx-callsettings-sheet"
            role="dialog"
            aria-label="Voice settings"
            {...dragHandlers}
          >
            <div className="lx-callsettings-handle" aria-hidden="true" />
            <div className="lx-callsettings-head">
              <button
                type="button"
                className="lx-callstage-min"
                aria-label="Close"
                onClick={() => setOpen(false)}
              >
                <X size={20} />
              </button>
              <h2>Voice settings</h2>
            </div>
            <SettingRow
              label="Language"
              value={language}
              options={LANGUAGE_OPTIONS}
              onChange={(next) => {
                if (next !== language) save({ language: next, ...languagePatch(config, next) })
              }}
            />
            <SettingRow
              label="Voice"
              value={voice}
              options={voices}
              onChange={(next) => {
                if (next !== voice) save({ tts_voice: next })
              }}
            />
          </div>
        </div>
      )}
    </>
  )
}
