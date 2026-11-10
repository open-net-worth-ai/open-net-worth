// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  getScreenSnapshot,
  buildScreenSnapshot,
  screenSuggestions,
  setHandles,
  setAmbientHandles,
  allHandles,
  handleById,
  setScreenFacts,
  setScreenPrompts,
} from './screenContract'

// ── DOM fixture helpers ──────────────────────────────────────────────────────

function setBody(html: string): void {
  document.body.innerHTML = html
}

function clearRegistry(): void {
  setHandles([])
  setAmbientHandles([])
  setScreenFacts(null)
  setScreenPrompts(null)
}

beforeEach(() => {
  clearRegistry()
})

afterEach(() => {
  document.body.innerHTML = ''
  clearRegistry()
})

// ── Fixtures ─────────────────────────────────────────────────────────────────

const BASIC_DOM = `
  <h1 data-ctx-title="Holdings">Holdings</h1>
  <div data-ctx-region="content">
    <div data-ctx data-entity-id="h-voo" data-ctx-type="holding"
         data-ctx-fields='{"ticker":"VOO","value":"S$320,000","today":"+0.7%"}'>
      VOO — S$320,000
    </div>
    <div data-ctx data-entity-id="h-aapl" data-ctx-type="holding"
         data-ctx-fields='{"ticker":"AAPL","value":"S$182,000","today":"-0.4%"}'>
      AAPL — S$182,000
    </div>
    <div data-ctx data-entity-id="h-nvda" data-ctx-type="holding">
      NVDA — displayed text only (no fields attr)
    </div>
  </div>
`

// ── Regression: on-demand handle `source` must survive the snapshot pipeline ──
// Bug: source was dropped in getScreenSnapshot + buildScreenSnapshot, so every
// handle reached the backend source-less → get_screen_detail echoed the
// descriptor placeholder instead of hydrating real data.
describe('handle source preservation', () => {
  it('getScreenSnapshot keeps handle.source', () => {
    setHandles([
      {
        id: 'series-nw',
        type: 'view',
        label: 'Net worth',
        descriptor: '12-month trend',
        source: 'net_worth_series:1Y',
      },
    ])
    const { handles } = getScreenSnapshot(new Set())
    expect(handles[0]?.source).toBe('net_worth_series:1Y')
  })

  it('buildScreenSnapshot keeps handle.source through to the wire-bound snapshot', () => {
    setHandles([
      {
        id: 'tbl-alloc',
        type: 'view',
        label: 'Allocation',
        descriptor: 'donut breakdown',
        source: 'holdings:all',
      },
    ])
    const snap = buildScreenSnapshot('dashboard', getScreenSnapshot(new Set()))
    expect(snap.handles?.[0]?.source).toBe('holdings:all')
  })
})

// ── Tests: basic title + facts ────────────────────────────────────────────────

describe('getScreenSnapshot — basic', () => {
  it('reads the title from data-ctx-title', () => {
    setBody(BASIC_DOM)
    const { title } = getScreenSnapshot(new Set(['h-voo', 'h-aapl', 'h-nvda']))
    expect(title).toBe('Holdings')
  })

  it('includes "Screen" fact pointing at the title', () => {
    setBody(BASIC_DOM)
    const { facts } = getScreenSnapshot(new Set())
    const screenFact = facts.find(([k]) => k === 'Screen')
    expect(screenFact).toBeDefined()
    expect(screenFact![1]).toBe('Holdings')
  })

  it('falls back to "this screen" when no data-ctx-title', () => {
    setBody('<div data-ctx-region="content"><p>hello</p></div>')
    const { title } = getScreenSnapshot(new Set())
    expect(title).toBe('this screen')
  })

  it('uses data-ctx-screen as title when no data-ctx-title is present', () => {
    setBody('<div data-ctx-screen="Agents"><p>some content</p></div>')
    const { title } = getScreenSnapshot(new Set())
    expect(title).toBe('Agents')
  })

  it('prefers data-ctx-title over data-ctx-screen', () => {
    setBody(`
      <div data-ctx-screen="Settings">
        <h1 data-ctx-title="API Keys">API Keys</h1>
        <div data-ctx-region="content"><p>content</p></div>
      </div>
    `)
    const { title } = getScreenSnapshot(new Set())
    expect(title).toBe('API Keys')
  })

  it('prefers data-ctx-title inside content region over one outside it', () => {
    setBody(`
      <div data-ctx-screen="Investments">
        <h1 data-ctx-title="Page title outside">Page title outside</h1>
        <div data-ctx-region="content">
          <h2 data-ctx-title="Holdings">Holdings</h2>
        </div>
      </div>
    `)
    const { title } = getScreenSnapshot(new Set())
    expect(title).toBe('Holdings')
  })
})

