// First run, screen 00 — the greeting. Shown before anything is asked: what this is, what it
// costs (nothing — no account, no upload, no telemetry), and the two ways in. It is NOT a
// numbered step (design spec: three setup screens stay 1–3) — its only job is to route to
// "Set up" (the existing step 0), so it owns no state itself.
//
// Restoring a backup lives on the folder step (StepWelcome), after you have chosen where the
// book lives — owner decision 2026-10-03; a restore needs that folder first.

import { Button } from '../ui'
import { FirstRunMark } from './Mark'

export interface StepGreetingProps {
  onSetup: () => void
  /** Behind an authenticating proxy: no local password, and the host owns where files live. */
  gatewayMode?: boolean
  /** Private Cloud already connected Open Net Worth AI: there is no model to choose. */
  managedAi?: boolean
}

const NEXT_STEPS = [
  {
    // Leads with the DECISION. The folder is no longer a choice made here (it is the default
    // or ONW_ROOT), so promising "where your data lives" set up an expectation the screen
    // could not meet — it states the folder, it does not ask about it.
    title: 'The currency you think in',
    body: 'Plus where your files live on this machine.',
  },
  {
    title: 'Bring your own model',
    body: 'Claude Code, Codex, an API key, or Ollama running locally.',
  },
  {
    title: 'A password, if you want one',
    body: 'Only matters if you will open this from another device.',
  },
]

export function StepGreeting({
  onSetup,
  gatewayMode = false,
  managedAi = false,
}: StepGreetingProps) {
  const nextSteps = managedAi
    ? [{ ...NEXT_STEPS[0]!, body: 'The one your totals are shown in.' }]
    : gatewayMode
      ? [
          { ...NEXT_STEPS[0]!, body: 'The one your totals are shown in.' },
          { ...NEXT_STEPS[1]!, body: 'Claude Code, Codex, or an API key.' },
        ]
      : NEXT_STEPS
  return (
    <div className="firstrun-greeting">
      <div className="firstrun-greeting__col firstrun-greeting__col--left">
        <FirstRunMark size="lg" />
        {/* No "Welcome" eyebrow here: it sets up "...to what?" and the headline answers a
            different question, so it reads as a fragment — and the mark above already
            names the app. Warmth belongs in the sentence below, not in a label. */}
        <h1 className="firstrun-headline" style={{ marginTop: 'var(--space-8)' }}>
          An open-source agent harness for personal finance.
        </h1>
        <p className="firstrun-lead" style={{ maxWidth: 428 }}>
          Not a chatbot: it works your real ledger. Banks and brokerages that sync themselves,
          automations that run while you sleep, skills you write — and every write permissioned,
          audited and reversible.
        </p>

        <div className="firstrun-greeting__actions">
          <Button variant="primary" onClick={onSetup}>
            Get started
          </Button>
        </div>
      </div>

      <div className="firstrun-greeting__col firstrun-greeting__col--right">
        <div className="firstrun-eyebrow firstrun-eyebrow--muted">What happens next</div>
        <div className="firstrun-next">
          {nextSteps.map((s, i) => (
            <div className="firstrun-next__item" key={s.title}>
              <span className="firstrun-next__num">{String(i + 1).padStart(2, '0')}</span>
              <div>
                <div className="firstrun-next__title">{s.title}</div>
                <div className="firstrun-next__body">{s.body}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="firstrun-foot">
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <rect x="3" y="11" width="18" height="10" rx="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
          <span>
            {gatewayMode
              ? 'No telemetry. Your data stays on your own server.'
              : 'No account, no upload, no telemetry. Your data stays yours.'}
          </span>
        </div>
      </div>
    </div>
  )
}
