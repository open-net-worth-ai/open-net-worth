import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Cluster, Text, Button, FormField, Select } from '../../ui'

// Shared chat-pairing UI: the access-policy <Select> (WHO may reach Lucius) + a Pending-approvals
// list, used by every chat connector section (today: Telegram) so a future channel shares ONE
// pairing surface — no per-channel fork. This setting decides who gets through; what they can
// then do is the tool-permission question, decided separately.

// 'open'/'disabled' are still real backend states (see access_policy.py) — 'disabled' in
// particular is a hardcoded internal backstop Pause/Remove force regardless of what's stored —
// but neither is offered as a PICK here (owner review, 2026-08-22): 'open' is a foot-gun sitting
// next to safe defaults ("anyone on the internet gets your finances"), and 'disabled' is nearly
// redundant with pausing the whole connector. A legacy stored value of either still behaves as
// specified; a user just can't newly opt into them from this dropdown.
export type AccessPolicy = 'pairing' | 'allowlist' | 'open' | 'disabled'

const POLICY_OPTIONS = [
  { value: 'pairing', label: 'Pairing — new senders ask for approval' },
  { value: 'allowlist', label: 'Allowlist — only the IDs you list' },
]

// No help line under the picker: each option's label already says what it does.

export function AccessPolicyField({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string
  value: AccessPolicy
  onChange: (v: AccessPolicy) => void
}) {
  return (
    <FormField label="Who can chat" htmlFor={`${idPrefix}-policy`}>
      <Select
        id={`${idPrefix}-policy`}
        value={value}
        options={POLICY_OPTIONS}
        onChange={(e) => onChange(e.target.value as AccessPolicy)}
      />
    </FormField>
  )
}

export interface PendingRow {
  connector: string
  sender_id: string
  code: string
  requested_at: string
  sender_label?: string
}

const POLL_MS = 4000

// Pending-approvals list for ONE connector. Polls /api/chat-pairing every few seconds WHILE
// this modal is open, so a request that lands after you opened it still shows up without
// closing and reopening — plus an immediate refresh after Approve/Reject. `active` gates the
// poll entirely: only "pairing" mode ever produces pending rows, so polling under any other
// policy (e.g. Allowlist) would just be a wasted request every few seconds forever.
// `onApproved` lets the parent reload its config (the allowlist changed) and say who got in;
// `welcomed` is whether the bot told them so. `empty` is what to show while nobody is waiting.
export function PendingApprovals({
  connector,
  active,
  onApproved,
  empty = null,
}: {
  connector: string
  active: boolean
  onApproved?: (row: PendingRow, welcomed: boolean) => void
  empty?: ReactNode
}) {
  const [rows, setRows] = useState<PendingRow[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => {
    authFetch(`/api/chat-pairing?connector=${encodeURIComponent(connector)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d: { requests: PendingRow[] }) => setRows(d.requests || []))
      .catch(() => setRows([]))
  }, [connector])

  useEffect(() => {
    if (!active) {
      setRows([])
      return
    }
    load()
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [active, load])

  async function act(row: PendingRow, action: 'approve' | 'reject') {
    const senderId = row.sender_id
    setBusy(senderId)
    try {
      const r = await authFetch(`/api/chat-pairing/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector, sender_id: senderId }),
      })
      if (r.ok) {
        const d = (await r.json().catch(() => ({}))) as { welcomed?: boolean }
        load()
        if (action === 'approve') onApproved?.(row, Boolean(d.welcomed))
      }
    } catch {
      /* best-effort — leave the row, the operator can retry */
    } finally {
      setBusy(null)
    }
  }

  if (rows.length === 0) return <>{active ? empty : null}</>

  return (
    <div
      style={{
        background: 'var(--surface2)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--radius-md)',
        padding: 'var(--space-4)',
      }}
    >
      <Stack gap="--space-2">
        <Text size="sm" weight={600}>
          Pending approvals ({rows.length})
        </Text>
        <Text size="xs" tone="faint">
          Check the code matches the one the bot sent.
        </Text>
        <Stack gap="--space-2">
          {rows.map((row) => (
            <Cluster key={row.sender_id} gap="--space-3" align="center" justify="between">
              <Text size="sm">
                {row.sender_label || <code>{row.sender_id}</code>} • code <code>{row.code}</code>
              </Text>
              <Cluster gap="--space-2" align="center">
                <Button
                  size="sm"
                  variant="primary"
                  style={{ padding: '4px 10px' }}
                  loading={busy === row.sender_id}
                  onClick={() => act(row, 'approve')}
                >
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  style={{ padding: '4px 10px' }}
                  disabled={busy === row.sender_id}
                  onClick={() => act(row, 'reject')}
                >
                  Reject
                </Button>
              </Cluster>
            </Cluster>
          ))}
        </Stack>
      </Stack>
    </div>
  )
}
