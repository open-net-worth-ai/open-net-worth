import { useEffect, useRef, useState } from 'react'
import { Paperclip } from 'lucide-react'
import { useLuciusChat } from '../../../state/luciusChat'
import { ATTACH_ACCEPT, toSendAttachments, useComposerAttachments } from './useComposerAttachments'
import { useVoiceCall } from './voiceCall'

// Attach on the full call screen: you stay on the call, the file goes straight to Lucius, and he
// answers on the call. Its own uploads, because opened from the pill there is no message box on the
// page (owner, 2026-11-06). It waits for him to finish a reply, as a send during one would be lost.
export function VoiceCallAttach({ disabled, busy }: { disabled: boolean; busy: boolean }) {
  const { activeId, conversationModel, effort, sendMessage, switchConversation } = useLuciusChat()
  const call = useVoiceCall()
  const att = useComposerAttachments()
  const input = useRef<HTMLInputElement>(null)
  const [refused, setRefused] = useState('')
  const count = att.attachments.length
  const onCallConversation = !call.sessionId || call.sessionId === activeId

  useEffect(() => {
    if (!count || att.uploading.length || busy || !onCallConversation) return
    const toSend = toSendAttachments(att.attachments)
    if (toSend.length) void sendMessage('', toSend, effort, false, conversationModel)
    setRefused(att.failed.map((a) => a.name).join(', '))
    att.clear()
    // sendMessage and the rest belong to this render, which is what must be sent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [count, att.uploading.length, busy, onCallConversation])

  const pick = () => {
    setRefused('')
    // The answer is read out on the call's conversation: the file belongs in that one.
    if (call.sessionId && call.sessionId !== activeId) switchConversation(call.sessionId)
    input.current?.click()
  }

  const note = refused ? `Couldn't send ${refused}.` : count ? 'Sending your file…' : ''
  return (
    <>
      <div className="lx-callstage-ctl lx-callstage-ctl--attach">
        <button
          type="button"
          className="lx-callstage-btn"
          aria-label="Attach a file"
          disabled={disabled}
          onClick={pick}
        >
          <Paperclip size={20} />
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept={ATTACH_ACCEPT}
          style={{ display: 'none' }}
          onChange={(e) => {
            att.addFiles(e.target.files)
            e.currentTarget.value = ''
          }}
        />
      </div>
      {note && (
        <p className="lx-callstage-attachnote" role="status">
          {note}
        </p>
      )}
    </>
  )
}
