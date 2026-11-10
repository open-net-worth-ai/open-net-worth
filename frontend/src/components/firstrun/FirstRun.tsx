// First run — three screens that make the MACHINE ready, then hand off to Lucius, who makes
// the BOOK ready (design spec §5.1/§5.2).
//
// Deliberately NOT a <Modal>. The outgoing wizard floated over the Overview tab, whose own
// empty state was already running a competing onboarding router underneath it — and offering
// MORE options than the dialog covering it. Two disagreeing flows legible at once is the defect
// this shape exists to remove: first run owns the viewport, then gets out of the way.
//
// Completion is stamped SERVER-side (POST /api/setup-status/complete). The retired
// localStorage['onboarding-dismissed'] flag was per-browser, so first run reappeared on a
// second device and on a private window.

import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import { Text } from '../ui'
import { desktopBridge } from '../../platform/desktop'
import { StepGreeting } from './StepGreeting'
import { StepWelcome } from './StepWelcome'
import { StepLucius } from './StepLucius'
import { StepPassword } from './StepPassword'
import { StepRestore } from './StepRestore'
import { FirstRunMark } from './Mark'
import { defaultCurrencyFromLocale } from './currencies'
import { ArrowLeft } from '../../ui/icons'

const TITLES = ['Your wealth, kept privately.', 'Lucius, your wealth manager.', 'Secure the vault.']

// The greeting is NOT a step either — it is the pitch shown before anything is asked, routing
// to "Set up" (step 0) or straight to restore. Only the three screens after it are numbered.
const GREETING = -2

// Restore supplies the book and currency. Provider credentials and machine security still
// need setup afterward; they are deliberately absent from portable exports.
const RESTORE = -1

// The url segment for each screen. Setup is a sequence: its position belongs in the address
// bar, so a reload lands where you were and the browser's own Back walks it.
const STAGE_SLUGS = ['folder', 'model', 'password'] as const

// Step 2 has two halves and the browser's Back must see BOTH — the split lived in component
// state, so Back skipped the provider page and left step 2 entirely. `model` is the provider
// half; `model-pick` is choosing the model.
const MODEL_PICK_SLUG = 'model-pick'

export function stageToStep(stage: string | undefined): number {
  if (!stage) return GREETING
  if (stage === 'restore') return RESTORE
  if (stage === MODEL_PICK_SLUG) return 1
  const i = STAGE_SLUGS.indexOf(stage as (typeof STAGE_SLUGS)[number])
  return i >= 0 ? i : GREETING
}

export function stepToStage(step: number): string {
  if (step === RESTORE) return 'restore'
  return STAGE_SLUGS[step] ?? ''
}

/** Which half of step 2 a url names. */
export function stageToLuciusStage(stage: string | undefined): 'provider' | 'model' {
  return stage === MODEL_PICK_SLUG ? 'model' : 'provider'
}

