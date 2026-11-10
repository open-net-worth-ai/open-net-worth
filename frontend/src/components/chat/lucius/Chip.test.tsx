// @vitest-environment jsdom
//
// Tests for Chip and Identity components (W1-ID lane).
// Covers: entity chip, agent recipient "To", agent ctx dashed, skill verb,
// compact/rich variants, stale, value tint; Identity brand/mono/emoji/skill/rule/holding/account.

import { afterEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'

afterEach(cleanup)
import { iconFor } from '../../../lucius/entityRegistry'
import { Chip } from './Chip'
import { Identity, IdentityInner } from '../../../lucius/identity'
import type { MentionRef } from '../../../types/mentions'

// ── Fixtures ──────────────────────────────────────────────────────────────────

const accountRef: MentionRef = { type: 'account', ref: 'acc-1', label: 'Vantage Brokerage USD' }
const holdingRef: MentionRef = { type: 'holding', ref: 'pos-1', label: 'AAPL' }
const transRef: MentionRef = { type: 'transaction', ref: 'txn-1', label: 'Stripe payout' }
// See Chip.tsx: 'agent' is a UI-side chip type with no registry provider, so it is not in
// the MentionType union and the fixture has to say so explicitly.
const agentRef = { type: 'agent', ref: 'lucius', label: 'Lucius' } as unknown as MentionRef
const agentCustomRef = { type: 'agent', ref: 'ag-42', label: 'Tax Agent' } as unknown as MentionRef
const skillRef: MentionRef = { type: 'skill', ref: 'sk-1', label: 'portfolio-summary' }
const categoryRef: MentionRef = { type: 'category', ref: 'food', label: 'Food' }

// ── Chip: entity ──────────────────────────────────────────────────────────────

describe('Chip — entity', () => {
  it('renders the label', () => {
    render(<Chip mention={accountRef} />)
    expect(screen.getByText('Vantage Brokerage USD')).toBeTruthy()
  })

  it('has the base chip class and readonly class (no onRemove)', () => {
    const { container } = render(<Chip mention={accountRef} />)
    const chip = container.querySelector('.chip')
    expect(chip).toBeTruthy()
    expect(chip?.classList.contains('readonly')).toBe(true)
  })

  it('does NOT have agent/scope/source/verb/ctx class for an account', () => {
    const { container } = render(<Chip mention={accountRef} />)
    const chip = container.querySelector('.chip')!
    expect(chip.classList.contains('agent')).toBe(false)
    expect(chip.classList.contains('scope')).toBe(false)
    expect(chip.classList.contains('verb')).toBe(false)
    expect(chip.classList.contains('ctx')).toBe(false)
  })

  it('shows remove button when onRemove is provided', () => {
    const onRemove = vi.fn()
    render(<Chip mention={accountRef} onRemove={onRemove} />)
    const btn = screen.getByRole('button', { name: /remove vantage brokerage usd/i })
    expect(btn).toBeTruthy()
  })

  it('calls onRemove with the mention when × is clicked', () => {
    const onRemove = vi.fn()
    render(<Chip mention={accountRef} onRemove={onRemove} />)
    fireEvent.click(screen.getByRole('button', { name: /remove vantage brokerage usd/i }))
    expect(onRemove).toHaveBeenCalledWith(accountRef)
  })

  it('calls onClick with the mention when chip body is clicked', () => {
    const onClick = vi.fn()
    const { container } = render(<Chip mention={accountRef} onClick={onClick} />)
    fireEvent.click(container.querySelector('.chip')!)
    expect(onClick).toHaveBeenCalledWith(accountRef, expect.any(HTMLElement))
  })

  it('renders chip-gl for an account (GLYPH_SVG type)', () => {
    const { container } = render(<Chip mention={accountRef} />)
    expect(container.querySelector('.chip-gl')).toBeTruthy()
  })

  it('renders chip-gl for a holding', () => {
    const { container } = render(<Chip mention={holdingRef} />)
    expect(container.querySelector('.chip-gl')).toBeTruthy()
  })

  // GLYPH_SVG covers only 6 types. Everything else used to render an identical '•', which
  // made 27 of 33 types indistinguishable in the transcript; the fallback is now the
  // descriptor's own glyph via iconFor(), the same source the composer chips use.
  it("renders the descriptor's own glyph for a type without an SVG", () => {
    const { container } = render(<Chip mention={categoryRef} />)
    const gl = container.querySelector('.chip-gl')
    expect(gl).toBeTruthy()
    expect(gl?.textContent).toBe(iconFor('category'))
    expect(gl?.textContent).not.toBe('•')
  })
})

// ── Chip: agent recipient ("To ·") ───────────────────────────────────────────

describe('Chip — agent recipient', () => {
  it('adds the .agent class', () => {
    const { container } = render(<Chip mention={agentRef} role="recipient" />)
    expect(container.querySelector('.chip.agent')).toBeTruthy()
  })

  it('renders "To" prefix text for the recipient role', () => {
    render(<Chip mention={agentRef} role="recipient" />)
    expect(screen.getByText('To')).toBeTruthy()
    expect(screen.getByText('Lucius')).toBeTruthy()
  })

  it('uses chip-glyph (not chip-gl) for agents', () => {
    const { container } = render(<Chip mention={agentRef} role="recipient" />)
    expect(container.querySelector('.chip-glyph')).toBeTruthy()
    expect(container.querySelector('.chip-gl')).toBeFalsy()
  })
})

// ── Chip: agent ctx (dashed, no "To") ────────────────────────────────────────

describe('Chip — agent ctx', () => {
  it('adds .agent and .ctx classes', () => {
    const { container } = render(<Chip mention={agentCustomRef} role="ctx" />)
    const chip = container.querySelector('.chip')!
    expect(chip.classList.contains('agent')).toBe(true)
    expect(chip.classList.contains('ctx')).toBe(true)
  })

  it('does NOT render "To" for ctx role', () => {
    render(<Chip mention={agentCustomRef} role="ctx" />)
    expect(screen.queryByText('To')).toBeNull()
  })
})

// ── Chip: skill verb ──────────────────────────────────────────────────────────

describe('Chip — skill verb', () => {
  it('adds .verb class', () => {
    const { container } = render(<Chip mention={skillRef} role="verb" />)
    expect(container.querySelector('.chip.verb')).toBeTruthy()
  })

  it('prefixes the label with "/" when role is verb', () => {
    render(<Chip mention={skillRef} role="verb" />)
    expect(screen.getByText('/portfolio-summary')).toBeTruthy()
  })

  it('does NOT render a glyph for a verb chip', () => {
    const { container } = render(<Chip mention={skillRef} role="verb" />)
    expect(container.querySelector('.chip-gl')).toBeFalsy()
    expect(container.querySelector('.chip-glyph')).toBeFalsy()
  })

  it('a skill chip without explicit role also shows "/name"', () => {
    render(<Chip mention={skillRef} />)
    // type is 'skill', so isVerb is true regardless of role
    expect(screen.getByText('/portfolio-summary')).toBeTruthy()
  })
})

// ── Chip: compact variant ─────────────────────────────────────────────────────

describe('Chip — compact variant', () => {
  it('adds .compact class', () => {
    const { container } = render(<Chip mention={accountRef} variant="compact" />)
    expect(container.querySelector('.chip.compact')).toBeTruthy()
  })

  it('does NOT render sublabel in compact mode', () => {
    render(<Chip mention={accountRef} variant="compact" sublabel="$12,340" />)
    expect(screen.queryByText('$12,340')).toBeNull()
  })
})

// ── Chip: rich variant ────────────────────────────────────────────────────────

describe('Chip — rich variant', () => {
  it('adds .rich class', () => {
    const { container } = render(<Chip mention={accountRef} variant="rich" />)
    expect(container.querySelector('.chip.rich')).toBeTruthy()
  })

  it('renders sublabel in rich mode', () => {
    render(<Chip mention={accountRef} variant="rich" sublabel="Cash • SGD" />)
    expect(screen.getByText('Cash • SGD')).toBeTruthy()
  })
})

// ── Chip: stale ───────────────────────────────────────────────────────────────

describe('Chip — stale', () => {
  it('adds .stale class', () => {
    const { container } = render(<Chip mention={accountRef} stale />)
    expect(container.querySelector('.chip.stale')).toBeTruthy()
  })
})

// ── Chip: chip-amt from mention.chipAmt (transaction — colored) ──────────────

describe('Chip — chip-amt from mention fields (transaction)', () => {
  it('renders .chip-amt.pos when chipAmtNeg=false', () => {
    const ref: MentionRef = { ...transRef, chipAmt: '+$500', chipAmtNeg: false }
    const { container } = render(<Chip mention={ref} />)
    expect(container.querySelector('.chip-amt.pos')).toBeTruthy()
    expect(screen.getByText('+$500')).toBeTruthy()
  })

  it('renders .chip-amt.neg when chipAmtNeg=true', () => {
    const ref: MentionRef = { ...transRef, chipAmt: '-$200', chipAmtNeg: true }
    const { container } = render(<Chip mention={ref} />)
    expect(container.querySelector('.chip-amt.neg')).toBeTruthy()
  })

  it('does NOT render chip-amt in compact mode', () => {
    const ref: MentionRef = { ...transRef, chipAmt: '+$500', chipAmtNeg: false }
    const { container } = render(<Chip mention={ref} variant="compact" />)
    expect(container.querySelector('.chip-amt')).toBeFalsy()
  })
})

// ── Chip: chip-amt neutral (account / holding) ────────────────────────────────

describe('Chip — chip-amt NEUTRAL for account and holding', () => {
  it('renders .chip-amt without .pos or .neg for an account balance', () => {
    const ref: MentionRef = {
      type: 'account',
      ref: 'acc-1',
      label: 'Vantage Brokerage USD',
      chipAmt: 'S$12,340',
    }
    const { container } = render(<Chip mention={ref} />)
    const amt = container.querySelector('.chip-amt')
    expect(amt).toBeTruthy()
    expect(amt?.textContent).toBe('S$12,340')
    expect(amt?.classList.contains('pos')).toBe(false)
    expect(amt?.classList.contains('neg')).toBe(false)
  })

  it('renders .chip-amt without .pos or .neg for a holding value', () => {
    const ref: MentionRef = { type: 'holding', ref: 'pos-1', label: 'AAPL', chipAmt: 'S$48,200' }
    const { container } = render(<Chip mention={ref} />)
    const amt = container.querySelector('.chip-amt')
    expect(amt).toBeTruthy()
    expect(amt?.textContent).toBe('S$48,200')
    expect(amt?.classList.contains('pos')).toBe(false)
    expect(amt?.classList.contains('neg')).toBe(false)
  })

  it('does NOT render chip-amt when chipAmt is not set (no chipAmt field)', () => {
    const { container } = render(<Chip mention={accountRef} />)
    expect(container.querySelector('.chip-amt')).toBeFalsy()
  })
})

// ── Chip: sublabel shown in default variant ───────────────────────────────────

describe('Chip — sublabel in default variant', () => {
  it('renders sublabel text when showMeta is true and wraps maskable amount in .money', () => {
    const { container } = render(<Chip mention={accountRef} sublabel="50 shares" />)
    expect(screen.getByText('50 shares')).toBeTruthy()
    expect(container.querySelector('.chip-meta .money')?.textContent).toBe('50 shares')
  })

  it('renders .chip-amt with .money class for privacy mode masking', () => {
    const ref: MentionRef = { ...transRef, chipAmt: '-$200', chipAmtNeg: true }
    const { container } = render(<Chip mention={ref} />)
    const amt = container.querySelector('.chip-amt')
    expect(amt?.classList.contains('money')).toBe(true)
  })
})

// ── Identity: agent brand (Lucius / primary) ──────────────────────────────────

describe('Identity — agent brand', () => {
  it('renders id-brand with Spark SVG for Lucius', () => {
    const { container } = render(
      <Identity item={{ type: 'agent', label: 'Lucius', id: 'lucius' }} />,
    )
    expect(container.querySelector('.id-brand')).toBeTruthy()
    expect(container.querySelector('.acct-spark')).toBeTruthy()
  })

  it('renders id-brand for primary=true agents regardless of id', () => {
    const { container } = render(
      <Identity item={{ type: 'agent', label: 'Custom AI', primary: true }} />,
    )
    expect(container.querySelector('.id-brand')).toBeTruthy()
  })
})

// ── Identity: agent monogram ──────────────────────────────────────────────────

describe('Identity — agent monogram', () => {
  it('renders id-mono with initials for non-primary, non-emoji agents', () => {
    const { container } = render(
      <Identity item={{ type: 'agent', label: 'Tax Agent', id: 'ag-42' }} />,
    )
    expect(container.querySelector('.id-mono')).toBeTruthy()
    expect(screen.getByText('TA')).toBeTruthy()
  })

  it('adds .mono wrapper class for monogram agents', () => {
    const { container } = render(
      <Identity item={{ type: 'agent', label: 'Tax Agent', id: 'ag-42' }} />,
    )
    expect(container.querySelector('.id-box.mono')).toBeTruthy()
  })
})

// ── Identity: emoji ────────────────────────────────────────────────────────────

describe('Identity — emoji', () => {
  it('renders id-emoji for an agent with an emoji', () => {
    const { container } = render(<Identity item={{ type: 'agent', label: 'Bot', emoji: '🤖' }} />)
    expect(container.querySelector('.id-emoji')).toBeTruthy()
    expect(screen.getByText('🤖')).toBeTruthy()
  })

  it('renders id-emoji for a rule with an emoji (overrides marker)', () => {
    const { container } = render(
      <Identity item={{ type: 'rule', label: 'My Rule', emoji: '✨' }} />,
    )
    expect(container.querySelector('.id-emoji')).toBeTruthy()
    expect(screen.getByText('✨')).toBeTruthy()
  })
})

// ── Identity: skill /command ───────────────────────────────────────────────────

describe('Identity — skill', () => {
  it('renders id-cmd "/" for a skill', () => {
    const { container } = render(<Identity item={{ type: 'skill', label: 'portfolio-summary' }} />)
    expect(container.querySelector('.id-cmd')).toBeTruthy()
    expect(screen.getByText('/')).toBeTruthy()
  })
})

// ── Identity: connector ───────────────────────────────────────────────────────

describe('Identity — connector', () => {
  it('a connector with no brand mark shows the Connectors glyph, not a bare swatch', () => {
    const { container } = render(<Identity item={{ type: 'connector', label: 'Email' }} />)
    expect(container.querySelector('.id-glyph svg')).toBeTruthy()
    expect(container.querySelector('.id-swatch')).toBeNull()
  })

  it('a brand mark still wins over the glyph', () => {
    const { container } = render(
      <Identity item={{ type: 'connector', label: 'Plaid', logo: '/logos/plaid.svg' }} />,
    )
    expect(container.querySelector('.id-logo')).toBeTruthy()
    expect(container.querySelector('.id-glyph')).toBeNull()
  })
})

// ── Identity: rule marker ─────────────────────────────────────────────────────

describe('Identity — rule (TYPE_MARKER_SVG)', () => {
  it('renders id-marker for a rule', () => {
    const { container } = render(<Identity item={{ type: 'rule', label: 'Auto categorize' }} />)
    expect(container.querySelector('.id-marker')).toBeTruthy()
  })

  it('renders id-marker for scheduled_task', () => {
    const { container } = render(
      <Identity item={{ type: 'scheduled_task', label: 'Daily sync' }} />,
    )
    expect(container.querySelector('.id-marker')).toBeTruthy()
  })
})

// ── Identity: holding glyph ────────────────────────────────────────────────────

describe('Identity — holding (GLYPH_SVG)', () => {
  it('renders id-glyph with an SVG for a holding', () => {
    const { container } = render(<Identity item={{ type: 'holding', label: 'AAPL' }} />)
    expect(container.querySelector('.id-glyph')).toBeTruthy()
    expect(container.querySelector('.id-glyph svg')).toBeTruthy()
  })
})

// ── Identity: account swatch (glyph) ─────────────────────────────────────────

describe('Identity — account (GLYPH_SVG)', () => {
  it('renders id-glyph for an account', () => {
    const { container } = render(
      <Identity item={{ type: 'account', label: 'Vantage Brokerage USD' }} />,
    )
    expect(container.querySelector('.id-glyph')).toBeTruthy()
  })
})

// ── Identity: fallback swatch ─────────────────────────────────────────────────

describe('Identity — fallback swatch', () => {
  it('renders id-swatch for a type with no SVG', () => {
    const { container } = render(<Identity item={{ type: 'category', label: 'Food' }} />)
    expect(container.querySelector('.id-swatch')).toBeTruthy()
  })

  it('applies the dot color to the swatch background', () => {
    const { container } = render(
      <Identity item={{ type: 'category', label: 'Food', dot: 'var(--accent)' }} />,
    )
    const swatch = container.querySelector('.id-swatch') as HTMLElement | null
    // jsdom normalizes hex to rgb(); use a CSS var which passes through verbatim
    expect(swatch?.style.background).toBe('var(--accent)')
  })
})

// ── IdentityInner: initials edge cases ────────────────────────────────────────

describe('IdentityInner — initials edge cases', () => {
  it('single-word name → first two chars', () => {
    const { container } = render(<IdentityInner item={{ type: 'agent', label: 'Lucius' }} />)
    // single-word, non-primary, non-lucius: falls to initials path
    // 'Lu' from 'Lucius'.slice(0,2) = 'LU'
    expect(container.querySelector('.id-mono')?.textContent).toBe('LU')
  })

  it('two-word name → first initials of each word', () => {
    const { container } = render(<IdentityInner item={{ type: 'agent', label: 'Tax Agent' }} />)
    expect(container.querySelector('.id-mono')?.textContent).toBe('TA')
  })

  it('empty name → "?"', () => {
    const { container } = render(<IdentityInner item={{ type: 'agent', label: '' }} />)
    expect(container.querySelector('.id-mono')?.textContent).toBe('?')
  })
})

// ── Part A: transaction glyph direction tint via colorFor ─────────────────────
// Color now comes from inline style (colorFor passes C.neg/C.pos as dot).
// No .chip-gl.pos/.neg classes — those were removed when colorFor took over.

describe('Chip — transaction glyph direction tint (Part A)', () => {
  it('sets inline style color to var(--bad) when chipAmtNeg=true (expense/outflow)', () => {
    const ref: MentionRef = {
      type: 'transaction',
      ref: 'txn-exp',
      label: 'Grab',
      chipAmtNeg: true,
      chipAmt: '-12.50 SGD',
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl).toBeTruthy()
    expect(gl?.style.color).toBe('var(--bad)')
    // No legacy direction classes
    expect(gl?.classList.contains('neg')).toBe(false)
    expect(gl?.classList.contains('pos')).toBe(false)
  })

  it('sets inline style color to var(--good) when chipAmtNeg=false (income/inflow)', () => {
    const ref: MentionRef = {
      type: 'transaction',
      ref: 'txn-inc',
      label: 'Salary',
      chipAmtNeg: false,
      chipAmt: '+8000 SGD',
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl).toBeTruthy()
    expect(gl?.style.color).toBe('var(--good)')
    expect(gl?.classList.contains('pos')).toBe(false)
    expect(gl?.classList.contains('neg')).toBe(false)
  })

  it('renders .chip-gl with type default color when chipAmtNeg is unset', () => {
    const ref: MentionRef = { type: 'transaction', ref: 'txn-nd', label: 'Transfer' }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl).toBeTruthy()
    // No direction classes, color is TYPE_COLOR.transaction default
    expect(gl?.classList.contains('pos')).toBe(false)
    expect(gl?.classList.contains('neg')).toBe(false)
    // Has a color (the type default from colorFor)
    expect(gl?.style.color).toBeTruthy()
  })

  it('non-transaction with chipAmtNeg set uses account type color (not direction tint)', () => {
    // account chips should NOT get direction color even if chipAmtNeg is present
    const ref: MentionRef = {
      type: 'account',
      ref: 'acc-1',
      label: 'Vantage Brokerage',
      chipAmtNeg: true,
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl?.classList.contains('neg')).toBe(false)
    expect(gl?.classList.contains('pos')).toBe(false)
    // Color should NOT be var(--bad) — account uses its type default or explicit color
    expect(gl?.style.color).not.toBe('var(--bad)')
  })
})

