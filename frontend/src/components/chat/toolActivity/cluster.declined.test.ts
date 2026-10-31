// A call the owner denied (permissions_v2 marks its result `declined_by: "user"`) did not run,
// but it did not FAIL either: the card already says "Denied". That reading applies only with the
// flag on; with it off, and for a result without the marker, classification is exactly as before.
import { describe, expect, it } from 'vitest'
import { failureOf, type ToolItem } from './cluster'

function tool(result: string): ToolItem {
  return {
    type: 'tool',
    name: 'delete_transactions',
    id: 't1',
    status: 'done',
    display: {
      label: 'Delete transactions',
      verb: 'delete',
      noun: 'transactions',
      owner_id: 'core',
      owner_name: '',
      detail: '',
    },
    result,
  }
}

const DECLINED = '{"ok":false,"declined_by":"user","error":"THE USER DECLINED"}'

describe.each([false, true])('failureOf — a denied call (permissions_v2=%s)', (on) => {
  it(`${on ? 'is not' : 'stays'} a failure when the owner declined it`, () => {
    expect(failureOf(tool(DECLINED), on).failed).toBe(!on)
  })

  it('is still a failure when an error carries no such marker', () => {
    expect(failureOf(tool('{"ok":false,"error":"THE USER DECLINED"}'), on).failed).toBe(true)
  })

  it('defaults to the old reading', () => {
    expect(failureOf(tool(DECLINED)).failed).toBe(true)
  })
})
