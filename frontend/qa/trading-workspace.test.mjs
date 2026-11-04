import assert from 'node:assert/strict'
import { after, afterEach, before, test } from 'node:test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

const appUrl = process.env.APP_URL || 'http://localhost:8092'
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8091'
const out = resolve('qa/browser-use/artifacts/trading-workspace')
for (const address of [appUrl, backendUrl]) {
  const url = new URL(address)
  assert.ok(['localhost', '127.0.0.1'].includes(url.hostname))
  assert.ok(Number(url.port) >= 8090 && Number(url.port) <= 8099)
}
assert.ok(process.env.ONW_QA_NONCE, 'Run through the attested qa-e2e harness')
mkdirSync(out, { recursive: true })

let browser, context, page, workspace, run
let strategyEscapeClosed = false
const networkChanges = []
const networkRecoveries = []
const instrument = { symbol: 'ACME', venue: 'simulation', asset_class: 'stock', currency: 'USD' }

async function api(path, { method = 'GET', body, headers = {}, expected = 200 } = {}) {
  const response = await fetch(`${backendUrl}${path}`, {
    method, headers: { 'Content-Type': 'application/json', ...headers },
    signal: AbortSignal.timeout(10000),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await response.text()
  assert.equal(response.status, expected, `${method} ${path}: ${text}`)
  return text ? JSON.parse(text) : null
}

async function shot(name) {
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true })
}

