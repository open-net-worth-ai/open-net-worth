import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { FormField, PasswordField, Stack, Text, TextField, useSecretReveal } from '../../ui'
import { restartCall } from '../../chat/lucius/voiceCall'
import { page, saveNeedsReload } from '../../chat/lucius/voicePagePolicy'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { Badge } from '../../ui/Badge'
import {
  sttHears,
  DEFAULT_NOISE_FILTER,
  freeMinutesEstimate,
  LANGUAGE_OPTIONS,
  languagePatch,
  NOISE_FILTER_OPTIONS,
  noiseFilterHelp,
  onLiveKitCloud,
  voiceCostLine,
  voiceIdAfterChange,
  voiceOptions,
} from './voiceOptions'
import { FieldSelect, VoicePicker } from './VoicePicker'

// Voice calls over /api/voice/config (the `voice_calls` flag). Two layers on a call: a fast
// FRONT voice holds the conversation, Lucius does the work behind it (ADR
// 2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius). The LiveKit key and secret are
// write-only like every connector secret: blank keeps the saved one, the eye reads it back.
// Like Telegram's form, this one passes `enabled` through — Pause/Resume on the card owns it.

interface WorkerStatus {
  flag_on: boolean
  libraries_installed: boolean
  /** Why calls can't run here for want of the voice libraries; "" when installed. */
  libraries_problem?: string
  running: boolean
  error: string
}

interface VoiceConfig {
  enabled: boolean
  ready: boolean
  livekit_url: string
  front_model: string
  stt_model: string
  tts_model: string
  tts_voice: string
  language: string
  noise_filter: string
  has_api_key: boolean
  has_api_secret: boolean
  worker: WorkerStatus
}

interface VoiceForm {
  livekit_url: string
  api_key: string
  api_secret: string
  front_model: string
  stt_model: string
  tts_model: string
  tts_voice: string
  language: string
  noise_filter: string
}

// LiveKit Cloud's built-in models — no extra key. The front voice must answer in about a
// second. Gemma is what LiveKit names in place of the retired Gemini 2.5 models; in testing it
// asked what a bare "sure" was agreeing to rather than inventing an instruction.
const FRONT_OPTIONS = [
  { value: 'google/gemma-4-31b-it', label: 'Gemma 4 (recommended)' },
  { value: 'openai/gpt-4.1-mini', label: 'GPT-4.1 mini' },
  { value: 'openai/gpt-4.1-nano', label: 'GPT-4.1 nano' },
]
const STT_OPTIONS = [
  { value: 'auto', label: 'Automatic (hears every language, expects finance words)' },
  { value: 'assemblyai/universal-streaming', label: 'AssemblyAI Universal (English)' },
  {
    value: 'assemblyai/universal-streaming-multilingual',
    label: 'AssemblyAI Universal (multilingual)',
  },
  { value: 'deepgram/nova-3', label: 'Deepgram Nova-3' },
]

function withVoiceModel(f: VoiceForm, tts_model: string) {
  return { tts_model, tts_voice: voiceIdAfterChange(f.tts_model, tts_model, f.tts_voice) }
}

function formOf(c: VoiceConfig): VoiceForm {
  return {
    livekit_url: c.livekit_url || '',
    api_key: '',
    api_secret: '',
    front_model: c.front_model,
    stt_model: c.stt_model,
    tts_model: c.tts_model,
    tts_voice: c.tts_voice || '',
    language: c.language || 'en',
    noise_filter: c.noise_filter || DEFAULT_NOISE_FILTER,
  }
}

function workerLine(c: VoiceConfig): { tone: 'faint' | 'warn' | 'good'; text: string } {
  if (c.worker.running)
    return { tone: 'good', text: 'Ready for calls. Press Call in Lucius’s chat.' }
  if (!c.worker.libraries_installed)
    return {
      tone: 'warn',
      text:
        c.worker.libraries_problem ||
        'The voice libraries are missing from this install. Reinstall Open Net Worth, then restart.',
    }
  if (c.worker.error) return { tone: 'warn', text: c.worker.error }
  if (!c.ready)
    return { tone: 'faint', text: 'Fill in your LiveKit project below to start taking calls.' }
  return { tone: 'faint', text: 'Paused. Use Resume on the connector card.' }
}

