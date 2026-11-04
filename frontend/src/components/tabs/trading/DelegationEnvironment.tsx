import type { RunCreate, Allocation } from '../../../types/trading'
import {
  Button,
  Checkbox,
  Cluster,
  FormField,
  NumberField,
  Panel,
  Select,
  Stack,
  Text,
  TextArea,
  TextField,
} from '../../ui'

const localTime = (value: string) => {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

export function DelegationEnvironment({
  value,
  onChange,
}: {
  value: RunCreate
  onChange: (value: RunCreate) => void
}) {
  const patch = (data: Partial<RunCreate>) => onChange({ ...value, ...data })
  const account = (index: number, data: Partial<Allocation>) =>
    patch({
      accounts: value.accounts.map((item, i) => (i === index ? { ...item, ...data } : item)),
    })
  return (
    <Stack gap="--space-4">
      <FormField label="Environment">
        <Select
          aria-label="Environment"
          value={value.mode}
          onChange={(e) =>
            patch({ mode: e.target.value as RunCreate['mode'], live_authorized: false })
          }
          options={[
            { value: 'backtest', label: 'Backtest' },
            { value: 'paper', label: 'Paper' },
            { value: 'live', label: 'Live' },
          ]}
        />
      </FormField>
      <Cluster>
        <FormField label="Start">
          <TextField
            aria-label="Start"
            type="datetime-local"
            required
            value={localTime(value.start_at)}
            onChange={(e) => {
              if (e.target.value) patch({ start_at: new Date(e.target.value).toISOString() })
            }}
          />
        </FormField>
        {value.end_at && (
          <FormField label="End">
            <TextField
              aria-label="End"
              type="datetime-local"
              required
              value={localTime(value.end_at)}
              onChange={(e) => {
                if (e.target.value) patch({ end_at: new Date(e.target.value).toISOString() })
              }}
            />
          </FormField>
        )}
      </Cluster>
      {value.mode !== 'backtest' && (
        <Checkbox
          label="Continue until stopped"
          checked={value.end_at === null}
          onChange={(e) =>
            patch({
              end_at: e.target.checked ? null : new Date(Date.now() + 30 * 86400000).toISOString(),
            })
          }
        />
      )}
      {value.mode === 'backtest' && !value.end_at && (
        <Button
          onClick={() => patch({ end_at: new Date(Date.now() + 30 * 86400000).toISOString() })}
        >
          Set historical end date
        </Button>
      )}
      {value.accounts.map((item, index) => (
        <Panel
          key={index}
          title={`Allocation ${index + 1}`}
          actions={
            value.accounts.length > 1 && (
              <Button
                variant="ghost"
                onClick={() => patch({ accounts: value.accounts.filter((_, i) => i !== index) })}
              >
                Remove allocation {index + 1}
              </Button>
            )
          }
        >
          <Stack gap="--space-4">
            <FormField label="Account label">
              <TextField
                aria-label={`Account label ${index + 1}`}
                required
                value={item.name}
                onChange={(e) => account(index, { name: e.target.value })}
              />
            </FormField>
            <Cluster>
              <FormField label="Capital">
                <NumberField
                  aria-label={`Allocated capital ${index + 1}`}
                  required
                  min={0.01}
                  step="any"
                  value={String(item.capital)}
                  onChange={(e) => account(index, { capital: e.target.value })}
                />
              </FormField>
              <FormField label="Currency">
                <TextField
                  aria-label={`Currency ${index + 1}`}
                  required
                  pattern="[A-Z]{3,8}"
                  value={item.currency}
                  onChange={(e) => account(index, { currency: e.target.value.toUpperCase() })}
                />
              </FormField>
              <FormField label="Venue">
                <TextField
                  aria-label={`Venue ${index + 1}`}
                  required
                  value={item.venue}
                  onChange={(e) => account(index, { venue: e.target.value })}
                />
              </FormField>
            </Cluster>
            {value.mode === 'live' && (
              <>
                <FormField label="Broker connector ID">
                  <TextField
                    aria-label={`Broker connector ID ${index + 1}`}
                    required
                    value={item.connector_id ?? ''}
                    onChange={(e) => account(index, { connector_id: e.target.value })}
                  />
                </FormField>
                <FormField label="Dedicated broker account ID">
                  <TextField
                    aria-label={`Dedicated broker account ID ${index + 1}`}
                    required
                    value={item.broker_account_id ?? ''}
                    onChange={(e) => account(index, { broker_account_id: e.target.value })}
                  />
                </FormField>
                <FormField label="Book account ID">
                  <TextField
                    aria-label={`Book account ID ${index + 1}`}
                    value={item.book_account_id ?? ''}
                    onChange={(e) => account(index, { book_account_id: e.target.value })}
                  />
                </FormField>
              </>
            )}
          </Stack>
        </Panel>
      ))}
      <Button
        onClick={() =>
          patch({
            accounts: [
              ...value.accounts,
              {
                name: 'Trading allocation',
                currency: value.base_currency,
                capital: '1000',
                venue: 'simulation',
              },
            ],
          })
        }
        disabled={value.accounts.length >= 20}
      >
        Add allocation
      </Button>
      <details>
        <summary>Data and currency settings</summary>
        <Stack gap="--space-4">
          <FormField label="Reporting currency">
            <TextField
              aria-label="Reporting currency"
              required
              pattern="[A-Z]{3,8}"
              value={value.base_currency}
              onChange={(e) => patch({ base_currency: e.target.value.toUpperCase() })}
            />
          </FormField>
          <FormField label="FX rates (JSON)">
            <TextArea
              aria-label="FX rates (JSON)"
              mono
              defaultValue={JSON.stringify(value.fx_rates)}
              onBlur={(e) => {
                try {
                  patch({ fx_rates: JSON.parse(e.target.value) })
                  e.target.setCustomValidity('')
                } catch {
                  e.target.setCustomValidity('Enter valid JSON currency-to-base rates.')
                }
              }}
            />
          </FormField>
          <Text size="sm" tone="muted">
            Historical observations must be supplied with explicit timestamps and availability.
            Public market data is configured in the mandate.
          </Text>
          <FormField label="Observation dataset (JSON)">
            <TextArea
              aria-label="Observation dataset (JSON)"
              mono
              rows={5}
              defaultValue={JSON.stringify(value.observations)}
              onBlur={(e) => {
                try {
                  const parsed = JSON.parse(e.target.value)
                  if (!Array.isArray(parsed)) throw new Error()
                  patch({ observations: parsed })
                  e.target.setCustomValidity('')
                } catch {
                  e.target.setCustomValidity('Enter a JSON array of market observations.')
                }
              }}
            />
          </FormField>
          <FormField label="Dataset version">
            <TextField
              aria-label="Dataset version"
              required
              value={value.data_version}
              onChange={(e) => patch({ data_version: e.target.value })}
            />
          </FormField>
        </Stack>
      </details>
      {value.mode === 'live' && (
        <Text tone="warn">
          Live trading uses real funds. Connection, account isolation, capabilities and permissions
          must pass before activation. You will explicitly authorize the reviewed allocation.
        </Text>
      )}
    </Stack>
  )
}
