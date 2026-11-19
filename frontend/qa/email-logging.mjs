// EML — logging transactions from email, end to end. Acceptance: docs/qa/acceptance/email-logging.md.
//
// A disposable backend (fresh tmp data, demo-seeded, never :8080) whose IMAP calls read an
// in-memory mailbox (frontend/qa/fake_mailbox.py) — everything above them is real: the email
// tools, the log-transactions-from-email and book-transactions skills, Lucius on the Claude
// Code CLI, and the ledger. The "Auto-log transactions from email" Automation is created from
// its template word for word and run, twice.
//
//   npm run e2e:email-logging                 (Sonnet; builds the frontend first)
//   GCL_MODEL=haiku EML_SKIP_BUILD=1 npm run e2e:email-logging

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  assertSafeBoot,
  buildQaChildEnv,
  reserveFreeLoopbackPort,
  resolveQaPython,
  seedDemo,
} from './boot.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')
const MODEL = process.env.GCL_MODEL || 'sonnet'
const OUT_DIR = resolve(REPO_ROOT, `qa/artifacts/email-logging-${MODEL}`)
rmSync(OUT_DIR, { recursive: true, force: true })
mkdirSync(OUT_DIR, { recursive: true })

if (spawnSync('claude', ['--version']).status !== 0) {
  console.log('SKIP: no Claude Code CLI on PATH — this journey needs Lucius')
  process.exit(0)
}

