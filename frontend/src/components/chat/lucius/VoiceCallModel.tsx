import { authFetch } from '../../../api/client'
import { useModels } from '../../../hooks/useModels'
import { useLuciusChat } from '../../../state/luciusChat'
import { ModelPicker } from '../../ui'
import { useVoiceCall } from './voiceCall'

// The model pill on the full call screen IS the conversation's model: one model per conversation,
// typed or spoken (owner, 2026-11-06). Saved at once, because on a call no typed message will carry
// it to the server, and his next answer reads it from there. The call goes on throughout.
export function VoiceCallModel({ disabled }: { disabled: boolean }) {
  const { models, defaultModel } = useModels(true)
  const { activeId, conversationModel, conversations, setConversationModel } = useLuciusChat()
  const call = useVoiceCall()
  const id = call.sessionId || activeId
  const model =
    id === activeId ? conversationModel : (conversations.find((c) => c.id === id)?.model ?? '')

  const change = async (next: string) => {
    setConversationModel(id, next)
    try {
      const r = await authFetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: next }),
      })
      if (!r.ok) throw new Error(`session ${r.status}`)
    } catch {
      setConversationModel(id, model) // back to what is really saved
    }
  }

  return (
    <div className="lx-callstage-ctl lx-callstage-ctl--model">
      <ModelPicker
        className="lx-callstage-model"
        size="sm"
        noSearchFocus
        label="Chat model"
        models={models}
        value={model}
        defaultLabel="Default"
        defaultModelId={defaultModel}
        onChange={(next) => void change(next)}
        disabled={disabled}
      />
    </div>
  )
}
