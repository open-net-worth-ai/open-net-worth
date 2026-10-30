// matchEntity.test.ts — unit tests for the type-synonym match algorithm.
//
// Covers every acceptance bullet from the handoff spec:
//   @account / @accounts / @ACCOUNT  → all accounts, hint "Accounts"
//   @stocks → holdings, @spending → transactions, @merchants → payees,
//   @subscriptions → automation
//   @ac (2 chars) → NO type flood
//   @1480 → transaction via amount, hint "amount · …"
//   @netflix → name hit still works alongside type search
//   An item never appears in two sections (tested via matchEntity ok+hint logic).

import { describe, it, expect } from 'vitest'
import { norm, typeMatch, searchHay, matchEntity, TYPE_SYNONYMS } from './matchEntity'
import type { MentionRow } from './mentionRow'

// ── helpers ─────────────────────────────────────────────────────────────────

function row(type: string, label: string, sublabel = '', value = 'v1'): MentionRow {
  return { type, id: value, label, sublabel, value, icon: '?' }
}

// ── norm ─────────────────────────────────────────────────────────────────────

describe('norm', () => {
  it('lowercases', () => expect(norm('ACCOUNT')).toBe('account'))
  it('strips spaces', () => expect(norm('hello world')).toBe('helloworld'))
  it('strips punctuation', () => expect(norm('S$1,480.00')).toBe('s148000'))
  it('strips leading/trailing whitespace', () => expect(norm('  acc ')).toBe('acc'))
  it('handles null/undefined gracefully', () => {
    expect(norm(null)).toBe('')
    expect(norm(undefined)).toBe('')
  })
  it('handles numbers', () => expect(norm(1480)).toBe('1480'))
})

// ── typeMatch ─────────────────────────────────────────────────────────────────

describe('typeMatch', () => {
  it('@account (exact) → true for type "account"', () => {
    expect(typeMatch('account', 'account')).toBe(true)
  })

  it('@accounts (exact plural) → true for type "account"', () => {
    expect(typeMatch('account', 'accounts')).toBe(true)
  })

  it('@acc (prefix of "account") → true for type "account"', () => {
    expect(typeMatch('account', 'acc')).toBe(true)
  })

  it('@ACCOUNT normalized → true (caller normalizes before passing)', () => {
    // The caller normalizes with norm() before passing nq; test the raw normalized form.
    expect(typeMatch('account', norm('ACCOUNT'))).toBe(true)
  })

  // 2-char guard
  it('@ac (2 chars) → false — no type flood', () => {
    expect(typeMatch('account', 'ac')).toBe(false)
  })

  it('@a (1 char) → false', () => {
    expect(typeMatch('account', 'a')).toBe(false)
  })

  it('@tx (2 chars) → false for transaction', () => {
    expect(typeMatch('transaction', 'tx')).toBe(false)
  })

  // Synonym tests
  it('@stocks → true for type "holding"', () => {
    expect(typeMatch('holding', 'stocks')).toBe(true)
  })

  it('@stock → true for type "holding"', () => {
    expect(typeMatch('holding', 'stock')).toBe(true)
  })

  it('@spending → true for type "transaction"', () => {
    expect(typeMatch('transaction', 'spending')).toBe(true)
  })

  it('@merchants → true for type "payee"', () => {
    expect(typeMatch('payee', 'merchants')).toBe(true)
  })

  it('@subscriptions → true for type "automation"', () => {
    expect(typeMatch('automation', 'subscriptions')).toBe(true)
  })

  it('@agents → false: the agent type was retired (2026-08-02)', () => {
    expect(typeMatch('agent', 'agents')).toBe(false)
  })

  it('every registered type answers to its own name, singular and plural', () => {
    // "@connector" found nothing while connector (and 18 other types) had no synonym entry.
    expect(typeMatch('connector', 'connector')).toBe(true)
    expect(typeMatch('connector', 'connectors')).toBe(true)
    expect(typeMatch('connector', 'conn')).toBe(true)
    expect(typeMatch('report', 'reports')).toBe(true)
    expect(typeMatch('commitment', 'commitments')).toBe(true)
    expect(typeMatch('funding_source', norm('funding sources'))).toBe(true)
  })

  it('an unregistered type stays silent even for its own name', () => {
    expect(typeMatch('no_such_type', 'no_such_type')).toBe(false)
  })

  it('@skills → true for type "skill"', () => {
    expect(typeMatch('skill', 'skills')).toBe(true)
  })

  it('@categories → true for type "category"', () => {
    expect(typeMatch('category', 'categories')).toBe(true)
  })

  it('@budget → true for type "budget_month"', () => {
    expect(typeMatch('budget_month', 'budget')).toBe(true)
  })

  it('@watchlist → true for type "watchlist"', () => {
    expect(typeMatch('watchlist', 'watchlist')).toBe(true)
  })

  it('@rules → true for type "automation"', () => {
    expect(typeMatch('automation', 'rules')).toBe(true)
  })

  // Prefix direction: synonym must startsWith query, NOT query.startsWith(synonym)
  it('prefix test direction: "acc" prefixes "account" (synonym.startsWith(query))', () => {
    // norm("account").startsWith(norm("acc")) = "account".startsWith("acc") = true ✓
    expect(typeMatch('account', 'acc')).toBe(true)
  })

  it('inverted prefix does NOT match: query "accountsxyz" does not prefix synonym "accounts"', () => {
    // "accounts".startsWith("accountsxyz") = false
    expect(typeMatch('account', 'accountsxyz')).toBe(false)
  })

  it('unknown type → false', () => {
    expect(typeMatch('unknown_type', 'account')).toBe(false)
  })

  // TYPE_SYNONYMS completeness — all registered types have entries
  it('all TYPE_SYNONYMS entries have at least one synonym of length ≥ 3', () => {
    for (const [type, syns] of Object.entries(TYPE_SYNONYMS)) {
      expect(syns.length, `${type} should have synonyms`).toBeGreaterThan(0)
      expect(
        syns.some((s) => s.length >= 3),
        `${type} synonyms should have ≥3 char entry`,
      ).toBe(true)
    }
  })
})

