// A call the owner denied (permissions_v2 marks its result `declined_by: "user"`) did not run,
// but it did not FAIL either: the card already says "Denied". A result without that marker is
// classified exactly as before.
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

describe('failureOf — a denied call', () => {
  it('is not a failure when the owner declined it', () => {
    expect(
      failureOf(tool('{"ok":false,"declined_by":"user","error":"THE USER DECLINED"}')).failed,
    ).toBe(false)
  })

  it('is still a failure when an error carries no such marker', () => {
    expect(failureOf(tool('{"ok":false,"error":"THE USER DECLINED"}')).failed).toBe(true)
  })
})
