import { useEffect, useState } from 'react'
import {
  Button,
  FormField,
  Modal,
  NumberField,
  SegmentedControl,
  Select,
  Tag,
  TextArea,
} from '../../ui'
import { fCurrency } from '../../../utils/format'
import {
  UNIVERSE,
  assetOf,
  isOpen,
  placeOrder,
  previewOrder,
  type OrderType,
  type Portfolio,
  type Side,
} from './paperStore'

const money = (n: number) => fCurrency(n, 'USD')

/** The paper order ticket: the kit's Modal (a bottom sheet on a phone) with a short form. */
export function OrderTicket({
  p,
  open,
  initial,
  onClose,
  onPlaced,
}: {
  p: Portfolio
  open: boolean
  initial?: { symbol?: string; side?: Side }
  onClose: () => void
  onPlaced: (message: string) => void
}) {
  const [side, setSide] = useState<Side>('buy')
  const [symbol, setSymbol] = useState('NVDA')
  const [type, setType] = useState<OrderType>('market')
  const [qty, setQty] = useState('10')
  const [price, setPrice] = useState('')
  const [note, setNote] = useState('')
  useEffect(() => {
    if (!open) return
    setSide(initial?.side ?? 'buy')
    setSymbol(initial?.symbol ?? 'NVDA')
    setType('market')
    setQty('10')
    setPrice('')
    setNote('')
  }, [open, initial?.side, initial?.symbol])

  const a = assetOf(symbol)
  const draft = {
    side,
    symbol,
    qty: Number(qty),
    type,
    price: price ? Number(price) : undefined,
    note: note || undefined,
  }
  const pv = previewOrder(p, draft)
  const label = side === 'buy' ? 'Place paper buy' : 'Place paper sell'
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={
        <span className="paper-title">
          Paper order <Tag tone="paper">Paper</Tag>
        </span>
      }
      subtitle={p.name}
      footer={
        <Button
          variant="paper"
          fullWidth
          disabled={!!pv.error}
          onClick={() => {
            const o = placeOrder(p.id, draft)
            const what = `${o.qty} ${a.symbol === 'XAU-G' ? 'g gold' : a.symbol}`
            onPlaced(
              o.status === 'filled'
                ? `Paper ${side} filled: ${what} at ${o.fillPrice?.toFixed(2)}`
                : `Paper ${side} placed: ${what}`,
            )
          }}
        >
          {label}
        </Button>
      }
    >
      <div className="paper-form">
        <SegmentedControl
          ariaLabel="Side"
          tone="primary"
          options={[
            { value: 'buy', label: 'Buy' },
            { value: 'sell', label: 'Sell' },
          ]}
          value={side}
          onChange={setSide}
        />
        <FormField
          label="Asset"
          htmlFor="pt-asset"
          help={`${a.price.toLocaleString()}${a.unit ? ` / ${a.unit}` : ''} · ${isOpen(a) ? 'Open' : 'Closed'}`}
        >
          <Select
            id="pt-asset"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            options={UNIVERSE.map((u) => ({
              value: u.symbol,
              label: `${u.symbol === 'XAU-G' ? 'Gold' : u.symbol} · ${u.name}`,
            }))}
          />
        </FormField>
        <FormField label="Order type">
          <SegmentedControl
            ariaLabel="Order type"
            options={[
              { value: 'market', label: 'Market' },
              { value: 'limit', label: 'Limit' },
              { value: 'stop', label: 'Stop' },
            ]}
            value={type}
            onChange={setType}
          />
        </FormField>
        <div className="paper-two">
          <FormField
            label={a.unit ? `Quantity (${a.unit})` : a.kind === 'crypto' ? 'Coins' : 'Shares'}
            htmlFor="pt-qty"
          >
            <NumberField
              id="pt-qty"
              min={0}
              step="any"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </FormField>
          {type !== 'market' && (
            <FormField label={type === 'limit' ? 'Limit price' : 'Stop price'} htmlFor="pt-price">
              <NumberField
                id="pt-price"
                min={0}
                step="any"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
              />
            </FormField>
          )}
        </div>
        <FormField label="Why (optional)" htmlFor="pt-note">
          <TextArea id="pt-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        </FormField>
        <div className="paper-preview">
          <span className="k">{type === 'limit' ? 'Limit' : 'Expected fill'}</span>
          <span>{pv.price.toFixed(2)}</span>
          <span className="k tot">Total</span>
          <span className="tot">{money(pv.total)}</span>
          <span className="k">Paper cash after</span>
          <span>{money(pv.cashAfter)}</span>
        </div>
        <div className={`paper-next${pv.error ? ' bad' : ''}`}>{pv.error ?? pv.next}</div>
      </div>
    </Modal>
  )
}