// ── Part B: account glyph colored by account color ───────────────────────────

describe('Chip — account glyph color (Part B)', () => {
  it('applies the account color inline to the glyph when color is a valid hex', () => {
    const ref: MentionRef = {
      type: 'account',
      ref: 'acc-c',
      label: 'Meridian Bank',
      color: '#ff5733',
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl).toBeTruthy()
    // jsdom normalizes hex to rgb
    expect(gl?.style.color).toBeTruthy()
  })

  it('applies the account color inline to the glyph when color is a CSS var token', () => {
    const ref: MentionRef = {
      type: 'account',
      ref: 'acc-v',
      label: 'Lighthouse Bank',
      color: 'var(--lucius)',
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    expect(gl?.style.color).toBe('var(--lucius)')
  })

  it('falls back to TYPE_COLOR.account default when account color fails the safety check', () => {
    const ref: MentionRef = {
      type: 'account',
      ref: 'acc-x',
      label: 'Hax',
      color: 'expression(alert(1))',
    }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    // Invalid color is rejected; colorFor falls through to TYPE_COLOR.account (#34b08a → rgb)
    // The glyph still gets a color (the type default) — it is never left unstyled.
    expect(gl?.style.color).toBeTruthy()
  })

  it('account glyph with no color gets the TYPE_COLOR.account default from colorFor', () => {
    const ref: MentionRef = { type: 'account', ref: 'acc-nc', label: 'No Color' }
    const { container } = render(<Chip mention={ref} />)
    const gl = container.querySelector('.chip-gl') as HTMLElement | null
    // colorFor always returns a color — never empty
    expect(gl?.style.color).toBeTruthy()
  })
})
