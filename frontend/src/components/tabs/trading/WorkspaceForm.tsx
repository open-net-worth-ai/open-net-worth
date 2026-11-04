import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { tradingApi } from '../../../api/trading'
import type { AgentKind, TradingWorkspace, TradingInstrument } from '../../../types/trading'
import {
  Button,
  Modal,
  FormField,
  TextField,
  TextArea,
  Select,
  NumberField,
  CheckboxGroup,
  Stack,
  ErrorState,
  Text,
} from '../../ui'
import { errorText } from './presentation'
import { InstrumentFields } from './InstrumentFields'

export function WorkspaceForm({
  workspace,
  onClose,
  onSaved,
}: {
  workspace?: TradingWorkspace
  onClose: () => void
  onSaved: (workspace: TradingWorkspace) => void
}) {
  const [name, setName] = useState(workspace?.name ?? '')
  const [instructions, setInstructions] = useState(workspace?.instructions ?? '')
  const [kind, setKind] = useState<AgentKind>(workspace?.agent_kind ?? 'lucius')
  const [label, setLabel] = useState(workspace?.agent_label ?? 'Lucius')
  const [markets, setMarkets] = useState<string[]>(
    workspace?.mandate.allowed_asset_classes ?? ['stock', 'crypto'],
  )
  const [maxOrder, setMaxOrder] = useState(String(workspace?.mandate.max_order_notional ?? ''))
  const [modelBudget, setModelBudget] = useState(
    String(workspace?.mandate.max_model_cost_usd ?? '10'),
  )
  const [fee, setFee] = useState(String(workspace?.mandate.fee_bps ?? '0'))
  const [slippage, setSlippage] = useState(String(workspace?.mandate.slippage_bps ?? '0'))
  const [dataSource, setDataSource] = useState<'supplied' | 'yahoo'>(
    workspace?.mandate.market_data_source ?? 'supplied',
  )
  const [instruments, setInstruments] = useState<TradingInstrument[]>(
    workspace?.mandate.instruments ?? [],
  )
  const [quoteAge, setQuoteAge] = useState(String(workspace?.mandate.max_quote_age_seconds ?? 300))
  const [participation, setParticipation] = useState(
    String(workspace?.mandate.max_participation ?? '1'),
  )
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  useLayoutEffect(() => {
    const active = document.activeElement
    if (
      active === document.body ||
      (active instanceof HTMLButtonElement && active.disabled && formRef.current?.contains(active))
    ) {
      formRef.current?.focus({ preventScroll: true })
    }
  }, [busy])
  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const data = {
        name,
        instructions,
        agent_kind: kind,
        agent_label: label,
        mandate: {
          allowed_asset_classes: markets as ('stock' | 'crypto')[],
          max_order_notional: maxOrder || null,
          max_model_cost_usd: modelBudget,
          fee_bps: fee,
          slippage_bps: slippage,
          max_participation: participation,
          max_quote_age_seconds: Number(quoteAge),
          market_data_source: dataSource,
          instruments,
        },
      }
      onSaved(
        workspace
          ? await tradingApi.updateWorkspace(workspace.id, data)
          : await tradingApi.createWorkspace(data),
      )
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={workspace ? 'Edit workspace definition' : 'New trading workspace'}
      size="lg"
    >
      <form ref={formRef} tabIndex={-1} onSubmit={save}>
        <Stack gap="--space-4">
          <FormField label="Name">
            <TextField
              aria-label="Name"
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </FormField>
          <FormField label="Instructions">
            <TextArea
              aria-label="Instructions"
              rows={4}
              maxLength={12000}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="Describe the goal and how the agent should manage its allocation."
            />
          </FormField>
          <FormField label="Agent">
            <Select
              aria-label="Agent"
              value={kind}
              onChange={(e) => {
                const value = e.target.value as AgentKind
                setKind(value)
                setLabel(
                  value === 'lucius'
                    ? 'Lucius'
                    : value === 'script'
                      ? 'Strategy script'
                      : 'External agent',
                )
              }}
              options={[
                { value: 'lucius', label: 'Lucius' },
                { value: 'external', label: 'External agent' },
                { value: 'script', label: 'Strategy script' },
              ]}
            />
          </FormField>
          <FormField label="Agent label">
            <TextField
              aria-label="Agent label"
              required
              maxLength={120}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </FormField>
          <CheckboxGroup
            ariaLabel="Permitted markets"
            value={markets}
            onChange={setMarkets}
            options={[
              { value: 'stock', label: 'Stocks' },
              { value: 'crypto', label: 'Crypto' },
            ]}
          />
          <FormField label="Paper market data">
            <Select
              aria-label="Paper market data"
              value={dataSource}
              onChange={(e) => setDataSource(e.target.value as 'supplied' | 'yahoo')}
              options={[
                { value: 'supplied', label: 'Supplied observations' },
                { value: 'yahoo', label: 'Public market quotes (Yahoo)' },
              ]}
            />
          </FormField>
          <InstrumentFields instruments={instruments} onChange={setInstruments} />
          {dataSource === 'yahoo' && !instruments.length && (
            <Text tone="warn">
              Add at least one permitted instrument to use public paper quotes.
            </Text>
          )}
          <FormField
            label="Maximum quote age (seconds)"
            info="Older quotes are refused for trading. Public quotes may be delayed or unavailable."
          >
            <NumberField
              aria-label="Maximum quote age (seconds)"
              required
              min={1}
              max={86400}
              step={1}
              value={quoteAge}
              onChange={(e) => setQuoteAge(e.target.value)}
            />
          </FormField>
          <FormField
            label="Maximum volume participation"
            info="Fraction of an observation's volume that a simulated order may fill; 1 means all available volume."
          >
            <NumberField
              aria-label="Maximum volume participation"
              required
              min={0.0001}
              max={1}
              step="any"
              value={participation}
              onChange={(e) => setParticipation(e.target.value)}
            />
          </FormField>
          <FormField
            label="Maximum order value"
            info="In the run's base currency. Leave blank to use available capital as the limit."
          >
            <NumberField
              aria-label="Maximum order value"
              min={0.01}
              step="any"
              value={maxOrder}
              onChange={(e) => setMaxOrder(e.target.value)}
            />
          </FormField>
          <FormField label="Agent usage budget (USD)">
            <NumberField
              aria-label="Agent usage budget (USD)"
              min={0}
              step="any"
              required
              value={modelBudget}
              onChange={(e) => setModelBudget(e.target.value)}
            />
          </FormField>
          <FormField label="Simulation fee (basis points)">
            <NumberField
              aria-label="Simulation fee (basis points)"
              min={0}
              max={1000}
              step="any"
              required
              value={fee}
              onChange={(e) => setFee(e.target.value)}
            />
          </FormField>
          <FormField label="Simulation slippage (basis points)">
            <NumberField
              aria-label="Simulation slippage (basis points)"
              min={0}
              max={1000}
              step="any"
              required
              value={slippage}
              onChange={(e) => setSlippage(e.target.value)}
            />
          </FormField>
          {workspace && (
            <p>Saved changes apply to new runs. Existing runs retain their frozen mandate.</p>
          )}
          {error && <ErrorState message={error} />}
          <Button
            type="submit"
            disabled={
              busy || markets.length === 0 || (dataSource === 'yahoo' && !instruments.length)
            }
          >
            {busy ? 'Saving…' : workspace ? 'Save definition' : 'Create workspace'}
          </Button>
        </Stack>
      </form>
    </Modal>
  )
}
