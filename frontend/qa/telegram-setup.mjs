// TGS — setting up the Telegram connector, end to end, the way a first-time user does it.
//
// A disposable backend (fresh tmp data, never :8080) whose Telegram Bot API base points at an
// in-process fake Bot API below — everything above that HTTP boundary is real: python-telegram-
// bot polling getUpdates, the connector's handlers and pairing seam, the token check, the
// config + pairing endpoints, and the production frontend build in Chromium. Lucius answers the
// friend's first real message through the app's OpenAI-compatible transport against the local
// mock LLM (./mock-llm-server.mjs) — no paid provider, no network egress.
//
// The journey (2026-11-15 — the owner and a friend both got lost in the old form):
//   paste a WRONG token → Save refuses it and says why
//   paste the right token → Save → the form says the bot is online, links to it, and waits
//   the friend taps Start in Telegram → the bot replies with a pairing code
//   the request appears in the form → Approve (no second save) → the bot says "You're in"
//   the friend asks a question → Lucius answers in Telegram
//
//   npm run e2e:telegram-setup            (builds the frontend first)
//   TGS_SKIP_BUILD=1 npm run e2e:telegram-setup

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
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
import { SCENARIO_MARKER, startMockLlmServer } from './mock-llm-server.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..', '..')
const OUT_DIR = resolve(REPO_ROOT, 'qa/artifacts/telegram-setup')
rmSync(OUT_DIR, { recursive: true, force: true })
mkdirSync(OUT_DIR, { recursive: true })

const GOOD_TOKEN = '7000000001:AAqa-fake-token-for-the-e2e-run'
const BAD_TOKEN = '7000000001:wrong'
const BOT = { id: 7000000001, is_bot: true, first_name: 'ACME Lucius', username: 'acme_lucius_bot' }
const FRIEND = {
  id: 4242001,
  is_bot: false,
  first_name: 'Ada',
  last_name: 'Lovelace',
  username: 'ada_qa',
}

// ── fake Telegram Bot API ─────────────────────────────────────────────────────────────

function fakeTelegram() {
  const state = {
    updates: [],
    nextUpdateId: 1,
    nextMessageId: 100,
    sent: [],
    reactions: [],
    calls: [],
  }
  const now = () => Math.floor(Date.now() / 1000)

  function friendSays(text) {
    const entities = text.startsWith('/')
      ? [{ type: 'bot_command', offset: 0, length: text.split(' ')[0].length }]
      : undefined
    state.updates.push({
      update_id: state.nextUpdateId++,
      message: {
        message_id: state.nextMessageId++,
        date: now(),
        chat: {
          id: FRIEND.id,
          type: 'private',
          first_name: FRIEND.first_name,
          last_name: FRIEND.last_name,
          username: FRIEND.username,
        },
        from: FRIEND,
        text,
        ...(entities ? { entities } : {}),
      },
    })
  }

  function message(chatId, text) {
    return {
      message_id: state.nextMessageId++,
      date: now(),
      chat: { id: Number(chatId), type: 'private' },
      from: BOT,
      text,
    }
  }

  async function readParams(req) {
    const chunks = []
    for await (const c of req) chunks.push(c)
    const raw = Buffer.concat(chunks).toString('utf8')
    const type = req.headers['content-type'] || ''
    if (type.includes('application/json')) return raw ? JSON.parse(raw) : {}
    const out = {}
    for (const [k, v] of new URLSearchParams(raw)) {
      try {
        out[k] = JSON.parse(v)
      } catch {
        out[k] = v
      }
    }
    return out
  }

  const server = createServer(async (req, res) => {
    const m = /^\/bot([^/]+)\/(\w+)$/.exec(req.url.split('?')[0])
    const reply = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(body))
    }
    if (!m) return reply(404, { ok: false, error_code: 404, description: 'Not Found' })
    const [, token, method] = m
    const params = await readParams(req)
    state.calls.push(method)
    if (decodeURIComponent(token) !== GOOD_TOKEN)
      return reply(401, { ok: false, error_code: 401, description: 'Unauthorized' })
    const ok = (result) => reply(200, { ok: true, result })
    switch (method) {
      case 'getMe':
        return ok({
          ...BOT,
          can_join_groups: true,
          can_read_all_group_messages: false,
          supports_inline_queries: false,
        })
      case 'getUpdates': {
        const offset = Number(params.offset ?? 0)
        if (offset < 0) return ok(state.updates.slice(offset))
        const deadline = Date.now() + Math.min(Number(params.timeout ?? 0), 1) * 1000
        for (;;) {
          const fresh = state.updates.filter((u) => u.update_id >= offset)
          if (fresh.length || Date.now() >= deadline) return ok(fresh)
          await new Promise((r) => setTimeout(r, 100))
        }
      }
      case 'sendMessage': {
        const msg = message(params.chat_id, String(params.text ?? ''))
        state.sent.push({
          chat_id: Number(params.chat_id),
          text: msg.text,
          message_id: msg.message_id,
        })
        return ok(msg)
      }
      case 'editMessageText': {
        const sent = state.sent.find((s) => s.message_id === Number(params.message_id))
        if (sent) sent.text = String(params.text ?? '')
        return ok(message(params.chat_id, String(params.text ?? '')))
      }
      case 'setMessageReaction':
        state.reactions.push({
          chat_id: Number(params.chat_id),
          message_id: Number(params.message_id),
        })
        return ok(true)
      default:
        return ok(true) // deleteWebhook, setMyCommands, sendChatAction, deleteMessage, …
    }
  })
  return { server, state, friendSays }
}