let APP_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone
function daysAgo(n) {
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: APP_TZ }).format(new Date())
  const d = new Date(`${today}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

const BANK = 'Monzo <alerts@monzo.example>'
// The skill reads from the last completed run, or the last day on a first run: every alert
// arrives today.
function mailbox() {
  const alert = (id, ago, subject, body) => ({
    message_id: `<${id}@monzo.example>`,
    from: BANK,
    subject,
    date: daysAgo(ago),
    body,
  })
  const grocer = alert(
    'eml-1',
    0,
    'You spent £12.40 at ACME GROCER',
    'Card payment: £12.40 at ACME GROCER LONDON on your Monzo account. Ref TXN-77812.',
  )
  return [
    grocer,
    alert(
      'eml-2',
      0,
      'You spent £3.00 at ACME CAFE',
      'Card payment: £3.00 at ACME CAFE on your Monzo account. Ref TXN-77901.',
    ),
    // A second, separate coffee: same amount, merchant and day, its own email.
    alert(
      'eml-3',
      0,
      'You spent £3.00 at ACME CAFE',
      'Card payment: £3.00 at ACME CAFE on your Monzo account. Ref TXN-77902.',
    ),
    alert(
      'eml-4',
      0,
      'You spent €48.00 at CAFE ACME PARIS',
      'Card payment: €48.00 (£42.10) at CAFE ACME PARIS on your Monzo account. You were charged £42.10. Ref TXN-78010.',
    ),
    alert(
      'eml-5',
      0,
      'You received £2,500.00',
      'Money in: £2,500.00 from ACME LTD SALARY to your Monzo account. Ref TXN-78111.',
    ),
    alert(
      'eml-6',
      0,
      'Direct Debit paid: ACME LIFE INSURANCE',
      'Direct Debit: £24.99 to ACME LIFE INSURANCE from your Monzo account. Ref DD-88412.',
    ),
    // The same alert delivered twice (same Message-ID): one transaction.
    { ...grocer },
    {
      message_id: '<news-1@monzo.example>',
      from: 'Monzo <news@monzo.example>',
      subject: '5 tips to save more this winter',
      date: daysAgo(0),
      body: 'Our favourite ways to save: round-ups, pots and more. Read on.',
    },
  ]
}
// What must land on CA_Monzo, exactly once each.
const EXPECTED = [-12.4, -3, -3, -42.1, 2500, -24.99]

async function bootBackend(mailboxPath) {
  const port = await reserveFreeLoopbackPort()
  const root = mkdtempSync(join(tmpdir(), 'onw-eml-'))
  const dirs = {
    dataDir: join(root, 'data'),
    cacheDir: join(root, 'cache'),
    logDir: join(root, 'logs'),
  }
  assertSafeBoot({ port, ...dirs })
  const env = {
    ...buildQaChildEnv({ ...dirs, demoMode: false, port }),
    ONW_FAKE_MAILBOX: mailboxPath,
  }
  // Claude Code finds its sign-in by home directory and, on macOS, by keychain user name.
  for (const k of ['HOME', 'USER', 'LOGNAME', 'SHELL']) if (process.env[k]) env[k] = process.env[k]
  seedDemo({ dataDir: dirs.dataDir, env, log: () => {} })
  const launcher = [
    'import runpy',
    `exec(open(${JSON.stringify(join(HERE, 'fake_mailbox.py'))}).read())`,
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
  while (Date.now() < deadline && !log.includes(`Uvicorn running on ${baseUrl}`)) {
    if (child.exitCode !== null) throw new Error(`backend exited:\n${log.slice(-3000)}`)
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

function template(key) {
  const src = readFileSync(
    join(REPO_ROOT, 'frontend/src/components/tabs/automations/automationTemplates.ts'),
    'utf8',
  )
  const at = src.indexOf(`key: '${key}'`)
  const start = src.indexOf('instructions:\n', at)
  const end = src.indexOf("',\n", start) + 1
  return [...src.slice(start, end).matchAll(/'((?:[^'\\]|\\.)*)'/g)].map((m) => m[1]).join('')
}

async function runAutomation(baseUrl, id, { timeoutMs = 12 * 60_000 } = {}) {
  await api(baseUrl, `/api/automations/${id}/run`, { method: 'POST' })
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 4000))
    const run = (await api(baseUrl, `/api/automations/${id}/runs?limit=1`)).runs?.[0]
    if (run && !['queued', 'running', 'pending', 'starting'].includes(run.status)) return run
  }
  throw new Error(`automation run did not finish within ${timeoutMs / 1000}s`)
}

async function bookedFromEmail(baseUrl, accountId) {
  const body = await api(baseUrl, `/api/transactions?account_ids=${encodeURIComponent(accountId)}`)
  const rows = Array.isArray(body) ? body : (body.transactions ?? body.items ?? [])
  return rows
    .filter((t) => !t.deleted && (t.external_refs ?? []).some((r) => r.source === 'email'))
    .map((t) => ({
      amount: t.to_account_id === accountId ? Number(t.to_amount) : -Number(t.from_amount),
      category: t.category ?? null,
      payeeId: t.payee_id ?? null,
      description: t.description ?? '',
      original:
        t.original_amount != null
          ? `${Number(t.original_amount).toFixed(2)} ${t.original_currency}`
          : null,
      refs: (t.external_refs ?? []).filter((r) => r.source === 'email').map((r) => r.ref),
    }))
}

const results = []
async function step(id, fn) {
  try {
    await fn()
    results.push({ id, ok: true })
    console.log(`PASS ${id}`)
  } catch (error) {
    results.push({ id, ok: false, error: String(error?.message ?? error) })
    console.log(`FAIL ${id}: ${error?.message ?? error}`)
  }
}

async function main() {
  if (!process.env.EML_SKIP_BUILD) {
    const b = spawnSync('npm', ['run', 'build'], {
      cwd: join(REPO_ROOT, 'frontend'),
      stdio: 'inherit',
    })
    if (b.status !== 0) throw new Error('frontend build failed')
  }
  const mailboxPath = join(mkdtempSync(join(tmpdir(), 'onw-eml-box-')), 'mailbox.json')
  writeFileSync(mailboxPath, '[]')
  const backend = await bootBackend(mailboxPath)
  const { baseUrl } = backend
  try {
    const profile = await api(baseUrl, '/api/profile').catch(() => ({}))
    if (profile?.timezone) APP_TZ = profile.timezone
    writeFileSync(mailboxPath, JSON.stringify(mailbox(), null, 2))
    await api(baseUrl, '/api/model-config', {
      method: 'PUT',
      body: JSON.stringify({ provider: 'claude_cli', model: MODEL }),
    })
    const account = await api(baseUrl, '/api/accounts', {
      method: 'POST',
      body: JSON.stringify({ name: 'CA_Monzo', type: 'cash', currency: 'GBP', initial_balance: 0 }),
    })
    const accountId = account.id ?? account.account?.id
    await api(baseUrl, '/api/email/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: true,
        provider: 'custom',
        address: 'me@example.test',
        password: 'eml-fake-password',
        imap_host: 'imap.example.test',
        imap_port: 993,
        imap_folder: 'INBOX',
      }),
    })
    const categoriesBefore = await api(baseUrl, '/api/categories?include_hidden=true')
    const automation = await api(baseUrl, '/api/automations', {
      method: 'POST',
      body: JSON.stringify({
        name: 'Auto-log transactions from email',
        instructions: template('mail_ledger'),
        trigger: { kind: 'schedule', type: 'interval', seconds: 43200 },
        permission_mode: 'auto',
      }),
    })

    await step('EML-LOGS-EVERY-ALERT-ONCE', async () => {
      const run = await runAutomation(baseUrl, automation.id)
      console.log(`  run (${MODEL}): ${run.status}`)
      writeFileSync(join(OUT_DIR, 'run-1.json'), JSON.stringify(run, null, 2))
      if (run.session_id) {
        const convo = await api(
          baseUrl,
          `/api/sessions/${encodeURIComponent(run.session_id)}/messages?tail=200`,
        ).catch((e) => ({ error: String(e) }))
        writeFileSync(join(OUT_DIR, 'run-1-conversation.json'), JSON.stringify(convo, null, 2))
      }
      const rows = await bookedFromEmail(baseUrl, accountId)
      assert.deepEqual(
        rows.map((r) => r.amount.toFixed(2)).sort(),
        EXPECTED.map((a) => a.toFixed(2)).sort(),
        'CA_Monzo does not hold exactly one row per real alert (newsletter out, resend once)',
      )
    })

    await step('EML-CATEGORISED', async () => {
      const cats = await api(baseUrl, '/api/categories?include_hidden=true')
      const label = Object.fromEntries(cats.map((c) => [c.id, c.label]))
      const rows = await bookedFromEmail(baseUrl, accountId)
      console.log(
        `  row categories (${MODEL}): ${rows.map((r) => `${r.amount}→${label[r.category] ?? '—'}`).join(', ')}`,
      )
      assert.deepEqual(
        rows.filter((r) => !r.category).map((r) => r.amount),
        [],
        'rows left uncategorised',
      )
      assert.deepEqual(
        rows
          .filter((r) => /^(other|stuff i forgot|misc)/i.test(label[r.category] ?? ''))
          .map((r) => r.amount),
        [],
        'a row was dumped in a catch-all category',
      )
      const of = (a) =>
        String(label[rows.find((r) => Math.abs(r.amount - a) < 0.001)?.category] ?? '')
      assert.match(of(-12.4), /groc|food/i, `grocery filed as ${of(-12.4)}`)
      assert.match(of(2500), /salar|income|wage|pay/i, `salary filed as ${of(2500)}`)
      assert.match(of(-24.99), /insur/i, `insurance filed as ${of(-24.99)}`)
      const before = new Set(categoriesBefore.map((c) => c.id))
      const created = cats.filter((c) => !before.has(c.id)).map((c) => c.label)
      console.log(`  categories created (${MODEL}): ${created.join(', ') || 'none'}`)
      assert.deepEqual(
        created.filter((l) => /acme|monzo/i.test(l)),
        [],
        'a category was named after a merchant',
      )
    })

    await step('EML-CLEAN-PAYEES', async () => {
      const payees = (await api(baseUrl, '/api/payees')).payees
      const nameOf = Object.fromEntries(payees.map((p) => [p.id, p.name]))
      const rows = await bookedFromEmail(baseUrl, accountId)
      const names = rows.map((r) => nameOf[r.payeeId] ?? '')
      console.log(`  payees (${MODEL}): ${names.join(', ')}`)
      assert.deepEqual(
        names.filter((n) => /TXN-|DD-\d|ref\b|\d{4,}/i.test(n)),
        [],
        'a payee carries a reference',
      )
      const grocer = payees.find((p) => /acme grocer/i.test(p.name))
      assert.ok(
        grocer && Object.values(grocer.default_categories ?? {}).length,
        'the grocer learned no default category',
      )
    })

    await step('EML-ORIGINAL-CHARGE', async () => {
      const paris = (await bookedFromEmail(baseUrl, accountId)).find(
        (r) => Math.abs(r.amount + 42.1) < 0.001,
      )
      assert.ok(paris, 'no £42.10 row for the euro purchase')
      assert.equal(paris.original, '48.00 EUR', `original charge stored as ${paris.original}`)
    })

    await step('EML-RERUN-BOOKS-NOTHING', async () => {
      const before = (await bookedFromEmail(baseUrl, accountId)).length
      await runAutomation(baseUrl, automation.id)
      const after = (await bookedFromEmail(baseUrl, accountId)).length
      assert.equal(after, before, `a second run changed the book (${before} → ${after})`)
    })
  } finally {
    backend.stop()
    writeFileSync(join(OUT_DIR, 'backend.log'), backend.backendLog())
    writeFileSync(join(OUT_DIR, 'results.json'), JSON.stringify(results, null, 2))
    rmSync(backend.root, { recursive: true, force: true })
  }
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`)
  if (failed.length) process.exit(1)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
