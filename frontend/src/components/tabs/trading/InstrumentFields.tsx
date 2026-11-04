import type { TradingInstrument } from '../../../types/trading'
import { Button, Cluster, FormField, Panel, Select, Stack, TextField } from '../../ui'

export function InstrumentFields({
  instruments,
  onChange,
}: {
  instruments: TradingInstrument[]
  onChange: (value: TradingInstrument[]) => void
}) {
  function update(index: number, patch: Partial<TradingInstrument>) {
    onChange(instruments.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }
  return (
    <Stack gap="--space-4">
      {instruments.map((item, index) => (
        <Panel
          title={`Instrument ${index + 1}`}
          key={index}
          actions={
            <Button
              variant="ghost"
              onClick={() => onChange(instruments.filter((_, i) => i !== index))}
            >
              Remove instrument {index + 1}
            </Button>
          }
        >
          <Stack gap="--space-4">
            <FormField label="Symbol">
              <TextField
                aria-label={`Instrument ${index + 1} symbol`}
                required
                maxLength={32}
                value={item.symbol}
                onChange={(e) => update(index, { symbol: e.target.value.toUpperCase() })}
              />
            </FormField>
            <FormField label="Asset class">
              <Select
                aria-label={`Instrument ${index + 1} asset class`}
                value={item.asset_class}
                onChange={(e) =>
                  update(index, { asset_class: e.target.value as 'stock' | 'crypto' })
                }
                options={[
                  { value: 'stock', label: 'Stock' },
                  { value: 'crypto', label: 'Crypto' },
                ]}
              />
            </FormField>
            <FormField label="Venue">
              <TextField
                aria-label={`Instrument ${index + 1} venue`}
                required
                value={item.venue}
                onChange={(e) => update(index, { venue: e.target.value })}
              />
            </FormField>
            <FormField label="Quote currency">
              <TextField
                aria-label={`Instrument ${index + 1} currency`}
                required
                pattern="[A-Z]{3,8}"
                value={item.currency}
                onChange={(e) => update(index, { currency: e.target.value.toUpperCase() })}
              />
            </FormField>
          </Stack>
        </Panel>
      ))}
      <Cluster>
        <Button
          onClick={() =>
            onChange([
              ...instruments,
              { symbol: '', asset_class: 'stock', venue: 'simulation', currency: 'USD' },
            ])
          }
        >
          Add permitted instrument
        </Button>
      </Cluster>
    </Stack>
  )
}