const children = [] // killed by the hard timeout too, so a hung run never leaves a server behind

// ── disposable backend ────────────────────────────────────────────────────────────────

async function bootBackend(fakeApi) {
  const port = await reserveFreeLoopbackPort()
  const root = mkdtempSync(join(tmpdir(), 'onw-tgs-'))
  const dirs = {
    dataDir: join(root, 'data'),
    cacheDir: join(root, 'cache'),
    logDir: join(root, 'logs'),
  }
  assertSafeBoot({ port, ...dirs })
  const env = { ...buildQaChildEnv({ ...dirs, demoMode: false, port }), TGS_FAKE_API: fakeApi }
  // QA boots switch the poller off so they can't steal a real bot's getUpdates; this run's
  // bot is the fake one, and the poller is what's under test.
  delete env.ONW_DISABLE_TELEGRAM
  seedDemo({ dataDir: dirs.dataDir, env, log: () => {} }) // a book past first run
  const launcher = [
    'import os, runpy',
    'import backend.connectors.telegram.botapi as botapi',
    "botapi.API_BASE = os.environ['TGS_FAKE_API']",
    "runpy.run_module('backend.main', run_name='__main__')",
  ].join('\n')
  const child = spawn(resolveQaPython(), ['-c', launcher], {
    cwd: REPO_ROOT,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  children.push(child)
  let log = ''
  child.stdout.on('data', (d) => (log += d))
  child.stderr.on('data', (d) => (log += d))
  const baseUrl = `http://127.0.0.1:${port}`
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`backend exited:\n${log.slice(-3000)}`)
    try {
      if ((await fetch(`${baseUrl}/api/health`)).ok) break
    } catch {
      /* not yet */
    }
    await new Promise((r) => setTimeout(r, 300))
  }
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

async function until(what, fn, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const v = await fn()
    if (v) return v
    if (Date.now() > deadline) throw new Error(`timed out waiting for: ${what}`)
    await new Promise((r) => setTimeout(r, 200))
  }
}

// ── the journey ───────────────────────────────────────────────────────────────────────

const results = []
async function step(id, fn) {
  const t0 = Date.now()
  await fn()
  results.push({ id, ms: Date.now() - t0 })
  console.log(`PASS ${id}`)
}

