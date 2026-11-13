import { describe, expect, it } from 'vitest'
import type { ProposedChange } from '../../../types/chat'
import { permissionSubject, permissionTitle } from './cardTypes'

describe('a REST API tool’s permission card', () => {
  const card = (tool: string) =>
    ({ type: 'tool_permission', grant_tool: tool, display: {} }) as unknown as ProposedChange

  it('is titled by its endpoint, with its API beside it', () => {
    // It read "Rest swagger petstore add pet" (a live card, 2026-11-11).
    const change = card('rest__swagger_petstore__add_pet')
    expect(permissionTitle(change)).toBe('Add pet')
    expect(permissionSubject(change)).toBe('Swagger petstore')
  })

  it('leaves every other tool as it was', () => {
    expect(permissionTitle(card('email_send_message'))).toBe('Email send message')
    expect(permissionSubject(card('email_send_message'))).toBeNull()
  })
})
