import assert from 'node:assert/strict'
import { after, afterEach, before, test } from 'node:test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

const appUrl = process.env.APP_URL || 'http://localhost:8092'
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8091'
const out = resolve('qa/browser-use/artifacts/trading-workspace')
const fixtureId = Date.now().toString(36)
const workspaceName = `Synthetic delegated browser acceptance ${fixtureId}`
const comparisonName = `Synthetic common-window comparison ${fixtureId}`
const comparisonActors = [`Comparison actor A ${fixtureId}`, `Comparison actor B ${fixtureId}`]
for (const address of [appUrl, backendUrl]) {
  const url = new URL(address)
  assert.ok(['localhost', '127.0.0.1'].includes(url.hostname))
  assert.ok(Number(url.port) >= 8090 && Number(url.port) <= 8099)
}
assert.ok(process.env.ONW_QA_NONCE, 'Use the attested qa-e2e lifecycle')
mkdirSync(out, { recursive: true })
const instrument = { symbol: 'ACME', venue: 'simulation', asset_class: 'stock', currency: 'USD' }
const instructions = 'Manage this synthetic allocation autonomously. Research, code, trade or wait within the reviewed limits.'
const observations = [0, 1, 2].map((minute) => ({ instrument, timestamp: `2025-01-02T15:0${minute}:00Z`, available_at: `2025-01-02T15:0${minute}:00Z`, price: String(100 + minute), open: String(99 + minute), high: String(102 + minute), low: String(98 + minute), close: String(100 + minute), volume: '100', interval: '1m', source: 'synthetic-acceptance' }))
let browser, context, page, workspace, run, token, decision, fill, fills
const failures = []
const errors = []
async function api(path, { method = 'GET', body, headers = {}, expected = 200 } = {}) {
  const response = await fetch(`${backendUrl}${path}`, { method, headers: { 'Content-Type': 'application/json', ...headers }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000) })
  const text = await response.text()
  assert.equal(response.status, expected, `${method} ${path}: ${text}`)
  return text ? JSON.parse(text) : null
}
async function gateway(name, args = {}) {
  const response = await api(`/api/trading/gateway/${run.id}`, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: { jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } } })
  assert.ok(!response.error && !response.result.isError, JSON.stringify(response))
  return JSON.parse(response.result.content[0].text)
}
async function waitUntil(get, check, message) {
  for (let n = 0; n < 180; n++) {
    const value = await get()
    if (check(value)) return value
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(message)
}
async function shot(name) { await page.screenshot({ path: `${out}/${name}.png`, fullPage: true }) }
async function confirm(name) {
  await page.getByRole('button', { name, exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: name.includes('workspace') ? name : `${name} workspace`, exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
}
before(async () => {
  await api('/api/qa/attest', { headers: { 'X-ONW-QA-Nonce': process.env.ONW_QA_NONCE } })
  const monitoring = await api('/api/watchlist/config', { method: 'PATCH', body: { enabled: false } })
  assert.equal(monitoring.enabled, false)
  const automations = await api('/api/automations?limit=500')
  assert.ok(automations.total <= 500)
  for (const automation of automations.automations) if (automation.status === 'active') await api(`/api/automations/${automation.id}`, { method: 'PATCH', body: { status: 'paused' } })
  const previous = await api('/api/trading/runs?limit=500')
  assert.ok(previous.total <= 500)
  for (const item of previous.items) {
    assert.ok(['backtest', 'paper'].includes(item.mode), 'The QA source must contain only simulated environments')
    assert.ok(!item.external_execution, 'The QA source must not contain a connected execution adapter')
    for (const account of item.accounts) assert.ok(!account.connector_id && !account.broker_account_id)
    if (item.state === 'running') await api(`/api/trading/runs/${item.id}/actions`, { method: 'POST', body: { action: 'pause' } })
  }
  assert.ok(!(await api('/api/automations?limit=500')).automations.some((item) => item.status === 'active'))
  assert.ok(!(await api('/api/trading/runs?limit=500')).items.some((item) => item.state === 'running'))
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, timezoneId: 'UTC' })
  page = await context.newPage()
  page.setDefaultTimeout(8000)
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('requestfailed', (request) => { if (new URL(request.url()).pathname.startsWith('/api/')) failures.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText }) })
})
afterEach(async (test) => { await shot(`redesign-${test.name.slice(0, 16).replace(/[^a-zA-Z0-9-]/g, '-')}`) })
after(async () => {
  if (token && run) await api(`/api/trading/runs/${run.id}/access`, { method: 'DELETE' }).catch(() => {})
  if (run) await api(`/api/trading/runs/${run.id}/actions`, { method: 'POST', body: { action: 'stop' } }).catch(() => {})
  writeFileSync(`${out}/request-failures.json`, JSON.stringify(failures, null, 2))
  writeFileSync(`${out}/browser-errors.json`, JSON.stringify(errors, null, 2))
  await browser?.close()
})

