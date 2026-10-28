// A screen's first-visit introduction: a picture of what it holds, why it is worth using, and
// two ways to start (Lucius, or by yourself). A screen with several parts walks through them
// one step at a time, with its progress shown; the two ways in come on the last step.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Check } from 'lucide-react'
import { Button, Modal } from '../ui'
import { Spark } from '../../lucius/identity'
import { guidedThisVisit, inFirstRun, markGuided, modalOpen, useTourShowing } from './tourTarget'
import './intro.css'

export interface IntroPoint {
  /** The benefit, read first. */
  title: string
  body: string
}

export interface IntroStep {
  title: string
  /** Drawn above the points. */
  hero?: ReactNode
  /** The hero already shows the title, so it is not repeated below it. */
  heroCarriesTitle?: boolean
  points: IntroPoint[]
}

export interface FeatureIntroProps {
  open: boolean
  steps: IntroStep[]
  /** Hand the setup to Lucius. */
  lucius: { label: string; onClick: () => void }
  /** Do it yourself. */
  secondary: { label: string; onClick: () => void }
  onClose: () => void
}

// Polled, as the tours wait for a modal: dialogs open and close outside React's view of this one.
function useNoModal(watching: boolean): boolean {
  const [clear, setClear] = useState(() => !modalOpen())
  useEffect(() => {
    if (!watching) return
    const check = () => setClear(!modalOpen())
    check()
    const timer = setInterval(check, 700)
    return () => clearInterval(timer)
  }, [watching])
  return clear
}

export function FeatureIntro({ open, steps, lucius, secondary, onClose }: FeatureIntroProps) {
  const [i, setI] = useState(0)
  // Never on top of a tour or tip, and never a second guide in one visit: it waits, unseen,
  // for a visit where it is the first (see `guidedThisVisit`).
  const touring = useTourShowing()
  const [claimed, setClaimed] = useState(false)
  // Nor on top of a dialog the user already has open (a statement import, say): an intro there
  // left that dialog inert under it. It waits for the dialog to close.
  const clear = useNoModal(open && !touring && !claimed)
  const shown = open && !touring && !inFirstRun() && (claimed || (clear && !guidedThisVisit()))
  useEffect(() => {
    if (shown && !claimed) {
      markGuided()
      setClaimed(true)
    }
  }, [shown, claimed])
  if (!shown) return null
  const step = steps[Math.min(i, steps.length - 1)]!
  const last = i >= steps.length - 1
  return (
    <Modal
      open={open}
      onClose={onClose}
      // The dialog's accessible name; shown in or below the hero instead of in the header.
      title={step.title}
      size="md"
      className="fi-modal"
      footer={
        <div className="fi-foot">
          {steps.length > 1 && (
            <span className="fi-progress">
              <span className="ui-visually-hidden">
                Step {i + 1} of {steps.length}
              </span>
              {steps.map((s, n) => (
                <span
                  key={s.title}
                  aria-hidden="true"
                  className={n === i ? 'fi-progress-dot fi-progress-dot--on' : 'fi-progress-dot'}
                />
              ))}
            </span>
          )}
          <span className="fi-actions">
            {i > 0 && (
              <Button size="sm" variant="ghost" onClick={() => setI((n) => n - 1)}>
                Back
              </Button>
            )}
            {last ? (
              <>
                <Button size="sm" variant="secondary" onClick={secondary.onClick}>
                  {secondary.label}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  className="fi-lucius"
                  data-autofocus
                  iconLeft={<Spark size={15} ariaHidden />}
                  onClick={lucius.onClick}
                >
                  {lucius.label}
                </Button>
              </>
            ) : (
              <Button size="sm" variant="primary" data-autofocus onClick={() => setI((n) => n + 1)}>
                Next
              </Button>
            )}
          </span>
        </div>
      }
    >
      {step.hero && <div className="fi-hero">{step.hero}</div>}
      {!step.heroCarriesTitle && (
        <p className="fi-title" aria-hidden="true">
          {step.title}
        </p>
      )}
      <ul className="fi-why">
        {step.points.map((p) => (
          <li key={p.title}>
            <Check size={15} strokeWidth={2.2} aria-hidden="true" />
            <span>
              <b>{p.title}</b> {p.body}
            </span>
          </li>
        ))}
      </ul>
    </Modal>
  )
}
