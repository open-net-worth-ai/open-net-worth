// GCL — GoCardless bank link, end to end. Acceptance: docs/qa/acceptance/gocardless-bank-link.md.
//
// A disposable backend (fresh tmp data, demo-seeded, never :8080) whose GoCardless API base points
// at an in-process fake; the production build driven in Chromium with its POP-UP BLOCKER ON — the
// Playwright default switches it off, which is how the 2026-11-12 "nothing opens" regression
// passed every test. The bank's consent page is served by request interception on its https host
// (the backend refuses a link that isn't https).
//
//   npm run e2e:gocardless-bank-link            (builds the frontend first)
//   GCL_SKIP_BUILD=1 npm run e2e:gocardless-bank-link

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import {
  assertSafeBoot,
  buildQaChildEnv,
  reserveFreeLoopbackPort,
  resolveQaPython,
  seedDemo,
} from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')
// GCL_MODEL picks the Claude Code model Lucius runs on (sonnet | haiku …); each keeps its own
// artifacts.
const MODEL = process.env.GCL_MODEL || 'sonnet'
const OUT_DIR = resolve(REPO_ROOT, `qa/artifacts/gocardless-bank-link-${MODEL}`)
const LINK_DELAY_MS = Number(process.env.GC_FAKE_LINK_DELAY_MS ?? 8000)
// GoCardless took ~4 s to delete a requisition on the 2026-11-12 NAS log.
const DELETE_DELAY_MS = Number(process.env.GC_FAKE_DELETE_DELAY_MS ?? 2000)
const BANK_HOST = 'https://ob.gocardless.test'
const SECRET_ID = 'gcl-secret-id-0001'
const SECRET_KEY = 'gcl-secret-key-must-never-render'
const BAD_KEY = 'gcl-wrong-key'

rmSync(OUT_DIR, { recursive: true, force: true }) // a failed run's screenshots must not outlive it
mkdirSync(OUT_DIR, { recursive: true })

// ── fake GoCardless Bank Account Data API ─────────────────────────────────────────────