async function waitUntil(get, check, message) {
  for (let n = 0; n < 50; n++) {
    const value = await get()
    if (check(value)) return value
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error(message)
}

async function selectRun(value, section = 'overview') {
  await page.goto(`${appUrl}/trading?workspace=${value.workspace_id}&run=${value.id}&section=${section}`)
  await page.getByRole('button', { name: 'View mandate', exact: true }).waitFor()
}

async function action(name, confirm = name) {
  await page.getByRole('button', { name, exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: confirm, exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
}

async function createRun(workspaceId, { capital = '1000', volume = '1' } = {}) {
  return api(`/api/trading/workspaces/${workspaceId}/runs`, {
    method: 'POST', expected: 201,
    body: {
      mode: 'backtest', start_at: '2025-01-02T15:00:00Z', end_at: '2025-01-05T15:00:00Z',
      accounts: [{ name: 'Synthetic account', capital, currency: 'USD', venue: 'simulation' }],
      observations: [0, 1, 2].map((hour) => ({ instrument, timestamp: `2025-01-02T${15 + hour}:00:00Z`, price: String(100 + hour), volume })),
      data_version: 'qa-trd-v1', agent_version: 'qa-v1',
    },
  })
}

before(async () => {
  await api('/api/qa/attest', { headers: { 'X-ONW-QA-Nonce': process.env.ONW_QA_NONCE } })
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  page = await context.newPage()
  writeFileSync(`${out}/network-recovery.json`, '[]')
  const failedRequests = []
  page.on('requestfailed', (request) => {
    if (!new URL(request.url()).pathname.startsWith('/api/')) return
    failedRequests.push({ path: new URL(request.url()).pathname, failure: request.failure()?.errorText })
    if (request.failure()?.errorText === 'net::ERR_NETWORK_CHANGED') networkChanges.push(new URL(request.url()).pathname)
    writeFileSync(`${out}/request-failures.json`, JSON.stringify(failedRequests, null, 2))
  })
  page.setDefaultTimeout(6000)
})

after(async () => {
  await page?.screenshot({ path: `${out}/final-state.png`, fullPage: true }).catch(() => {})
  await browser?.close()
})

afterEach(async (context) => {
  await shot(`after-${context.name.slice(0, 18).replace(/[^a-zA-Z0-9-]/g, '-')}`)
})

test('TRD-01: create workspace and immutable multi-account synthetic run through UI', async () => {
  let release
  const ready = new Promise((resolve) => { release = resolve })
  await page.route('**/api/trading/workspaces', async (route) => { await ready; await route.continue() })
  try {
    await page.goto(`${appUrl}/trading`, { waitUntil: 'domcontentloaded' })
    await page.getByText('Loading trading workspaces…', { exact: true }).waitFor()
    await shot('TRD-01-loading')
  } finally {
    release()
    await page.unrouteAll({ behavior: 'wait' })
  }
  await page.getByText('Create your first trading workspace', { exact: true }).waitFor()
  await shot('TRD-01-empty')
  await page.getByRole('button', { name: 'New workspace', exact: true }).first().click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Name', { exact: true }).fill('QA Trading workspace')
  await modal.getByLabel('Instructions', { exact: true }).fill('Synthetic QA account only.')
  await modal.getByRole('button', { name: 'Create workspace', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  workspace = (await api('/api/trading/workspaces')).items.find((item) => item.name === 'QA Trading workspace')
  assert.ok(workspace)
  await page.waitForURL((url) => url.searchParams.get('workspace') === workspace.id)
  await page.getByRole('button', { name: 'Create a run', exact: true }).waitFor()
  await page.reload()
  await page.getByRole('button', { name: 'Create a run', exact: true }).click()
  await modal.getByLabel('Environment', { exact: true }).selectOption('backtest')
  await modal.getByLabel('Start', { exact: true }).fill('2025-01-02T15:00')
  await modal.getByLabel('End', { exact: true }).fill('2025-01-05T15:00')
  await modal.getByRole('button', { name: 'Add account', exact: true }).click()
  await modal.getByRole('button', { name: 'Load synthetic example', exact: true }).click()
  assert.match(await modal.getByLabel('Dataset version', { exact: true }).inputValue(), /synthetic/)
  await shot('TRD-01-configured')
  await modal.getByRole('button', { name: 'Create run', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  run = (await api(`/api/trading/workspaces/${workspace.id}/runs`)).items.at(-1)
  assert.equal(run.mode, 'backtest')
  assert.equal((await api(`/api/trading/runs/${run.id}/accounts`)).items.length, 2)
  const market = await api(`/api/trading/runs/${run.id}/market-data`)
  assert.deepEqual(new Set(market.items.map((row) => row.instrument.asset_class)), new Set(['stock', 'crypto']))
  await shot('TRD-01-ready')
})

test('TRD-02: real lifecycle transitions retain immutable run identity', async () => {
  assert.ok(run, 'TRD-01 must create a run')
  await selectRun(run)
  await action('Start run')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).state, 'running')
  await action('Pause', 'Pause run')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).state, 'paused')
  await shot('TRD-02-paused')
  await action('Resume', 'Resume run')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).mode, 'backtest')
  await page.getByRole('button', { name: 'View mandate', exact: true }).click()
  await page.getByRole('dialog').waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await shot('TRD-02-running')
})

test('TRD-04: execute real isolated strategy, retain source and scoped access without browser persistence', async () => {
  assert.ok(run)
  await api('/api/tool-permissions/submit_trading_order', { method: 'PUT', body: { level: 'always_allow' } })
  await selectRun(run)
  await page.getByRole('button', { name: 'Run strategy script', exact: true }).click()
  const modal = page.getByRole('dialog')
  const source = 'import json,sys\ns=json.load(sys.stdin)\na=s["accounts"]["items"][0]\ni=s["observations"]["items"][0]["instrument"]\nprint(json.dumps({"summary":"QA authored strategy", "orders":[{"account_id":a["id"],"instrument":i,"side":"buy","quantity":"2","request_id":"qa-ui-script-order"}]}))'
  await modal.getByLabel('Python strategy source', { exact: true }).fill(source)
  await modal.getByRole('button', { name: 'Execute strategy', exact: true }).click()
  await modal.getByText(/Script finished/).waitFor({ timeout: 15000 })
  await shot('TRD-04-script')
  await page.keyboard.press('Escape')
  strategyEscapeClosed = await modal.waitFor({ state: 'hidden', timeout: 1000 }).then(() => true).catch(() => false)
  if (!strategyEscapeClosed) {
    await shot('TRD-06-strategy-escape-failed')
    await modal.getByRole('button', { name: 'Close', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
  }
  assert.equal((await api(`/api/trading/runs/${run.id}/orders`)).items.length, 1)
  await page.getByRole('button', { name: 'External agent access', exact: true }).click()
  await modal.getByRole('button', { name: 'Issue run credential', exact: true }).click()
  const token = await modal.getByLabel('Scoped credential', { exact: true }).inputValue()
  assert.ok(token.length >= 32)
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  assert.ok(!storage.includes(token))
  const gateway = `/api/trading/gateway/${run.id}`
  await api(gateway, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: { jsonrpc: '2.0', id: 1, method: 'tools/list' } })
  await modal.getByRole('button', { name: 'Revoke run access', exact: true }).click()
  await modal.getByText('Run credentials revoked.', { exact: true }).waitFor()
  await api(gateway, { method: 'POST', expected: 401, headers: { Authorization: `Bearer ${token}` }, body: { jsonrpc: '2.0', id: 2, method: 'tools/list' } })
  await shot('TRD-04-access-revoked')
  await page.keyboard.press('Escape')
})

test('TRD-03: advance actual simulation and inspect resulting orders, fills and holdings', async () => {
  assert.ok(run)
  await selectRun(run)
  await page.getByRole('button', { name: 'Advance historical environment', exact: true }).click()
  const fills = await waitUntil(() => api(`/api/trading/runs/${run.id}/fills`), (value) => value.items.length > 0, 'No fill after historical advance')
  assert.equal(fills.items[0].quantity, '2')
  const performance = await api(`/api/trading/runs/${run.id}/performance`)
  assert.equal(performance.valuation_complete, true)
  const networkCheckpoint = networkChanges.length
  await selectRun(run, 'orders')
  await page.getByRole('button', { name: /View order/ }).first().waitFor({ timeout: 3000 }).catch(async (error) => {
    const changed = networkChanges.slice(networkCheckpoint)
    if (!changed.includes(`/api/trading/runs/${run.id}/orders`)) throw error
    const retry = page.getByRole('alert').filter({ hasText: 'Some execution records could not be refreshed' }).getByRole('button', { name: 'Retry', exact: true })
    await shot('TRD-03-network-changed-before-retry')
    await retry.click()
    networkRecoveries.push({ paths: changed, action: 'Clicked visible execution-records Retry once' })
    writeFileSync(`${out}/network-recovery.json`, JSON.stringify(networkRecoveries, null, 2))
  })
  await page.getByRole('button', { name: /View order/ }).first().click()
  await page.getByRole('dialog').waitFor()
  assert.match(await page.getByRole('dialog').getByText(/Fees/).innerText(), /2025/)
  await shot('TRD-03-order-fills')
  await page.keyboard.press('Escape')
  await selectRun(run, 'holdings')
  await page.getByRole('table').first().waitFor()
  await shot('TRD-03-holdings')
  await selectRun(run, 'artifacts')
  await page.getByText(/^script started$/i).waitFor()
  await shot('TRD-03-artifacts')
  await page.getByRole('button', { name: 'View artifact details', exact: true }).first().click()
  const artifact = page.getByRole('dialog')
  await artifact.getByText(/qa-ui-script-order/).waitFor()
  await shot('TRD-03-artifact-source')
  await page.keyboard.press('Escape')
  await artifact.waitFor({ state: 'hidden' })
})

test('TRD-02 edge: cancellation retains a real partial fill and its cash movement', async () => {
  assert.ok(workspace)
  const partial = await createRun(workspace.id)
  await api(`/api/trading/runs/${partial.id}/actions`, { method: 'POST', body: { action: 'start' } })
  const account = (await api(`/api/trading/runs/${partial.id}/accounts`)).items[0]
  const order = await api(`/api/trading/runs/${partial.id}/orders`, {
    method: 'POST', expected: 201,
    body: { account_id: account.id, instrument, side: 'buy', quantity: '2', request_id: 'qa-partial' },
  })
  await api(`/api/trading/runs/${partial.id}/actions`, { method: 'POST', body: { action: 'advance' } })
  const before = (await api(`/api/trading/runs/${partial.id}/orders`)).items[0]
  assert.equal(before.status, 'partially_filled')
  assert.equal(before.filled_quantity, '1')
  await selectRun(partial, 'orders')
  await page.getByRole('button', { name: 'Cancel order ACME', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Request cancellation', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  const canceled = await waitUntil(() => api(`/api/trading/runs/${partial.id}/orders`), (value) => value.items[0]?.status === 'canceled', 'Cancellation did not settle')
  assert.equal(canceled.items[0].id, order.id)
  assert.equal(canceled.items[0].filled_quantity, '1')
  assert.equal((await api(`/api/trading/runs/${partial.id}/accounts`)).items[0].cash, '899')
  await shot('TRD-02-partial-canceled')
})

test('TRD-03 edge: failed refresh retains financial values and Retry restores updates', async () => {
  await selectRun(run)
  await page.getByText('$2,000.00', { exact: true }).first().waitFor()
  const pattern = `**/api/trading/runs/${run.id}/performance`
  await page.route(pattern, (route) => route.abort())
  const error = page.getByRole('alert').filter({ hasText: 'Valuation updates interrupted.' })
  try {
    await api(`/api/trading/runs/${run.id}/decisions`, { method: 'POST', expected: 201, body: { summary: 'QA refresh failure probe' } })
    await error.waitFor()
    assert.equal(await page.getByText('$2,000.00', { exact: true }).first().isVisible(), true)
    await shot('TRD-03-stale-fetch-error')
  } finally {
    await page.unroute(pattern)
  }
  await error.getByRole('button', { name: 'Retry', exact: true }).click()
  await error.waitFor({ state: 'hidden' })
  await shot('TRD-03-retry-recovered')
})

test('TRD-01 edge: rejected draft survives and unconfigured live start remains refused', async () => {
  assert.ok(run)
  await selectRun(run)
  await page.getByRole('button', { name: 'New run', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Reporting currency', { exact: true }).fill('EUR')
  await modal.getByLabel('Account label', { exact: true }).fill('Retained draft account')
  await modal.getByRole('button', { name: 'Create run', exact: true }).click()
  await modal.getByRole('alert').waitFor()
  assert.equal(await modal.getByLabel('Account label', { exact: true }).inputValue(), 'Retained draft account')
  await shot('TRD-01-rejected-draft')
  await modal.getByLabel('Reporting currency', { exact: true }).fill('USD')
  await modal.getByLabel('Environment', { exact: true }).selectOption('live')
  assert.equal(await modal.getByRole('button', { name: 'Create run', exact: true }).isDisabled(), true)
  await modal.getByLabel('Broker connector ID', { exact: true }).fill('mcp:synthetic-unconfigured')
  await modal.getByLabel('Dedicated broker account ID', { exact: true }).fill('synthetic-unfunded-account')
  await modal.getByRole('checkbox').check()
  await modal.getByRole('button', { name: 'Create run', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  const live = (await api(`/api/trading/workspaces/${workspace.id}/runs`)).items.at(-1)
  await page.getByRole('button', { name: 'Start run', exact: true }).click()
  await modal.getByRole('button', { name: 'Start run', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: /No verified live trading adapter/ }).waitFor()
  assert.equal((await api(`/api/trading/runs/${live.id}`)).state, 'ready')
  await shot('TRD-01-live-refused')
  await page.keyboard.press('Escape')
})

test('TRD-05: compatible comparison results are derived from real persisted runs', async () => {
  assert.ok(workspace)
  const first = await createRun(workspace.id)
  const second = await createRun(workspace.id)
  for (const value of [first, second]) {
    await api(`/api/trading/runs/${value.id}/actions`, { method: 'POST', body: { action: 'start' } })
    await api(`/api/trading/runs/${value.id}/actions`, { method: 'POST', body: { action: 'advance' } })
  }
  await page.goto(`${appUrl}/trading?view=compare`)
  await page.getByRole('button', { name: 'New comparison', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByLabel('Comparison name', { exact: true }).fill('QA shared scenario')
  await modal.getByRole('checkbox').nth(-2).check()
  await modal.getByRole('checkbox').last().check()
  await modal.getByRole('button', { name: 'Create comparison', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
  const rows = (await api('/api/trading/comparisons')).items
  const comparison = rows.find((item) => item.name === 'QA shared scenario')
  assert.deepEqual(new Set(comparison.run_ids), new Set([first.id, second.id]))
  await page.waitForTimeout(500)
  await shot('TRD-05-comparison')
  const different = await createRun(workspace.id, { capital: '1234' })
  await page.reload()
  await page.getByRole('button', { name: 'New comparison', exact: true }).click()
  await modal.getByLabel('Comparison name', { exact: true }).fill('QA incompatible scenario')
  await modal.getByRole('checkbox').nth(-2).check()
  await modal.getByRole('checkbox').last().check()
  await modal.getByRole('button', { name: 'Create comparison', exact: true }).click()
  await modal.getByRole('alert').waitFor()
  assert.equal((await api('/api/trading/comparisons')).items.length, rows.length)
  await shot('TRD-05-incompatible-refused')
  await page.keyboard.press('Escape')
  await modal.waitFor({ state: 'hidden' })
  assert.notEqual(first.id, different.id)
})

test('TRD-06: actual responsive layouts and retained run URL', async () => {
  assert.ok(run)
  await selectRun(run, 'orders')
  await page.reload()
  await page.getByRole('button', { name: /View order/ }).first().waitFor()
  const metrics = []
  async function check(width, sidebar, chat) {
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    const details = page.getByRole('button', { name: /View order/ }).first()
    await details.scrollIntoViewIfNeeded()
    await page.waitForTimeout(250)
    const size = await details.evaluate((button) => {
      const rect = button.getBoundingClientRect()
      const painted = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
      const panel = document.querySelector('.lx-side.on')
      const panelRect = panel?.getBoundingClientRect()
      const panelPaint = panelRect && document.elementFromPoint(panelRect.x + panelRect.width / 2, panelRect.y + 60)
      return {
        page: document.documentElement.scrollWidth, viewport: innerWidth,
        actionClickable: button === painted || button.contains(painted),
        panelVisible: !!panel,
        panelUnoccluded: !panel || panel === panelPaint || panel.contains(panelPaint),
      }
    })
    metrics.push({ width, sidebar, chat, ...size })
    writeFileSync(`${out}/responsive-metrics.json`, JSON.stringify(metrics, null, 2))
    await shot(`TRD-06-${width}-${sidebar}-${chat}`)
    assert.ok(size.page <= size.viewport + 1, `${width}/${sidebar}/${chat}: document overflow ${size.page}`)
    assert.equal(size.actionClickable, true, `${width}/${sidebar}/${chat}: Orders Details is occluded at bottom scroll`)
    assert.equal(size.panelVisible, chat === 'open')
    assert.equal(size.panelUnoccluded, true, `${width}/${sidebar}/${chat}: Lucius panel is occluded`)
    await details.click()
    await page.getByRole('dialog').waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
  }
  for (const width of [640, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    if (width === 640) {
      for (const chat of ['closed', 'open']) {
        if (chat === 'open') {
          await page.locator('#chat-toggle-btn').click()
          await page.locator('.lx-side.on').waitFor()
          await page.mouse.move(width / 2, 60)
          const coachmark = page.getByRole('button', { name: 'Done', exact: true })
          if (await coachmark.waitFor({ timeout: 1500 }).then(() => true).catch(() => false)) await coachmark.click()
        }
        await check(width, 'drawer-closed', chat)
        await page.getByRole('button', { name: 'Open navigation menu', exact: true }).click()
        const drawer = page.locator('.sidebar.drawer-open')
        await drawer.waitFor()
        await page.waitForTimeout(250)
        assert.ok(await drawer.getByRole('button', { name: 'Trading', exact: true }).isVisible())
        await shot(`TRD-06-${width}-drawer-open-${chat}`)
        await page.locator('.sidebar-scrim').click({ position: { x: 300, y: 400 } })
        await drawer.waitFor({ state: 'hidden' })
        if (chat === 'open') {
          await page.locator('.lx-side.on').getByRole('button', { name: 'Collapse', exact: true }).click()
          await page.locator('.lx-side.on').waitFor({ state: 'hidden' })
        }
      }
      continue
    }
    for (const sidebar of ['expanded', 'collapsed']) {
      const toggle = page.locator('.sidebar-toggle').first()
      if ((await toggle.getAttribute('title') === 'Collapse') !== (sidebar === 'expanded')) await toggle.click()
      await page.mouse.move(width / 2, 60)
      await check(width, sidebar, 'closed')
      await page.locator('#chat-toggle-btn').click()
      await page.locator('.lx-side.on').waitFor()
      if ((await toggle.getAttribute('title') === 'Collapse') !== (sidebar === 'expanded')) await toggle.click()
      await page.mouse.move(width / 2, 60)
      await check(width, sidebar, 'open')
      await page.locator('.lx-side.on').getByRole('button', { name: 'Collapse', exact: true }).click()
      await page.locator('.lx-side.on').waitFor({ state: 'hidden' })
    }
  }
  await action('Stop run')
  assert.equal((await api(`/api/trading/runs/${run.id}`)).state, 'stopped')
  await shot('TRD-06-stopped')
})

test('TRD-06: Escape closes the strategy modal after real execution', () => {
  assert.equal(strategyEscapeClosed, true, 'Successful strategy dialog did not close on Escape; see TRD-06-strategy-escape-failed.png')
})
