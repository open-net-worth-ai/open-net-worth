import { useEffect, useState } from 'react'
import type { CallState } from './voiceCall'
import type { OrbState } from './VoiceOrb'

// What a call says about itself, shared by the bar, the stage and the pill so the three never
// disagree. `line` is the stage's status: it never changes with who is speaking — that is shown,
// not announced, so a screen reader isn't read every breath.

export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60)
  return `${m}:${String(seconds % 60).padStart(2, '0')}`
}

export interface CallStatus {
  /** The bar's one line ("Connected · 2:14"). */
  text: string
  dot: 'wait' | 'warn' | 'live'
  /** The stage's line under the orb — empty unless something is happening (working, ringing,
   *  reconnecting): listening and muted need no words. */
  line: string
  /** The bare clock while live ("2:14"); the phase word otherwise. */
  short: string
  /** The call's clock, only while it is connected ("2:14"); empty otherwise. */
  time: string
}

export function callStatus(call: CallState, now: number, busy: boolean): CallStatus {
  switch (call.phase) {
    case 'connecting':
      return { text: 'Calling…', dot: 'wait', line: 'Calling…', short: 'Calling…', time: '' }
    case 'ringing':
      return { text: 'Ringing…', dot: 'wait', line: 'Ringing…', short: 'Ringing…', time: '' }
    case 'reconnecting':
      return {
        text: 'Reconnecting…',
        dot: 'warn',
        line: 'Your connection dropped. Reconnecting…',
        short: 'Reconnecting…',
        time: '',
      }
    case 'ended': {
      const t = clock(call.endedSeconds ?? 0)
      // The top already says it ("Call ended · 2:14"): once is enough.
      return { text: `Call ended · ${t}`, dot: 'wait', line: '', short: 'Call ended', time: '' }
    }
    default: {
      const secs = call.connectedAt ? Math.max(0, Math.round((now - call.connectedAt) / 1000)) : 0
      if (call.luciusTrouble) {
        return {
          text: "Lucius's connection dropped",
          dot: 'warn',
          line: "Lucius's connection dropped. Reconnecting…",
          short: 'Reconnecting…',
          time: clock(secs),
        }
      }
      // Muted needs no words: the pressed Unmute button already says it (owner, 2026-11-03).
      const line = busy ? 'Lucius is working…' : ''
      return {
        text: busy ? 'Lucius is working…' : `Connected · ${clock(secs)}`,
        dot: 'live',
        line,
        short: busy ? 'Working…' : clock(secs),
        time: clock(secs),
      }
    }
  }
}

export function orbState(call: CallState, busy: boolean): OrbState {
  switch (call.phase) {
    case 'connecting':
    case 'ringing':
      return 'calling'
    case 'reconnecting':
      return 'reconnecting'
    case 'ended':
      return 'ended'
    default:
      if (call.luciusTrouble) return 'reconnecting'
      return busy ? 'working' : 'live'
  }
}

export type PillMode =
  | 'rest'
  | 'calling'
  | 'live'
  | 'attention'
  | 'reconnecting'
  | 'ended'
  | 'problem'

/** What the dock's collapsed pill shows: the call lives in the pill, no chat needed. */
export function pillMode(call: CallState, needsApproval: boolean): PillMode {
  if (call.problem) return 'problem'
  switch (call.phase) {
    case 'ended':
      return 'ended'
    case 'reconnecting':
      return 'reconnecting'
    case 'connecting':
    case 'ringing':
      return 'calling'
    case 'live':
      if (call.luciusTrouble) return 'reconnecting'
      return needsApproval ? 'attention' : 'live'
    default:
      return 'rest'
  }
}

/** Now, ticking once a second while the call is live (for its clock). */
export function useCallClock(call: CallState): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (call.phase !== 'live') return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [call.phase])
  return now
}