// ── Tests: two-layer split ────────────────────────────────────────────────────

describe('getScreenSnapshot — two-layer (visible vs off-screen)', () => {
  it('rows in visibleIds appear under VISIBLE NOW', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set(['h-voo']))
    expect(raw).toContain('VISIBLE NOW')
    expect(raw).toContain('ticker=VOO')
  })

  it('rows NOT in visibleIds appear under ALSO ON THIS SCREEN', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set(['h-voo']))
    expect(raw).toContain('ALSO ON THIS SCREEN')
    expect(raw).toContain('ticker=AAPL')
  })

  it('VISIBLE NOW uses 1-indexed ordering', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set(['h-voo', 'h-aapl']))
    // First visible row should be "1. ..."
    expect(raw).toMatch(/1\. /)
    expect(raw).toMatch(/2\. /)
  })

  it('empty visibleIds → no VISIBLE NOW section, all rows go to ALSO ON THIS SCREEN', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).not.toContain('VISIBLE NOW')
    expect(raw).toContain('ALSO ON THIS SCREEN')
  })

  it('full visibleIds → VISIBLE NOW only, no ALSO ON THIS SCREEN section', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set(['h-voo', 'h-aapl', 'h-nvda']))
    expect(raw).toContain('VISIBLE NOW')
    expect(raw).not.toContain('ALSO ON THIS SCREEN')
  })
})

// ── Tests: open-dialog entity dedupe ─────────────────────────────────────────
// Regression: a detail drawer (role=dialog) shows the SAME entity as the row behind it.
// Both were harvested → Lucius reported one item as two ("appears to be a duplicate").

describe('getScreenSnapshot — open-dialog dedupe', () => {
  it('drops the background row when the same entity is shown in the open dialog', () => {
    setBody(`
      <h1 data-ctx-title="Accounts">Accounts</h1>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="transaction:t1" data-ctx-type="transaction"
             data-ctx-fields='{"payee":"BEHIND"}'>row behind</div>
      </div>
      <aside role="dialog" aria-label="Transaction details">
        <div data-ctx data-entity-id="transaction:t1" data-ctx-type="transaction"
             data-ctx-fields='{"payee":"DRAWER"}'>drawer</div>
      </aside>
    `)
    const { raw } = getScreenSnapshot(new Set(['transaction:t1']))
    expect(raw).toContain('payee=DRAWER') // the focused dialog instance is kept
    expect(raw).not.toContain('payee=BEHIND') // its background duplicate is dropped
  })

  it('keeps DISTINCT background entities (only the dialog dupe is removed)', () => {
    setBody(`
      <h1 data-ctx-title="Accounts">Accounts</h1>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="transaction:t1" data-ctx-type="transaction" data-ctx-fields='{"payee":"OPEN"}'>open</div>
        <div data-ctx data-entity-id="transaction:t2" data-ctx-type="transaction" data-ctx-fields='{"payee":"OTHER"}'>other</div>
      </div>
      <aside role="dialog" aria-label="Transaction details">
        <div data-ctx data-entity-id="transaction:t1" data-ctx-type="transaction" data-ctx-fields='{"payee":"OPEN"}'>drawer</div>
      </aside>
    `)
    const { raw } = getScreenSnapshot(new Set(['transaction:t1', 'transaction:t2']))
    expect(raw).toContain('payee=OTHER') // a different background entity stays as context
  })
})

// ── Tests: data-ctx-fields parse vs innerText fallback ───────────────────────

