import { useEffect, useState } from 'react'
import { Sparkles, X, RefreshCw, ArrowRight } from '../../ui/icons'
import { Button } from '../ui'
import { Spark } from '../../lucius/identity'
import { getCurrencySymbol } from '../../utils/format'
import { luciusSuggestAlerts, luciusReviewList } from '../../utils/luciusEngine'
import type {
  AlertSuggestion,
  LuciusAsset,
  LuciusContext,
  LuciusFinding,
  LuciusReviewItem,
} from '../../utils/luciusEngine'

// Lucius PROPOSES; the user disposes. None of these surfaces mutate watchlist
// state on their own — every suggestion is a card the user adds, edits, or skips.

function opSym(op: 'above' | 'below'): string {
  return op === 'above' ? '≥' : '≤'
}

// ── Draft / Rewrite link (instruction fields) ─────────────────────────────────

export function LuciusDraftLink({
  label = 'Draft with Lucius',
  onDraft,
}: {
  label?: string
  onDraft: () => void
}) {
  const [thinking, setThinking] = useState(false)
  function run() {
    setThinking(true)
    setTimeout(() => {
      setThinking(false)
      onDraft()
    }, 550)
  }
  return (
    <button type="button" className="intel-lx-draft" disabled={thinking} onClick={run}>
      <span className={thinking ? 'lx-pulse' : ''}>
        <Sparkles size={11} />
      </span>
      {thinking ? 'Drafting…' : label}
    </button>
  )
}

// ── Per-ticker alert suggestions (inside the Price Alerts modal) ───────────────