test('TRD-01: one reviewed delegation creates a persistent workspace', async () => {
  await page.goto(`${appUrl}/trading`)
  await page.getByRole('button', { name: 'Create with Lucius', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Expert setup', exact: true }).first().click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Name', { exact: true }).fill(workspaceName)
  await modal.getByLabel('Original mandate', { exact: true }).fill(instructions)
  await modal.getByLabel('Environment', { exact: true }).selectOption('backtest')
  await modal.getByLabel('Start', { exact: true }).fill('2025-01-02T15:00')
  await modal.getByLabel('End', { exact: true }).fill('2025-01-02T15:10')
  await modal.getByLabel('Executor connection', { exact: true }).selectOption('external')
  await modal.getByLabel('Executor label', { exact: true }).fill('Synthetic external executor')
  await modal.getByLabel('Market data', { exact: true }).selectOption('supplied')
  await modal.getByText('Data and currency settings', { exact: true }).click()
  await modal.getByLabel('Observation dataset (JSON)', { exact: true }).fill(JSON.stringify(observations))
  await modal.getByLabel('Dataset version', { exact: true }).fill('delegated-browser-v2')
  await modal.getByRole('button', { name: 'Review workspace', exact: true }).click()
  await modal.getByText('Unsaved draft.', { exact: false }).waitFor()
  assert.equal((await api('/api/trading/workspaces')).items.filter((w) => w.name === workspaceName).length, 0)
  await shot('redesign-review')
  await modal.getByRole('button', { name: 'Create and start workspace', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  workspace = (await api('/api/trading/workspaces')).items.find((w) => w.name === workspaceName)
  assert.ok(workspace)
  run = (await api(`/api/trading/workspaces/${workspace.id}/overview`)).current_run
  assert.equal(run.state, 'running')
  await page.getByRole('heading', { name: 'Market & portfolio', exact: true }).waitFor()
  await page.getByRole('heading', { name: 'Agent work', exact: true }).waitFor()
  await page.getByRole('img', { name: /ACME candlestick/ }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Advance historical environment', exact: true }).count(), 0)
  await page.reload()
  await page.getByRole('heading', { name: workspace.name, exact: true }).waitFor()
})

test('TRD-02: pause, amend and resume preserve original authority', async () => {
  await confirm('Pause')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).state, 'paused')
  await page.getByRole('button', { name: 'Amend mandate', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Maximum order value', { exact: true }).fill('250')
  await modal.getByLabel('Reason for amendment', { exact: true }).fill('Synthetic order boundary for browser acceptance')
  await modal.getByRole('button', { name: 'Review amendment', exact: true }).click()
  await modal.getByRole('button', { name: 'Apply amendment', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  const overview = await api(`/api/trading/workspaces/${workspace.id}/overview`)
  assert.equal(overview.amendments.length, 1)
  assert.equal(overview.original_mandate.instructions, instructions)
  await page.getByRole('button', { name: 'Original mandate', exact: true }).click()
  await modal.getByText(instructions, { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await confirm('Resume')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).state, 'running')
})

test('TRD-04: actual external tools persist files and host-observed evidence', async () => {
  await page.getByText('Executor connection and manual diagnostics', { exact: true }).click()
  await page.getByRole('button', { name: 'External agent access', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByRole('button', { name: 'Issue run credential', exact: true }).click()
  token = await modal.getByLabel('Scoped credential', { exact: true }).inputValue()
  assert.ok(token.length >= 32)
  const persistence = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  assert.ok(!persistence.includes(token))
  await page.keyboard.press('Escape')
  await modal.waitFor({ state: 'hidden' })
  for (const name of ['get_trading_environment', 'get_trading_account', 'get_trading_market_data', 'write_trading_file', 'read_trading_files', 'record_trading_work', 'record_trading_decision', 'submit_trading_order', 'run_trading_strategy']) await api(`/api/tool-permissions/${name}`, { method: 'PUT', body: { level: 'always_allow', connector_id: 'core' } })
  await gateway('get_trading_market_data')
  await gateway('write_trading_file', { name: 'research.md', content: 'Synthetic fixture analysis: inspect the initial bar and place one guarded test order.' })
  await gateway('write_trading_file', { name: 'research.md', content: 'Synthetic revised analysis: execute two guarded orders under one recorded strategy decision.' })
  await gateway('record_trading_work', { kind: 'analysis', payload: { summary: 'Synthetic executor report: reviewed the supplied initial bar.', actor_id: 'synthetic-executor' } })
  await gateway('record_trading_decision', { summary: 'Synthetic external client will execute a guarded purchase strategy.', wake_after_seconds: 60 })
  const execution = await gateway('run_trading_strategy', { source: 'import json,sys\ns=json.load(sys.stdin)\na=s["accounts"]["items"][0]\ni=s["observations"]["items"][0]["instrument"]\norders=[{"account_id":a["id"],"instrument":i,"side":"buy","quantity":str(q),"request_id":"delegated-browser-order-"+str(q)} for q in (1,2)]\nprint(json.dumps({"orders":orders,"summary":"Synthetic browser acceptance purchase"}))' })
  assert.equal(execution.orders.length, 2)
  assert.equal(execution.orders[0].decision_id, execution.orders[1].decision_id)
  decision = { id: execution.orders[0].decision_id }
  const result = await waitUntil(() => api(`/api/trading/runs/${run.id}/fills`), (value) => value.items.length === 2, 'Scheduler did not automatically advance and match both synthetic orders')
  fills = result.items
  fill = fills.find((item) => item.order_id === execution.orders[0].id)
  await page.reload()
  await page.getByRole('heading', { name: 'Agent work', exact: true }).waitFor()
  await page.getByText('Synthetic executor report: reviewed the supplied initial bar.', { exact: true }).waitFor()
  await page.getByRole('tab', { name: 'Files', exact: true }).click()
  await page.getByRole('button', { name: 'research.md · v2', exact: true }).click()
  await page.getByLabel('File version', { exact: true }).selectOption('1')
  await page.getByText('Synthetic fixture analysis: inspect the initial bar and place one guarded test order.', { exact: true }).waitFor()
  const saved = await api(`/api/trading/runs/${run.id}/files?name=research.md&version=1`)
  assert.match(saved.content, /Synthetic fixture analysis/)
  await page.getByRole('tab', { name: 'Timeline', exact: true }).click()
})

test('TRD-03: fill links to actual decision, as-of data and account statement', async () => {
  const decisionArticle = page.getByText('Synthetic browser acceptance purchase', { exact: true }).locator('xpath=ancestor::article')
  await decisionArticle.getByRole('button', { name: 'Inspect linked evidence', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByRole('button', { name: /Inspect order ACME/ }).first().waitFor()
  assert.equal(await modal.getByRole('button', { name: /Inspect order ACME/ }).count(), 2)
  const evidence = await api(`/api/trading/runs/${run.id}/evidence/event/${decision.id}`)
  assert.equal(evidence.orders.length, 2)
  assert.equal(evidence.fills.length, 2)
  await shot('redesign-multiorder-decision')
  await modal.getByRole('button', { name: /Inspect order ACME · buy 2/ }).click()
  await modal.getByText('buy 2 ACME · simulation', { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /Buy 1 ACME @/ }).click()
  await modal.getByRole('tab', { name: 'Data', exact: true }).click()
  await modal.getByText('Data available at this decision', { exact: true }).waitFor()
  await modal.getByRole('tab', { name: 'Agent work', exact: true }).click()
  await modal.getByText('Recorded decision', { exact: true }).waitFor()
  await modal.getByRole('tab', { name: 'Files', exact: true }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('tab', { name: 'Statement', exact: true }).click()
  await page.getByText('Account statement', { exact: true }).waitFor()
  const statement = await api(`/api/trading/runs/${run.id}/statement`)
  assert.equal(statement.accounts[0].closing_cash, String(1000 - fills.reduce((total, item) => total + Number(item.quantity) * Number(item.price) + Number(item.fees), 0)))
  assert.equal(statement.accounts[0].cash_reconciled, true)
  assert.equal(statement.fills.length, 2)
  writeFileSync(`${out}/redesign-statement.json`, JSON.stringify(statement, null, 2))
  await shot('redesign-statement')
  await page.getByRole('tab', { name: 'Chart', exact: true }).click()
})

test('TRD-03: full large execution records remain retrievable in the browser', async () => {
  const execution = await gateway('run_trading_strategy', { source: 'import json\nprint(json.dumps({"orders":[],"summary":"Synthetic large-output verification","report":"BEGIN LARGE WORK RECEIPT\\n"+chr(92)*100000+"\\nEND LARGE WORK RECEIPT"}))' })
  assert.ok(execution.ok)
  const events = await api(`/api/trading/runs/${run.id}/events?limit=500`)
  const record = events.items.findLast((item) => item.kind === 'agent_tool_result' && item.payload.availability === 'chunked')
  assert.ok(record, 'Actual isolated execution result should be stored as retrievable chunks')
  const full = await api(`/api/trading/runs/${run.id}/work/${record.id}`)
  assert.ok(JSON.stringify(full.payload).includes('END LARGE WORK RECEIPT'))
  await page.reload()
  await page.getByRole('heading', { name: 'Agent work', exact: true }).waitFor()
  const article = page.locator('article.trading-event').filter({ hasText: '"availability": "chunked"' }).last()
  await article.locator('summary').click()
  await article.getByRole('button', { name: 'Load complete record', exact: true }).click()
  const loadedArticle = page.getByRole('button', { name: 'Load complete record', exact: true }).locator('xpath=ancestor::article')
  await waitUntil(() => loadedArticle.locator('pre').textContent(), (text) => text.includes('END LARGE WORK RECEIPT'), 'Browser never loaded the complete execution output')
  assert.equal(await page.locator('.trading-work-stream').getByText(/^agent record part$/i).count(), 0)
  await shot('redesign-full-work-record')
  await loadedArticle.locator('summary').click()
  await page.getByRole('tab', { name: 'Execution', exact: true }).click()
  assert.equal(await page.locator('.trading-work-stream').getByText(/^agent record part$/i).count(), 0)
  await page.getByRole('tab', { name: 'Timeline', exact: true }).click()
})

test('TRD-06: equal layers and compact controls remain reachable', async () => {
  const metrics = []
  for (const width of [1440, 1024, 640, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.getByRole('heading', { name: 'Current work', exact: true }).waitFor()
    const compact = page.getByRole('tab', { name: 'Agent work', exact: true }).filter({ visible: true })
    if (await compact.count()) {
      await compact.click()
      await page.getByRole('heading', { name: 'Agent work', exact: true }).waitFor()
      await page.getByRole('tab', { name: 'Market & portfolio', exact: true }).click()
    }
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.getByRole('heading', { name: 'Trading', exact: true }).scrollIntoViewIfNeeded()
    for (const label of ['Original mandate', '1 amendments', 'Amend mandate']) {
      const button = page.getByRole('button', { name: label, exact: true })
      const bounds = await button.boundingBox()
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `${label} is clipped at ${width}px: ${JSON.stringify(bounds)}`)
      assert.ok(await button.evaluate((element) => {
        const control = element.getBoundingClientRect()
        const panel = element.closest('.trading-mandate-panel')?.getBoundingClientRect()
        return panel && control.left >= panel.left && control.right <= panel.right
      }), `${label} extends beyond its mandate panel at ${width}px`)
      await button.click({ trial: true })
    }
    const metric = await page.evaluate(() => ({ width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1, layers: [...document.querySelectorAll('.trading-observation-grid > div')].map((element) => ({ width: element.getBoundingClientRect().width, display: getComputedStyle(element).display })) }))
    assert.equal(metric.overflow, false, JSON.stringify(metric))
    if (width === 1440) assert.ok(Math.abs(metric.layers[0].width - metric.layers[1].width) < 2)
    metrics.push(metric)
    await shot(`redesign-width-${width}`)
  }
  writeFileSync(`${out}/redesign-responsive.json`, JSON.stringify(metrics, null, 2))
  await page.setViewportSize({ width: 1440, height: 1100 })
})

test('TRD-05: comparison retains explicit common assumptions', async () => {
  const candidates = []
  for (const label of comparisonActors) {
    const result = await api('/api/trading/delegations', { method: 'POST', expected: 201, body: {
      workspace: { name: label, agent_kind: 'external', agent_label: label, instructions: 'Synthetic comparison baseline.' },
      environment: { mode: 'backtest', start_at: '2025-01-02T15:00:00Z', end_at: '2025-01-02T15:10:00Z', accounts: [{ capital: '1000' }], observations, data_version: 'comparison-browser-v2' }, start: false,
    } })
    candidates.push(result)
  }
  await page.getByRole('tab', { name: 'Compare', exact: true }).click()
  await page.getByRole('button', { name: 'New comparison', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Comparison name', { exact: true }).fill(comparisonName)
  for (const name of comparisonActors) await modal.getByRole('checkbox', { name: new RegExp(name) }).check()
  await modal.getByRole('button', { name: 'Create comparison', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  await page.getByText('Shared scenario', { exact: true }).waitFor()
  await page.getByRole('heading', { name: 'Results', exact: true }).waitFor()
  const comparisons = await api('/api/trading/comparisons')
  const comparison = comparisons.items.find((item) => item.name === comparisonName)
  assert.ok(comparison.comparable)
  assert.equal(comparison.results.length, 2)
  assert.equal(comparison.scenario.data_version, 'comparison-browser-v2')
  await page.getByRole('button', { name: new RegExp(`${comparisonActors[0]} ·`) }).click()
  await page.getByRole('heading', { name: comparisonActors[0], exact: true }).waitFor()
  assert.equal(new URL(page.url()).searchParams.get('workspace'), candidates[0].workspace.id)
  assert.deepEqual(errors, [])
})
