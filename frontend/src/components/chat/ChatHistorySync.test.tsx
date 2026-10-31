// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { markChatSwitch } from '../../state/chatHistory'

// A stand-in for the shared chat state: one open conversation, the listed ones, and the same
// entry points the real provider marks as a person's switch.
type Conv = { id: string }
let store: { activeId: string; conversations: Conv[] }
const listeners = new Set<() => void>()
const set = (next: Partial<typeof store>) => {
  store = { ...store, ...next }
  listeners.forEach((l) => l())
}
let minted = 0
const chatApi = {
  switchConversation: (id: string) => {
    if (id !== store.activeId) markChatSwitch()
    set({ activeId: id })
  },
  createConversation: (opts?: { replaceHistory?: boolean }) => {
    if (!opts?.replaceHistory) markChatSwitch()
    const id = `new${++minted}`
    set({ activeId: id })
    return id
  },
}
const persist = (id: string) => set({ conversations: [{ id }, ...store.conversations] })
const remove = (id: string) =>
  set({ conversations: store.conversations.filter((c) => c.id !== id) })

vi.mock('../../state/luciusChat', () => ({
  useLuciusChat: () => {
    const snap = useSyncExternalStore(
      (l) => {
        listeners.add(l)
        return () => listeners.delete(l)
      },
      () => store,
    )
    return { ...snap, ...chatApi }
  },
}))

import { ChatHistorySync } from './ChatHistorySync'

async function setup(initial: string) {
  const rootRoute = createRootRoute({
    component: () => (
      <>
        <ChatHistorySync />
        <Outlet />
      </>
    ),
  })
  const lucius = createRoute({
    getParentRoute: () => rootRoute,
    path: '/lucius',
    validateSearch: (s: Record<string, unknown>) => ({
      conversation: typeof s.conversation === 'string' ? s.conversation : undefined,
    }),
  })
  const dashboard = createRoute({ getParentRoute: () => rootRoute, path: '/dashboard' })
  const history = createMemoryHistory({ initialEntries: [initial] })
  const router = createRouter({ routeTree: rootRoute.addChildren([lucius, dashboard]), history })
  render(<RouterProvider router={router} />)
  await act(() => router.load())
  await act(() => new Promise((r) => setTimeout(r, 20)))
  const href = () => router.state.location.href
  return { router, history, href }
}

afterEach(cleanup)

beforeEach(() => {
  minted = 0
  store = { activeId: 'a', conversations: [{ id: 'a' }, { id: 'b' }, { id: 'old' }] }
})

describe('ChatHistorySync — the Lucius page', () => {
  it('shows the open chat in the address, and Back/Forward move between chats', async () => {
    const { history, href } = await setup('/lucius')
    await waitFor(() => expect(href()).toBe('/lucius?conversation=a'))

    act(() => chatApi.switchConversation('b'))
    await waitFor(() => expect(href()).toBe('/lucius?conversation=b'))

    act(() => history.back())
    await waitFor(() => expect(store.activeId).toBe('a'))
    expect(href()).toBe('/lucius?conversation=a')

    act(() => history.forward())
    await waitFor(() => expect(store.activeId).toBe('b'))
    expect(href()).toBe('/lucius?conversation=b')
  })

  it('a new chat is plain /lucius until its first message, and Back returns to the chat before', async () => {
    const { history, href } = await setup('/lucius')
    await waitFor(() => expect(href()).toBe('/lucius?conversation=a'))

    act(() => void chatApi.createConversation())
    await waitFor(() => expect(href()).toBe('/lucius'))

    act(() => persist('new1'))
    await waitFor(() => expect(href()).toBe('/lucius?conversation=new1'))

    act(() => history.back())
    await waitFor(() => expect(store.activeId).toBe('a'))
    expect(href()).toBe('/lucius?conversation=a')
  })

  it('Back from an untouched new chat drops it, and Forward starts a new one again', async () => {
    const { history, href } = await setup('/lucius')
    await waitFor(() => expect(href()).toBe('/lucius?conversation=a'))
    act(() => void chatApi.createConversation())
    await waitFor(() => expect(href()).toBe('/lucius'))

    act(() => history.back())
    await waitFor(() => expect(store.activeId).toBe('a'))

    act(() => history.forward())
    await waitFor(() => expect(store.activeId).toBe('new2'))
    expect(href()).toBe('/lucius')
  })

  it('an opened link switches to its chat and keeps it in the address', async () => {
    const { href } = await setup('/lucius?conversation=old')
    await waitFor(() => expect(store.activeId).toBe('old'))
    expect(href()).toBe('/lucius?conversation=old')
  })
})

describe('ChatHistorySync — the side panel over another page', () => {
  it('Back returns to the chat before, and the page address never changes', async () => {
    const { history, href } = await setup('/dashboard')
    act(() => chatApi.switchConversation('b'))
    await waitFor(() => expect(history.location.state).toMatchObject({ luciusChat: 'b' }))
    expect(href()).toBe('/dashboard')

    act(() => history.back())
    await waitFor(() => expect(store.activeId).toBe('a'))
    expect(href()).toBe('/dashboard')

    act(() => history.forward())
    await waitFor(() => expect(store.activeId).toBe('b'))
  })

  it('leaves history alone when the open chat changes without a switch', async () => {
    const { history } = await setup('/dashboard')
    const before = history.length
    act(() => set({ activeId: 'b' }))
    await new Promise((r) => setTimeout(r, 50))
    expect(history.length).toBe(before)
    expect(history.location.state).not.toHaveProperty('luciusChat')
  })

  it('Back never reopens a chat that was deleted meanwhile', async () => {
    const { history } = await setup('/dashboard')
    act(() => chatApi.switchConversation('b'))
    await waitFor(() => expect(history.location.state).toMatchObject({ luciusChat: 'b' }))
    act(() => remove('a'))

    act(() => history.back())
    await new Promise((r) => setTimeout(r, 50))
    expect(store.activeId).toBe('b')
  })
})