/** The instance's own "today" zone (its profile timezone), read once the backend is up. */
let APP_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone
/** A date `n` days before the instance's today. */
function daysAgo(n) {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: APP_TZ }).format(new Date())
  const d = new Date(`${today}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

/** What the daily sync must land in the book from the fake bank: [account name, date, amount].
 *  The pending taxi fare must not, nor the history older than its two-week window. */
const EXPECTED_BOOKED = [
  ['CA_Monzo', 2, -12.4],
  ['CA_Monzo', 3, 2500],
  ['CA_Monzo', 4, -3],
  ['CA_Monzo', 4, -3],
  ['CA_Monzo', 5, -42.1],
  ['CA_Monzo', 6, -1],
  ['CA_Monzo', 7, -24.99],
  ['CA_Monzo', 8, -850],
  ['CA_Monzo', 10, -60],
  ['Savings Pot', 1, 0.42],
]

/** Older than the daily window: booked only when the user asks Lucius for that range. */
const EXPECTED_HISTORY = [
  ['CA_Monzo', 40, -80],
  ['CA_Monzo', 75, -120],
]

function fakeGoCardless() {
  const state = {
    calls: [],
    tokens: new Set(),
    minted: 0,
    reqSeq: 0,
    requisitions: new Map(), // id → {status, accounts, redirect, institution}
    failNextRequisition: false,
  }
  const ACCOUNTS = {
    'gcl-acc-0001': { name: 'Current Account', iban: 'GB00MONZ00000000001234', type: 'CACC' },
    'gcl-acc-0002': { name: 'Savings Pot', iban: 'GB00MONZ00000000005678', type: 'SVGS' },
  }
  // What a Monzo-like bank returns — each row is a way data could go missing. EXPECTED_BOOKED
  // is what must land in the book, exactly once each.
  const transactions = () => ({
    'gcl-acc-0001': {
      booked: [
        {
          transactionId: 'tx_c1',
          bookingDate: daysAgo(2),
          transactionAmount: { amount: '-12.40', currency: 'GBP' },
          remittanceInformationUnstructured: 'CARD 1234 ACME STORES LONDON',
          merchantCategoryCode: '5411',
          creditorName: 'Acme Grocer',
        },
        {
          transactionId: 'tx_c2',
          bookingDate: daysAgo(3),
          transactionAmount: { amount: '2500.00', currency: 'GBP' },
          debtorName: 'ACME LTD',
        },
        // Two separate coffees, no bank id, identical in every field.
        {
          bookingDate: daysAgo(4),
          transactionAmount: { amount: '-3.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME CAFE',
        },
        {
          bookingDate: daysAgo(4),
          transactionAmount: { amount: '-3.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME CAFE',
        },
        {
          transactionId: 'tx_c5',
          bookingDate: daysAgo(5),
          transactionAmount: { amount: '-42.10', currency: 'GBP' },
          remittanceInformationUnstructured: 'CAFE ACME PARIS',
          currencyExchange: [
            {
              sourceCurrency: 'EUR',
              exchangeRate: '1.1401',
              instructedAmount: { amount: '48.00', currency: 'EUR' },
              targetCurrency: 'GBP',
            },
          ],
        },
        {
          transactionId: 'tx_c6',
          bookingDateTime: `${daysAgo(6)}T08:15:00Z`,
          transactionAmount: { amount: '-1.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME PARKING',
        },
        {
          // No insurance or education category exists: each must get a fitting new one.
          transactionId: 'tx_c8',
          bookingDate: daysAgo(7),
          transactionAmount: { amount: '-24.99', currency: 'GBP' },
          remittanceInformationUnstructured: 'DD ACME LIFE INSURANCE PREMIUM 88412',
          merchantCategoryCode: '6300',
          creditorName: 'Acme Life Insurance',
        },
        {
          transactionId: 'tx_c9',
          bookingDate: daysAgo(8),
          transactionAmount: { amount: '-850.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME UNIVERSITY TUITION FEE TERM 2',
          merchantCategoryCode: '8220',
          creditorName: 'Acme University',
        },
        {
          // History older than two weeks: the daily sync never fetches it (a large model
          // bill nobody asked for); asking Lucius for the range does.
          transactionId: 'tx_c10',
          bookingDate: daysAgo(40),
          transactionAmount: { amount: '-80.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME GYM MEMBERSHIP',
          creditorName: 'Acme Gym',
        },
        {
          transactionId: 'tx_c11',
          bookingDate: daysAgo(75),
          transactionAmount: { amount: '-120.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME TRAINS SEASON TICKET',
          creditorName: 'Acme Trains',
        },
        {
          transactionId: 'tx_c7',
          bookingDate: daysAgo(10),
          transactionAmount: { amount: '-60.00', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME ENERGY DIRECT DEBIT',
        },
      ],
      pending: [
        {
          valueDate: daysAgo(0),
          transactionAmount: { amount: '-5.50', currency: 'GBP' },
          remittanceInformationUnstructured: 'ACME TAXI',
        },
      ],
    },
    'gcl-acc-0002': {
      booked: [
        {
          transactionId: 'tx_s1',
          bookingDate: daysAgo(1),
          transactionAmount: { amount: '0.42', currency: 'GBP' },
          remittanceInformationUnstructured: 'Interest',
        },
      ],
      pending: [],
    },
  })
  const json = (res, status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(body))
  }
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fake')
    const path = url.pathname.replace(/^\/api\/v2/, '')
    let body = ''
    for await (const chunk of req) body += chunk
    const data = body ? JSON.parse(body) : {}
    state.calls.push({ method: req.method, path })
    if (path === '/token/new/') {
      if (data.secret_id !== SECRET_ID || data.secret_key !== SECRET_KEY) return json(res, 401, {})
      const access = `gcl-access-token-${++state.minted}`
      state.tokens.add(access)
      return json(res, 200, { access, access_expires: 86400 })
    }
    const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
    if (!state.tokens.has(token)) return json(res, 401, {})
    if (path === '/institutions/') {
      return json(res, 200, [
        { id: 'MONZO_MONZGB2L', name: 'Monzo', transaction_total_days: '730' },
        { id: 'ACME_BANK_GB', name: 'ACME Bank', transaction_total_days: '90' },
      ])
    }
    const inst = path.match(/^\/institutions\/([^/]+)\/$/)
    if (inst) {
      const name = inst[1] === 'MONZO_MONZGB2L' ? 'Monzo' : 'ACME Bank'
      return json(res, 200, {
        id: inst[1],
        name,
        transaction_total_days: '730',
        max_access_valid_for_days: '90',
      })
    }
    if (path === '/agreements/enduser/')
      return json(res, 201, { id: `gcl-agr-${state.reqSeq + 1}` })
    if (path === '/requisitions/' && req.method === 'POST') {
      await new Promise((r) => setTimeout(r, LINK_DELAY_MS))
      if (state.failNextRequisition) {
        state.failNextRequisition = false
        return json(res, 500, {})
      }
      const id = `gcl-req-${String(++state.reqSeq).padStart(4, '0')}`
      state.requisitions.set(id, {
        status: 'CR',
        accounts: [],
        redirect: data.redirect,
        institution: data.institution_id,
      })
      return json(res, 201, { id, link: `${BANK_HOST}/consent/${id}` })
    }
    const reqm = path.match(/^\/requisitions\/([^/]+)\/$/)
    if (reqm) {
      const r = state.requisitions.get(reqm[1])
      if (!r) return json(res, 404, {})
      if (req.method === 'DELETE') {
        await new Promise((r) => setTimeout(r, DELETE_DELAY_MS))
        state.requisitions.delete(reqm[1])
        return json(res, 200, {})
      }
      return json(res, 200, { id: reqm[1], status: r.status, accounts: r.accounts })
    }
    const det = path.match(/^\/accounts\/([^/]+)\/details\/$/)
    if (det && ACCOUNTS[det[1]]) {
      const a = ACCOUNTS[det[1]]
      return json(res, 200, {
        account: { name: a.name, currency: 'GBP', iban: a.iban, cashAccountType: a.type },
      })
    }
    const txm = path.match(/^\/accounts\/([^/]+)\/transactions\/$/)
    const TRANSACTIONS = transactions()
    if (txm && TRANSACTIONS[txm[1]]) {
      const from = url.searchParams.get('date_from') ?? '0000-01-01'
      const to = url.searchParams.get('date_to') ?? '9999-12-31'
      const inWindow = (t) => {
        const d = (t.bookingDate ?? t.bookingDateTime ?? t.valueDate ?? '').slice(0, 10)
        return d >= from && d <= to
      }
      const { booked, pending } = TRANSACTIONS[txm[1]]
      return json(res, 200, {
        transactions: { booked: booked.filter(inWindow), pending: pending.filter(inWindow) },
      })
    }
    if (/^\/accounts\/[^/]+\/balances\/$/.test(path)) return json(res, 200, { balances: [] })
    return json(res, 404, {})
  })
  /** The bank's consent page: Approve marks the requisition linked and redirects the way
   *  GoCardless does — `?ref=<id>` appended to the redirect as given, even after a query. */
  async function bankRoute(route) {
    const url = new URL(route.request().url())
    const consent = url.pathname.match(/^\/consent\/([^/]+)$/)
    const approve = url.pathname.match(/^\/approve\/([^/]+)$/)
    if (consent && state.requisitions.has(consent[1])) {
      return route.fulfill({
        contentType: 'text/html',
        body: `<!doctype html><title>Monzo consent</title><h1>Allow access to your Monzo accounts?</h1>
          <a id="approve" href="/approve/${consent[1]}">Approve</a>`,
      })
    }
    if (approve && state.requisitions.has(approve[1])) {
      const r = state.requisitions.get(approve[1])
      r.status = 'LN'
      r.accounts = Object.keys(ACCOUNTS)
      return route.fulfill({
        status: 302,
        headers: { location: `${r.redirect}?ref=${approve[1]}` },
      })
    }
    return route.fulfill({ status: 404, body: 'unknown consent' })
  }
  const count = (re, method) =>
    state.calls.filter((c) => re.test(c.path) && (!method || c.method === method)).length
  return { server, state, bankRoute, count }
}

// ── disposable backend with GoCardless pointed at the fake ────────────────────────────

/** Lucius runs on the operator's Claude Code login (`claude` on PATH). The CLI reads its
 *  sign-in from the real home directory, so only this mode passes HOME through; every data,
 *  cache and log path stays in the throwaway root regardless. */
const LUCIUS = process.env.GCL_LUCIUS !== '0' && spawnSync('claude', ['--version']).status === 0

async function bootBackend(fakeApi) {
  const port = await reserveFreeLoopbackPort()
  const root = mkdtempSync(join(tmpdir(), 'onw-gcl-'))
  const dirs = {
    dataDir: join(root, 'data'),
    cacheDir: join(root, 'cache'),
    logDir: join(root, 'logs'),
  }
  assertSafeBoot({ port, ...dirs })
  const env = { ...buildQaChildEnv({ ...dirs, demoMode: false, port }), GCL_FAKE_API: fakeApi }
  if (LUCIUS) {
    // Claude Code finds its sign-in by home directory and, on macOS, by keychain user name.
    for (const k of ['HOME', 'USER', 'LOGNAME', 'SHELL'])
      if (process.env[k]) env[k] = process.env[k]
  }
  seedDemo({ dataDir: dirs.dataDir, env, log: () => {} })
  const launcher = [
    'import os, runpy',
    'import backend.services.aggregation.gocardless as gc',
    "gc.API_BASE = os.environ['GCL_FAKE_API']",
    "runpy.run_module('backend.main', run_name='__main__')",
  ].join('\n')
  const child = spawn(resolveQaPython(), ['-c', launcher], {
    cwd: REPO_ROOT,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  const baseUrl = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`backend exited:\n${log.slice(-3000)}`)
    if (log.includes(`Uvicorn running on ${baseUrl}`)) {
      try {
        if ((await fetch(`${baseUrl}/api/health`)).ok) break
      } catch {
        /* not yet */
      }
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  if (!log.includes(`Uvicorn running on ${baseUrl}`))
    throw new Error(`backend did not start:\n${log.slice(-3000)}`)
  return { baseUrl, stop: () => child.kill('SIGKILL'), root, backendLog: () => log }
}

async function api(baseUrl, path, init = {}) {
  const r = await fetch(`${baseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const text = await r.text()
  assert.ok(r.ok, `${init.method ?? 'GET'} ${path} → ${r.status}: ${text}`)
  return text ? JSON.parse(text) : null
}

async function caMonzoOf(baseUrl) {
  const list = await api(baseUrl, '/api/accounts')
  return (Array.isArray(list) ? list : list.accounts).find((a) => a.name === 'CA_Monzo')
}

/** Every live ledger row on `accountId` carrying a GoCardless ref → {date, amount, refs}. */
async function bookedFromGoCardless(baseUrl, accountId) {
  const body = await api(baseUrl, `/api/transactions?account_ids=${encodeURIComponent(accountId)}`)
  const rows = Array.isArray(body) ? body : (body.transactions ?? body.items ?? [])
  return rows
    .filter((t) => !t.deleted && (t.external_refs ?? []).some((r) => r.source === 'gocardless'))
    .map((t) => ({
      date: String(t.date).slice(0, 10),
      amount: t.to_account_id === accountId ? Number(t.to_amount) : -Number(t.from_amount),
      refs: (t.external_refs ?? []).filter((r) => r.source === 'gocardless').map((r) => r.ref),
      payeeId: t.payee_id ?? null,
      category: t.category ?? null,
      original:
        t.original_amount != null
          ? `${Number(t.original_amount).toFixed(2)} ${t.original_currency}`
          : null,
      description: t.description ?? '',
    }))
}

/** The GoCardless sync template's instructions, exactly as a user creating it gets them. */
function goCardlessTemplate() {
  const src = readFileSync(
    join(REPO_ROOT, 'frontend/src/components/tabs/automations/automationTemplates.ts'),
    'utf8',
  )
  const at = src.indexOf("key: 'sync_gocardless'")
  const start = src.indexOf('instructions:\n', at)
  const end = src.indexOf("',\n", start) + 1
  const instructions = [...src.slice(start, end).matchAll(/'((?:[^'\\]|\\.)*)'/g)]
    .map((m) => m[1])
    .join('')
  assert.match(instructions, /book-transactions skill/, 'template no longer names the skill')
  return instructions
}

