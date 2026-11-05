// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { emitScreenRequest, onScreenRequest, type ScreenRequest } from './bus'
import { readEvents } from './dataEventsStream'
import {
  decideScreenReply,
  RANK_CALL,
  RANK_COVERED,
  RANK_FOCUSED,
  RANK_VISIBLE,
} from './screenRead'

const view = {
  sharing: true,
  onCall: false,
  focused: false,
  visible: true,
  covered: false,
}

describe('decideScreenReply', () => {
  it('ranks the device running the call above a focused one above a merely visible one', () => {
    expect(decideScreenReply(view)).toEqual({ status: 'ok', rank: RANK_VISIBLE })
    expect(decideScreenReply({ ...view, focused: true })).toEqual({
      status: 'ok',
      rank: RANK_FOCUSED,
    })
    expect(decideScreenReply({ ...view, focused: true, onCall: true })).toEqual({
      status: 'ok',
      rank: RANK_CALL,
    })
  })

  it('does not answer from a tab out of view', () => {
    expect(decideScreenReply({ ...view, visible: false, onCall: true })).toBeNull()
  })

  it('refuses at full rank when sharing is off, so no other device is read instead', () => {
    expect(decideScreenReply({ ...view, sharing: false, onCall: true })).toEqual({
      status: 'sharing_off',
      rank: RANK_CALL,
    })
  })

  it('claims no rank when the page is covered, so a device in view can still answer', () => {
    expect(decideScreenReply({ ...view, covered: true, onCall: true })).toEqual({
      status: 'no_screen',
      rank: RANK_COVERED,
    })
  })
})

describe('readEvents', () => {
  const stream = (...frames: string[]) =>
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame))
        controller.close()
      },
    })

  it('hands a screen request to the bus, without calling it a data change', async () => {
    const heard: ScreenRequest[] = []
    const off = onScreenRequest((r) => heard.push(r))
    const domains: string[][] = []
    await readEvents(
      stream(
        'data: {"type":"screen_request","request_id":"r1","session_id":"c1"}\n\n',
        'data: {"type":"screen_request","request_id":7}\n\n',
        'data: {"type":"data_changed","domains":["ledger"]}\n\n',
      ),
      (d) => domains.push(d),
    )
    off()
    emitScreenRequest({ request_id: 'after-off', session_id: 'c1' })
    expect(heard).toEqual([{ request_id: 'r1', session_id: 'c1' }])
    expect(domains).toEqual([['ledger']])
  })
})