/** Whether an instance default is already stored (`GET /api/model-config`). */
async function hasSavedModel(): Promise<boolean> {
  try {
    const res = await authFetch('/api/model-config')
    return res.ok && Boolean((await res.json())?.default_model)
  } catch {
    return false
  }
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

export interface FirstRunProps {
  onDone: () => void
  onAuthChanged?: () => void
  /** The url segment currently showing — needed because step 2 has two halves. */
  currentStage?: string
  restorePending?: boolean
  /** Behind an authenticating proxy there is no instance password to set — skip that step. */
  gatewayMode?: boolean
  /** Which screen to open on, from the url. Omitted (in tests) it starts at the greeting. */
  initialStep?: number
  /** Called whenever the screen changes, so the url can follow. */
  onStepChange?: (stage: string, opts?: { replace?: boolean }) => void
}

export function FirstRun({
  onDone,
  onAuthChanged,
  initialStep,
  onStepChange,
  currentStage,
  restorePending = false,
  gatewayMode = false,
}: FirstRunProps) {
  const titles = gatewayMode ? TITLES.slice(0, 2) : TITLES
  const desktop = Boolean(desktopBridge())
  // CONTROLLED by the url when the caller supplies both props, uncontrolled otherwise.
  //
  // The middle ground — local state plus an effect syncing it from the url — does not work,
  // and failed exactly as you would expect: clicking Back set the state, the url had not
  // updated yet, and the effect fired with the STALE prop and snapped the screen back. Two
  // writers for one fact always resolve into whichever fires last. The url is the fact; a
  // component rendered without a router (every test here) keeps its own copy instead.
  const controlled = initialStep !== undefined && onStepChange !== undefined

  const [localLuciusStage, setLocalLuciusStage] = useState<'provider' | 'model'>('provider')
  const luciusStage = controlled ? stageToLuciusStage(currentStage) : localLuciusStage
  const setLuciusStage = (next: 'provider' | 'model') => {
    if (controlled) {
      // Going back REPLACES: pushing an entry meant the browser's Back popped you straight
      // to the page you had just left, so Back bounced between the two halves forever.
      onStepChange!(next === 'model' ? MODEL_PICK_SLUG : 'model', {
        replace: next === 'provider',
      })
    } else setLocalLuciusStage(next)
  }
  const [localStep, setLocalStep] = useState(initialStep ?? GREETING)
  const step = controlled ? (initialStep ?? GREETING) : localStep

  const setStep = useCallback(
    (next: number, opts?: { replace?: boolean }) => {
      // Always ANNOUNCE. Only move the local copy when nothing else owns the position —
      // under the router the new url comes back as a prop, and moving here as well is the
      // second writer that made Back undo itself.
      if (!controlled) setLocalStep(next)
      onStepChange?.(stepToStage(next), opts)
    },
    [controlled, onStepChange],
  )

  const [currency, setCurrency] = useState(defaultCurrencyFromLocale())
  const [busy, setBusy] = useState(false)
  const [navigationError, setNavigationError] = useState<string | null>(null)
  const [restoredThisSession, setRestoredThisSession] = useState(false)
  const restored = restorePending || restoredThisSession
  // Step 2 has two halves (provider, then model). Owned here so the topbar's Back can unwind
  // the model half before leaving the step — pressing it there used to skip straight back to
  // the currency screen, past the page you had just come from.

  // The REAL resolved path, from the backend — a hardcoded "~/.open-net-worth" would be a
  // claim the server may not honour (DATA_DIR can point anywhere; a legacy install still runs
  // from inside the repo).
  const [dataDir, setDataDir] = useState<string | null>(null)
  const [dataSource, setDataSource] = useState<string | null>(null)
  const [dataLocation, setDataLocation] = useState<{
    has_book: boolean
    accounts: number | null
    transactions: number | null
    writable: boolean
  } | null>(null)
  // Named so the picker can re-run it after a folder is chosen — the row must show the new
  // path immediately, not on the next reload.
  const loadLocation = useCallback(() => {
    void authFetch('/api/setup-status/data-location')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d?.data_dir) return
        // The WORKING FOLDER is what the screen shows and what the picker starts from — an
        // instance is one folder holding data/, cache/, logs/ and backups/, and naming only
        // its data/ half invites someone to move that half on its own.
        // `suggested_root` is the default UNLESS this machine already runs an instance there,
        // in which case it names a free one beside it (open-net-worth-2). Proposing the folder
        // the first install is using would invite two apps onto one root.
        const shown = d.instance_root || d.data_dir
        setDataDir(shown)
        setDataSource(d.source ?? null)
        // Same folder, inspected — does it already hold a book?
        return authFetch(`/api/setup-status/inspect-path?path=${encodeURIComponent(shown)}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((loc) => {
            if (loc) setDataLocation(loc)
          })
      })
      .catch(() => {
        // Informational only — first run must work without it.
      })
  }, [])

  useEffect(() => {
    loadLocation()
  }, [loadLocation])

  async function saveCurrencyAndAdvance() {
    setBusy(true)
    try {
      await authFetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_currency: currency }),
      })
    } catch {
      // A failed currency save must not trap the user on screen 1 — Lucius confirms it, and
      // Settings can change it. Blocking here would strand first run on a transient error.
    } finally {
      setBusy(false)
    }
    setStep(1)
  }

  async function finish() {
    try {
      await authFetch('/api/setup-status/complete', { method: 'POST' })
    } catch {
      // The stamp is a convenience signal, never a gate. If it fails the user still gets into
      // the app; first run may reappear once, which is far better than being stuck in it.
    }
    onDone()
  }

  async function goBack() {
    setNavigationError(null)
    if (desktop && step === 0) {
      if (busy) return
      setBusy(true)
      try {
        const bridge = desktopBridge()
        if (!bridge?.showBookChoice) throw new Error('Book selection is unavailable.')
        await bridge.showBookChoice()
      } catch {
        setNavigationError('Could not return to book folders. Try again.')
      } finally {
        setBusy(false)
      }
      return
    }
    if (step === 1 && luciusStage === 'model') {
      setLuciusStage('provider')
      return
    }
    // Step 3 follows a SAVED model. Back to the provider page would show it unchosen — and
    // does, once the component has remounted — so go back to the page the user just left.
    if (step === 2 && (await hasSavedModel())) {
      if (controlled) onStepChange!(MODEL_PICK_SLUG, { replace: true })
      else {
        setLocalLuciusStage('model')
        setLocalStep(1)
      }
      return
    }
    setStep(step === RESTORE ? 0 : step === 0 ? GREETING : step - 1)
  }

  if (step === GREETING) {
    return (
      <div className="firstrun-shell">
        <StepGreeting onSetup={() => setStep(0)} gatewayMode={gatewayMode} />
      </div>
    )
  }

  return (
    <div className="firstrun-shell">
      <div className="firstrun-page">
        <div className="firstrun-topbar">
          <FirstRunMark />
          {step !== RESTORE && (
            <div className="firstrun-counter">
              <span className="firstrun-counter__current">{pad2(step + 1)}</span>
              <span className="firstrun-counter__rule" aria-hidden="true" />
              <span className="firstrun-counter__total">{pad2(titles.length)}</span>
              <span className="sr-only">
                Step {step + 1} of {titles.length}
              </span>
            </div>
          )}
          {!(restored && step === 1 && luciusStage === 'provider') && (
            <button
              type="button"
              className="firstrun-back"
              disabled={desktop && step === 0 && busy}
              onClick={() => void goBack()}
            >
              <ArrowLeft size={14} aria-hidden="true" /> Back
            </button>
          )}
        </div>

        <div className="firstrun-content">
          <div className="firstrun-step">
            <div className="firstrun-bar" aria-hidden="true" />
            <h1 className="firstrun-headline">
              {step === RESTORE
                ? 'Restore a backup'
                : desktop && step === 0
                  ? 'Choose your app currency.'
                  : titles[step]}
            </h1>

            {navigationError && (
              <div role="alert">
                <Text as="p" tone="bad">
                  {navigationError}
                </Text>
              </div>
            )}

            {step === RESTORE && (
              <StepRestore
                onDone={() => {
                  setRestoredThisSession(true)
                  setStep(1, { replace: true })
                }}
                onBack={() => setStep(0)}
              />
            )}

            {step === 0 && (
              <StepWelcome
                currency={currency}
                onCurrencyChange={setCurrency}
                dataDir={dataDir}
                dataSource={dataSource}
                dataLocation={dataLocation}
                onLocationChanged={loadLocation}
                busy={busy}
                gatewayMode={gatewayMode}
                onNext={() => void saveCurrencyAndAdvance()}
                onRestore={() => setStep(RESTORE)}
              />
            )}
            {step === 1 && (
              <StepLucius
                stage={luciusStage}
                onStageChange={setLuciusStage}
                gatewayMode={gatewayMode}
                onDone={() => (gatewayMode ? void finish() : setStep(2))}
              />
            )}
            {step === 2 && (
              <StepPassword onDone={() => void finish()} onAuthChanged={onAuthChanged} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