describe('getScreenSnapshot — data-ctx-fields vs innerText fallback', () => {
  it('parses data-ctx-fields JSON and formats as key=value', () => {
    setBody(BASIC_DOM)
    const { raw } = getScreenSnapshot(new Set(['h-voo']))
    expect(raw).toContain('ticker=VOO')
    expect(raw).toContain('value=S$320,000')
    expect(raw).toContain('today=+0.7%')
  })

  it('falls back to innerText when data-ctx-fields is absent', () => {
    setBody(BASIC_DOM)
    // h-nvda has no data-ctx-fields
    const { raw } = getScreenSnapshot(new Set(['h-nvda']))
    expect(raw).toContain('NVDA')
  })

  it('falls back to innerText when data-ctx-fields is invalid JSON', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="bad-json" data-ctx-type="holding"
             data-ctx-fields="not-valid-json">
          Fallback text
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['bad-json']))
    expect(raw).toContain('Fallback text')
  })

  it('falls back to innerText when data-ctx-fields is empty object', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="empty-fields" data-ctx-type="holding"
             data-ctx-fields="{}">
          Empty fields text
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['empty-fields']))
    expect(raw).toContain('Empty fields text')
  })
})

// ── Tests: form-harvest ───────────────────────────────────────────────────────

describe('getScreenSnapshot — form-harvest', () => {
  it('harvests input values with label[for] association', () => {
    setBody(`
      <div data-ctx-region="content">
        <label for="age-input">Retirement age</label>
        <input id="age-input" value="65" />
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('FORM / CONTROLS ON SCREEN')
    expect(raw).toContain('Retirement age = 65')
  })

  it('NEVER harvests credential/secret control values (password, revealed API key, token region)', () => {
    setBody(`
      <div data-ctx-region="content">
        <label for="pw">Password</label>
        <input id="pw" type="password" value="hunter2-secret" />
        <label for="apikey">API Key</label>
        <input id="apikey" type="text" value="sk-live-DEADBEEF" />
        <input name="smtp_token" type="text" value="tok-PRIVATE" aria-label="SMTP" />
        <div data-ctx-secret>
          <label for="bearer">Connector</label>
          <input id="bearer" type="text" value="Bearer-XYZ" />
        </div>
        <label for="city">City</label>
        <input id="city" value="Singapore" />
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    // None of the secret values may appear anywhere in the snapshot text.
    expect(raw).not.toContain('hunter2-secret')
    expect(raw).not.toContain('sk-live-DEADBEEF')
    expect(raw).not.toContain('tok-PRIVATE')
    expect(raw).not.toContain('Bearer-XYZ')
    // A normal labelled field is still harvested.
    expect(raw).toContain('City = Singapore')
  })

  it('harvests [role=switch] as on/off from aria-checked', () => {
    setBody(`
      <div data-ctx-region="content">
        <div role="switch" aria-label="Auto-rebalance" aria-checked="true"></div>
        <div role="switch" aria-label="Tax harvesting" aria-checked="false"></div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Auto-rebalance = on')
    expect(raw).toContain('Tax harvesting = off')
  })

  it('harvests [role=checkbox] as on/off', () => {
    setBody(`
      <div data-ctx-region="content">
        <div role="checkbox" aria-label="Include transfers" aria-checked="true"></div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Include transfers = on')
  })

  it('skips inputs inside [data-ctx-ignore] subtrees', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx-ignore>
          <label for="ignored-input">Should be ignored</label>
          <input id="ignored-input" value="secret" />
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).not.toContain('Should be ignored')
    expect(raw).not.toContain('secret')
  })

  it('uses aria-label when label[for] is absent', () => {
    setBody(`
      <div data-ctx-region="content">
        <input aria-label="Monthly savings" value="2000" />
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Monthly savings = 2000')
  })

  it('shows placeholder as (default: ...) when input is empty', () => {
    setBody(`
      <div data-ctx-region="content">
        <input aria-label="Withdrawal rate" placeholder="4.0" value="" />
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Withdrawal rate = (default: 4.0)')
  })
})

// ── Tests: what a dialog says ─────────────────────────────────────────────────

describe('getScreenSnapshot — what a dialog says', () => {
  const rows = (n: number) =>
    Array.from(
      { length: n },
      (_, i) =>
        `<div data-ctx data-entity-id="t${i}" data-ctx-type="txn"
              data-ctx-fields='{"payee":"ACME payee with a long enough label ${i}"}'>row ${i}</div>`,
    ).join('')
  const page = (n: number) => `<main data-ctx-region="content" data-ctx-title="X">${rows(n)}</main>`

  it('reads a text-only dialog over a full list, ahead of the rows the size cap would cut', () => {
    document.body.innerHTML = `${page(200)}<div role="dialog" aria-label="Confirm"><p>Delete ACME?</p></div>`
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Delete ACME?')
    expect(raw.indexOf('Delete ACME?')).toBeLessThan(raw.indexOf('ALSO ON THIS SCREEN'))
  })

  it('says it once over a short page', () => {
    document.body.innerHTML = `${page(1)}<div role="dialog" aria-label="Confirm"><p>Delete ACME?</p></div>`
    const { raw } = getScreenSnapshot(new Set())
    expect(raw.split('Delete ACME?')).toHaveLength(2)
  })

  it('treats the top of a stack as the dialog in focus, and still reads the one beneath', () => {
    document.body.innerHTML = `${page(0)}
      <div role="dialog" aria-label="Documents"><p>Beneath text</p></div>
      <div role="dialog" aria-label="Rename"><p>Top text</p></div>`
    const { raw, facts } = getScreenSnapshot(new Set())
    expect(facts).toContainEqual(['Open dialog', 'Rename'])
    expect(raw.indexOf('Top text')).toBeLessThan(raw.indexOf('Beneath text'))
    expect(raw).toContain('Beneath text')
  })

  it('keeps a secret region inside a dialog out', () => {
    document.body.innerHTML = `${page(0)}<div role="dialog" aria-label="File"><p>Shown</p><pre data-ctx-secret="">Hidden text</pre></div>`
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('Shown')
    expect(raw).not.toContain('Hidden text')
  })

  it('never reads an intro guide as a dialog, though it is one', () => {
    document.body.innerHTML = `${page(0)}<div class="fi-modal" role="dialog" aria-label="Example"><p>Sample figures</p></div>`
    const { raw, facts } = getScreenSnapshot(new Set())
    expect(raw).not.toContain('Sample figures')
    expect(facts.some(([k]) => k === 'Open dialog')).toBe(false)
  })
})

// ── Tests: modal occlusion ────────────────────────────────────────────────────

describe('getScreenSnapshot — modal occlusion', () => {
  it('treats role=dialog as the primary content region', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="bg-row" data-ctx-type="account"
             data-ctx-fields='{"name":"Background account"}'>
          Background account
        </div>
      </div>
      <div role="dialog" aria-label="Account details">
        <h2 data-ctx-title="Account details">Account details</h2>
        <div data-ctx data-entity-id="modal-row" data-ctx-type="account"
             data-ctx-fields='{"name":"Modal account","balance":"S$10,000"}'>
          Modal account
        </div>
      </div>
    `)
    // Modal row is visible; background row is not
    const { raw } = getScreenSnapshot(new Set(['modal-row']))
    expect(raw).toContain('A DIALOG IS OPEN')
    expect(raw).toContain('VISIBLE NOW')
    expect(raw).toContain('name=Modal account')
  })

  it('adds Open dialog fact', () => {
    setBody(`
      <div data-ctx-region="content">
        <p>Page content</p>
      </div>
      <div role="dialog" aria-label="Transaction editor">
        <p>Modal content</p>
      </div>
    `)
    const { facts } = getScreenSnapshot(new Set())
    const dialogFact = facts.find(([k]) => k === 'Open dialog')
    expect(dialogFact).toBeDefined()
    expect(dialogFact![1]).toBe('Transaction editor')
  })

  it('ignores dialog with data-ctx-ignore', () => {
    setBody(`
      <div data-ctx-region="content"><p>Main content</p></div>
      <div role="dialog" data-ctx-ignore aria-label="Tooltip dialog">
        <p>Ignored</p>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).not.toContain('A DIALOG IS OPEN')
  })
})