export function VoiceSection() {
  const [cfg, setCfg] = useState<VoiceConfig | null>(null)
  const [form, setForm] = useState<VoiceForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/voice/config')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: VoiceConfig) => {
        setCfg(c)
        setForm(formOf(c))
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.has_api_key ? cfg.enabled : true,
        livekit_url: form.livekit_url.trim(),
        front_model: form.front_model,
        stt_model: form.stt_model,
        tts_model: form.tts_model,
        tts_voice: form.tts_voice.trim(),
        language: form.language,
        noise_filter: form.noise_filter,
      }
      if (form.api_key.trim()) body.api_key = form.api_key.trim()
      if (form.api_secret.trim()) body.api_secret = form.api_secret.trim()
      const put = () =>
        authFetch('/api/voice/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
      let r!: Response
      // A call reads these once, when it starts: a new one restarts a call that's on.
      if (form.language !== cfg.language || form.noise_filter !== cfg.noise_filter) {
        await restartCall(async () => {
          r = await put()
        })
      } else {
        r = await put()
      }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the Voice calls settings.')
        return false
      }
      // This page's security policy still names the old server: without a reload, Call is refused.
      if (saveNeedsReload(cfg.livekit_url, form.livekit_url)) {
        page.reload()
        return true
      }
      setForm((f) => f && { ...f, api_key: '', api_secret: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setKey = useCallback((v: string) => setForm((f) => f && { ...f, api_key: v }), [])
  const setSecret = useCallback((v: string) => setForm((f) => f && { ...f, api_secret: v }), [])
  const key = useSecretReveal({
    saved: !!cfg?.has_api_key,
    value: form?.api_key ?? '',
    onChange: setKey,
    reveal: useCallback(() => revealConnectorSecret('voice', 'api_key'), []),
  })
  const secret = useSecretReveal({
    saved: !!cfg?.has_api_secret,
    value: form?.api_secret ?? '',
    onChange: setSecret,
    reveal: useCallback(() => revealConnectorSecret('voice', 'api_secret'), []),
  })
  const urlTouched = useTouched()
  const urlValid = !form || /^wss?:\/\/\S+$/.test(form.livekit_url.trim())
  const keysValid =
    !form ||
    ((!!form.api_key.trim() || !!cfg?.has_api_key) &&
      (!!form.api_secret.trim() || !!cfg?.has_api_secret))

  useConnectorSave({
    ready: !!form && !!cfg && urlValid && keysValid,
    values: form && {
      ...form,
      api_key: unrevealed(form.api_key, key),
      api_secret: unrevealed(form.api_secret, secret),
    },
    baseline: cfg ? formOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Voice calls settings.
      </Text>
    )
  }
  if (!cfg || !form) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const set = <K extends keyof VoiceForm>(k: K, v: VoiceForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })
  // Another language beside a model that hears only English would mishear every word:
  // move to Automatic with it (the owner can still pick another model that hears it).
  const pickLanguage = (language: string) =>
    setForm((f) => f && { ...f, language, ...languagePatch(f, language) })
  const pickVoice = (model: string) => setForm((f) => f && { ...f, ...withVoiceModel(f, model) })
  const status = workerLine(cfg)
  const onCloud = onLiveKitCloud(form.livekit_url)
  const estimate = onCloud ? freeMinutesEstimate(form) : undefined

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Talk to Lucius out loud. A fast voice keeps the conversation going while Lucius does the
        real work, with the same tools, permissions and approval cards as the chat. Calls run on
        your own{' '}
        <a href="https://cloud.livekit.io" target="_blank" rel="noreferrer">
          LiveKit
        </a>{' '}
        project: create one, then copy its URL and an API key here.
      </Text>
      <Text size="xs" tone={status.tone}>
        {status.text}
      </Text>

      <FormField
        label="LiveKit URL"
        htmlFor="conn-voice-url"
        required
        help="From your LiveKit project's settings, e.g. wss://my-project.livekit.cloud"
        error={
          urlTouched.touched && !urlValid
            ? 'Starts with wss:// (or ws:// for a server on this machine).'
            : undefined
        }
      >
        <TextField
          id="conn-voice-url"
          value={form.livekit_url}
          placeholder="wss://my-project.livekit.cloud"
          onChange={(e) => set('livekit_url', e.target.value)}
          onBlur={urlTouched.onBlur}
        />
      </FormField>
      <FormField
        label="API key"
        htmlFor="conn-voice-key"
        required
        help={key.caption || 'Starts with API…'}
        error={key.error}
      >
        <PasswordField
          id="conn-voice-key"
          value={form.api_key}
          placeholder={cfg.has_api_key ? '••••••••' : 'API…'}
          onChange={(e) => set('api_key', e.target.value)}
          {...key.fieldProps}
        />
      </FormField>
      <FormField
        label="API secret"
        htmlFor="conn-voice-secret"
        required
        help={secret.caption}
        error={secret.error}
      >
        <PasswordField
          id="conn-voice-secret"
          value={form.api_secret}
          placeholder={cfg.has_api_secret ? '••••••••' : 'secret'}
          onChange={(e) => set('api_secret', e.target.value)}
          {...secret.fieldProps}
        />
      </FormField>

      <FormField
        label="Front voice"
        htmlFor="conn-voice-front"
        help="Holds the conversation and answers in about a second. It never sees your data and never states a number: Lucius's answers are read out word for word."
      >
        <FieldSelect
          id="conn-voice-front"
          value={form.front_model}
          options={FRONT_OPTIONS}
          onChange={(e) => set('front_model', e.target.value)}
        />
      </FormField>
      <FormField
        label={
          <span className="lx-beta-label">
            Language <Badge>Beta</Badge>
          </span>
        }
        htmlFor="conn-voice-language"
        help="What you'll speak on calls. Lucius hears it, answers in it, and his voice speaks it."
      >
        <FieldSelect
          id="conn-voice-language"
          value={form.language}
          options={LANGUAGE_OPTIONS}
          onChange={(e) => pickLanguage(e.target.value)}
        />
      </FormField>
      <FormField
        label="Speech to text"
        htmlFor="conn-voice-stt"
        error={
          !sttHears(form.stt_model, form.language)
            ? "This model doesn't hear your language. Pick Automatic."
            : undefined
        }
      >
        <FieldSelect
          id="conn-voice-stt"
          value={form.stt_model}
          options={STT_OPTIONS}
          onChange={(e) => set('stt_model', e.target.value)}
        />
      </FormField>
      {onCloud && (
        <FormField
          label="Noise cancelling"
          htmlFor="conn-voice-noise"
          help={noiseFilterHelp(form.noise_filter)}
        >
          <FieldSelect
            id="conn-voice-noise"
            value={form.noise_filter}
            options={NOISE_FILTER_OPTIONS}
            onChange={(e) => set('noise_filter', e.target.value)}
          />
        </FormField>
      )}
      <FormField label="Voice model" htmlFor="conn-voice-tts" help={voiceCostLine(form.tts_model)}>
        <FieldSelect
          id="conn-voice-tts"
          value={form.tts_model}
          options={voiceOptions(form.language, form.tts_model)}
          onChange={(e) => pickVoice(e.target.value)}
        />
      </FormField>
      <VoicePicker
        key={form.tts_model}
        model={form.tts_model}
        voiceId={form.tts_voice}
        onChange={(voiceId) => set('tts_voice', voiceId)}
      />
      {estimate && (
        <Stack gap="--space-1">
          <Text size="sm">{estimate.headline}</Text>
          <Text size="xs" tone="faint">
            {estimate.detail}
          </Text>
        </Stack>
      )}

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