// ── searchHay ────────────────────────────────────────────────────────────────

describe('searchHay', () => {
  it('includes normalized label', () => {
    const r = row('account', 'Meridian Multiplier', 'Savings • SGD')
    expect(searchHay(r)).toContain('meridianmultiplier')
  })

  it('includes normalized sublabel', () => {
    const r = row('transaction', 'Netflix', 'Subscriptions • Jun 12')
    expect(searchHay(r)).toContain('subscriptions')
  })

  it('includes amount digits extracted from sublabel (e.g. S$1,480)', () => {
    const r = row('transaction', 'Rent', 'S$1,480.00 • Jun 20')
    const hay = searchHay(r)
    expect(hay).toContain('1480')
    expect(hay).toContain('148000')
  })

  it('is memoized: second call returns same string', () => {
    const r = row('account', 'Lighthouse Bank', 'Checking')
    const h1 = searchHay(r)
    const h2 = searchHay(r)
    expect(h1).toBe(h2)
  })

  it('does NOT include the type string (type is for typeMatch, not name search)', () => {
    // Including type in the hay would cause "ac" to hit every "account" row — wrong.
    const r = row('payee', 'FairPrice', '9 transactions')
    expect(searchHay(r)).not.toContain('payee')
  })
})

// ── matchEntity ───────────────────────────────────────────────────────────────