// ── Tests: view-state (tabs, data-ctx-state) ──────────────────────────────────

describe('getScreenSnapshot — view state', () => {
  it('captures data-ctx-state elements', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx-state="Period: 1Y"></div>
        <div data-ctx data-entity-id="row1" data-ctx-type="holding">Row</div>
      </div>
    `)
    const { raw, facts } = getScreenSnapshot(new Set(['row1']))
    expect(raw).toContain('VIEW STATE')
    expect(raw).toContain('Period: 1Y')
    const viewingFact = facts.find(([k]) => k === 'Viewing')
    expect(viewingFact).toBeDefined()
    expect(viewingFact![1]).toContain('Period: 1Y')
  })

  it('auto-captures active tabs via role=tab + aria-selected', () => {
    setBody(`
      <div data-ctx-region="content">
        <div role="tablist" aria-label="Investments">
          <button role="tab" aria-selected="true">Holdings</button>
          <button role="tab" aria-selected="false">Performance</button>
        </div>
      </div>
    `)
    const { facts } = getScreenSnapshot(new Set())
    const viewingFact = facts.find(([k]) => k === 'Viewing')
    expect(viewingFact).toBeDefined()
    expect(viewingFact![1]).toContain('Investments: Holdings')
  })
})

// ── Tests: data-ctx-fact elements ────────────────────────────────────────────

describe('getScreenSnapshot — declared facts', () => {
  it('reads data-ctx-fact attributes', () => {
    setBody(`
      <div data-ctx-region="content">
        <span data-ctx-fact="Net worth">S$1,234,567</span>
        <span data-ctx-fact="Today">+S$2,100</span>
      </div>
    `)
    const { facts } = getScreenSnapshot(new Set())
    const nw = facts.find(([k]) => k === 'Net worth')
    const today = facts.find(([k]) => k === 'Today')
    expect(nw).toBeDefined()
    expect(nw![1]).toBe('S$1,234,567')
    expect(today).toBeDefined()
    expect(today![1]).toBe('+S$2,100')
  })

  it('deduplicates facts by label (case-insensitive)', () => {
    setBody(`
      <div data-ctx-region="content">
        <span data-ctx-fact="Net worth">S$1,234,567</span>
        <span data-ctx-fact="Net Worth">S$1,234,568</span>
      </div>
    `)
    const { facts } = getScreenSnapshot(new Set())
    const nwFacts = facts.filter(([k]) => k.toLowerCase() === 'net worth')
    expect(nwFacts).toHaveLength(1)
  })
})

// ── Tests: computed-facts hook ────────────────────────────────────────────────

describe('getScreenSnapshot — computed-facts hook', () => {
  it('calls setScreenFacts fn with visible entity ids', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="r1" data-ctx-type="holding">Row 1</div>
        <div data-ctx data-entity-id="r2" data-ctx-type="holding">Row 2</div>
      </div>
    `)
    setScreenFacts((visIds) => {
      const ids = [...visIds].sort()
      return [['Movers', ids.join(', ')]]
    })
    const { facts } = getScreenSnapshot(new Set(['r1']))
    const moversFact = facts.find(([k]) => k === 'Movers')
    expect(moversFact).toBeDefined()
    expect(moversFact![1]).toBe('r1')
  })

  it('ignores exceptions thrown in the screenFacts fn', () => {
    setBody(BASIC_DOM)
    setScreenFacts(() => {
      throw new Error('test error')
    })
    expect(() => getScreenSnapshot(new Set())).not.toThrow()
  })
})

