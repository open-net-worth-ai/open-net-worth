import { useState, type FormEvent } from 'react'
import { tradingApi } from '../../../api/trading'
import type { TradingWorkspace, WorkspaceOverview } from '../../../types/trading'
import {
  Button,
  Cluster,
  ErrorState,
  FormField,
  Modal,
  NumberField,
  Panel,
  Stack,
  Text,
  TextArea,
} from '../../ui'
import { CodeBlock } from '../../chat/CodeBlock'
import { errorText, tradingMoney, tradingTime } from './presentation'

export function TradingMandate({
  workspace,
  overview,
}: {
  workspace: TradingWorkspace
  overview?: WorkspaceOverview
}) {
  const [dialog, setDialog] = useState<'original' | 'history' | 'amend' | null>(null)
  const [instructions, setInstructions] = useState(workspace.instructions)
  const [maxOrder, setMaxOrder] = useState(String(workspace.mandate.max_order_notional ?? ''))
  const [reason, setReason] = useState('')
  const [review, setReview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const original = overview?.original_mandate
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!review) {
      setReview(true)
      return
    }
    setBusy(true)
    setError('')
    try {
      await tradingApi.amend(workspace.id, {
        instructions,
        mandate: { ...workspace.mandate, max_order_notional: maxOrder || null },
        reason,
        expected_revision: workspace.revision,
      })
      setDialog(null)
      setReview(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  function openAmend() {
    setInstructions(workspace.instructions)
    setMaxOrder(String(workspace.mandate.max_order_notional ?? ''))
    setReason('')
    setReview(false)
    setError('')
    setDialog('amend')
  }
  return (
    <>
      <Panel
        title="Mandate"
        className="trading-mandate-panel"
        actions={
          <Cluster>
            <Button size="sm" onClick={() => setDialog('original')}>
              Original mandate
            </Button>
            <Button size="sm" onClick={() => setDialog('history')}>
              {overview?.amendments.length ?? 0} amendments
            </Button>
            <Button size="sm" onClick={openAmend}>
              Amend mandate
            </Button>
          </Cluster>
        }
      >
        <Stack gap="--space-3">
          <Text className="trading-mandate-text">
            {original?.instructions ?? workspace.instructions}
          </Text>
          <Text size="sm" tone="muted">
            {original
              ? 'Original instruction preserved verbatim'
              : overview
                ? 'Original instruction was not recorded for this earlier workspace'
                : 'Original mandate is being loaded'}{' '}
            · Effective revision {workspace.revision} · {workspace.agent_label}
          </Text>
          {original && workspace.instructions !== original.instructions && (
            <Text as="p">Current instruction: {workspace.instructions}</Text>
          )}
          <Text size="sm">
            Maximum order{' '}
            {workspace.mandate.max_order_notional == null
              ? 'Available authorized capital'
              : String(workspace.mandate.max_order_notional)}{' '}
            · Agent budget {tradingMoney(workspace.mandate.max_model_cost_usd, 'USD')}
          </Text>
        </Stack>
      </Panel>
      {dialog && (
        <Modal
          open
          onClose={() => setDialog(null)}
          title={
            dialog === 'original'
              ? 'Original mandate'
              : dialog === 'history'
                ? 'Mandate amendments'
                : review
                  ? 'Review mandate amendment'
                  : 'Amend mandate'
          }
          size="lg"
        >
          {dialog === 'original' &&
            (original ? (
              <Stack gap="--space-4">
                <Text className="trading-mandate-text">{original.instructions}</Text>
                <Text size="sm" tone="muted">
                  Recorded {tradingTime(original.created_at)} · Revision {original.revision}
                </Text>
                <CodeBlock className="trading-code">{JSON.stringify(original, null, 2)}</CodeBlock>
              </Stack>
            ) : (
              <Text tone="muted">
                Original mandate was not recorded for this earlier workspace. Current instructions
                are shown on the workspace page.
              </Text>
            ))}
          {dialog === 'history' && (
            <Stack gap="--space-4">
              {overview?.amendments.length ? (
                overview.amendments.map((item) => (
                  <details key={item.id}>
                    <summary>
                      Revision {item.revision} · {item.reason} · {tradingTime(item.created_at)}
                    </summary>
                    <CodeBlock className="trading-code">{JSON.stringify(item, null, 2)}</CodeBlock>
                  </details>
                ))
              ) : (
                <Text tone="muted">
                  No recorded amendments. Earlier order authority is retained when a mandate
                  changes.
                </Text>
              )}
            </Stack>
          )}
          {dialog === 'amend' && (
            <form onSubmit={save}>
              <Stack gap="--space-4">
                <Text tone="muted">
                  The original instruction and earlier execution records remain unchanged. The
                  amendment applies to subsequent work after it takes effect.
                </Text>
                <FormField label="Effective instruction">
                  <TextArea
                    aria-label="Effective instruction"
                    required
                    maxLength={12000}
                    readOnly={review}
                    value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                    rows={5}
                  />
                </FormField>
                <FormField label="Maximum order value">
                  <NumberField
                    aria-label="Maximum order value"
                    min={0.01}
                    step="any"
                    readOnly={review}
                    value={maxOrder}
                    onChange={(e) => setMaxOrder(e.target.value)}
                  />
                </FormField>
                <FormField label="Reason for amendment">
                  <TextArea
                    aria-label="Reason for amendment"
                    required
                    maxLength={4000}
                    readOnly={review}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </FormField>
                {error && <ErrorState message={error} />}
                <Cluster>
                  {review && (
                    <Button disabled={busy} onClick={() => setReview(false)}>
                      Edit amendment
                    </Button>
                  )}
                  <Button type="submit" variant="primary" disabled={busy}>
                    {busy ? 'Applying…' : review ? 'Apply amendment' : 'Review amendment'}
                  </Button>
                </Cluster>
              </Stack>
            </form>
          )}
        </Modal>
      )}
    </>
  )
}