async function main() {
  if (!process.env.TGS_SKIP_BUILD) {
    const b = spawnSync('npm', ['run', 'build'], {
      cwd: join(REPO_ROOT, 'frontend'),
      stdio: 'inherit',
    })
    if (b.status !== 0) throw new Error('frontend build failed')
  }
  const tg = fakeTelegram()
  const tgPort = await reserveFreeLoopbackPort()
  await new Promise((r) => tg.server.listen(tgPort, '127.0.0.1', r))
  const llm = await startMockLlmServer()
  const backend = await bootBackend(`http://127.0.0.1:${tgPort}`)
  const { baseUrl } = backend
  const browser = await chromium.launch()
  const consoleErrors = []
  let page
  const shot = (name) => page.screenshot({ path: join(OUT_DIR, `${name}.png`) })
  const dialog = () => page.locator('.rd-modal').last()
  try {
    // Precondition, as for a user who set Lucius up already: a model to answer with.
    await api(baseUrl, '/api/model-config/providers/custom', {
      method: 'PUT',
      body: JSON.stringify({
        api_key: 'sk-qa-mock',
        base_url: `${llm.url}/v1`,
        model: 'qa-mock-model',
      }),
    })
    await api(baseUrl, '/api/model-config', {
      method: 'PUT',
      body: JSON.stringify({ default_model: 'custom:qa-mock-model' }),
    })

    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      deviceScaleFactor: 2,
    })
    page = await ctx.newPage()
    page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
    page.on('pageerror', (e) => consoleErrors.push(String(e)))

    await step('TGS-01 the form opens on Pairing with three steps and no chat-ID box', async () => {
      await page.goto(`${baseUrl}/connectors?connector=telegram`)
      const connect = page.getByRole('button', { name: 'Connect' })
      if (await connect.isVisible({ timeout: 8000 }).catch(() => false)) await connect.click()
      await page.getByLabel(/^Bot token/).waitFor({ timeout: 15_000 })
      assert.equal(await page.getByLabel('Who can chat', { exact: true }).inputValue(), 'pairing')
      for (const title of ['Create your bot', 'Choose who can chat', 'Say hi to your bot'])
        await page.getByRole('region', { name: title }).waitFor()
      await page.getByText(/Click Save to bring your bot online/).waitFor()
      assert.equal(await page.getByLabel('Add a chat ID').count(), 0)
      await shot('01-new-connector')
    })

    await step('TGS-02 a token Telegram refuses is not saved, and the form says why', async () => {
      await page.getByLabel(/^Bot token/).fill(BAD_TOKEN)
      await dialog().getByRole('button', { name: 'Save' }).click()
      // Said ON the token field, in step 1 — not at the bottom of the form.
      await page
        .getByRole('region', { name: 'Create your bot' })
        .getByText(/Telegram didn't accept this bot token/)
        .waitFor({ timeout: 15_000 })
      const cfg = await api(baseUrl, '/api/telegram/config')
      assert.equal(cfg.has_bot_token, false)
      await shot('02-wrong-token')
    })

    await step(
      'TGS-03 the right token: one Save brings the bot online and links to it',
      async () => {
        await page.getByLabel(/^Bot token/).fill(GOOD_TOKEN)
        await dialog().getByRole('button', { name: 'Save' }).click()
        await page.getByText('Online as @acme_lucius_bot').waitFor({ timeout: 30_000 })
        const link = page.getByRole('link', { name: /Open @acme_lucius_bot in Telegram/ })
        assert.equal(await link.getAttribute('href'), 'https://t.me/acme_lucius_bot')
        await page.getByText(/Waiting for your first message/).waitFor()
        await dialog().getByRole('button', { name: 'Done' }).waitFor({ timeout: 10_000 })
        await until('the poller to be reading updates', () => tg.state.calls.includes('getUpdates'))
        await shot('03-online-waiting')
      },
    )

    await step('TGS-04 the friend taps Start and gets a pairing code from the bot', async () => {
      tg.friendSays('/start')
      const reply = await until('a pairing-code reply', () =>
        tg.state.sent.find((s) => s.chat_id === FRIEND.id && /Pairing code/.test(s.text)),
      )
      const { requests } = await api(baseUrl, '/api/chat-pairing?connector=telegram')
      assert.equal(requests.length, 1)
      assert.ok(
        reply.text.includes(`<b>${requests[0].code}</b>`),
        `bold code ${requests[0].code} not in: ${reply.text}`,
      )
      assert.equal(requests[0].sender_label, 'Ada Lovelace (@ada_qa)')
      console.log(`      bot → friend: ${JSON.stringify(reply.text)}`)
    })

    await step('TGS-05 the request appears in the open form without a reload', async () => {
      await page.getByText('Ada Lovelace (@ada_qa)').waitFor({ timeout: 15_000 })
      await page.getByText(/Pending approvals \(1\)/).waitFor()
      await shot('04-request-waiting-for-approval')
    })

    await step(
      'TGS-06 Approve lets them in with no second save, and the bot tells them',
      async () => {
        const putsBefore = tg.state.calls.length
        await dialog().getByRole('button', { name: 'Approve' }).click()
        await page
          .getByText(/Ada Lovelace \(@ada_qa\) can chat with Lucius now — the bot just told them/)
          .waitFor({ timeout: 15_000 })
        await until(
          'the approved chat ID in the form',
          async () =>
            (await page
              .getByRole('list', { name: 'Approved chats' })
              .getByText(String(FRIEND.id))
              .count()) > 0,
        )
        const cfg = await api(baseUrl, '/api/telegram/config')
        assert.deepEqual(cfg.allowed_chat_ids, [String(FRIEND.id)])
        const welcome = await until('the welcome message', () =>
          tg.state.sent.find((s) => s.chat_id === FRIEND.id && /You're in/.test(s.text)),
        )
        console.log(`      bot → friend: ${JSON.stringify(welcome.text)}`)
        await dialog().getByRole('button', { name: 'Done' }).waitFor()
        assert.ok(tg.state.calls.length >= putsBefore)
        await shot('05-approved')
      },
    )

    await step('TGS-07 the friend asks a question and Lucius answers in Telegram', async () => {
      const before = tg.state.sent.length
      tg.friendSays(`${SCENARIO_MARKER}1 how is my portfolio doing?`)
      await until('the 👀 reaction an approved sender gets', () =>
        tg.state.reactions.some((r) => r.chat_id === FRIEND.id),
      )
      const answer = await until(
        "Lucius's answer",
        () =>
          tg.state.sent
            .slice(before)
            .find((s) => s.chat_id === FRIEND.id && /diversified/.test(s.text)),
        90_000,
      )
      assert.ok(!tg.state.sent.slice(before).some((s) => /Pairing code/.test(s.text)))
      console.log(`      Lucius → friend: ${JSON.stringify(answer.text.slice(0, 120))}…`)
    })

    await step('TGS-08 Done leaves the form; the connector page shows it live', async () => {
      await dialog().getByRole('button', { name: 'Done' }).click()
      await page.getByText('Tool permissions').first().waitFor({ timeout: 10_000 })
      await shot('06-detail-after-done')
    })

    await step('TGS-09 the setup form fits a phone', async () => {
      const phone = await browser.newContext({
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      })
      const p = await phone.newPage()
      await p.goto(`${baseUrl}/connectors?connector=telegram`)
      await p.getByRole('button', { name: 'More actions' }).click()
      await p.getByText('Edit configuration').click()
      await p.getByText('Online as @acme_lucius_bot').waitFor({ timeout: 15_000 })
      const overflow = await p.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      assert.ok(overflow <= 1, `page scrolls sideways by ${overflow}px`)
      await p.screenshot({ path: join(OUT_DIR, '07-phone.png') })
      await phone.close()
    })

    const appErrors = consoleErrors.filter((e) => !/Failed to load resource|favicon/.test(e))
    assert.deepEqual(appErrors, [], `browser console errors:\n${appErrors.join('\n')}`)
    console.log(`\nALL ${results.length} STEPS PASSED — screenshots in ${OUT_DIR}`)
  } catch (error) {
    console.log(`\nFAIL: ${error?.stack ?? error}`)
    if (page)
      await page.screenshot({ path: join(OUT_DIR, 'failure.png'), fullPage: true }).catch(() => {})
    writeFileSync(join(OUT_DIR, 'backend.log'), backend.backendLog())
    process.exitCode = 2
  } finally {
    writeFileSync(
      join(OUT_DIR, 'results.json'),
      JSON.stringify({ results, sent: tg.state.sent }, null, 2),
    )
    await browser.close()
    backend.stop()
    await llm.stop()
    tg.server.closeAllConnections?.()
    tg.server.close()
    rmSync(backend.root, { recursive: true, force: true })
  }
}

const killer = setTimeout(() => {
  console.log('HARD TIMEOUT — killing the run')
  for (const c of children) c.kill('SIGKILL')
  process.exit(3)
}, 8 * 60_000)
killer.unref()
await main()