// ── Tests: handle registry ────────────────────────────────────────────────────

describe('handle registry', () => {
  it('setHandles / allHandles / handleById', () => {
    setHandles([
      { id: 'h1', type: 'price_series', label: 'VOO 1Y' },
      { id: 'h2', type: 'news_story', label: 'NVDA earnings' },
    ])
    expect(allHandles()).toHaveLength(2)
    expect(handleById('h1')?.label).toBe('VOO 1Y')
    expect(handleById('h2')?.type).toBe('news_story')
    expect(handleById('missing')).toBeUndefined()
  })

  it('setAmbientHandles are prepended in allHandles and resolved by handleById', () => {
    setHandles([{ id: 'screen-h', type: 'table', label: 'Holdings table' }])
    setAmbientHandles([{ id: 'nav-h', type: 'story', label: 'Nav summary' }])
    const all = allHandles()
    // ambient handles come first
    expect(all[0]!.id).toBe('nav-h')
    expect(all[1]!.id).toBe('screen-h')
    // handleById resolves both
    expect(handleById('nav-h')?.label).toBe('Nav summary')
    expect(handleById('screen-h')?.label).toBe('Holdings table')
  })

  it('snapshot handles manifest reflects all registered handles', () => {
    setBody(BASIC_DOM)
    setHandles([{ id: 'price-1y', type: 'price_series', label: 'Price history 1Y' }])
    const { handles } = getScreenSnapshot(new Set())
    expect(handles).toHaveLength(1)
    expect(handles[0]!.id).toBe('price-1y')
    expect(handles[0]!.type).toBe('price_series')
    expect(handles[0]!.label).toBe('Price history 1Y')
  })
})

