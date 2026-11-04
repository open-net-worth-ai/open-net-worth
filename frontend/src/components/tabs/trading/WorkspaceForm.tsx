import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { tradingApi } from '../../../api/trading'
import type {
  AgentKind,
  DelegationCreate,
  DelegationReview,
  TradingWorkspace,
} from '../../../types/trading'
import {
  Button,
  CheckboxGroup,
  Cluster,
  ErrorState,
  FormField,
  Modal,
  NumberField,
  Panel,
  Select,
  Stack,
  Text,
  TextArea,
  TextField,
} from '../../ui'
import { errorText } from './presentation'
import { WorkspaceReview } from './WorkspaceReview'
import { InstrumentFields } from './InstrumentFields'
import { DelegationEnvironment } from './DelegationEnvironment'
import { defaultDelegation } from './delegationDraft'

export function WorkspaceForm({
  draft,
  workspace,
  onClose,
  onSaved,
}: {
  draft?: DelegationCreate
  workspace?: TradingWorkspace
  onClose: () => void
  onSaved: (workspace: TradingWorkspace, runId?: string) => void
}) {
  const [value, setValue] = useState<DelegationCreate>(
    () =>
      draft ?? {
        ...defaultDelegation(),
        ...(workspace
          ? {
              workspace: {
                name: workspace.name,
                instructions: workspace.instructions,
                agent_kind: workspace.agent_kind,
                agent_label: workspace.agent_label,
                mandate: workspace.mandate,
                strategy_source: workspace.strategy_source,
              },
            }
          : {}),
      },
  )
  const [review, setReview] = useState<DelegationReview | null>(null)
  const [authorized, setAuthorized] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const definition = value.workspace
  const mandate = definition.mandate
  function update(patch: Partial<DelegationCreate['workspace']>) {
    if (!workspace)
      setValue((current) => ({ ...current, workspace: { ...current.workspace, ...patch } }))
  }
  function limit(patch: Partial<typeof mandate>) {
    update({ mandate: { ...mandate, ...patch } })
  }
  useLayoutEffect(() => {
    const active = document.activeElement
    if (
      active === document.body ||
      (active instanceof HTMLButtonElement && active.disabled && formRef.current?.contains(active))
    )
      formRef.current?.focus({ preventScroll: true })
  }, [busy])
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (!review) {
        setReview(await tradingApi.reviewDelegation(value))
        setAuthorized(false)
      } else {
        const payload = {
          workspace: review.workspace,
          environment: {
            ...review.environment,
            live_authorized: review.environment.mode === 'live' && authorized,
          },
          start: true,
        }
        const result = workspace
          ? await tradingApi.activateWorkspace(workspace.id, payload, workspace.revision)
          : await tradingApi.createDelegation(payload)
        onSaved(result.workspace, result.run.id)
      }
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  async function authorizeLive(checked: boolean) {
    setAuthorized(checked)
    if (!review) return
    setBusy(true)
    setError('')
    try {
      setReview(
        await tradingApi.reviewDelegation({
          workspace: review.workspace,
          environment: { ...review.environment, live_authorized: checked },
          start: true,
        }),
      )
    } catch (err) {
      setError(errorText(err))
      setAuthorized(false)
    } finally {
      setBusy(false)
    }
  }
  const isLive = (review?.environment ?? value.environment).mode === 'live'
  return (
    <Modal
      open
      onClose={onClose}
      title={review ? 'Review trading mandate' : 'Create trading workspace'}
      size="lg"
    >
      <form ref={formRef} tabIndex={-1} onSubmit={submit}>
        <Stack gap="--space-4">
          {review ? (
            <WorkspaceReview
              review={review}
              authorized={authorized}
              busy={busy}
              onAuthorize={authorizeLive}
            />
          ) : (
            <>
              <Text tone="muted">
                Define the delegation once. The workspace keeps its mandate, history and working
                state as the agent researches, trades or waits.
              </Text>
              {workspace && (
                <Text size="sm" tone="muted">
                  This existing mandate is fixed for activation. Use Amend mandate on the workspace
                  to review changes separately.
                </Text>
              )}
              <fieldset className="trading-definition-fields" disabled={!!workspace}>
                <FormField label="Name">
                  <TextField
                    aria-label="Name"
                    required
                    maxLength={120}
                    value={definition.name}
                    onChange={(e) => update({ name: e.target.value })}
                  />
                </FormField>
                <FormField label="Original mandate">
                  <TextArea
                    aria-label="Original mandate"
                    required
                    rows={4}
                    maxLength={12000}
                    value={definition.instructions}
                    onChange={(e) => update({ instructions: e.target.value })}
                    placeholder="Manage this allocation for one month. Research opportunities, test strategies and trade within the agreed limits."
                  />
                </FormField>
              </fieldset>
              <Panel title="Funds and environment">
                <DelegationEnvironment
                  value={value.environment}
                  onChange={(environment) => setValue((current) => ({ ...current, environment }))}
                />
              </Panel>
              <fieldset className="trading-definition-fields" disabled={!!workspace}>
                <Panel title="Delegation and limits">
                  <Stack gap="--space-4">
                    <FormField label="Executor connection">
                      <Select
                        aria-label="Executor connection"
                        value={definition.agent_kind}
                        onChange={(e) => {
                          const kind = e.target.value as AgentKind
                          update({
                            agent_kind: kind,
                            agent_label:
                              kind === 'lucius'
                                ? 'Lucius'
                                : kind === 'script'
                                  ? 'Script executor'
                                  : 'External executor',
                          })
                        }}
                        options={[
                          { value: 'lucius', label: 'Lucius (managed)' },
                          { value: 'external', label: 'External executor (agent, CLI or team)' },
                          { value: 'script', label: 'Script executor' },
                        ]}
                      />
                    </FormField>
                    <FormField label="Executor label">
                      <TextField
                        aria-label="Executor label"
                        required
                        maxLength={120}
                        value={definition.agent_label}
                        onChange={(e) => update({ agent_label: e.target.value })}
                      />
                    </FormField>
                    {definition.agent_kind === 'script' && (
                      <FormField
                        label="Initial strategy source"
                        info="Versioned Python source runs in an isolated container. The executor can continue on scheduled wakes."
                      >
                        <TextArea
                          aria-label="Initial strategy source"
                          mono
                          rows={10}
                          required
                          maxLength={64000}
                          value={definition.strategy_source ?? ''}
                          onChange={(e) => update({ strategy_source: e.target.value })}
                        />
                      </FormField>
                    )}
                    <Text size="sm" tone="muted">
                      One delegated authority may be a single agent or a team. Available controls
                      and visibility depend on its integration. External executors connect through
                      scoped access after creation.
                    </Text>
                    <CheckboxGroup
                      ariaLabel="Permitted markets"
                      value={mandate.allowed_asset_classes}
                      onChange={(markets) =>
                        limit({ allowed_asset_classes: markets as ('stock' | 'crypto')[] })
                      }
                      options={[
                        { value: 'stock', label: 'Stocks' },
                        { value: 'crypto', label: 'Crypto' },
                      ]}
                    />
                    <FormField label="Market data">
                      <Select
                        aria-label="Market data"
                        value={mandate.market_data_source ?? 'supplied'}
                        onChange={(e) =>
                          limit({ market_data_source: e.target.value as 'supplied' | 'yahoo' })
                        }
                        options={[
                          { value: 'supplied', label: 'Supplied observations' },
                          { value: 'yahoo', label: 'Public market data (Yahoo)' },
                        ]}
                      />
                    </FormField>
                    <InstrumentFields
                      instruments={mandate.instruments ?? []}
                      onChange={(instruments) => limit({ instruments })}
                    />
                    <FormField
                      label="Maximum order value"
                      info="In the reporting currency; blank uses available authorized capital."
                    >
                      <NumberField
                        aria-label="Maximum order value"
                        min={0.01}
                        step="any"
                        value={String(mandate.max_order_notional ?? '')}
                        onChange={(e) => limit({ max_order_notional: e.target.value || null })}
                      />
                    </FormField>
                    <FormField label="Agent usage budget (USD)">
                      <NumberField
                        aria-label="Agent usage budget (USD)"
                        required
                        min={0}
                        step="any"
                        value={String(mandate.max_model_cost_usd)}
                        onChange={(e) => limit({ max_model_cost_usd: e.target.value })}
                      />
                    </FormField>
                    <details>
                      <summary>Execution assumptions</summary>
                      <Stack gap="--space-4">
                        <FormField label="Maximum quote age (seconds)">
                          <NumberField
                            aria-label="Maximum quote age (seconds)"
                            required
                            min={1}
                            max={86400}
                            value={String(mandate.max_quote_age_seconds)}
                            onChange={(e) =>
                              limit({ max_quote_age_seconds: Number(e.target.value) })
                            }
                          />
                        </FormField>
                        <FormField label="Simulation fee (basis points)">
                          <NumberField
                            aria-label="Simulation fee (basis points)"
                            required
                            min={0}
                            max={1000}
                            step="any"
                            value={String(mandate.fee_bps)}
                            onChange={(e) => limit({ fee_bps: e.target.value })}
                          />
                        </FormField>
                        <FormField label="Simulation slippage (basis points)">
                          <NumberField
                            aria-label="Simulation slippage (basis points)"
                            required
                            min={0}
                            max={1000}
                            step="any"
                            value={String(mandate.slippage_bps)}
                            onChange={(e) => limit({ slippage_bps: e.target.value })}
                          />
                        </FormField>
                        <FormField label="Maximum volume participation">
                          <NumberField
                            aria-label="Maximum volume participation"
                            required
                            min={0.0001}
                            max={1}
                            step="any"
                            value={String(mandate.max_participation)}
                            onChange={(e) => limit({ max_participation: e.target.value })}
                          />
                        </FormField>
                      </Stack>
                    </details>
                  </Stack>
                </Panel>
              </fieldset>
            </>
          )}
          {error && <ErrorState message={error} />}
          <Cluster justify="between">
            {review && (
              <Button
                disabled={busy}
                onClick={() => {
                  setValue({
                    workspace: review.workspace,
                    environment: review.environment,
                    start: true,
                  })
                  setReview(null)
                }}
              >
                Edit draft
              </Button>
            )}
            <Button
              type="submit"
              variant="primary"
              disabled={
                busy ||
                !mandate.allowed_asset_classes.length ||
                (!!review && (!review.ready || (isLive && !authorized)))
              }
            >
              {busy
                ? 'Checking…'
                : review
                  ? isLive
                    ? 'Create and start live trading'
                    : 'Create and start workspace'
                  : 'Review workspace'}
            </Button>
          </Cluster>
        </Stack>
      </form>
    </Modal>
  )
}
