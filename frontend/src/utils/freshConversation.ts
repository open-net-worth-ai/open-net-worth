import type { LuciusChatValue } from '../state/luciusChat'

/** Start a new conversation unless the active one already is a new, empty one. A
 *  `persist: false` conversation never joins `conversations` (it stays out of the sidebar and
 *  off the server until it has a real message), so "the active id isn't in the list" IS
 *  "already sitting on an empty one" — reuse it instead of minting another. */
export function startFreshConversation(
  chat: Pick<LuciusChatValue, 'conversations' | 'activeId' | 'createConversation'>,
): void {
  if (chat.conversations.some((c) => c.id === chat.activeId)) chat.createConversation()
}
