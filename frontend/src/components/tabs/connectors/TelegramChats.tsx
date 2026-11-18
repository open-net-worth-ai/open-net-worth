import { useState } from 'react'
import { Button, ConfirmDialog, FormField, Stack, Text, TextField } from '../../ui'

// Who can chat with the bot, as ONE list: a row per chat — its Telegram name, the ID under it,
// Remove — and a box to add an ID by hand. It replaced a comma-separated text box with a names
// list under it (2026-11-16): every ID showed twice, and the two looked like separate things.
// The form still keeps the IDs as one comma string, so Save sends exactly what it always did.
//
// On a set-up bot, Add and Remove take effect at once, like Approve — nobody expects to tap Add
// and then still have to Save (owner, 2026-11-16). Remove asks first. Before the first save
// there is no bot yet: Add only edits the form, and such an ID goes without asking.

export function parseChatIds(raw: string): string[] {
  return [...new Set(raw.split(/[\s,]+/).filter(Boolean))]
}

const CHAT_ID = /^-?\d+$/

export function ChatList({
  title,
  help,
  ids,
  labels,
  checkedIds,
  savedIds,
  regainAccess,
  onChange,
  onRevoke,
  onAdd,
}: {
  title: string
  help?: string
  ids: string[]
  labels: Record<string, string>
  /** IDs Telegram has answered for: a missing name for one of these means it can't see that
   *  chat. An ID not in here is still being looked up, and shows just its number. */
  checkedIds: string[]
  /** What the server has now — only these need a confirm, and they are revoked at once. */
  savedIds: string[]
  /** How someone removed gets back in, for the confirm's body. */
  regainAccess: string
  onChange: (ids: string[]) => void
  onRevoke: (id: string) => Promise<boolean>
  /** Saves the new chat at once; absent (no bot yet) → Add only edits the form. */
  onAdd?: (id: string) => Promise<boolean>
}) {
  const [draft, setDraft] = useState('')
  const [bad, setBad] = useState(false)
  const [revoking, setRevoking] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const remove = (id: string) => {
    if (savedIds.includes(id)) setRevoking(id)
    else onChange(ids.filter((x) => x !== id))
  }

  const confirmRevoke = async () => {
    if (!revoking) return
    setBusy(true)
    const ok = await onRevoke(revoking)
    setBusy(false)
    if (ok) setRevoking(null)
  }
  const who = (id: string) => labels[id] || id

  const [adding, setAdding] = useState(false)
  const add = async () => {
    const id = draft.trim()
    if (!CHAT_ID.test(id)) {
      setBad(true)
      return
    }
    if (ids.includes(id)) {
      setDraft('')
      return
    }
    if (!onAdd) {
      onChange([...ids, id])
      setDraft('')
      return
    }
    setAdding(true)
    const ok = await onAdd(id)
    setAdding(false)
    if (ok) setDraft('')
  }

  return (
    <Stack gap="--space-3">
      {ids.length > 0 && (
        <ul className="tg-chat-list" aria-label={title}>
          {ids.map((id) => {
            const name = labels[id]
            return (
              <li key={id} className="tg-chat-list__row">
                <div className="tg-chat-list__who">
                  <Text size="base" weight={600} tone="ink">
                    {name || id}
                  </Text>
                  {name ? (
                    <Text size="sm" tone="muted" className="tg-chat-list__id">
                      {id}
                    </Text>
                  ) : (
                    checkedIds.includes(id) && (
                      <Text size="sm" tone="muted">
                        Not found — check the number
                      </Text>
                    )
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${name || id}`}
                  onClick={() => remove(id)}
                >
                  Remove
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      <FormField
        label="Add a chat ID"
        htmlFor="conn-tg-add-chat"
        help={help}
        error={bad ? 'A chat ID is a number, like 123456789. A group’s starts with -.' : undefined}
      >
        <div className="tg-add-row">
          <TextField
            id="conn-tg-add-chat"
            value={draft}
            placeholder="123456789"
            inputMode="numeric"
            invalid={bad}
            onChange={(e) => {
              setBad(false)
              setDraft(e.target.value)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                void add()
              }
            }}
          />
          <Button onClick={() => void add()} disabled={!draft.trim()} loading={adding}>
            Add
          </Button>
        </div>
      </FormField>
      <ConfirmDialog
        data={
          revoking && {
            title: `Remove ${who(revoking)}?`,
            body: `They won't be able to chat with Lucius or approve anything from Telegram. ${regainAccess}`,
            confirmLabel: 'Remove',
            danger: true,
            confirmDisabled: busy,
          }
        }
        onConfirm={() => void confirmRevoke()}
        onCancel={() => !busy && setRevoking(null)}
      />
    </Stack>
  )
}