// ── Tests: nav region ────────────────────────────────────────────────────────

describe('getScreenSnapshot — nav region', () => {
  it('nav rows appear separately from content rows', () => {
    setBody(`
      <div data-ctx-title="Accounts"></div>
      <nav data-ctx-region="nav" data-ctx-region-label="ACCOUNTS">
        <div data-ctx data-entity-id="a-meridian" data-ctx-type="account"
             data-ctx-fields='{"name":"Meridian Multiplier","balance":"S$148,250"}'>
          Meridian Multiplier
        </div>
      </nav>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="t-1" data-ctx-type="transaction"
             data-ctx-fields='{"desc":"Coffee","amount":"-S$5.50"}'>
          Coffee
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['a-meridian', 't-1']))
    expect(raw).toContain('VISIBLE NOW')
    expect(raw).toContain('ACCOUNTS (visible)')
  })

  it('off-screen nav rows appear under nav (off-screen / behind panel)', () => {
    setBody(`
      <nav data-ctx-region="nav" data-ctx-region-label="ACCOUNTS">
        <div data-ctx data-entity-id="a-meridian" data-ctx-type="account"
             data-ctx-fields='{"name":"Meridian Bank"}'>Meridian Bank</div>
        <div data-ctx data-entity-id="a-lighthouse" data-ctx-type="account"
             data-ctx-fields='{"name":"Lighthouse Bank"}'>Lighthouse Bank</div>
      </nav>
      <div data-ctx-region="content"><p>Content</p></div>
    `)
    // Only a-meridian is "visible"
    const { raw } = getScreenSnapshot(new Set(['a-meridian']))
    expect(raw).toContain('ACCOUNTS (off-screen / behind panel)')
  })
})

// ── Tests: data-ctx-ignore opt-out ────────────────────────────────────────────

describe('getScreenSnapshot — data-ctx-ignore opt-out', () => {
  it('rows inside data-ctx-ignore are excluded from the snapshot', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="visible-row" data-ctx-type="holding">Visible</div>
        <div data-ctx-ignore>
          <div data-ctx data-entity-id="ignored-row" data-ctx-type="holding">Ignored</div>
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['visible-row', 'ignored-row']))
    expect(raw).toContain('Visible')
    expect(raw).not.toContain('Ignored')
  })

  it('rows inside role=tooltip are excluded', () => {
    setBody(`
      <div data-ctx-region="content">
        <div role="tooltip">
          <div data-ctx data-entity-id="tooltip-row">Tooltip content</div>
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['tooltip-row']))
    expect(raw).not.toContain('Tooltip content')
  })

  it('rows inside role=menu are excluded', () => {
    setBody(`
      <div data-ctx-region="content">
        <div role="menu">
          <div data-ctx data-entity-id="menu-row">Menu item</div>
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['menu-row']))
    expect(raw).not.toContain('Menu item')
  })
})

// ── Tests: buildScreenSnapshot helper ────────────────────────────────────────

describe('buildScreenSnapshot', () => {
  it('converts facts array to Record', () => {
    setBody(BASIC_DOM)
    const result = getScreenSnapshot(new Set(['h-voo']))
    const snapshot = buildScreenSnapshot('holdings', result)
    expect(snapshot.id).toBe('holdings')
    expect(snapshot.title).toBe('Holdings')
    expect(snapshot.facts).toEqual(expect.objectContaining({ Screen: 'Holdings' }))
    expect(typeof snapshot.facts).toBe('object')
  })

  it('attaches visibleText and handles to the snapshot', () => {
    setBody(BASIC_DOM)
    setHandles([{ id: 'h1', type: 'price_series', label: 'VOO 1Y' }])
    const result = getScreenSnapshot(new Set(['h-voo']))
    const snapshot = buildScreenSnapshot('holdings', result)
    expect(snapshot.visibleText).toBeTruthy()
    expect(snapshot.handles).toHaveLength(1)
    expect(snapshot.handles?.[0]?.id).toBe('h1')
  })

  it('sets asOf to a valid ISO date string', () => {
    setBody(BASIC_DOM)
    const result = getScreenSnapshot(new Set())
    const snapshot = buildScreenSnapshot('holdings', result)
    expect(snapshot.asOf).toMatch(/^\d{4}-\d{2}-\d{2}T/)
  })
})

// ── Tests: screenSuggestions ──────────────────────────────────────────────────

describe('screenSuggestions', () => {
  it('returns generic fallback when no screenPrompts registered', () => {
    setBody('<h1 data-ctx-title="Overview">Overview</h1>')
    const suggestions = screenSuggestions()
    expect(Array.isArray(suggestions)).toBe(true)
    expect(suggestions.length).toBeGreaterThan(0)
    // Generic fallback includes the screen name
    expect(suggestions.some((s) => s.includes('Overview'))).toBe(true)
  })

  it('uses registered screenPrompts fn when available', () => {
    setBody('<h1 data-ctx-title="Tax">Tax</h1>')
    setScreenPrompts(({ title }) => [`What are my ${title} obligations?`])
    const suggestions = screenSuggestions()
    expect(suggestions).toEqual(['What are my Tax obligations?'])
  })

  it('returns modal-specific suggestions when a dialog is open', () => {
    setBody(`
      <h1 data-ctx-title="Holdings">Holdings</h1>
      <div role="dialog" aria-label="Add transaction">
        <p>Dialog content</p>
      </div>
    `)
    const suggestions = screenSuggestions()
    expect(suggestions.some((s) => s.includes('Add transaction'))).toBe(true)
  })

  it('ignores exceptions thrown by the screenPrompts fn', () => {
    setBody('<h1 data-ctx-title="Holdings">Holdings</h1>')
    setScreenPrompts(() => {
      throw new Error('oops')
    })
    expect(() => screenSuggestions()).not.toThrow()
    // Falls back to generic
    const suggestions = screenSuggestions()
    expect(suggestions.length).toBeGreaterThan(0)
  })
})

// ── Tests: raw text length cap ────────────────────────────────────────────────

describe('getScreenSnapshot — raw text cap', () => {
  it('caps raw at 3200 chars', () => {
    const longText = 'x'.repeat(200)
    const rows = Array.from(
      { length: 30 },
      (_, i) =>
        `<div data-ctx data-entity-id="r${i}" data-ctx-type="holding"
              data-ctx-fields='{"a":"${longText}"}'>${longText}</div>`,
    ).join('')
    setBody(`
      <div data-ctx-region="content">${rows}</div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    expect(raw.length).toBeLessThanOrEqual(3200)
  })
})