export function LuciusAlertSuggest({
  asset,
  ctx,
  onAdd,
}: {
  asset: LuciusAsset
  ctx: LuciusContext
  onAdd: (s: AlertSuggestion) => void
}) {
  const [phase, setPhase] = useState<'idle' | 'thinking' | 'shown'>('idle')
  const [items, setItems] = useState<AlertSuggestion[]>([])
  const sym = getCurrencySymbol(asset.ccy)

  function generate() {
    setPhase('thinking')
    setTimeout(() => {
      setItems(luciusSuggestAlerts(asset, ctx))
      setPhase('shown')
    }, 800)
  }
  function take(s: AlertSuggestion, i: number) {
    onAdd(s)
    setItems((its) => its.filter((_, j) => j !== i))
  }
  function dismiss(i: number) {
    setItems((its) => its.filter((_, j) => j !== i))
  }

  if (phase === 'idle') {
    return (
      <div className="lx-surface intel-lx-idle">
        <span className="lx-mark">
          <Sparkles size={13} />
        </span>
        <div className="intel-lx-idle-body">
          <div className="intel-lx-idle-title">Not sure what to set?</div>
          <div className="intel-lx-idle-sub">
            Lucius can suggest alert levels for {asset.symbol} from your position and target mix.
          </div>
        </div>
        <Button
          size="sm"
          variant="primary"
          className="fi-lucius intel-lx-suggest-cta"
          iconLeft={<Spark size={15} ariaHidden />}
          onClick={generate}
        >
          Suggest alerts
        </Button>
      </div>
    )
  }
  if (phase === 'thinking') {
    return (
      <div className="lx-surface">
        <div className="lx-thinking">
          <span className="lx-pulse">
            <Sparkles size={13} />
          </span>
          Reading {asset.symbol}'s price, your position, and target mix…
        </div>
      </div>
    )
  }
  return (
    <div className="lx-surface intel-lx-shown">
      <div className="intel-lx-shown-head">
        <span className="lx-mark lx-mark--sm">
          <Sparkles size={12} />
        </span>
        <span className="intel-lx-shown-title">
          {items.length
            ? `Lucius suggests ${items.length} alert${items.length === 1 ? '' : 's'}`
            : 'All suggestions handled'}
        </span>
        <div className="intel-lx-shown-actions">
          {items.length > 1 && (
            <button
              type="button"
              className="intel-lx-addall"
              onClick={() => {
                items.forEach(onAdd)
                setItems([])
              }}
            >
              Add all
            </button>
          )}
          <button
            type="button"
            className="intel-lx-regen"
            title="Regenerate"
            aria-label="Regenerate"
            onClick={generate}
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        </div>
      </div>
      {items.length === 0 ? (
        <div className="intel-lx-empty">
          Added what you need.{' '}
          <button type="button" className="intel-lx-more" onClick={generate}>
            Suggest more <ArrowRight size={13} aria-hidden="true" />
          </button>
        </div>
      ) : (
        <div className="intel-lx-sugs">
          {items.map((s, i) => (
            <div key={i} className="intel-lx-sug">
              <span className="intel-lx-sug-chip">
                {opSym(s.op)} {sym}
                {s.value.toLocaleString()}
              </span>
              <div className="intel-lx-sug-body">
                <div className="intel-lx-sug-why">{s.why}</div>
                <div className="intel-lx-sug-note">
                  <span className="intel-lx-sug-note-label">Instruction • </span>
                  {s.note}
                </div>
              </div>
              <div className="intel-lx-sug-actions">
                <button type="button" className="intel-lx-sug-add" onClick={() => take(s, i)}>
                  Add
                </button>
                <button type="button" className="intel-lx-sug-skip" onClick={() => dismiss(i)}>
                  Skip
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Whole-list review (inside the workspace list view) ─────────────────────────

export function LuciusReviewButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <Button
      size="sm"
      variant="primary"
      className="fi-lucius intel-lx-review-btn"
      iconLeft={<Spark size={15} ariaHidden />}
      aria-pressed={active}
      onClick={onClick}
    >
      Review with Lucius
    </Button>
  )
}

const SEV_COLOR: Record<LuciusFinding['sev'], string> = {
  gap: 'var(--warn)',
  risk: 'var(--bad)',
  info: 'var(--faint)',
}

export function LuciusReviewPanel({
  listName,
  listMuted,
  items,
  ctx,
  onFix,
  onClose,
}: {
  listName: string
  listMuted: boolean
  items: LuciusReviewItem[]
  ctx: LuciusContext
  onFix: (finding: LuciusFinding) => void
  onClose: () => void
}) {
  const [thinking, setThinking] = useState(true)
  const [result, setResult] = useState<ReturnType<typeof luciusReviewList> | null>(null)

  useEffect(() => {
    setThinking(true)
    const t = setTimeout(() => {
      setResult(luciusReviewList({ name: listName, muted: listMuted, items }, ctx))
      setThinking(false)
    }, 850)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listName])

  return (
    <div className="lx-surface intel-lx-review">
      {thinking || !result ? (
        <div className="lx-thinking">
          <span className="lx-pulse">
            <Sparkles size={13} />
          </span>
          Reviewing {listName} — coverage, concentration, and instructions…
        </div>
      ) : (
        <>
          <div className="intel-lx-review-head">
            <span className="lx-mark">
              <Sparkles size={13} />
            </span>
            <div className="intel-lx-review-headbody">
              <div className="intel-lx-review-eyebrow">Lucius review</div>
              <div className="intel-lx-review-headline">{result.headline}</div>
            </div>
            <button
              type="button"
              className="intel-lx-review-close"
              onClick={onClose}
              aria-label="Close review"
            >
              <X size={11} />
            </button>
          </div>
          {result.findings.map((f) => (
            <div key={f.key} className="intel-lx-finding">
              <span
                className="intel-lx-finding-dot"
                style={{ background: SEV_COLOR[f.sev] }}
                aria-hidden
              />
              <div className="intel-lx-finding-body">
                <div className="intel-lx-finding-title">{f.title}</div>
                <div className="intel-lx-finding-text">{f.text}</div>
              </div>
              {!f.noCta && f.cta && (
                <button type="button" className="intel-lx-finding-cta" onClick={() => onFix(f)}>
                  {f.cta}
                </button>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  )
}
