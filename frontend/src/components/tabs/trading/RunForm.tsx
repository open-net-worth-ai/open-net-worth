import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { tradingApi } from '../../../api/trading'
import type {
  Allocation,
  TradingMode,
  TradingRun,
  TradingWorkspace,
  TradingObservation,
} from '../../../types/trading'
import {
  Button,
  Modal,
  FormField,
  TextField,
  TextArea,
  Select,
  NumberField,
  Checkbox,
  Stack,
  Panel,
  Cluster,
  ErrorState,
  Text,
} from '../../ui'
import { errorText } from './presentation'

const localInput = (date: Date) =>
  new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
const allocation = (): Allocation => ({
  name: 'Trading account',
  currency: 'USD',
  capital: '1000',
  venue: 'simulation',
})

export function RunForm({
  workspace,
  onClose,
  onSaved,
}: {
  workspace: TradingWorkspace
  onClose: () => void
  onSaved: (run: TradingRun) => void
}) {
  const [mode, setMode] = useState<TradingMode>('paper')
  const [start, setStart] = useState(localInput(new Date()))
  const [end, setEnd] = useState(localInput(new Date(Date.now() + 30 * 86400000)))
  const [accounts, setAccounts] = useState<Allocation[]>([allocation()])
  const [base, setBase] = useState('USD')
  const [rates, setRates] = useState('{"USD":"1"}')
  const [observations, setObservations] = useState('[]')
  const [version, setVersion] = useState('user-supplied')
  const [agentVersion, setAgentVersion] = useState('unspecified')
  const [authorized, setAuthorized] = useState(false)
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
  function update(index: number, patch: Partial<Allocation>) {
    setAccounts((items) => items.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }
  function sample() {
    const from = new Date(start).toISOString()
    const sample: TradingObservation[] = [
      {
        instrument: { symbol: 'ACME', venue: 'simulation', asset_class: 'stock', currency: 'USD' },
        timestamp: from,
        price: '100',
        volume: '1000',
      },
      {
        instrument: {
          symbol: 'SAMPLE',
          venue: 'simulation',
          asset_class: 'crypto',
          currency: 'USD',
        },
        timestamp: from,
        price: '10',
        volume: '1000',
      },
      {
        instrument: { symbol: 'ACME', venue: 'simulation', asset_class: 'stock', currency: 'USD' },
        timestamp: new Date(new Date(start).getTime() + 3600000).toISOString(),
        price: '102',
        volume: '1000',
      },
      {
        instrument: {
          symbol: 'SAMPLE',
          venue: 'simulation',
          asset_class: 'crypto',
          currency: 'USD',
        },
        timestamp: new Date(new Date(start).getTime() + 3600000).toISOString(),
        price: '11',
        volume: '1000',
      },
    ]
    setObservations(JSON.stringify(sample, null, 2))
    setVersion('synthetic-example-v1')
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const parsed = JSON.parse(observations)
      if (!Array.isArray(parsed)) throw new Error('Observations must be a JSON array.')
      onSaved(
        await tradingApi.createRun(workspace.id, {
          mode,
          start_at: new Date(start).toISOString(),
          end_at: new Date(end).toISOString(),
          accounts,
          base_currency: base,
          fx_rates: JSON.parse(rates),
          observations: parsed,
          data_version: version,
          agent_version: agentVersion,
          live_authorized: authorized,
        }),
      )
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal open onClose={onClose} title={`New run · ${workspace.name}`} size="lg">
      <form ref={formRef} tabIndex={-1} onSubmit={save}>
        <Stack gap="--space-4">
          <Text tone="muted">
            The environment and account bindings are fixed for this run. Review the mandate before
            starting.
          </Text>
          <FormField label="Environment">
            <Select
              aria-label="Environment"
              value={mode}
              onChange={(e) => {
                setMode(e.target.value as TradingMode)
                setAuthorized(false)
              }}
              options={[
                { value: 'backtest', label: 'Backtest' },
                { value: 'paper', label: 'Paper' },
                { value: 'live', label: 'Live' },
              ]}
            />
          </FormField>
          <FormField label="Start">
            <TextField
              aria-label="Start"
              type="datetime-local"
              required
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </FormField>
          <FormField label="End">
            <TextField
              aria-label="End"
              type="datetime-local"
              required
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </FormField>
          {accounts.map((account, index) => (
            <Panel
              key={index}
              title={`Account ${index + 1}`}
              actions={
                accounts.length > 1 && (
                  <Button
                    variant="ghost"
                    onClick={() => setAccounts((items) => items.filter((_, i) => i !== index))}
                  >
                    Remove account {index + 1}
                  </Button>
                )
              }
            >
              <Stack gap="--space-4">
                <FormField label="Account label">
                  <TextField
                    aria-label="Account label"
                    required
                    value={account.name}
                    onChange={(e) => update(index, { name: e.target.value })}
                  />
                </FormField>
                <FormField label="Currency">
                  <TextField
                    aria-label="Currency"
                    required
                    pattern="[A-Z]{3,8}"
                    value={account.currency}
                    onChange={(e) => update(index, { currency: e.target.value.toUpperCase() })}
                  />
                </FormField>
                <FormField label="Allocated capital">
                  <NumberField
                    aria-label="Allocated capital"
                    required
                    min={0.01}
                    step="any"
                    value={String(account.capital)}
                    onChange={(e) => update(index, { capital: e.target.value })}
                  />
                </FormField>
                <FormField label="Venue">
                  <TextField
                    aria-label="Venue"
                    required
                    value={account.venue}
                    onChange={(e) => update(index, { venue: e.target.value })}
                  />
                </FormField>
                {mode === 'live' && (
                  <>
                    <FormField label="Broker connector ID">
                      <TextField
                        aria-label="Broker connector ID"
                        required
                        value={account.connector_id ?? ''}
                        onChange={(e) => update(index, { connector_id: e.target.value })}
                      />
                    </FormField>
                    <FormField label="Dedicated broker account ID">
                      <TextField
                        aria-label="Dedicated broker account ID"
                        required
                        value={account.broker_account_id ?? ''}
                        onChange={(e) => update(index, { broker_account_id: e.target.value })}
                      />
                    </FormField>
                    <FormField label="Book account ID">
                      <TextField
                        aria-label="Book account ID"
                        value={account.book_account_id ?? ''}
                        onChange={(e) => update(index, { book_account_id: e.target.value })}
                      />
                    </FormField>
                  </>
                )}
              </Stack>
            </Panel>
          ))}
          <Button
            variant="secondary"
            onClick={() => setAccounts((items) => [...items, allocation()])}
            disabled={accounts.length >= 20}
          >
            Add account
          </Button>
          <FormField label="Reporting currency">
            <TextField
              aria-label="Reporting currency"
              required
              pattern="[A-Z]{3,8}"
              value={base}
              onChange={(e) => setBase(e.target.value.toUpperCase())}
            />
          </FormField>
          <FormField
            label="FX rates"
            info="JSON currency-to-base rates. The reporting currency must have rate 1."
          >
            <TextArea
              aria-label="FX rates"
              mono
              required
              value={rates}
              onChange={(e) => setRates(e.target.value)}
            />
          </FormField>
          {mode !== 'live' && (
            <>
              <Cluster justify="between">
                <Text weight={600}>Market observations</Text>
                <Button variant="secondary" onClick={sample}>
                  Load synthetic example
                </Button>
              </Cluster>
              <Text tone="muted" size="sm">
                Supply timestamped observations with instrument, price and optional available_at /
                volume. Historical observations become visible only at their availability time. The
                example is fabricated data, not a market backtest.
              </Text>
              <FormField label="Observation dataset (JSON)">
                <TextArea
                  aria-label="Observation dataset (JSON)"
                  mono
                  rows={7}
                  required
                  value={observations}
                  onChange={(e) => setObservations(e.target.value)}
                />
              </FormField>
            </>
          )}
          <FormField label="Dataset version">
            <TextField
              aria-label="Dataset version"
              required
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
          </FormField>
          <FormField label="Agent / strategy version">
            <TextField
              aria-label="Agent / strategy version"
              required
              value={agentVersion}
              onChange={(e) => setAgentVersion(e.target.value)}
            />
          </FormField>
          {mode === 'live' && (
            <>
              <Text tone="muted">
                Use a dedicated funded account or a fully mediated account. Broker capabilities and
                reconciliation must pass before the run can start. Set up connections in Connectors.
              </Text>
              <Checkbox
                checked={authorized}
                onChange={(e) => setAuthorized(e.target.checked)}
                label="I authorize this run to trade the specified accounts within its mandate."
              />
            </>
          )}
          {error && <ErrorState message={error} />}
          <Button type="submit" disabled={busy || (mode === 'live' && !authorized)}>
            {busy ? 'Creating…' : 'Create run'}
          </Button>
        </Stack>
      </form>
    </Modal>
  )
}
