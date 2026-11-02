import { describe, expect, it } from 'vitest'
import { removeEntry, removeTool, setTool, toolRows } from './toolPermissions'
import type { ToolLists } from './toolPermissions'

const lists: ToolLists = {
  always_allowed: [
    { src: 'Open Net Worth', desc: 'Allow update transactions', tools: ['update_transactions'] },
    { src: 'Telegram', desc: 'Digest', tools: ['telegram_send_message', 'webhook_send_message'] },
  ],
  blocked: ['email_send_message'],
}

describe('Automation tool lists', () => {
  it('flattens both lists into one row per tool', () => {
    expect(toolRows(lists).map((row) => [row.tool, row.value])).toEqual([
      ['update_transactions', 'always_allow'],
      ['telegram_send_message', 'always_allow'],
      ['webhook_send_message', 'always_allow'],
      ['email_send_message', 'blocked'],
    ])
  })

  it('shows an entry that names no tool as a row that cannot be re-valued', () => {
    const [row] = toolRows({
      always_allowed: [{ src: 'Telegram', desc: 'Send the digest' }],
      blocked: [],
    })
    expect(row).toMatchObject({ tool: null, label: 'Send the digest', entryIndex: 0 })
  })

  it('removing one tool of a multi-tool entry keeps the rest of the entry', () => {
    const next = removeTool(lists, 'webhook_send_message')
    expect(next.always_allowed[1]).toEqual({
      src: 'Telegram',
      desc: 'Digest',
      tools: ['telegram_send_message'],
    })
  })

  it('removing the only tool of an entry drops the entry', () => {
    expect(removeTool(lists, 'update_transactions').always_allowed).toHaveLength(1)
  })

  it('removes a legacy entry by position', () => {
    expect(removeEntry(lists, 0).always_allowed.map((p) => p.src)).toEqual(['Telegram'])
  })

  it('never leaves a tool on both lists', () => {
    const blocked = setTool(lists, 'update_transactions', 'blocked')
    expect(blocked.blocked).toEqual(['email_send_message', 'update_transactions'])
    expect(toolRows(blocked).filter((row) => row.tool === 'update_transactions')).toHaveLength(1)

    const allowed = setTool(lists, 'email_send_message', 'always_allow')
    expect(allowed.blocked).toEqual([])
    expect(allowed.always_allowed.at(-1)).toEqual({
      src: 'Open Net Worth',
      desc: 'Allow email send message',
      tools: ['email_send_message'],
    })
  })

  it('treats a capability name as a tool name, as the runner does', () => {
    const withCapability: ToolLists = {
      always_allowed: [{ src: 'Gmail', desc: 'Read mail', capability: 'email_get_messages' }],
      blocked: [],
    }
    expect(toolRows(withCapability)[0]?.tool).toBe('email_get_messages')
    expect(removeTool(withCapability, 'email_get_messages').always_allowed).toEqual([])
  })
})
