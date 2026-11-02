import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { useModels } from '../../../hooks/useModels'
import {
  FormField,
  ModelPicker,
  PasswordField,
  Select,
  Stack,
  Text,
  TextField,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'

// Voice calls over /api/voice/config (the `voice_calls` flag). Two layers on a call: a fast
// FRONT voice holds the conversation, Lucius does the work behind it (ADR
// 2026-11-03-voice-calls-are-a-fast-front-voice-over-lucius). The LiveKit key and secret are
// write-only like every connector secret: blank keeps the saved one, the eye reads it back.
// Like Telegram's form, this one passes `enabled` through — Pause/Resume on the card owns it.

interface WorkerStatus {
  flag_on: boolean
  libraries_installed: boolean
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
  lucius_model: string
  language: string
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
  lucius_model: string
  language: string
}

// LiveKit Cloud's built-in models — no extra key. The front voice must answer in about a
// second; Flash-Lite was the fastest in testing and the only one that asked "go ahead with
// what?" rather than inventing an instruction from a bare "sure".
const FRONT_OPTIONS = [
  { value: 'google/gemini-2.5-flash-lite', label: 'Gemini 2.5 Flash-Lite (fastest, recommended)' },
  { value: 'google/gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
  { value: 'openai/gpt-4.1-mini', label: 'GPT-4.1 mini' },
  { value: 'openai/gpt-4.1-nano', label: 'GPT-4.1 nano' },
]
// Codes as LiveKit Inference takes them; mirrors backend LANGUAGES (db/voice_config_store.py).
const LANGUAGE_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'hi', label: 'Hindi' },
  { value: 'id', label: 'Indonesian' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'pt', label: 'Portuguese' },
]
/** Hears English only — refused beside any other language (backend ENGLISH_ONLY_STT). */
const ENGLISH_ONLY_STT = new Set(['assemblyai/universal-streaming'])
const STT_OPTIONS = [
  { value: 'auto', label: 'Automatic (the best model for your language)' },
  { value: 'assemblyai/universal-streaming', label: 'AssemblyAI Universal (English)' },
  {
    value: 'assemblyai/universal-streaming-multilingual',
    label: 'AssemblyAI Universal (multilingual)',
  },
  { value: 'deepgram/nova-3', label: 'Deepgram Nova-3' },
]
const TTS_OPTIONS = [
  { value: 'cartesia/sonic-3', label: 'Cartesia Sonic 3' },
  { value: 'cartesia/sonic-3.6', label: 'Cartesia Sonic 3.6' },
  { value: 'elevenlabs/eleven_flash_v2_5', label: 'ElevenLabs Flash 2.5' },
]

function formOf(c: VoiceConfig): VoiceForm {
  return {
    livekit_url: c.livekit_url || '',
    api_key: '',
    api_secret: '',
    front_model: c.front_model,
    stt_model: c.stt_model,
    tts_model: c.tts_model,
    tts_voice: c.tts_voice || '',
    lucius_model: c.lucius_model || '',
    language: c.language || 'en',
  }
}

function workerLine(c: VoiceConfig): { tone: 'faint' | 'warn' | 'good'; text: string } {
  if (c.worker.running)
    return { tone: 'good', text: 'Ready for calls. Press Call in Lucius’s chat.' }
  if (!c.worker.libraries_installed)
    return {
      tone: 'warn',
      text: 'The voice libraries aren’t installed on this machine: run pip install -r requirements-voice.txt, then restart.',
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
  const { models } = useModels(true)

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
        lucius_model: form.lucius_model,
        language: form.language,
      }
      if (form.api_key.trim()) body.api_key = form.api_key.trim()
      if (form.api_secret.trim()) body.api_secret = form.api_secret.trim()
      const r = await authFetch('/api/voice/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the Voice calls settings.')
        return false
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
    setForm(
      (f) =>
        f && {
          ...f,
          language,
          stt_model: language !== 'en' && ENGLISH_ONLY_STT.has(f.stt_model) ? 'auto' : f.stt_model,
        },
    )
  const status = workerLine(cfg)

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
        <Select
          id="conn-voice-front"
          value={form.front_model}
          options={FRONT_OPTIONS}
          onChange={(e) => set('front_model', e.target.value)}
        />
      </FormField>
      <FormField
        label="Lucius on calls"
        help="The model Lucius answers with during a call. A faster one keeps the waits short."
      >
        <ModelPicker
          size="field"
          label="Lucius on calls"
          models={models}
          value={form.lucius_model}
          defaultLabel="Same as the conversation"
          onChange={(next) => set('lucius_model', next)}
        />
      </FormField>
      <FormField
        label="Language"
        htmlFor="conn-voice-language"
        help="What you'll speak on calls. Lucius hears it, answers in it, and his voice speaks it."
      >
        <Select
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
          form.language !== 'en' && ENGLISH_ONLY_STT.has(form.stt_model)
            ? 'This model hears English only. Pick Automatic.'
            : undefined
        }
      >
        <Select
          id="conn-voice-stt"
          value={form.stt_model}
          options={STT_OPTIONS}
          onChange={(e) => set('stt_model', e.target.value)}
        />
      </FormField>
      <FormField label="Voice" htmlFor="conn-voice-tts">
        <Select
          id="conn-voice-tts"
          value={form.tts_model}
          options={TTS_OPTIONS}
          onChange={(e) => set('tts_model', e.target.value)}
        />
      </FormField>
      <FormField
        label="Voice id"
        htmlFor="conn-voice-voice"
        help="Optional: a voice id from the provider. Blank uses its default voice."
      >
        <TextField
          id="conn-voice-voice"
          value={form.tts_voice}
          onChange={(e) => set('tts_voice', e.target.value)}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