describe('matchEntity', () => {
  // Empty query
  it('empty query → {ok:true} for any row', () => {
    expect(matchEntity(row('account', 'Meridian Bank'), '')).toEqual({ ok: true })
    expect(matchEntity(row('transaction', 'Netflix', '', 't1'), '')).toEqual({ ok: true })
  })

  // Whitespace-only query normalizes to empty → ok
  it('whitespace-only query → {ok:true}', () => {
    expect(matchEntity(row('account', 'Meridian Bank'), '   ')).toEqual({ ok: true })
  })

  // ── Acceptance: @account / @accounts / @ACCOUNT → all accounts, hint "Accounts" ──
  it('@account → account row gets {ok:true, hint:"Accounts"} (no account named "account")', () => {
    const r = row('account', 'Meridian Multiplier', 'Savings • SGD')
    const result = matchEntity(r, 'account')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Accounts')
  })

  it('@accounts → account row gets hint "Accounts"', () => {
    const r = row('account', 'Lighthouse Bank 360', 'Checking • SGD')
    const result = matchEntity(r, 'accounts')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Accounts')
  })

  it('@ACCOUNT (unnormalized) → account row gets hint "Accounts"', () => {
    const r = row('account', 'Compass USD', 'E-wallet • USD')
    const result = matchEntity(r, 'ACCOUNT')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Accounts')
  })

  // ── Acceptance: synonym-based matches ──
  it('@stocks → holding row gets {ok:true, hint:"Holdings"}', () => {
    const r = row('holding', 'AAPL', 'Apple • 760 sh')
    const result = matchEntity(r, 'stocks')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Holdings')
  })

  it('@spending → transaction row gets {ok:true, hint:"Transactions"}', () => {
    const r = row('transaction', 'Cold Storage', 'Groceries • Jun 15')
    const result = matchEntity(r, 'spending')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Transactions')
  })

  it('@merchants → payee row gets {ok:true, hint:"Payees"}', () => {
    const r = row('payee', 'FairPrice', '9 transactions • Groceries')
    const result = matchEntity(r, 'merchants')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Payees')
  })

  it('@subscriptions → automation row gets {ok:true, hint:"Automations"}', () => {
    const r = row('automation', 'Rent autopay', 'Every 20th • S$4,200')
    const result = matchEntity(r, 'subscriptions')
    expect(result.ok).toBe(true)
    expect(result.hint).toBe('Automations')
  })

  it('@agents → no type flood now that the agent type is retired', () => {
    // A row still literally named "…Agent" may match on NAME; what must not happen is the
    // whole type matching the synonym and flooding the picker.
    const r = row('agent', 'Tax Agent', 'Estimates, deadlines, reliefs')
    expect(matchEntity(r, 'agents').hint).not.toBe('Agents')
  })

  // ── Acceptance: @ac (2 chars) → NO type flood ──
  it('@ac (2 chars) → {ok:false} for account row not named "ac"', () => {
    const r = row('account', 'Meridian Multiplier', 'Savings • SGD')
    const result = matchEntity(r, 'ac')
    expect(result.ok).toBe(false)
  })

  it('@tx (2 chars) → {ok:false} for transaction row', () => {
    const r = row('transaction', 'Netflix', 'Subscriptions • Jun 12')
    const result = matchEntity(r, 'tx')
    expect(result.ok).toBe(false)
  })

  // ── Acceptance: @netflix → name hit still works ──
  it('@netflix → payee named Netflix matches by name, no hint', () => {
    const r = row('payee', 'Netflix', 'Monthly • Subscriptions')
    const result = matchEntity(r, 'netflix')
    expect(result.ok).toBe(true)
    expect(result.hint).toBeUndefined()
  })

  it('@netflix → transaction named Netflix matches by name, no type hint', () => {
    const r = row('transaction', 'Netflix', 'Subscriptions • Jun 12')
    const result = matchEntity(r, 'netflix')
    expect(result.ok).toBe(true)
    // No type hint — it matched by name, not type-synonym
    expect(result.hint).toBeUndefined()
  })

  // Name hit + type hit coexist: an account named "Account Savings" matches by name (no hint),
  // while a plain "Meridian Bank" account matches only by type (hint set).
  it('name hit and type hit coexist — named row gets no type hint', () => {
    const named = row('account', 'Account Savings', '')
    const other = row('account', 'Meridian Multiplier', '')
    const namedResult = matchEntity(named, 'account')
    const otherResult = matchEntity(other, 'account')
    // Named row: name contains "account" → name hit (no type hint)
    expect(namedResult.ok).toBe(true)
    expect(namedResult.hint).toBeUndefined()
    // Meridian Bank row: name doesn't contain "account" → type hit with hint
    expect(otherResult.ok).toBe(true)
    expect(otherResult.hint).toBe('Accounts')
  })

  // ── Acceptance: @1480 → amount match ──
  it('@1480 → transaction with S$1,480 in sublabel matches via amount', () => {
    const r = row('transaction', 'Rent — Marina Rise', 'Rent • S$1,480.00 • Jun 20')
    const result = matchEntity(r, '1480')
    expect(result.ok).toBe(true)
    expect(result.hint).toMatch(/amount/)
  })

  it('@1480 amount match includes the sublabel in hint', () => {
    const r = row('transaction', 'Rent', 'Rent • S$1,480.00 • Jun 20')
    const result = matchEntity(r, '1480')
    expect(result.hint).toContain('amount •')
  })

  // Non-matching cases
  it('@xyz → {ok:false} for a row with no matching name or type', () => {
    const r = row('account', 'Meridian Multiplier', 'Savings • SGD')
    expect(matchEntity(r, 'xyz').ok).toBe(false)
  })

  it('@samsung → {ok:false} for Apple holding', () => {
    const r = row('holding', 'AAPL', 'Apple • 760 sh')
    expect(matchEntity(r, 'samsung').ok).toBe(false)
  })

  // Type match only kicks in for the correct type
  it('@accounts does NOT match a transaction row', () => {
    const r = row('transaction', 'Netflix', 'Subscriptions')
    const result = matchEntity(r, 'accounts')
    // "accounts" is not in TYPE_SYNONYMS.transaction, and "accounts" is not in the name
    expect(result.ok).toBe(false)
  })

  it('@stocks does NOT match an account row', () => {
    const r = row('account', 'Meridian Bank', 'Savings')
    expect(matchEntity(r, 'stocks').ok).toBe(false)
  })

  // Prefix direction: the synonym must startsWith the query (not query.startsWith(synonym))
  it('prefix direction: "acc" prefix of "account" → match', () => {
    const r = row('account', 'Meridian Multiplier', '')
    expect(matchEntity(r, 'acc').ok).toBe(true)
  })

  it('prefix direction inverted: "accountsXYZ" is NOT a prefix of any synonym → no match', () => {
    const r = row('account', 'Meridian Multiplier', '')
    expect(matchEntity(r, 'accountsXYZ').ok).toBe(false)
  })
})