// ── Tests: uninstrumented-screen content fallback ────────────────────────────

describe('getScreenSnapshot — uninstrumented screen content fallback', () => {
  it('falls back to [data-ctx-screen] as content root when no [data-ctx-region="content"] exists', () => {
    setBody(`
      <div data-ctx-screen="Agents">
        <h2>Agent list</h2>
        <p>No region markers here</p>
      </div>
    `)
    // The visible text fallback triggers when contentRows < 3; with 0 rows and
    // contentEls=[screenEl] the VISIBLE TEXT section should include the page text.
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).toContain('VISIBLE TEXT')
    expect(raw).toContain('Agent list')
  })

  it('does NOT fall back when [data-ctx-region="content"] is present', () => {
    // Should read only the content region, not the outer wrapper noise.
    setBody(`
      <div data-ctx-screen="Cash">
        <div class="sidebar-stuff">Sidebar noise</div>
        <div data-ctx-region="content">
          <p>Account rows</p>
        </div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set())
    // When there IS a content region the fallback is NOT triggered, so sidebar text
    // stays out of scope (it's not inside the content region).
    expect(raw).not.toContain('Sidebar noise')
  })
})

// ── Tests: screenSuggestions — data-ctx-screen chain ─────────────────────────

describe('screenSuggestions — data-ctx-screen fallback', () => {
  it('uses data-ctx-screen when no data-ctx-title is present', () => {
    setBody('<div data-ctx-screen="Connectors"><p>Integration settings</p></div>')
    const suggestions = screenSuggestions()
    expect(suggestions.some((s) => s.includes('Connectors'))).toBe(true)
  })

  it('still prefers data-ctx-title over data-ctx-screen', () => {
    setBody(`
      <div data-ctx-screen="Settings">
        <h1 data-ctx-title="Tax">Tax</h1>
      </div>
    `)
    const suggestions = screenSuggestions()
    expect(suggestions.some((s) => s.includes('Tax'))).toBe(true)
    expect(suggestions.some((s) => s.includes('Settings'))).toBe(false)
  })
})

// ── Tests: data-ctx-occluder (edge occluders) ─────────────────────────────────
//
// getScreenSnapshot receives a pre-filtered visibleIds set from useVisibleIds.
// These tests verify that rows excluded by the occluder scan (not in visibleIds)
// are correctly classified as "ALSO ON THIS SCREEN" rather than "VISIBLE NOW",
// and that occluder elements themselves are never leaked into the snapshot body.

describe('getScreenSnapshot — data-ctx-occluder contract', () => {
  it('a row passed in visibleIds still appears under VISIBLE NOW even when an occluder is present', () => {
    setBody(`
      <div data-ctx-occluder data-ctx-occluder-edge="top" data-ctx-ignore>Topbar</div>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="below-bar" data-ctx-type="transaction"
             data-ctx-fields='{"desc":"Coffee","amount":"-S$5"}'>Coffee</div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['below-bar']))
    expect(raw).toContain('VISIBLE NOW')
    expect(raw).toContain('desc=Coffee')
  })

  it('a row NOT in visibleIds (filtered by occluder scan) lands in ALSO ON THIS SCREEN', () => {
    setBody(`
      <div data-ctx-occluder data-ctx-occluder-edge="top" data-ctx-ignore>Topbar</div>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="under-bar" data-ctx-type="transaction"
             data-ctx-fields='{"desc":"Rent","amount":"-S$2000"}'>Rent</div>
      </div>
    `)
    // useVisibleIds would exclude this row; simulate by NOT passing it in visibleIds
    const { raw } = getScreenSnapshot(new Set())
    expect(raw).not.toContain('VISIBLE NOW')
    expect(raw).toContain('ALSO ON THIS SCREEN')
    expect(raw).toContain('desc=Rent')
  })

  it('occluder element text does NOT appear in the snapshot (data-ctx-ignore respected)', () => {
    setBody(`
      <div data-ctx-occluder data-ctx-occluder-edge="top" data-ctx-ignore>
        As of Jan 2026 • Prices: live • Search • Hide amounts
      </div>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="r1" data-ctx-type="holding"
             data-ctx-fields='{"ticker":"VOO","value":"S$320,000"}'>VOO</div>
      </div>
    `)
    const { raw } = getScreenSnapshot(new Set(['r1']))
    expect(raw).not.toContain('Prices: live')
    expect(raw).not.toContain('Hide amounts')
    expect(raw).toContain('ticker=VOO')
  })

  it('occluder row is never itself listed as a content row', () => {
    // Occluder element has a data-entity-id — must NOT appear in rows
    setBody(`
      <div data-ctx-occluder data-ctx-occluder-edge="top"
           data-entity-id="topbar-entity">Topbar</div>
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="real-row" data-ctx-type="holding">Row</div>
      </div>
    `)
    // Even if the occluder had an entity id it must not pollute the rows
    // getScreenSnapshot only queries rows inside content/nav regions so this
    // is implicitly guaranteed — but let's assert it explicitly.
    const { raw } = getScreenSnapshot(new Set(['topbar-entity', 'real-row']))
    // The occluder is outside [data-ctx-region="content"] so regionOf → null → filtered
    const visibleNowCount = (raw.match(/\d+\. /g) ?? []).length
    // Only real-row should be numbered
    expect(visibleNowCount).toBe(1)
  })
})

// ── Tests: In view count fact ─────────────────────────────────────────────────

describe('getScreenSnapshot — In view count fact', () => {
  it('reports visible row count by type in facts', () => {
    setBody(`
      <div data-ctx-region="content">
        <div data-ctx data-entity-id="h1" data-ctx-type="holding">H1</div>
        <div data-ctx data-entity-id="h2" data-ctx-type="holding">H2</div>
        <div data-ctx data-entity-id="a1" data-ctx-type="account">A1</div>
      </div>
    `)
    const { facts } = getScreenSnapshot(new Set(['h1', 'h2', 'a1']))
    const inViewFact = facts.find(([k]) => k === 'In view')
    expect(inViewFact).toBeDefined()
    expect(inViewFact![1]).toContain('2 holdings')
    expect(inViewFact![1]).toContain('1 account')
  })
})
