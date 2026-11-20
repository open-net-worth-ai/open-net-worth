// Test double for `utils/chatServerSync.syncWithServer` in suites that mock
// `utils/chatStore`: routes the transcript read through that mock's
// `reconcileWithRemote`, as a turn-still-running (non-authoritative) read — the path those
// suites were written to pin. The finished-conversation path has its own suite
// (useChat.serverAuthority.test.ts).
import { reconcileWithRemote } from '../../utils/chatStore'
import type { Message } from '../../types/chat'

export async function syncWithServer(convId: string, local: Message[] | null) {
  const messages = (await reconcileWithRemote(convId, local)) ?? null
  return { messages, authoritative: false, received: new Set<string>() }
}