/** Run an Automation now and wait for its run to finish → the finished run. */
async function runAutomation(baseUrl, automationId, { timeoutMs = 12 * 60_000 } = {}) {
  await api(baseUrl, `/api/automations/${automationId}/run`, { method: 'POST' })
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 4000))
    const { runs } = await api(baseUrl, `/api/automations/${automationId}/runs?limit=1`)
    const run = runs?.[0]
    if (run && !['queued', 'running', 'pending', 'starting'].includes(run.status)) return run
  }
  throw new Error(`automation run did not finish within ${timeoutMs / 1000}s`)
}

/** The newest conversation's messages — evidence of what Lucius said and did. */
async function transcript(baseUrl) {
  const list = await api(baseUrl, '/api/sessions')
  const sessions = Array.isArray(list) ? list : (list.sessions ?? [])
  const newest = sessions[0]
  if (!newest?.id) return []
  return api(baseUrl, `/api/sessions/${encodeURIComponent(newest.id)}/messages?tail=30`)
}

/** Send one message to Lucius from the open composer and see the turn through: approve every
 *  card it raises, until the turn has ended (no Stop button) and nothing new is asked. */
async function runLuciusTurn(page, text, { timeoutMs = 8 * 60_000 } = {}) {
  const box = page.getByRole('textbox', { name: /message|ask lucius/i }).last()
  await box.waitFor({ timeout: 20_000 })
  if (text) await box.fill(text)
  await page.getByRole('button', { name: 'Send', exact: true }).last().click()
  const deadline = Date.now() + timeoutMs
  let approvals = 0
  let quietSince = 0
  await page.waitForTimeout(3000)
  while (Date.now() < deadline) {
    const allow = page.getByRole('button', { name: /^Allow once$/ }).first()
    if (await allow.isVisible().catch(() => false)) {
      await allow.click()
      approvals += 1
      quietSince = 0
      await page.waitForTimeout(1500)
      continue
    }
    const working = await page
      .getByRole('button', { name: /^Stop/ })
      .first()
      .isVisible()
      .catch(() => false)
    if (working) quietSince = 0
    else if (!quietSince) quietSince = Date.now()
    else if (Date.now() - quietSince > 8000) return { approvals }
    await page.waitForTimeout(1500)
  }
  throw new Error(
    `Lucius turn did not finish within ${timeoutMs / 1000}s (approved ${approvals} cards)`,
  )
}

// ── the journey ───────────────────────────────────────────────────────────────────────

