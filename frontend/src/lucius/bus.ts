// lucius/bus.ts — the Lucius frontend event bus (framework-free leaf; no React imports).
// Two event kinds:
//  • data_changed(domains): the backend turn executed write tools (SSE `data_changed`,
//    domains registry-tagged server-side) — subscribers refetch. See useDataVersion.
//  • screen commands: an ephemeral UI command (`plan.stage_whatif`, …) dispatched by the
//    run_screen_command SSE or by a chat chip. STICKY: with no live non-passive
//    subscriber the LATEST command per name parks and replays exactly once to the next
//    non-passive subscriber (screen hidden/unmounted, navigation mid-turn, chip from
//    another tab). Passive subscribers observe without consuming (e.g. Plan's sub-tab
//    auto-switch).

export interface ScreenCommandInvocation {
  name: string
  args: Record<string, unknown>
}

type DataChangedCb = (domains: string[]) => void
type CommandHandler = (cmd: ScreenCommandInvocation) => void
interface CommandSub {
  prefix: string
  handler: CommandHandler
  passive: boolean
}

const dataSubs = new Set<DataChangedCb>()
const cmdSubs = new Set<CommandSub>()
const parked = new Map<string, ScreenCommandInvocation>()

export function emitDataChanged(domains: string[]): void {
  for (const cb of [...dataSubs]) {
    try {
      cb(domains)
    } catch {
      /* a subscriber error must never break the bus */
    }
  }
}

export function onDataChanged(cb: DataChangedCb): () => void {
  dataSubs.add(cb)
  return () => {
    dataSubs.delete(cb)
  }
}

export function dispatchScreenCommand(cmd: ScreenCommandInvocation): void {
  if (!cmd?.name) return
  let consumed = false
  for (const s of [...cmdSubs]) {
    if (!cmd.name.startsWith(s.prefix)) continue
    try {
      s.handler(cmd)
    } catch {
      /* ditto */
    }
    if (!s.passive) consumed = true
  }
  if (!consumed) parked.set(cmd.name, cmd)
}

// NOTE for command authors: matching is startsWith(prefix), and a non-passive match
// marks the dispatch consumed. Command NAMES must therefore never be a strict prefix
// of one another (e.g. `plan.reset` + `plan.reset_scenario` would collide) — keep
// names prefix-free in screenCommands.ts.
export function onScreenCommand(
  prefix: string,
  handler: CommandHandler,
  opts?: { passive?: boolean },
): () => void {
  const sub: CommandSub = { prefix, handler, passive: opts?.passive === true }
  cmdSubs.add(sub)
  if (!sub.passive) {
    for (const [name, cmd] of [...parked]) {
      if (!name.startsWith(prefix)) continue
      parked.delete(name)
      try {
        handler(cmd)
      } catch {
        /* ditto */
      }
    }
  }
  return () => {
    cmdSubs.delete(sub)
  }
}

// ── Live sessions ─────────────────────────────────────────────────────────────
// An Automation run is a Lucius turn that this browser did not start: it happens
// server-side, so there is no SSE stream for the chat panel to follow and nothing
// ever appended while the run worked. Whoever IS tracking a run's status (the
// Automations tab polls it) publishes the session ids that are live; the chat panel
// subscribes and follows the transcript for as long as one of them is the
// conversation on screen. Empty list = nothing live, stop following.

type LiveSessionsCb = (sessionIds: string[]) => void

const liveSubs = new Set<LiveSessionsCb>()
let liveSessions: string[] = []

function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}

export function setLiveSessions(sessionIds: string[]): void {
  const next = [...new Set(sessionIds.filter(Boolean))].sort()
  if (sameIds(next, liveSessions)) return
  liveSessions = next
  for (const cb of [...liveSubs]) {
    try {
      cb(liveSessions)
    } catch {
      /* a subscriber error must never break the bus */
    }
  }
}

export function onLiveSessions(cb: LiveSessionsCb): () => void {
  liveSubs.add(cb)
  cb(liveSessions)
  return () => {
    liveSubs.delete(cb)
  }
}

export function _resetBusForTests(): void {
  dataSubs.clear()
  cmdSubs.clear()
  parked.clear()
  liveSubs.clear()
  liveSessions = []
}

// ── Conversation changed ──────────────────────────────────────────────────────
// A turn this browser did not send can start AND finish between two live polls — a voice
// call's small talk is recorded in milliseconds — so the open chat never saw it. Whoever
// knows a conversation may have changed (a voice call, while it is on) says so here, and the
// chat showing it catches up: joins a running turn live, or re-reads a finished one.

type ConversationChangedCb = (sessionId: string) => void

const conversationSubs = new Set<ConversationChangedCb>()

export function emitConversationChanged(sessionId: string): void {
  for (const cb of [...conversationSubs]) {
    try {
      cb(sessionId)
    } catch {
      /* a subscriber error must never break the bus */
    }
  }
}

export function onConversationChanged(cb: ConversationChangedCb): () => void {
  conversationSubs.add(cb)
  return () => {
    conversationSubs.delete(cb)
  }
}
