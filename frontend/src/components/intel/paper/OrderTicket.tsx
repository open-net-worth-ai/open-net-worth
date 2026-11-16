import { useEffect, useState } from 'react'
import {
  Button,
  Checkbox,
  FormField,
  Modal,
  NumberField,
  SegmentedControl,
  Select,
  Tag,
  TextArea,
  TextField,
} from '../../ui'
import { fCurrency } from '../../../utils/format'
import {
  fetchPaperSearch,
  placePaperOrder,
  previewPaperOrder,
  type OrderDraft,
  type OrderSide,
  type OrderType,
  type PaperAsset,
  type PaperPreview,
} from '../../../api/paperTrading'

const PREVIEW_DEBOUNCE_MS = 300

/** The paper order ticket: the kit's Modal (a bottom sheet on a phone) with a short form. The
 *  symbol resolves when the field is left, as Add holding's does; the preview is the server's,
 *  the same code a place runs, so what it says is what happens. */
export function OrderTicket({
  portfolioId,
  portfolioName,
  homeCurrency,
  open,
  initial,
  onClose,
  onPlaced,
}: {
  portfolioId: string
  portfolioName: string
  homeCurrency: string
  open: boolean
  initial?: { asset?: PaperAsset; side?: OrderSide }
  onClose: () => void
  onPlaced: (toast: string) => void
}) {
  // A fresh form each time the ticket opens: the parent mounts it anew (a new `key`), so these
  // start from `initial` and nothing has to be reset by hand.
  const [side, setSide] = useState<OrderSide>(initial?.side ?? 'buy')
  const [symbol, setSymbol] = useState(initial?.asset?.symbol ?? '')
  const [asset, setAsset] = useState<PaperAsset | null>(initial?.asset ?? null)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const [type, setType] = useState<OrderType>('market')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [tif, setTif] = useState<'day' | 'gtc'>('day')
  const [note, setNote] = useState('')
  const [extended, setExtended] = useState(false)
  // The server's answer for one draft. A preview counts only while the form still says what it
  // was asked for, so an edit never shows the previous draft's figures.
  const [answer, setAnswer] = useState<{
    key: string
    preview: PaperPreview | null
    error: string | null
  } | null>(null)
  const [placeError, setPlaceError] = useState<{ key: string; message: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const isFund = asset?.kind === 'fund_nav'
  // Only stocks and ETFs have a pre- and after-market session.
  const canExtend = asset?.kind === 'listed'
  const orderType: OrderType = isFund ? 'nav' : type
  const quantity = Number(qty)
  const level = Number(price)
  const draft: OrderDraft | null =
    asset && quantity > 0 && (orderType === 'market' || orderType === 'nav' || level > 0)
      ? {
          side,
          quantity,
          order_type: orderType,
          ...(orderType === 'limit' ? { limit_price: level } : {}),
          ...(orderType === 'stop' ? { stop_price: level } : {}),
          tif,
          ...(canExtend && extended ? { extended_ok: true } : {}),
          ...(note.trim() ? { note: note.trim() } : {}),
        }
      : null
  const draftKey =
    draft && asset ? `${asset.asset_key ?? asset.symbol}|${JSON.stringify(draft)}` : ''
  const current = answer && answer.key === draftKey ? answer : null
  const preview = current?.preview ?? null
  const previewError =
    (placeError && placeError.key === draftKey ? placeError.message : null) ??
    current?.error ??
    null

  useEffect(() => {
    if (!open || !asset || !draft) return
    let stale = false
    const t = setTimeout(() => {
      previewPaperOrder(portfolioId, asset, draft)
        .then((p) => !stale && setAnswer({ key: draftKey, preview: p, error: null }))
        .catch(
          (e: Error) => !stale && setAnswer({ key: draftKey, preview: null, error: e.message }),
        )
    }, PREVIEW_DEBOUNCE_MS)
    return () => {
      stale = true
      clearTimeout(t)
    }
    // draftKey stands for asset + draft: the preview is asked again only when either changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, portfolioId, draftKey])

  async function resolveSymbol(raw: string) {
    const q = raw.trim()
    if (!q || (asset && asset.symbol.toUpperCase() === q.toUpperCase())) return
    setAsset(null)
    try {
      const results = await fetchPaperSearch(q)
      const exact = results.find((r) => r.symbol.toUpperCase() === q.toUpperCase())
      const hit = exact ?? results[0]
      if (!hit) setSymbolError(`Nothing found for ${q}`)
      else {
        setAsset(hit)
        setSymbol(hit.symbol)
        setSymbolError(null)
      }
    } catch {
      setSymbolError('The search did not answer. Try again.')
    }
  }

  async function place() {
    if (!asset || !draft) return
    setBusy(true)
    try {
      const out = await placePaperOrder(portfolioId, asset, draft)
      onPlaced(out.toast)
    } catch (e) {
      setPlaceError({ key: draftKey, message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const rejected = preview?.decision === 'reject'
  const money = (n: number) => fCurrency(n, preview?.estimate?.currency ?? homeCurrency)
  const unitLabel = asset?.unit
    ? `Quantity (${asset.unit})`
    : asset?.kind === 'crypto'
      ? 'Coins'
      : asset?.kind === 'fund_nav'
        ? 'Units'
        : 'Shares'
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={
        <span className="paper-title">
          Paper order <Tag>Paper</Tag>
        </span>
      }
      subtitle={portfolioName}
      footer={
        <Button
          variant="primary"
          fullWidth
          loading={busy}
          disabled={!draft || !preview || rejected}
          onClick={() => void place()}
        >
          {side === 'buy' ? 'Place paper buy' : 'Place paper sell'}
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
          label="Symbol"
          htmlFor="pt-symbol"
          error={symbolError}
          help={asset ? `${asset.name} · ${asset.kind_label}` : undefined}
        >
          <TextField
            id="pt-symbol"
            placeholder="AAPL, BTC, VWCE.DE, gold"
            value={symbol}
            autoComplete="off"
            invalid={!!symbolError}
            onChange={(e) => {
              setSymbol(e.target.value.toUpperCase())
              setSymbolError(null)
            }}
            onBlur={(e) => void resolveSymbol(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void resolveSymbol(symbol)}
          />
        </FormField>
        {!isFund && (
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
        )}
        <div className="paper-two">
          <FormField label={unitLabel} htmlFor="pt-qty">
            <NumberField
              id="pt-qty"
              min={0}
              step="any"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </FormField>
          {(orderType === 'limit' || orderType === 'stop') && (
            <FormField
              label={orderType === 'limit' ? 'Limit price' : 'Stop price'}
              htmlFor="pt-price"
            >
              <NumberField
                id="pt-price"
                min={0}
                step="any"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
              />
            </FormField>
          )}
          {orderType !== 'market' && orderType !== 'nav' && (
            <FormField label="Good for" htmlFor="pt-tif">
              <Select
                id="pt-tif"
                value={tif}
                onChange={(e) => setTif(e.target.value as 'day' | 'gtc')}
                options={[
                  { value: 'day', label: 'Today' },
                  { value: 'gtc', label: 'Until cancelled' },
                ]}
              />
            </FormField>
          )}
        </div>
        {canExtend && (
          <Checkbox
            id="pt-extended"
            label="Pre- and after-market too"
            checked={extended}
            onChange={(e) => setExtended(e.target.checked)}
          />
        )}
        <FormField label="Why (optional)" htmlFor="pt-note">
          <TextArea
            id="pt-note"
            rows={2}
            maxLength={280}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </FormField>
        {preview?.thesis && <div className="paper-next">{preview.thesis.label}</div>}
        {preview?.estimate && !rejected && (
          <div className="paper-preview">
            <span className="k">{orderType === 'limit' ? 'Limit' : 'Expected fill'}</span>
            <span>{preview.price != null ? preview.price.toFixed(2) : '—'}</span>
            <span className="k tot">Total</span>
            <span className="tot">{money(preview.estimate.total)}</span>
            <span className="k">Cash left after</span>
            <span>
              {preview.cash_after_home != null
                ? fCurrency(preview.cash_after_home, homeCurrency)
                : '—'}
            </span>
          </div>
        )}
        {(previewError || preview?.reason || preview?.next_line) && (
          <div className={`paper-next${previewError || rejected ? ' bad' : ''}`}>
            {previewError ?? (rejected ? preview?.reason : preview?.next_line)}
          </div>
        )}
        {preview?.warnings.map((w) => (
          <div key={w} className="paper-next">
            {w}
          </div>
        ))}
      </div>
    </Modal>
  )
}