const results = []
async function step(id, fn, page) {
  const t0 = Date.now()
  try {
    await fn()
    results.push({ id, ok: true, ms: Date.now() - t0 })
    console.log(`PASS ${id}`)
  } catch (error) {
    if (String(error?.message).startsWith('SKIP:')) {
      results.push({ id, ok: true, skipped: true, ms: Date.now() - t0, error: error.message })
      console.log(`SKIP ${id}: ${error.message.slice(6)}`)
      return
    }
    results.push({ id, ok: false, ms: Date.now() - t0, error: String(error?.message ?? error) })
    console.log(`FAIL ${id}: ${error?.message ?? error}`)
    if (page)
      await page.screenshot({ path: join(OUT_DIR, `${id}.png`), fullPage: true }).catch(() => {})
  }
}

const httpFailures = []
// A read the TEST cut off by navigating away (`page.goto` mid-load) logs "Failed to fetch"; a
// user leaving a page does the same. Kept apart and reported, never counted as an app error.
const abortedByNavigation = []
function watchConsole(page, errors, label) {
  // A read the page itself abandoned — the run navigating away mid-load — fails with the
  // browser's net::ERR_ABORTED and the app logs "Failed to fetch". Only that pairing is
  // set aside; a "Failed to fetch" with no aborted request beside it is a real error.
  let abortedAt = 0
  page.on('requestfailed', (r) => {
    if ((r.failure()?.errorText ?? '').includes('ERR_ABORTED')) abortedAt = Date.now()
  })
  page.on('console', (m) => {
    // A failed request's console line has no URL; the response listener below records it.
    if (m.type() !== 'error' || m.text().startsWith('Failed to load resource')) return
    if (m.text().includes('Failed to fetch') && Date.now() - abortedAt < 3000) {
      abortedByNavigation.push(`${label}: ${m.text().split('\n')[0]}`)
    } else errors.push(`${label}: ${m.text()}`)
  })
  page.on('pageerror', (e) => errors.push(`${label}: uncaught ${e.message}`))
  page.on('response', (r) => {
    if (r.status() >= 400)
      httpFailures.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`)
  })
}

/** The Connectors tab's first-visit intro covers the page once per browser profile. */
async function dismissIntro(page) {
  // Each tab's first-visit intro covers the page once per browser profile.
  const close = page.getByRole('button', { name: /^(Set up myself|Got it)$/ })
  const shown = await close
    .first()
    .waitFor({ state: 'visible', timeout: 5000 })
    .then(() => true)
    .catch(() => false)
  if (shown) await close.first().click()
  // Lucius's panel, once opened, walks through its tips ("Lucius sees this screen") over the page.
  const tip = page.locator('.pt-layer').getByRole('button', { name: /^Done$/ })
  if (await tip.isVisible().catch(() => false)) await tip.click()
}

async function openGoCardlessForm(page, baseUrl) {
  await page.goto(`${baseUrl}/connectors?connector=gocardless`)
  await dismissIntro(page)
  const form = page.locator('.gc-form')
  const detail = page.getByText('Tool permissions')
  // The deep link lands on the form or on the page, depending on the connector's state.
  await form.or(detail).first().waitFor({ state: 'visible', timeout: 20_000 })
  if (!(await form.isVisible())) {
    const dialog = page.locator('.rd-modal')
    await dialog.getByRole('button', { name: 'More actions' }).click()
    await dialog.getByRole('button', { name: 'Edit configuration' }).click()
  }
  await form.waitFor({ state: 'visible', timeout: 15_000 })
}

async function main() {
  if (!process.env.GCL_SKIP_BUILD) {
    const b = spawnSync('npm', ['run', 'build'], {
      cwd: join(REPO_ROOT, 'frontend'),
      stdio: 'inherit',
    })
    if (b.status !== 0) throw new Error('frontend build failed')
  }
  const fake = fakeGoCardless()
  const fakePort = await reserveFreeLoopbackPort()
  await new Promise((r) => fake.server.listen(fakePort, '127.0.0.1', r))
  const backend = await bootBackend(`http://127.0.0.1:${fakePort}/api/v2`)
  const { baseUrl } = backend
  // `channel: 'chromium'` is full Chromium's headless mode: the default headless shell lets a
  // window.open through 6 s after a click, so it cannot see the bug this run exists for.
  const browser = await chromium.launch({
    channel: 'chromium',
    ignoreDefaultArgs: ['--disable-popup-blocking'],
  })
  const consoleErrors = []
  const seenHtml = []
  try {
    await api(baseUrl, '/api/accounts', {
      method: 'POST',
      body: JSON.stringify({
        name: 'CA_Monzo',
        type: 'cash',
        currency: 'GBP',
        initial_balance: 1000,
      }),
    })
    const profile = await api(baseUrl, '/api/profile').catch(() => ({}))
    if (profile?.timezone) APP_TZ = profile.timezone
    if (LUCIUS) {
      // Precondition, as for a user who set Lucius up already: Claude Code is the AI provider.
      await api(baseUrl, '/api/model-config', {
        method: 'PUT',
        body: JSON.stringify({ provider: 'claude_cli', model: MODEL }),
      })
    }
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    await ctx.route(`${BANK_HOST}/**`, fake.bankRoute)
    const app = await ctx.newPage()
    watchConsole(app, consoleErrors, 'app')

    await step(
      'GCL-NF-BLOCKER-ACTIVE',
      async () => {
        await app.goto(`${baseUrl}/connectors`)
        await dismissIntro(app)
        await app.evaluate(() => {
          const b = document.createElement('button')
          b.id = 'gcl-late-open'
          b.textContent = 'late open'
          b.onclick = () =>
            setTimeout(() => (window.__gclLate = window.open('about:blank', '_blank')), 6000)
          document.body.appendChild(b)
        })
        const before = ctx.pages().length
        await app.click('#gcl-late-open')
        await app.waitForTimeout(6800)
        assert.equal(
          await app.evaluate(() => window.__gclLate),
          null,
          'a late window.open got a tab',
        )
        assert.equal(ctx.pages().length, before, 'a late window.open opened a page')
      },
      app,
    )

    await step(
      'GCL-ADD-FROM-DIRECTORY',
      async () => {
        await app.goto(`${baseUrl}/connectors`)
        await dismissIntro(app)
        await app.getByRole('button', { name: /Browse & add/ }).click()
        const dir = app.locator('.rd-modal').last()
        await dir
          .getByPlaceholder(/search/i)
          .first()
          .fill('GoCardless')
        await dir.getByText('GoCardless', { exact: true }).first().click()
        await dir
          .getByRole('button', { name: /^(Add connector|Add|Connect|Set up)$/ })
          .first()
          .click()
        await app.locator('.gc-form').waitFor({ timeout: 15_000 })
        await app.getByText('Save your secret ID and key first.').waitFor()
      },
      app,
    )

    await step(
      'GCL-CREDS-BAD',
      async () => {
        if (!(await app.locator('.gc-form').isVisible())) await openGoCardlessForm(app, baseUrl)
        await app.fill('#conn-gc-id', SECRET_ID)
        await app.fill('#conn-gc-key', BAD_KEY)
        await app.getByRole('button', { name: 'Save', exact: true }).click()
        await openGoCardlessForm(app, baseUrl)
        await app.getByRole('button', { name: 'List banks' }).click()
        await app.getByText(/rejected the secret ID/i).waitFor({ timeout: 15_000 })
        const listed = await api(baseUrl, '/api/connectors')
        const gc = listed.connectors.find((c) => c.id === 'gocardless')
        assert.notEqual(gc?.status, 'active', 'reads Connected over keys GoCardless refuses')
        assert.equal(
          await app.locator('#conn-gc-bank').count(),
          0,
          'a bank list appeared on rejected keys',
        )
      },
      app,
    )

    await step(
      'GCL-CREDS-SAVE',
      async () => {
        await openGoCardlessForm(app, baseUrl)
        await app.fill('#conn-gc-key', SECRET_KEY)
        await app.getByRole('button', { name: 'Save', exact: true }).click()
        await app.getByText('Connected', { exact: true }).first().waitFor({ timeout: 15_000 })
        const status = await api(baseUrl, '/api/gocardless/status')
        assert.equal(status.has_credentials, true)
      },
      app,
    )

    await step(
      'GCL-BANKS-LIST',
      async () => {
        await openGoCardlessForm(app, baseUrl)
        await app.fill('#conn-gc-country', 'GB')
        await app.getByRole('button', { name: 'List banks' }).click()
        await app.locator('#conn-gc-bank').waitFor({ timeout: 15_000 })
        await app.selectOption('#conn-gc-bank', 'MONZO_MONZGB2L')
      },
      app,
    )

    await step(
      'GCL-LINK-FAILS',
      async () => {
        fake.state.failNextRequisition = true
        const pagesBefore = ctx.pages().length
        await app.getByRole('button', { name: /^Link (this )?bank$/ }).click()
        await app
          .getByText(/GoCardless HTTP 500|Could not start the bank link/i)
          .waitFor({ timeout: LINK_DELAY_MS + 15_000 })
        await app.waitForTimeout(500)
        assert.equal(ctx.pages().length, pagesBefore, 'a blank tab was left open')
        assert.equal(
          await app.getByText('Your banks', { exact: true }).count(),
          0,
          'a bank row appeared after a failed link',
        )
      },
      app,
    )

    let bank
    await step(
      'GCL-LINK-OPENS-BANK',
      async () => {
        const clicked = Date.now()
        const popup = ctx.waitForEvent('page', { timeout: 3000 })
        popup.catch(() => {}) // awaited below; a failure before then must not crash the run
        await app.getByRole('button', { name: /^Link (this )?bank$/ }).click()
        bank = await popup
        const openedMs = Date.now() - clicked
        results.push({ id: 'GCL-NF-FEEDBACK:tab-opened-ms', ok: openedMs < 3000, ms: openedMs })
        const busy = await app
          .getByRole('button', { name: /^Link (this )?bank$/ })
          .evaluate((b) => b.disabled || b.getAttribute('aria-busy') === 'true')
        assert.ok(busy, 'Link bank shows no working state while the link is pending')
        await bank.waitForURL(/\/consent\/gcl-req-/, { timeout: LINK_DELAY_MS + 15_000 })
        await bank.getByText('Allow access to your Monzo accounts?').waitFor()
        await app.getByText('Waiting for you to finish at the bank').waitFor({ timeout: 15_000 })
      },
      app,
    )

    await step(
      'GCL-NF-FEEDBACK',
      async () => {
        const r = results.find((x) => x.id === 'GCL-NF-FEEDBACK:tab-opened-ms')
        assert.ok(r?.ok, `tab took ${r?.ms}ms to open`)
      },
      app,
    )

    await step(
      'GCL-NF-NOOPENER',
      async () => {
        assert.ok(bank, 'no bank tab')
        assert.equal(await bank.evaluate(() => window.opener), null)
      },
      bank,
    )

    await step(
      'GCL-RETURN-TO-CONNECTOR',
      async () => {
        assert.ok(bank, 'no bank tab')
        watchConsole(bank, consoleErrors, 'returned')
        await bank.click('#approve')
        await bank.waitForURL((u) => u.origin === baseUrl, { timeout: 15_000 })
        await bank.getByText('Tool permissions').waitFor({ timeout: 20_000 })
        await bank
          .getByRole('heading', { name: /GoCardless/ })
          .or(bank.getByText('GoCardless').first())
          .first()
          .waitFor()
        assert.match(bank.url(), /\/connectors/, `landed on ${bank.url()}`)
      },
      bank,
    )

    await step(
      'GCL-ACCOUNTS-ON-PAGE',
      async () => {
        await bank.getByText('Current Account').waitFor({ timeout: 20_000 })
        await bank.getByText('Savings Pot').waitFor()
        const text = await bank.locator('body').innerText()
        assert.match(text, /1234/, 'last four of Current Account missing')
        assert.match(text, /5678/, 'last four of Savings Pot missing')
        assert.equal(await bank.locator('.gc-form').count(), 0, 'needed the form to see accounts')
        seenHtml.push(await bank.content())
      },
      bank,
    )

    await step(
      'GCL-LINK-EXISTING',
      async () => {
        await bank
          .getByRole('button', { name: 'Link Current Account to an existing account' })
          .click()
        const picker = bank.getByRole('group', { name: 'Link Current Account to an account' })
        await picker.waitFor()
        await bank.getByPlaceholder('Search accounts…').fill('CA_Monzo')
        await bank.getByText('CA_Monzo', { exact: true }).click()
        await bank.getByText('Linked to “CA_Monzo”').waitFor({ timeout: 15_000 })
        const accounts = await api(baseUrl, '/api/accounts')
        const list = Array.isArray(accounts) ? accounts : accounts.accounts
        const ca = list.find((a) => a.name === 'CA_Monzo')
        assert.ok(
          (ca.external_ids ?? []).some(
            (e) => e.source === 'gocardless' && e.ref === 'gcl-acc-0001',
          ),
          'CA_Monzo carries no GoCardless link',
        )
      },
      bank,
    )

    await step(
      'GCL-ADD-NEW',
      async () => {
        const row = bank.locator('.conn-acct-row', { hasText: 'Savings Pot' })
        await row.getByRole('button', { name: 'Add', exact: true }).click()
        await bank.getByRole('button', { name: /Add account/ }).click()
        await bank.getByText(/Linked to “Savings Pot/).waitFor({ timeout: 15_000 })
      },
      bank,
    )

    await step(
      'GCL-NAV-BACK',
      async () => {
        const dialog = bank.locator('.rd-modal').last()
        await dialog
          .getByRole('button', { name: /Connectors/ })
          .first()
          .click()
        const row = bank.getByText('GoCardless', { exact: true }).first()
        await row.waitFor({ timeout: 15_000 })
        await row.click()
        await bank.getByText('2 of 2 added').waitFor({ timeout: 20_000 })
        await bank.getByText('Linked to “CA_Monzo”').waitFor()
        assert.equal(await bank.locator('.gc-form').count(), 0, 'reopening landed on the form')
      },
      bank,
    )

    const accountsList = await api(baseUrl, '/api/accounts')
    const accountIds = Object.fromEntries(
      (Array.isArray(accountsList) ? accountsList : accountsList.accounts).map((a) => [
        a.name,
        a.id,
      ]),
    )
    let categoriesBefore = []
    const caMonzo = () => caMonzoOf(baseUrl)
    const bookedAll = async () => [
      ...(await bookedFromGoCardless(baseUrl, accountIds.CA_Monzo)).map((r) => ['CA_Monzo', r]),
      ...(await bookedFromGoCardless(baseUrl, accountIds['Savings Pot'])).map((r) => [
        'Savings Pot',
        r,
      ]),
    ]

    await step(
      'GCL-LUCIUS-FETCH',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        // The bank sync as users run it: an Automation made from the GoCardless template.
        const automation = await api(baseUrl, '/api/automations', {
          method: 'POST',
          body: JSON.stringify({
            name: 'Sync GoCardless',
            description: 'Fetch new GoCardless transactions and book them.',
            instructions: goCardlessTemplate(),
            trigger: { kind: 'schedule', type: 'interval', seconds: 43200 },
            permission_mode: 'auto',
          }),
        })
        categoriesBefore = await api(baseUrl, '/api/categories?include_hidden=true')
        const run = await runAutomation(baseUrl, automation.id)
        console.log(`  sync run (${MODEL}): ${run.status}`)
        writeFileSync(join(OUT_DIR, 'sync-run.json'), JSON.stringify(run, null, 2))
        if (run.session_id) {
          const convo = await api(
            baseUrl,
            `/api/sessions/${encodeURIComponent(run.session_id)}/messages?tail=200`,
          ).catch((e) => ({ error: String(e) }))
          writeFileSync(join(OUT_DIR, 'sync-conversation.json'), JSON.stringify(convo, null, 2))
        }
        const got = await bookedAll()
        const want = EXPECTED_BOOKED.map(
          ([acct, ago, amount]) => `${acct} ${daysAgo(ago)} ${amount.toFixed(2)}`,
        ).sort()
        const have = got.map(([acct, r]) => `${acct} ${r.date} ${r.amount.toFixed(2)}`).sort()
        assert.deepEqual(have, want, "the book does not hold exactly the bank's booked rows")
        const refs = got.flatMap(([, r]) => r.refs)
        assert.equal(new Set(refs).size, refs.length, 'two rows share one bank ref')
      },
      bank,
    )

    await step(
      'GCL-CATEGORISED',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        const cats = await api(baseUrl, '/api/categories?include_hidden=true')
        const label = Object.fromEntries(cats.map((c) => [c.id, c.label]))
        const got = await bookedAll()
        const uncategorised = got.filter(([, r]) => !r.category).map(([a, r]) => `${a} ${r.amount}`)
        assert.deepEqual(uncategorised, [], 'rows left uncategorised')
        const labelOf = (amount) =>
          String(
            label[got.find(([, r]) => Math.abs(r.amount - amount) < 0.001)?.[1].category] ?? '',
          )
        assert.match(labelOf(-12.4), /groc|food/i, `grocery row filed as ${labelOf(-12.4)}`)
        assert.match(labelOf(2500), /salar|income|wage|pay/i, `salary filed as ${labelOf(2500)}`)
        console.log(
          `  row categories (${MODEL}): ` +
            got.map(([, r]) => `${r.amount}→${label[r.category] ?? '—'}`).join(', '),
        )
        assert.deepEqual(
          got.filter(([, r]) => /^(other|stuff i forgot|misc)/i.test(label[r.category] ?? '')),
          [],
          'a row was dumped in a catch-all category',
        )
        // Nothing in the book fits these two: each needs a fitting category, made if missing.
        assert.match(labelOf(-24.99), /insur/i, `insurance filed as ${labelOf(-24.99)}`)
        assert.match(
          labelOf(-850),
          /educat|tuition|school|universit|course|studi/i,
          `tuition filed as ${labelOf(-850)}`,
        )
        const before = new Set(categoriesBefore.map((c) => c.id))
        const created = cats.filter((c) => !before.has(c.id)).map((c) => c.label)
        console.log(`  categories created (${MODEL}): ${created.join(', ') || 'none'}`)
        if (!categoriesBefore.some((c) => /insur/i.test(c.label))) {
          assert.ok(
            created.some((l) => /insur/i.test(l)),
            'no insurance category was created',
          )
        }
        assert.deepEqual(
          created.filter((l) => /acme|monzo|card \d/i.test(l)),
          [],
          'a category was named after a merchant or bank',
        )
        const norm = (l) =>
          l
            .toLowerCase()
            .replace(/[^a-z]/g, '')
            .replace(/s$/, '')
        const existing = new Set(categoriesBefore.map((c) => norm(c.label)))
        assert.deepEqual(
          created.filter((l) => existing.has(norm(l))),
          [],
          'a near-copy of an existing category was created',
        )
        const payees = (await api(baseUrl, '/api/payees')).payees
        const grocer = payees.find((p) => p.name.toLowerCase() === 'acme grocer')
        assert.ok(
          grocer && Object.values(grocer.default_categories ?? {}).length,
          'Acme Grocer learned no default category, so the next row from it is not automatic',
        )
      },
      bank,
    )

    await step(
      'GCL-LUCIUS-REFETCH',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        const before = (await bookedAll()).length
        assert.ok(before > 0, 'nothing was booked by the sync, so a refetch proves nothing')
        // The other way in: the connector page's "Fetch my GoCardless transactions".
        await bank.goto(`${baseUrl}/connectors?connector=gocardless`)
        await dismissIntro(bank)
        await bank.getByRole('button', { name: /Fetch my GoCardless transactions/ }).click()
        await runLuciusTurn(bank, null)
        const after = (await bookedAll()).length
        assert.equal(after, before, `a second fetch changed the book (${before} → ${after} rows)`)
      },
      bank,
    )

    await step(
      'GCL-LUCIUS-RANGE',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        // History is asked for, never defaulted: the user names the range and Lucius books the
        // older rows. It does NOT move the account's start — only the user knows whether those
        // rows are right (owner, 2026-11-13).
        const startBefore = await caMonzo()
        const before = await bookedAll()
        await runLuciusTurn(
          bank,
          `Fetch my GoCardless transactions from ${daysAgo(90)} and book any that are missing.`,
        )
        const fresh = (await bookedAll())
          .filter(([, r]) => !before.some(([, b]) => b.refs.join() === r.refs.join()))
          .map(([acct, r]) => `${acct} ${r.date} ${r.amount.toFixed(2)}`)
          .sort()
        const want = EXPECTED_HISTORY.map(
          ([acct, ago, amount]) => `${acct} ${daysAgo(ago)} ${amount.toFixed(2)}`,
        ).sort()
        assert.deepEqual(fresh, want, 'the asked-for range did not book exactly the older rows')
        const after = await caMonzo()
        assert.equal(
          after.initial_balance_date,
          startBefore.initial_balance_date,
          "Lucius moved CA_Monzo's start on its own",
        )
      },
      bank,
    )

    await step(
      'GCL-PAYEE-NAMED',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        const payees = (await api(baseUrl, '/api/payees')).payees
        const nameOf = Object.fromEntries(payees.map((p) => [p.id, p.name]))
        const rows = await bookedFromGoCardless(baseUrl, accountIds.CA_Monzo)
        const payeeFor = (amount) =>
          String(nameOf[rows.find((r) => Math.abs(r.amount - amount) < 0.001)?.payeeId] ?? '')
        // Who the bank says was paid / who paid — not the card text.
        assert.equal(
          payeeFor(-12.4).toLowerCase(),
          'acme grocer',
          `card payment payee: ${payeeFor(-12.4)}`,
        )
        assert.equal(payeeFor(2500).toLowerCase(), 'acme ltd', `salary payee: ${payeeFor(2500)}`)
        const raw = payees.filter((p) => /^card \d/i.test(p.name)).map((p) => p.name)
        assert.deepEqual(raw, [], 'a payee was named after raw card text')
      },
      bank,
    )

    await step(
      'GCL-ORIGINAL-CHARGE',
      async () => {
        if (!LUCIUS) throw new Error('SKIP: no Claude Code CLI on PATH (or GCL_LUCIUS=0)')
        // The euro purchase: debited £42.10, charged €48.00 — kept in the ledger's own fields.
        const paris = (await bookedFromGoCardless(baseUrl, accountIds.CA_Monzo)).find(
          (r) => Math.abs(r.amount + 42.1) < 0.001,
        )
        assert.equal(paris?.original, '48.00 EUR', `original charge stored as ${paris?.original}`)
      },
      bank,
    )

    await step('GCL-NF-BANK-DAILY-LIMIT', async () => {
      // Banks may allow 4 account reads a day; two whole fetches must stay inside it.
      for (const acc of ['gcl-acc-0001', 'gcl-acc-0002']) {
        const n = fake.count(new RegExp(`^/accounts/${acc}/transactions/$`))
        assert.ok(n <= 4, `${acc} transactions read ${n}× in one session`)
      }
    })

    await step(
      'GCL-STATUS-AFTER',
      async () => {
        await openGoCardlessForm(app, baseUrl)
        await app.getByText(/Linked · 2 accounts/).waitFor({ timeout: 20_000 })
        seenHtml.push(await app.content())
      },
      app,
    )

    await step('GCL-LINK-FALLBACK', async () => {
      const noTab = await ctx.newPage()
      watchConsole(noTab, consoleErrors, 'fallback')
      await noTab.addInitScript(() => {
        window.open = () => null
      })
      await openGoCardlessForm(noTab, baseUrl)
      await noTab.getByRole('button', { name: 'List banks' }).click()
      await noTab.locator('#conn-gc-bank').waitFor({ timeout: 15_000 })
      await noTab.selectOption('#conn-gc-bank', 'ACME_BANK_GB')
      await noTab.getByRole('button', { name: /^Link (this )?bank$/ }).click()
      const link = noTab.getByRole('link', { name: /open your bank's page/ })
      await link.waitFor({ timeout: LINK_DELAY_MS + 15_000 })
      assert.match(
        await link.getAttribute('href'),
        /^https:\/\/ob\.gocardless\.test\/consent\/gcl-req-/,
      )
      await noTab.close()
    })

    await step(
      'GCL-ABANDON-REMOVE',
      async () => {
        await openGoCardlessForm(app, baseUrl)
        const deletesBefore = fake.count(/^\/requisitions\/gcl-req-/, 'DELETE')
        await app.getByRole('button', { name: 'Remove ACME Bank' }).click()
        const confirm = app.getByRole('dialog', { name: 'Remove ACME Bank?' })
        const removed = app.waitForResponse(
          (r) => /\/api\/gocardless\/banks\/[^/]+\/remove$/.test(r.url()),
          {
            timeout: 15_000,
          },
        )
        await confirm.getByRole('button', { name: 'Remove', exact: true }).click()
        // GoCardless takes seconds to revoke; the row must say it is working meanwhile.
        // By attribute: a busy button may leave the accessibility tree's name lookup.
        const working = await app
          .locator('button[aria-label="Remove ACME Bank"]')
          .evaluate((b) => b.getAttribute('aria-busy') === 'true' || b.disabled)
        assert.ok(working, 'Remove shows no working state while GoCardless revokes the consent')
        const res = await removed
        assert.ok(res.ok(), `remove answered ${res.status()}: ${await res.text()}`)
        await app
          .locator('button[aria-label="Remove ACME Bank"]')
          .waitFor({ state: 'detached', timeout: 15_000 })
        await app.getByText('Your banks', { exact: true }).waitFor()
        const revoked = fake.count(/^\/requisitions\/gcl-req-/, 'DELETE') === deletesBefore + 1
        assert.ok(
          revoked,
          `the bank left the list but its consent was not deleted at GoCardless (before=${deletesBefore}, ` +
            `calls since: ${JSON.stringify(fake.state.calls.slice(-12))})`,
        )
        assert.equal(
          (await api(baseUrl, '/api/gocardless/status')).banks.length,
          1,
          'bank still stored',
        )
        assert.ok(
          await app.getByText('Monzo', { exact: true }).count(),
          'removing one bank dropped the other',
        )
      },
      app,
    )

    await step(
      'GCL-LINK-FROM-ACCOUNT',
      async () => {
        // Krishna, 2026-11-12: link from the account itself. Free Savings Pot's feed on the
        // connector page, then link it back from the account's own menu.
        await app.goto(`${baseUrl}/connectors?connector=gocardless`)
        await dismissIntro(app)
        const savingsRow = app.locator('.conn-acct-row', { hasText: 'Savings Pot' }).first()
        await savingsRow.getByRole('button', { name: 'Unlink' }).click()
        await savingsRow
          .getByRole('button', { name: 'Unlink' })
          .waitFor({ state: 'detached', timeout: 15_000 })

        await app.goto(
          `${baseUrl}/accounts?account=${encodeURIComponent(accountIds['Savings Pot'])}`,
        )
        await dismissIntro(app)
        await app.getByRole('button', { name: 'More actions' }).first().click()
        await app.getByRole('button', { name: /Link to a bank account/ }).click()
        const dialog = app.getByRole('dialog', { name: /Link Savings Pot to a bank account/ })
        const linkSavings = dialog.getByRole('button', { name: /Link Savings Pot to Savings Pot/ })
        await linkSavings.waitFor({ timeout: 20_000 })
        // The feed already going to CA_Monzo is shown, not offered.
        await dialog.getByText(/Linked to “CA_Monzo”/).waitFor()
        assert.equal(await dialog.getByRole('button', { name: /to Current Account/ }).count(), 0)
        assert.ok(await dialog.getByText('Suggested').isVisible(), 'no suggestion offered')
        await linkSavings.click()
        await dialog.getByText(/Savings Pot is linked to Savings Pot through GoCardless/).waitFor({
          timeout: 15_000,
        })
        const accounts = await api(baseUrl, '/api/accounts')
        const savings = (Array.isArray(accounts) ? accounts : accounts.accounts).find(
          (a) => a.name === 'Savings Pot',
        )
        assert.ok(
          (savings.external_ids ?? []).some(
            (e) => e.source === 'gocardless' && e.ref === 'gcl-acc-0002',
          ),
          'Savings Pot carries no GoCardless link',
        )
        await dialog.getByRole('button', { name: 'Done' }).click()
      },
      app,
    )

    await step(
      'GCL-NF-RATE-BUDGET',
      async () => {
        const readsBefore = fake.count(/\/(transactions|balances)\/$/)
        for (let i = 0; i < 2; i += 1) {
          await app.goto(`${baseUrl}/connectors?connector=gocardless`)
          await app
            .locator('.conn-acct-row', { hasText: 'Current Account' })
            .first()
            .waitFor({ timeout: 20_000 })
          await openGoCardlessForm(app, baseUrl)
          await app
            .locator('.conn-acct-row', { hasText: 'Current Account' })
            .first()
            .waitFor({ timeout: 20_000 })
        }
        assert.equal(
          fake.count(/\/(transactions|balances)\/$/),
          readsBefore,
          'opening screens read transactions/balances',
        )
        for (const acc of ['gcl-acc-0001', 'gcl-acc-0002']) {
          const n = fake.count(new RegExp(`^/accounts/${acc}/details/$`))
          assert.ok(n <= 1, `${acc} details read ${n}×`)
        }
      },
      app,
    )

    await step('GCL-NF-MOBILE', async () => {
      const phone = await browser.newContext({
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      })
      const p = await phone.newPage()
      watchConsole(p, consoleErrors, 'mobile')
      await p.goto(`${baseUrl}/connectors?connector=gocardless`)
      await dismissIntro(p)
      await p.getByText('Current Account').waitFor({ timeout: 20_000 })
      const overflow = await p.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      await p.screenshot({ path: join(OUT_DIR, 'mobile-connector.png'), fullPage: true })
      assert.ok(overflow <= 1, `page scrolls sideways by ${overflow}px`)
      await phone.close()
    })

    await step('GCL-NF-NO-SECRETS', async () => {
      const status = JSON.stringify(await api(baseUrl, '/api/gocardless/status'))
      const tokens = [...fake.state.tokens]
      const reqIds = [...fake.state.requisitions.keys()]
      for (const [label, text] of [
        ['status API', status],
        ...seenHtml.map((h, i) => [`page ${i}`, h]),
      ]) {
        for (const secret of [SECRET_KEY, ...tokens, ...reqIds]) {
          assert.ok(!text.includes(secret), `${label} contains ${secret}`)
        }
      }
    })

    await step('GCL-NF-NO-CONSOLE-ERRORS', async () => {
      assert.deepEqual(consoleErrors, [], 'console errors / uncaught exceptions')
      // Only the two failures this run causes on purpose: rejected keys (GCL-CREDS-BAD) and
      // GoCardless refusing the consent (GCL-LINK-FAILS).
      const expected = new Set([
        '422 GET /api/gocardless/institutions',
        '502 POST /api/gocardless/link',
      ])
      const unexpected = httpFailures.filter((f) => !expected.has(f))
      assert.deepEqual(unexpected, [], 'unexpected failed requests')
    })

    await app.screenshot({ path: join(OUT_DIR, 'final-form.png'), fullPage: true })
    if (bank && !bank.isClosed())
      await bank.screenshot({ path: join(OUT_DIR, 'connector-page.png'), fullPage: true })
  } finally {
    await browser.close()
    backend.stop()
    fake.server.close()
    writeFileSync(join(OUT_DIR, 'backend.log'), backend.backendLog())
    writeFileSync(
      join(OUT_DIR, 'results.json'),
      JSON.stringify(
        { results, httpFailures, abortedByNavigation, calls: fake.state.calls },
        null,
        2,
      ),
    )
    spawnSync('rm', ['-rf', backend.root])
  }
  const failed = results.filter((r) => !r.ok && !r.id.includes(':'))
  console.log(
    `\n${results.filter((r) => r.ok && !r.id.includes(':')).length} passed, ${failed.length} failed`,
  )
  if (failed.length) process.exit(1)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
